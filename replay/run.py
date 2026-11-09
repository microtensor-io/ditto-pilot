from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx

ARMS: dict[str, dict[str, Any]] = {
    "haiku": {"model": "claude-haiku-5-5", "router": "off", "kinds": ("compaction", "title")},
    "sonnet": {"model": "claude-sonnet-5-5", "router": "off", "kinds": ("compaction", "title")},
    "microtensor": {
        "model": "claude-haiku-5-5",
        "router": "on",
        "kinds": ("compaction", "title", "turn"),
    },
}
TITLE_THINKING = {"claude-haiku-5-5": {"type": "disabled"}, "claude-sonnet-5-5": {"type": "between_tools"}}
TIMEOUT = httpx.Timeout(900.0, connect=30.0)


def body_for(request: dict[str, Any], model: str) -> dict[str, Any]:
    body = json.loads(json.dumps(request["body"]))
    body["model"] = model
    if request["kind"] == "title" and model in TITLE_THINKING:
        body["thinking"] = TITLE_THINKING[model]
    return body


def send(http: httpx.Client, url: str, body: dict[str, Any], step: str) -> dict[str, Any]:
    started = time.perf_counter()
    first = 0.0
    text: list[str] = []
    usage: dict[str, Any] = {}
    status = 0
    error = ""
    model = ""
    stop = ""
    try:
        with http.stream(
            "POST",
            url.rstrip("/") + "/v1/messages",
            json=body,
            headers={"anthropic-version": "2023-06-01", "x-dittobench-step": step},
            timeout=TIMEOUT,
        ) as response:
            status = response.status_code
            if status != 200:
                error = response.read().decode("utf-8", "replace")[:600]
            else:
                event = ""
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = json.loads(line[5:].strip() or "{}")
                    kind = data.get("type") or event
                    if kind == "message_start":
                        message = data.get("message") or {}
                        model = str(message.get("model") or "")
                        usage.update(message.get("usage") or {})
                    elif kind == "content_block_delta":
                        delta = data.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            if not first:
                                first = time.perf_counter()
                            text.append(str(delta.get("text") or ""))
                    elif kind == "message_delta":
                        usage.update(data.get("usage") or {})
                        stop = str((data.get("delta") or {}).get("stop_reason") or "")
                    elif kind == "error":
                        error = json.dumps(data)[:600]
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"[:600]
    ended = time.perf_counter()
    return {
        "status": status,
        "error": error,
        "served_model": model,
        "stop_reason": stop,
        "text": "".join(text),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "ttft_ms": round((first - started) * 1000, 1) if first else None,
        "total_ms": round((ended - started) * 1000, 1),
    }


def run_arm(name: str, requests: list[dict[str, Any]], routers: dict[str, str], out: Path) -> None:
    arm = ARMS[name]
    target = out / f"{name}.jsonl"
    done = set()
    if target.is_file():
        done = {json.loads(line)["id"] for line in target.read_text(encoding="utf-8").splitlines() if line}
    with httpx.Client() as http, target.open("a", encoding="utf-8") as handle:
        for request in requests:
            if request["kind"] not in arm["kinds"] or request["id"] in done:
                continue
            found = send(
                http, routers[arm["router"]], body_for(request, arm["model"]), f"{name}/{request['id']}"
            )
            row = {
                "arm": name,
                "id": request["id"],
                "kind": request["kind"],
                "item_id": request["item_id"],
                "requested_model": arm["model"],
                **found,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(
                name,
                request["id"],
                found["status"],
                found["served_model"],
                found["output_tokens"],
                found["total_ms"],
                found["error"][:120],
                flush=True,
            )


def main() -> int:
    parser = argparse.ArgumentParser(prog="replay-run")
    parser.add_argument("--requests", default="replay/requests.jsonl")
    parser.add_argument("--router-off", required=True)
    parser.add_argument("--router-on", required=True)
    parser.add_argument("--arms", default="haiku,sonnet,microtensor")
    parser.add_argument("--only", default="")
    parser.add_argument("--ids", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default="results/outputs")
    args = parser.parse_args()
    requests = [
        json.loads(line)
        for line in Path(args.requests).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.only:
        wanted = set(args.only.split(","))
        requests = [r for r in requests if r["kind"] in wanted]
    if args.ids:
        chosen = set(args.ids.split(","))
        requests = [r for r in requests if r["id"] in chosen]
    if args.limit:
        seen: dict[str, int] = {}
        kept = []
        for request in requests:
            seen[request["kind"]] = seen.get(request["kind"], 0) + 1
            if seen[request["kind"]] <= args.limit:
                kept.append(request)
        requests = kept
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    routers = {"off": args.router_off, "on": args.router_on}
    threads = [
        threading.Thread(target=run_arm, args=(name, requests, routers, out))
        for name in args.arms.split(",")
        if name
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
