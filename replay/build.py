from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

HEADER = re.compile(r"^\[(user|assistant|tool_use|tool_result)([^\]]*)\]$")
ATTRIBUTE = re.compile(r"(\w+)=(\S+)")
SHELL_NAMES = {"bash", "Bash", "shell", "execute_bash"}
SEARCH_NAMES = {"grep", "find", "Grep", "Glob", "search_file", "search_dir", "find_file"}
TITLE_SYSTEM = (
    "Generate a concise, sentence case title of 3 to 7 words that captures the main goal of "
    'this coding session. Reply with JSON only, in the form {"title": "..."}.'
)
TITLE_CONTEXT_CHARS = 3000
TURN_PROMPTS = (
    "Run the failing tests again and tell me which ones still fail.",
    "Summarise in two sentences what you changed so far.",
    "Which file should we look at next, and why?",
)


def blocks_of(text: str) -> list[dict[str, Any]]:
    body = text.strip()
    body = body.removeprefix("<session>")
    body = body.removesuffix("</session>")
    found: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in body.strip("\n").split("\n"):
        match = HEADER.match(line)
        if match:
            if current is not None:
                found.append(current)
            rest = match.group(2).split()
            current = {
                "kind": match.group(1),
                "status": rest[0] if rest and "=" not in rest[0] else "",
                "attrs": dict(ATTRIBUTE.findall(match.group(2))),
                "lines": [],
            }
            continue
        if current is None:
            continue
        current["lines"].append(line)
    if current is not None:
        found.append(current)
    for block in found:
        block["text"] = "\n".join(block.pop("lines")).strip("\n")
    return found


def tool_input(name: str, attrs: dict[str, str], text: str) -> dict[str, Any]:
    path = attrs.get("path", "")
    if name in SHELL_NAMES:
        return {"command": text, "path": path} if path else {"command": text}
    if name in SEARCH_NAMES:
        found: dict[str, Any] = {"pattern": text}
        if path:
            found["path"] = path
        return found
    found = {"path": path} if path else {}
    if text:
        found["description"] = text
    return found


def messages_of(text: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    pending: list[str] = []
    counter = 0

    def add(role: str, block: dict[str, Any]) -> None:
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].append(block)
        else:
            messages.append({"role": role, "content": [block]})

    for block in blocks_of(text):
        kind, attrs, body = block["kind"], block["attrs"], block["text"]
        if kind == "user":
            if body:
                add("user", {"type": "text", "text": body})
        elif kind == "assistant":
            if body:
                add("assistant", {"type": "text", "text": body})
        elif kind == "tool_use":
            counter += 1
            call_id = f"toolu_{counter:04d}"
            name = attrs.get("name", "tool")
            add(
                "assistant",
                {"type": "tool_use", "id": call_id, "name": name, "input": tool_input(name, attrs, body)},
            )
            pending.append(call_id)
        elif kind == "tool_result":
            call_id = pending.pop(0) if pending else f"toolu_orphan_{counter:04d}"
            result: dict[str, Any] = {"type": "tool_result", "tool_use_id": call_id, "content": body}
            if block["status"] in ("ok", "error"):
                result["is_error"] = block["status"] == "error"
            add("user", result)
    if messages and messages[0]["role"] != "user":
        messages.insert(0, {"role": "user", "content": [{"type": "text", "text": "Continue."}]})
    return paired(messages)


def paired(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    carry: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if message["role"] == "assistant":
            if carry:
                at = next(
                    (i for i, b in enumerate(message["content"]) if b.get("type") == "tool_use"),
                    len(message["content"]),
                )
                message["content"][at:at] = carry
                carry = []
            continue
        answered = {b.get("tool_use_id") for b in message["content"] if b.get("type") == "tool_result"}
        previous = messages[index - 1] if index else None
        if previous is None or previous["role"] != "assistant":
            continue
        late = [b for b in previous["content"] if b.get("type") == "tool_use" and b["id"] not in answered]
        kept = [b for b in previous["content"] if b not in late]
        if late and kept and index + 1 < len(messages):
            previous["content"] = kept
            carry = late
        elif late:
            message["content"][0:0] = [
                {
                    "type": "tool_result",
                    "tool_use_id": b["id"],
                    "content": "[Request interrupted by user for tool use]",
                    "is_error": True,
                }
                for b in late
            ]
    if messages and messages[-1]["role"] == "assistant":
        open_calls = [b for b in messages[-1]["content"] if b.get("type") == "tool_use"]
        if open_calls:
            messages.append({"role": "user", "content": []})
            return paired(messages)
    return messages


def compaction_request(messages: list[dict[str, Any]], instruction: str) -> dict[str, Any]:
    history = json.loads(json.dumps(messages))
    if history[-1]["role"] == "user":
        history[-1]["content"].append({"type": "text", "text": instruction})
    else:
        history.append({"role": "user", "content": [{"type": "text", "text": instruction}]})
    return {
        "model": "claude-haiku-5-5",
        "max_tokens": 20000,
        "stream": True,
        "system": [{"type": "text", "text": "You are Claude Code, an interactive coding agent."}],
        "messages": history,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }


def title_request(messages: list[dict[str, Any]]) -> dict[str, Any]:
    first = next(
        (
            item["text"]
            for message in messages
            if message["role"] == "user"
            for item in message["content"]
            if item["type"] == "text"
        ),
        "",
    )
    return {
        "model": "claude-haiku-5-5",
        "max_tokens": 256,
        "stream": True,
        "system": TITLE_SYSTEM,
        "messages": [{"role": "user", "content": f"<session>\n{first[:TITLE_CONTEXT_CHARS]}\n</session>"}],
    }


def turn_request(messages: list[dict[str, Any]], prompt: str) -> dict[str, Any]:
    history = json.loads(json.dumps(messages))
    if history[-1]["role"] == "user":
        history[-1]["content"].append({"type": "text", "text": prompt})
    else:
        history.append({"role": "user", "content": [{"type": "text", "text": prompt}]})
    return {
        "model": "claude-haiku-5-5",
        "max_tokens": 1024,
        "stream": True,
        "system": [{"type": "text", "text": "You are Claude Code, an interactive coding agent."}],
        "messages": history,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="replay-build")
    parser.add_argument("--items", required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--out", default="replay/requests.jsonl")
    parser.add_argument("--turns", type=int, default=3)
    args = parser.parse_args()
    instruction = Path(args.instruction).read_text(encoding="utf-8").strip()
    rows = [
        json.loads(line) for line in Path(args.items).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    out: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        messages = messages_of(item["task"]["user"])
        out.append(
            {
                "id": f"compaction/{item['item_id']}",
                "kind": "compaction",
                "item_id": item["item_id"],
                "body": compaction_request(messages, instruction),
            }
        )
        out.append(
            {
                "id": f"title/{item['item_id']}",
                "kind": "title",
                "item_id": item["item_id"],
                "body": title_request(messages),
            }
        )
        if index < args.turns:
            out.append(
                {
                    "id": f"turn/{item['item_id']}",
                    "kind": "turn",
                    "item_id": item["item_id"],
                    "body": turn_request(messages, TURN_PROMPTS[index % len(TURN_PROMPTS)]),
                }
            )
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in out), encoding="utf-8")
    counts: dict[str, int] = {}
    for row in out:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    print(json.dumps({"requests": len(out), **counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
