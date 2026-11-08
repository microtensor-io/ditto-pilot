from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from bench.classify import kind_of, load_detectors


def read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def arm_row(
    arm: str, calls: list[dict[str, Any]], results: list[dict[str, Any]], detectors: list[dict[str, Any]]
) -> dict[str, Any]:
    compactions = [c for c in calls if kind_of(c, detectors) == "compaction"]
    tasks = {(r["task"], r["run"]) for r in results}
    runs = sorted({r["run"] for r in results})
    per_run = [sum(1 for r in results if r["run"] == run and r.get("passed")) for run in runs]
    task_cost: dict[str, int] = defaultdict(int)
    for call in calls:
        task_cost[call.get("run_id", "")] += int(call.get("cost_usd_micros") or 0)
    ok = [c for c in compactions if int(c.get("status") or 0) < 400]
    return {
        "arm": arm,
        "tasks_per_run": len({r["task"] for r in results}),
        "passed_per_run": per_run,
        "passed_mean": mean([float(p) for p in per_run]),
        "compactions": len(compactions),
        "compactions_per_task": len(compactions) / len(tasks) if tasks else None,
        "cost_per_compaction_usd": (mean([float(c.get("cost_usd_micros") or 0) for c in ok]) or 0) / 1e6
        if ok
        else None,
        "cost_per_task_usd": (mean([float(v) for v in task_cost.values()]) or 0) / 1e6 if task_cost else None,
        "time_per_compaction_s": (mean([float(c.get("total_ms") or 0) for c in ok]) or 0) / 1000
        if ok
        else None,
        "errors": sum(1 for c in calls if int(c.get("status") or 0) >= 400),
        "fallbacks_413": sum(1 for c in calls if int(c.get("status") or 0) == 413),
        "stand_in_calls": sum(1 for c in calls if c.get("stand_in")),
    }


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(prog="bench-summarize")
    parser.add_argument("--logs", nargs="+", default=["logs/relay.jsonl"])
    parser.add_argument("--results", default="results")
    parser.add_argument("--detectors", default="bench/detectors.json")
    parser.add_argument("--out", default="results/summary.json")
    args = parser.parse_args()
    detectors = load_detectors(Path(args.detectors))
    calls = [c for path in args.logs for c in read(Path(path))]
    table = []
    for arm in ("A", "B"):
        results = read(Path(args.results) / f"arm-{arm}.jsonl")
        if not results:
            continue
        table.append(arm_row(arm, [c for c in calls if c.get("arm") == arm], results, detectors))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(table, indent=1), encoding="utf-8")
    head = [
        "Arm",
        "Passed per run",
        "Compactions",
        "Cost per compaction (USD)",
        "Cost per task (USD)",
        "Time per compaction (s)",
        "Errors",
        "413 fallbacks",
    ]
    print("| " + " | ".join(head) + " |")
    print("|" + " --- |" * len(head))
    for row in table:
        cells = [
            row["arm"],
            " / ".join(f"{p} of {row['tasks_per_run']}" for p in row["passed_per_run"]),
            str(row["compactions"]),
            fmt(row["cost_per_compaction_usd"]),
            fmt(row["cost_per_task_usd"]),
            fmt(row["time_per_compaction_s"], 1),
            str(row["errors"]),
            str(row["fallbacks_413"]),
        ]
        print("| " + " | ".join(cells) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
