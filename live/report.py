from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from matplotlib.figure import Figure

from replay.charts import GRID, MUTED, TEXT, heading, save, style, write_csv

ARM_COLORS = {"A": "#eb6834", "B": "#1baf7a"}
ARM_LABELS = {"A": "A: router off, all Claude", "B": "B: router on, compaction to Microtensor"}


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def notes_of(row: dict[str, Any]) -> str:
    found = []
    if row.get("stopped"):
        found.append(row["stopped"])
    subtype = (row.get("result") or {}).get("subtype")
    if subtype and subtype != "success":
        found.append(str(subtype))
    if row.get("compaction_requests", 0) > row.get("compaction_ok", 0):
        found.append(f"{row['compaction_requests'] - row['compaction_ok']} compaction requests failed")
    if row.get("relay_errors"):
        found.append(f"{len(row['relay_errors'])} relay errors: " + "; ".join(row["relay_errors"][:2]))
    if not row.get("hidden_applied", True):
        found.append("hidden tests did not apply")
    found.append("no fallback (fallbacks are off)")
    return " | ".join(found)


def rows_of(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ran = [s for s in sessions if s.get("arm")]
    by_task: dict[str, list[dict[str, Any]]] = {}
    for row in ran:
        by_task.setdefault(row["task_id"], []).append(row)
    found = []
    for row in ran:
        pair = by_task[row["task_id"]]
        counted = len(pair) == 2 and all(r["compact_boundaries"] > 0 for r in pair)
        requests = row["compaction_requests"]
        found.append(
            {
                "task_id": row["task_id"],
                "arm": row["arm"],
                "router": row["router"],
                "model": row["model"],
                "result": "passed" if row["passed"] else "failed",
                "compactions": row["compact_boundaries"],
                "compaction_requests": requests,
                "cost_per_compaction_usd": round(row["compaction_cost_usd"] / requests, 6)
                if requests
                else "",
                "total_cost_usd": round(row["cost_usd"], 4),
                "total_time_s": round(row["seconds"], 1),
                "turns": (row.get("result") or {}).get("num_turns"),
                "counted": "yes" if counted else "no, did not compact in both arms",
                "errors_or_fallbacks": notes_of(row),
            }
        )
    return found


def summary_of(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counted = [r for r in rows if r["counted"] == "yes"]
    found = []
    for arm in ("A", "B"):
        mine = [r for r in counted if r["arm"] == arm]
        if not mine:
            continue
        found.append(
            {
                "arm": arm,
                "label": ARM_LABELS[arm],
                "tasks": len(mine),
                "passed": sum(1 for r in mine if r["result"] == "passed"),
                "mean_cost_usd": round(sum(r["total_cost_usd"] for r in mine) / len(mine), 4),
                "mean_time_min": round(sum(r["total_time_s"] for r in mine) / len(mine) / 60, 1),
                "compactions": sum(r["compactions"] for r in mine),
            }
        )
    return found


def passed_chart(summary: list[dict[str, Any]], out: Path) -> None:
    fig = Figure(figsize=(9, 2.8), layout="tight")
    ax = fig.subplots()
    names = [s["label"] for s in summary]
    values = [s["passed"] for s in summary]
    bars = ax.barh(names, values, color=[ARM_COLORS[s["arm"]] for s in summary], height=0.5)
    for bar, line in zip(bars, summary, strict=True):
        ax.text(
            bar.get_width(),
            bar.get_y() + bar.get_height() / 2,
            f"  {line['passed']} of {line['tasks']}",
            va="center",
            color=TEXT,
        )
    ax.invert_yaxis()
    ax.set_xlim(0, max([s["tasks"] for s in summary] or [1]) * 1.25)
    ax.set_xlabel("tasks passed (hidden tests, run after the session)")
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    heading(
        ax,
        "Tasks passed per arm",
        "Live Claude Code 2.1.159 sessions that compacted at least once in both arms.",
    )
    save(fig, out / "tasks_passed.png")
    write_csv(
        out / "tasks_passed.csv",
        ["arm", "tasks", "passed"],
        [[s["label"], s["tasks"], s["passed"]] for s in summary],
    )


def cost_chart(rows: list[dict[str, Any]], out: Path) -> None:
    counted = [r for r in rows if r["counted"] == "yes"]
    tasks = list(dict.fromkeys(r["task_id"] for r in counted))
    fig = Figure(figsize=(9, 1.6 + 0.75 * max(len(tasks), 1)), layout="tight")
    ax = fig.subplots()
    height = 0.36
    for offset, arm in ((-height / 2, "A"), (height / 2, "B")):
        values = [
            next((r["total_cost_usd"] for r in counted if r["task_id"] == t and r["arm"] == arm), 0)
            for t in tasks
        ]
        positions = [i + offset for i in range(len(tasks))]
        bars = ax.barh(positions, values, height=height - 0.04, color=ARM_COLORS[arm], label=ARM_LABELS[arm])
        for bar, value in zip(bars, values, strict=True):
            ax.text(
                bar.get_width(),
                bar.get_y() + bar.get_height() / 2,
                f"  ${value:.2f}",
                va="center",
                color=TEXT,
                fontsize=9,
            )
    ax.set_yticks(range(len(tasks)), [t.split("__")[1] for t in tasks])
    ax.invert_yaxis()
    top = max([r["total_cost_usd"] for r in counted] or [1])
    ax.set_xlim(0, top * 1.3)
    ax.set_xlabel("USD per session at list price (relay log)")
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(0, -0.3),
        ncol=2,
        frameon=False,
        fontsize=9,
        labelcolor=MUTED,
    )
    heading(ax, "Cost per task per arm", "Whole session cost: main model turns plus compactions.")
    save(fig, out / "cost_per_task.png")
    write_csv(
        out / "cost_per_task.csv",
        ["task_id", "arm", "total_cost_usd"],
        [[r["task_id"], ARM_LABELS[r["arm"]], r["total_cost_usd"]] for r in counted],
    )


def table(summary: list[dict[str, Any]]) -> str:
    lines = [
        "| Arm | Tasks passed | Average cost per task | Average time per task | Compactions |",
        "| --- | --- | --- | --- | --- |",
    ]
    for s in summary:
        lines.append(
            f"| {s['label']} | {s['passed']} of {s['tasks']} | ${s['mean_cost_usd']:.2f} | {s['mean_time_min']} min | {s['compactions']} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="live-report")
    parser.add_argument("--run", default="results/live/run")
    parser.add_argument("--out", default="results/live")
    args = parser.parse_args()
    out = Path(args.out)
    sessions = load(Path(args.run) / "sessions.jsonl")
    rows = rows_of(sessions)
    summary = summary_of(rows)
    header = list(rows[0]) if rows else ["task_id"]
    write_csv(out / "sessions.csv", header, [[r[k] for k in header] for r in rows])
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "summary.md").write_text(table(summary) + "\n", encoding="utf-8")
    style()
    charts = out / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    if summary:
        passed_chart(summary, charts)
        cost_chart(rows, charts)
    print(table(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
