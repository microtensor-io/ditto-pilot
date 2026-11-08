from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib
from matplotlib.figure import Figure

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e4e3df"
COLORS = {"haiku": "#2a78d6", "sonnet": "#eb6834", "microtensor": "#1baf7a"}
LABELS = {"haiku": "Claude Haiku 5.5", "sonnet": "Claude Sonnet 5.5", "microtensor": "Microtensor"}
ROUTES = (
    ("claude-haiku-5-5", "Claude Haiku 5.5", "#2a78d6", ""),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5", "#eb6834", ""),
    ("microtensor/ctx_compress", "microtensor/ctx_compress (certified)", "#1baf7a", ""),
    ("microtensor/session_title", "microtensor/session_title (stand in)", "#eda100", "//"),
)
KINDS = (("compaction", "Compaction"), ("title", "Title"), ("turn", "Normal turn"))
DPI = 160


def style() -> None:
    matplotlib.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": GRID,
            "axes.labelcolor": MUTED,
            "xtick.color": MUTED,
            "ytick.color": TEXT,
            "text.color": TEXT,
            "font.size": 10,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
        }
    )


def heading(ax: Any, title: str, subtitle: str) -> None:
    ax.set_title(title, loc="left", pad=26)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=MUTED, fontsize=9, va="bottom")


def write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def save(fig: Any, path: Path) -> None:
    fig.savefig(path, dpi=DPI)


def cost_chart(lines: list[dict[str, Any]], out: Path) -> dict[str, str]:
    present = [line for line in lines if line.get("cost_per_compaction_usd") is not None]
    fig = Figure(figsize=(9, 3.2), layout="tight")
    ax = fig.subplots()
    names = [LABELS[line["arm"]] for line in present]
    values = [line["cost_per_compaction_usd"] for line in present]
    bars = ax.barh(names, values, color=[COLORS[line["arm"]] for line in present], height=0.5)
    for bar, value in zip(bars, values, strict=True):
        ax.text(
            bar.get_width(), bar.get_y() + bar.get_height() / 2, f"  ${value:.4f}", va="center", color=TEXT
        )
    ax.invert_yaxis()
    ax.set_xlabel("USD per compaction")
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlim(0, max(values or [0.001]) * 1.3)
    heading(
        ax,
        "Cost per compaction",
        "Mean over the 36 sessions. Claude at list price from the relay log; Microtensor at the pilot price of $0.005.",
    )
    save(fig, out / "cost_per_compaction.png")
    write_csv(
        out / "cost_per_compaction.csv",
        ["model", "cost_per_compaction_usd", "compactions_answered"],
        [[LABELS[line["arm"]], line["cost_per_compaction_usd"], line["answered"]] for line in present],
    )
    return {"name": "cost_per_compaction", "title": "Cost per compaction, Haiku vs Sonnet vs Microtensor"}


def quality_cost_chart(lines: list[dict[str, Any]], out: Path) -> dict[str, str]:
    present = [
        line for line in lines if line.get("cost_per_compaction_usd") and line.get("pass_rate") is not None
    ]
    fig = Figure(figsize=(9, 4.4), layout="tight")
    ax = fig.subplots()
    for line in present:
        x, y = line["cost_per_compaction_usd"], line["pass_rate"] * 100
        ax.scatter([x], [y], s=140, color=COLORS[line["arm"]], edgecolors=SURFACE, linewidths=2, zorder=3)
        ax.annotate(
            f"{LABELS[line['arm']]}\n{y:.1f}% pass, ${x:.4f}",
            (x, y),
            xytext=(10, 6),
            textcoords="offset points",
            fontsize=9,
            color=TEXT,
        )
    ax.set_xscale("log")
    ax.set_xlabel("USD per compaction (log scale)")
    ax.set_ylabel("Passed the arena grader, %")
    ax.set_ylim(0, 100)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    heading(ax, "Quality against cost", "One dot per model. Up and to the left is better.")
    save(fig, out / "quality_vs_cost.png")
    write_csv(
        out / "quality_vs_cost.csv",
        ["model", "cost_per_compaction_usd", "pass_rate", "mean_item_score", "score_without_budget_rule"],
        [
            [
                LABELS[line["arm"]],
                line["cost_per_compaction_usd"],
                line["pass_rate"],
                line.get("item_score"),
                line.get("score_without_budget_gate"),
            ]
            for line in present
        ],
    )
    return {"name": "quality_vs_cost", "title": "Quality against cost, one dot per model"}


