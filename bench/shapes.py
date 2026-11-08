from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bench.classify import load_detectors, request_type
from relay.app import shape_of

COLUMNS = (
    "file",
    "route_id",
    "tools",
    "tool_choice",
    "max_tokens",
    "messages",
    "last_user_chars",
    "system_chars",
    "stream",
    "thinking",
    "detected",
)


def rows(folder: Path, detectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for path in sorted(folder.glob("*.json")):
        if path.name.endswith(".meta.json"):
            continue
        raw = path.read_bytes()
        meta_path = path.with_name(path.name[: -len(".json")] + ".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        shape = shape_of(raw)
        found.append(
            {
                "file": path.name,
                "route_id": meta.get("route_id", ""),
                **shape,
                "detected": request_type(shape, detectors),
            }
        )
    return found


def main() -> int:
    parser = argparse.ArgumentParser(prog="bench-shapes")
    parser.add_argument("captures")
    parser.add_argument("--detectors", default="bench/detectors.json")
    args = parser.parse_args()
    table = rows(Path(args.captures), load_detectors(Path(args.detectors)))
    print("| " + " | ".join(COLUMNS) + " |")
    print("|" + " --- |" * len(COLUMNS))
    for row in table:
        print("| " + " | ".join(str(row.get(name, "")) for name in COLUMNS) + " |")
    print(f"\n{len(table)} captured requests", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
