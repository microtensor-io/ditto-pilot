from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

HEADINGS = (
    "Primary request and intent",
    "Key technical concepts",
    "Files and code sections",
    "Errors and fixes",
    "Problem solving",
    "All user messages",
    "Pending tasks",
    "Current work",
    "Next step",
)
CLAUDE_CODE_NAMES = (
    "primary request",
    "key technical",
    "files and code",
    "errors and fix",
    "problem solving",
    "all user messages",
    "pending tasks",
    "current work",
    "next step",
)
NUMBERED = re.compile(r"^\s*(?:#+\s*)?\**\s*([1-9])\.\s*\**\s*([^:\n]*?)\**\s*:\s*\**\s*(.*)$")
SUMMARY = re.compile(r"<summary>(.*?)(?:</summary>|\Z)", re.DOTALL)
CONTESTANTS = {"haiku": "HAIKU", "sonnet": "SONNET", "microtensor": "MT"}


def name_matches(index: int, title: str) -> bool:
    lowered = title.lower().replace("optional ", "")
    return CLAUDE_CODE_NAMES[index - 1] in lowered


def to_headings(text: str) -> tuple[str, int]:
    found = SUMMARY.search(text)
    body = found.group(1) if found else text
    sections: dict[int, list[str]] = {}
    order: list[int] = []
    current = 0
    for line in body.splitlines():
        hit = NUMBERED.match(line)
        if hit:
            number = int(hit.group(1))
            if number == len(order) + 1 and name_matches(number, hit.group(2)):
                current = number
                order.append(number)
                sections[number] = [hit.group(3)] if hit.group(3).strip() else []
                continue
        if current:
            sections[current].append(line)
    if not order:
        return text.strip(), 0
    parts = [f"## {HEADINGS[i - 1]}\n" + "\n".join(sections[i]).strip() for i in order]
    return "\n\n".join(parts).strip() + "\n", len(order)


def main() -> int:
    parser = argparse.ArgumentParser(prog="replay-grade-prep")
    parser.add_argument("--outputs", default="results/outputs")
    parser.add_argument("--work", default="results/work")
    args = parser.parse_args()
    target = Path(args.work) / "outputs"
    target.mkdir(parents=True, exist_ok=True)
    for arm, contestant in CONTESTANTS.items():
        source = Path(args.outputs) / f"{arm}.jsonl"
        if not source.is_file():
            continue
        rows: list[dict[str, Any]] = []
        sections_found: list[int] = []
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["kind"] != "compaction":
                continue
            text, count = to_headings(row.get("text") or "")
            sections_found.append(count)
            rows.append(
                {
                    "contestant": contestant,
                    "item_id": row["item_id"],
                    "text": text,
                    "raw_text": row.get("text") or "",
                    "sections_found": count,
                    "input_tokens": row.get("input_tokens"),
                    "output_tokens": row.get("output_tokens"),
                    "ttft_ms": row.get("ttft_ms"),
                    "total_ms": row.get("total_ms"),
                    **({"error": row["error"]} if row.get("error") or row.get("status") != 200 else {}),
                }
            )
        (target / f"{contestant}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
        print(
            contestant, len(rows), "rows,", sum(1 for c in sections_found if c == 9), "with all nine sections"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