def time_chart(lines: list[dict[str, Any]], out: Path) -> dict[str, str]:
    present = [line for line in lines if line.get("median_time_s") is not None]
    fig = Figure(figsize=(9, 3.2), layout="tight")
    ax = fig.subplots()
    for row, line in enumerate(present):
        median, p95 = line["median_time_s"], line["p95_time_s"]
        color = COLORS[line["arm"]]
        ax.plot([median, p95], [row, row], color=color, linewidth=2, zorder=2)
        ax.scatter([median], [row], s=90, color=color, zorder=3, label="Median" if row == 0 else None)
        ax.scatter(
            [p95],
            [row],
            s=90,
            facecolors=SURFACE,
            edgecolors=color,
            linewidths=2,
            zorder=3,
            label="p95" if row == 0 else None,
        )
        ax.text(median, row, f"{median:.1f} s   ", ha="right", va="center", color=TEXT, fontsize=9)
        ax.text(p95, row, f"   {p95:.1f} s", ha="left", va="center", color=MUTED, fontsize=9)
    ax.set_yticks(range(len(present)), [LABELS[line["arm"]] for line in present])
    ax.invert_yaxis()
    ax.set_ylim(len(present) - 0.4, -0.7)
    ax.set_xlabel("Seconds, request to last byte")
    ax.set_xlim(0, max([line["p95_time_s"] for line in present] or [1]) * 1.3)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    legend = ax.legend(loc="lower right", frameon=False)
    for handle in legend.legend_handles:
        handle.set_edgecolor(MUTED)
    heading(ax, "Time per compaction", "Filled dot median, open dot p95, over the 36 sessions.")
    save(fig, out / "time_per_compaction.png")
    write_csv(
        out / "time_per_compaction.csv",
        ["model", "median_time_s", "p95_time_s"],
        [[LABELS[line["arm"]], line["median_time_s"], line["p95_time_s"]] for line in present],
    )
    return {"name": "time_per_compaction", "title": "Time per compaction, median and p95 per model"}


def routes_chart(relay: list[dict[str, Any]], out: Path) -> dict[str, str]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in relay:
        step = str(row.get("step") or "")
        if "/" not in step or row.get("path", "/v1/messages") != "/v1/messages":
            continue
        kind = step.split("/", 2)[1]
        counts[(kind, str(row.get("route_id") or ""))] += 1
    fig = Figure(figsize=(9, 3.4), layout="tight")
    ax = fig.subplots()
    names = [label for _, label in KINDS]
    left = [0] * len(KINDS)
    for route, label, color, hatch in ROUTES:
        values = [counts.get((kind, route), 0) for kind, _ in KINDS]
        if not any(values):
            continue
        bars = ax.barh(
            names,
            values,
            left=left,
            color=color,
            hatch=hatch,
            edgecolor=SURFACE,
            linewidth=2,
            height=0.55,
            label=label,
        )
        widest = max(1, max(sum(counts.get((kind, r), 0) for r, _, _, _ in ROUTES) for kind, _ in KINDS))
        for bar, value in zip(bars, values, strict=True):
            if not value:
                continue
            narrow = value < 0.06 * widest
            ax.text(
                bar.get_x() + (bar.get_width() if narrow else bar.get_width() / 2),
                bar.get_y() + bar.get_height() / 2,
                f"  {value}" if narrow else str(value),
                ha="left" if narrow else "center",
                va="center",
                color=TEXT,
                fontsize=9,
            )
        left = [a + b for a, b in zip(left, values, strict=True)]
    ax.invert_yaxis()
    ax.set_xlabel("Requests")
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.3), ncol=2, frameon=False, fontsize=9)
    heading(
        ax,
        "Requests by type and the route that served them",
        "From the relay log: every request in all three arms.",
    )
    save(fig, out / "requests_by_type_and_route.png")
    write_csv(
        out / "requests_by_type_and_route.csv",
        ["request_type", "route_id", "requests"],
        [[kind, route, count] for (kind, route), count in sorted(counts.items())],
    )
    return {"name": "requests_by_type_and_route", "title": "Requests by type and the route that served them"}


def measures_chart(lines: list[dict[str, Any]], out: Path) -> dict[str, str]:
    measures = (
        ("Passed the grader", lambda line: line["pass_rate"]),
        (
            "Kept all 9 headings",
            lambda line: line["nine_headings"] / line["graded"] if line["graded"] else None,
        ),
        (
            "Within the budget",
            lambda line: line["within_budget"] / line["graded"] if line["graded"] else None,
        ),
        ("Recall of key facts", lambda line: line["recall"]),
        (
            "No invented identifiers",
            lambda line: 1 - line["hallucination"] if line["hallucination"] is not None else None,
        ),
    )
    present = [line for line in lines if line.get("graded")]
    fig = Figure(figsize=(9, 5.2), layout="tight")
    ax = fig.subplots()
    height = 0.8 / max(1, len(present))
    rows = []
    for index, line in enumerate(present):
        values = [fn(line) for _, fn in measures]
        positions = [row + index * height for row in range(len(measures))]
        shown = [0 if v is None else v * 100 for v in values]
        ax.barh(
            positions,
            shown,
            height=height * 0.9,
            color=COLORS[line["arm"]],
            edgecolor=SURFACE,
            linewidth=1,
            label=LABELS[line["arm"]],
        )
        for position, value in zip(positions, values, strict=True):
            if value is not None:
                ax.text(value * 100 + 1, position, f"{value * 100:.0f}%", va="center", fontsize=8, color=TEXT)
        rows += [
            [LABELS[line["arm"]], name, value] for (name, _), value in zip(measures, values, strict=True)
        ]
    ax.set_yticks([row + 0.4 - height / 2 for row in range(len(measures))], [name for name, _ in measures])
    ax.invert_yaxis()
    ax.set_xlim(0, 110)
    ax.set_xlabel("%")
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, frameon=False)
    heading(
        ax,
        "Quality by measure",
        "Arena grader on the 36 compactions, the same grader as microtensor-comparisons exp1 and exp2.",
    )
    save(fig, out / "quality_by_measure.png")
    write_csv(out / "quality_by_measure.csv", ["model", "measure", "share"], rows)
    return {"name": "quality_by_measure", "title": "Quality by measure per model"}


def sessions_chart(graded: dict[str, dict[str, dict[str, Any]]], out: Path) -> dict[str, str]:
    arms = [arm for arm in ("haiku", "sonnet", "microtensor") if graded.get(arm)]
    items = sorted({item for arm in arms for item in graded[arm]})
    anchor = "sonnet" if "sonnet" in arms else arms[0]
    items.sort(key=lambda item: -float(graded[anchor].get(item, {}).get("item_score") or 0))
    fig = Figure(figsize=(9, max(4, 0.26 * len(items) + 1.5)), layout="tight")
    ax = fig.subplots()
    rows = []
    for arm in arms:
        xs = [float(graded[arm].get(item, {}).get("item_score") or 0) for item in items]
        ax.scatter(
            xs,
            range(len(items)),
            s=36,
            color=COLORS[arm],
            edgecolors=SURFACE,
            linewidths=1.5,
            label=LABELS[arm],
            zorder=3,
        )
        rows += [
            [item, LABELS[arm], x, graded[arm].get(item, {}).get("passed")]
            for item, x in zip(items, xs, strict=True)
        ]
    ax.axvline(0.6, color=MUTED, linewidth=1, linestyle=(0, (4, 3)), zorder=1)
    ax.text(0.6, -1.2, " pass mark 0.60", color=MUTED, fontsize=8)
    ax.set_yticks(range(len(items)), [item.replace("exp1-", "") for item in items], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.set_xlabel("Item score from the arena grader")
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right", frameon=False)
    heading(ax, "Each session's score", "One row per session, sorted by the Sonnet score.")
    save(fig, out / "session_scores.png")
    write_csv(out / "session_scores.csv", ["item_id", "model", "item_score", "passed"], rows)
    return {"name": "session_scores", "title": "Each session's score across the three models"}


def draw(
    lines: list[dict[str, Any]],
    relay: list[dict[str, Any]],
    graded: dict[str, dict[str, dict[str, Any]]],
    out: Path,
) -> list[dict[str, str]]:
    style()
    out.mkdir(parents=True, exist_ok=True)
    return [
        cost_chart(lines, out),
        quality_cost_chart(lines, out),
        time_chart(lines, out),
        routes_chart(relay, out),
        measures_chart(lines, out),
        sessions_chart(graded, out),
    ]


def markdown(charts: list[dict[str, str]], prefix: str) -> str:
    parts = []
    for chart in charts:
        parts.append(
            f"![{chart['title']}]({prefix}{chart['name']}.png)\n\n"
            f"Numbers: [{chart['name']}.csv]({prefix}{chart['name']}.csv)"
        )
    return "\n\n".join(parts)
