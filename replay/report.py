from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

from replay import charts
from replay.grade_prep import CONTESTANTS

ARM_LABELS = {
    "haiku": "Claude Haiku 5.5 (default)",
    "sonnet": "Claude Sonnet 5.5 (default)",
    "microtensor": "Microtensor route table",
}
TABLE_START = "<!-- results:start -->"
TABLE_END = "<!-- results:end -->"
TITLE_JSON = re.compile(r"\{.*\}", re.DOTALL)


def rows_of(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, round(share * (len(ordered) - 1))))]


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def costs_of(log: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for row in rows_of(log):
        step = str(row.get("step") or "")
        if step:
            found[step] = row
    return found


def title_of(text: str) -> str:
    match = TITLE_JSON.search(text or "")
    if not match:
        return ""
    try:
        return str(json.loads(match.group(0)).get("title") or "").strip()
    except (ValueError, AttributeError):
        return ""


def no_budget_score(mark: dict[str, Any]) -> float:
    continuation = mark.get("continuation")
    if continuation is None:
        continuation = 0.0
    return (
        float(mark["format_valid"])
        * (1 - float(mark["hallucination"]))
        * (0.5 * float(mark["recall"]) + 0.5 * float(continuation))
    )


def compaction_line(
    arm: str,
    outputs: list[dict[str, Any]],
    graded: dict[str, dict[str, Any]],
    costs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = [r for r in outputs if r["kind"] == "compaction"]
    ok = [r for r in rows if r["status"] == 200 and not r["error"] and r["text"]]
    marks = [graded[r["item_id"]] for r in ok if r["item_id"] in graded]
    spent = [
        float(costs[f"{arm}/{r['id']}"].get("cost_usd_micros") or 0) / 1_000_000
        for r in ok
        if f"{arm}/{r['id']}" in costs
    ]
    times = [float(r["total_ms"]) / 1000 for r in ok]
    passed = sum(1 for m in marks if m["passed"])
    return {
        "arm": arm,
        "label": ARM_LABELS[arm],
        "requests": len(rows),
        "answered": len(ok),
        "errors": len(rows) - len(ok),
        "graded": len(marks),
        "passed": passed,
        "pass_rate": passed / len(marks) if marks else None,
        "nine_headings": sum(1 for m in marks if m["format_valid"]),
        "within_budget": sum(1 for m in marks if m["within_budget"]),
        "recall": mean([float(m["recall"]) for m in marks]),
        "hallucination": mean([float(m["hallucination"]) for m in marks]),
        "continuation": mean([float(m["continuation"]) for m in marks if m.get("continuation") is not None]),
        "item_score": mean([float(m["item_score"]) for m in marks]),
        "score_without_budget_gate": mean([no_budget_score(m) for m in marks]),
        "cost_per_compaction_usd": mean(spent),
        "median_time_s": percentile(times, 0.5),
        "p95_time_s": percentile(times, 0.95),
        "output_tokens_mean": mean([float(r["output_tokens"]) for r in ok]),
    }


def title_line(arm: str, outputs: list[dict[str, Any]], costs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in outputs if r["kind"] == "title"]
    ok = [r for r in rows if r["status"] == 200 and not r["error"]]
    titles = [title_of(r["text"]) for r in ok]
    sized = [t for t in titles if 3 <= len(t.split()) <= 7]
    spent = [
        float(costs[f"{arm}/{r['id']}"].get("cost_usd_micros") or 0) / 1_000_000
        for r in ok
        if f"{arm}/{r['id']}" in costs
    ]
    return {
        "arm": arm,
        "requests": len(rows),
        "answered": len(ok),
        "valid_json_title": sum(1 for t in titles if t),
        "three_to_seven_words": len(sized),
        "median_time_s": percentile([float(r["total_ms"]) / 1000 for r in ok], 0.5),
        "cost_per_title_usd": mean(spent),
        "served_by": sorted({str(costs.get(f"{arm}/{r['id']}", {}).get("route_id") or "") for r in ok}),
    }


def fmt(value: Any, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "usd":
        return f"${value:.4f}"
    if kind == "s":
        return f"{value:.1f} s"
    if kind == "f":
        return f"{value:.2f}"
    return str(value)


def table(lines: list[dict[str, Any]], titles: list[dict[str, Any]], turns: dict[str, Any]) -> str:
    head = "| | " + " | ".join(line["label"] for line in lines) + " |"
    rule = "|---|" + "---|" * len(lines)
    spec = [
        ("Compactions answered", "answered", ""),
        ("Passed the arena grader", "passed", ""),
        ("Pass rate", "pass_rate", "pct"),
        ("Kept all 9 headings", "nine_headings", ""),
        ("Within the arena budget", "within_budget", ""),
        ("Recall of key facts", "recall", "pct"),
        ("Hallucination", "hallucination", "pct"),
        ("Continuation", "continuation", "f"),
        ("Score without the budget rule", "score_without_budget_gate", "f"),
        ("Cost per compaction", "cost_per_compaction_usd", "usd"),
        ("Median time", "median_time_s", "s"),
        ("p95 time", "p95_time_s", "s"),
        ("Errors", "errors", ""),
    ]
    out = [head, rule]
    for label, key, kind in spec:
        out.append(f"| {label} | " + " | ".join(fmt(line.get(key), kind) for line in lines) + " |")
    title_rows = [
        "",
        "| Titles | " + " | ".join(ARM_LABELS[t["arm"]] for t in titles) + " |",
        "|---|" + "---|" * len(titles),
        "| Answered | " + " | ".join(str(t["answered"]) for t in titles) + " |",
        "| Valid JSON title | " + " | ".join(str(t["valid_json_title"]) for t in titles) + " |",
        "| 3 to 7 words | " + " | ".join(str(t["three_to_seven_words"]) for t in titles) + " |",
        "| Median time | " + " | ".join(fmt(t["median_time_s"], "s") for t in titles) + " |",
        "| Cost per title | " + " | ".join(fmt(t["cost_per_title_usd"], "usd") for t in titles) + " |",
    ]
    turn_rows = [
        "",
        (
            f"Normal turns through the route table: {turns['answered']} of {turns['requests']} answered, "
            f"{turns['untouched']} served by the model they asked for, so the table left them alone."
        ),
    ]
    return "\n".join(out + title_rows + turn_rows)


def main() -> int:
    parser = argparse.ArgumentParser(prog="replay-report")
    parser.add_argument("--outputs", default="results/outputs")
    parser.add_argument("--graded", default="results/work/graded")
    parser.add_argument("--relay-log", default="results/relay.jsonl")
    parser.add_argument("--items", required=True)
    parser.add_argument("--readme", default="README.md")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()
    costs = costs_of(Path(args.relay_log))
    items = {r["item_id"]: r for r in rows_of(Path(args.items))}
    outputs = {arm: rows_of(Path(args.outputs) / f"{arm}.jsonl") for arm in CONTESTANTS}
    graded = {
        arm: {r["item_id"]: r for r in rows_of(Path(args.graded) / f"{contestant}.jsonl")}
        for arm, contestant in CONTESTANTS.items()
    }
    lines = [compaction_line(arm, outputs[arm], graded[arm], costs) for arm in CONTESTANTS if outputs[arm]]
    titles = [title_line(arm, outputs[arm], costs) for arm in CONTESTANTS if outputs[arm]]
    turn_rows = [r for r in outputs.get("microtensor", []) if r["kind"] == "turn"]
    turns = {
        "requests": len(turn_rows),
        "answered": sum(1 for r in turn_rows if r["status"] == 200 and not r["error"]),
        "untouched": sum(
            1 for r in turn_rows if str(r.get("served_model") or "").startswith(r["requested_model"])
        ),
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps({"compaction": lines, "titles": titles, "turns": turns}, indent=1), encoding="utf-8"
    )
    drawn = charts.draw(lines, rows_of(Path(args.relay_log)), graded, out / "charts")
    rendered = table(lines, titles, turns) + "\n\n### Charts\n\n" + charts.markdown(drawn, "results/charts/")
    (out / "table.md").write_text(rendered + "\n", encoding="utf-8")
    readme = Path(args.readme)
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        block = f"{TABLE_START}\n{rendered}\n{TABLE_END}"
        if TABLE_START in text and TABLE_END in text:
            text = text[: text.index(TABLE_START)] + block + text[text.index(TABLE_END) + len(TABLE_END) :]
        else:
            first = text.find("\n## ")
            text = (
                text[:first] + "\n\n" + block + "\n" + text[first:]
                if first >= 0
                else text + "\n" + block + "\n"
            )
        readme.write_text(text, encoding="utf-8")
    hf = out / "hf"
    (hf / "charts").mkdir(parents=True, exist_ok=True)
    for chart in drawn:
        for suffix in (".png", ".csv"):
            source = out / "charts" / (chart["name"] + suffix)
            (hf / "charts" / source.name).write_bytes(source.read_bytes())
    (hf / "charts.md").write_text(charts.markdown(drawn, "charts/") + "\n", encoding="utf-8")
    by_arm = {arm: {r["item_id"]: r for r in outputs[arm] if r["kind"] == "compaction"} for arm in outputs}
    compaction_rows = []
    for item_id, item in items.items():
        row: dict[str, Any] = {
            "item_id": item_id,
            "set": item["label"].get("paper", {}).get("set", ""),
            "session_tokens": item["label"].get("input_tokens"),
            "budget_tokens": item["label"].get("budget_tokens"),
            "session": item["task"]["user"],
        }
        for arm in CONTESTANTS:
            answer = by_arm.get(arm, {}).get(item_id) or {}
            mark = graded.get(arm, {}).get(item_id) or {}
            cost = costs.get(f"{arm}/compaction/{item_id}", {})
            row[f"{arm}_summary"] = answer.get("text", "")
            row[f"{arm}_passed"] = mark.get("passed")
            row[f"{arm}_item_score"] = mark.get("item_score")
            row[f"{arm}_nine_headings"] = mark.get("format_valid")
            row[f"{arm}_within_budget"] = mark.get("within_budget")
            row[f"{arm}_recall"] = mark.get("recall")
            row[f"{arm}_hallucination"] = mark.get("hallucination")
            row[f"{arm}_continuation"] = mark.get("continuation")
            row[f"{arm}_cost_usd"] = (float(cost.get("cost_usd_micros") or 0) / 1_000_000) if cost else None
            row[f"{arm}_time_s"] = (float(answer["total_ms"]) / 1000) if answer.get("total_ms") else None
            row[f"{arm}_output_tokens"] = answer.get("output_tokens")
        compaction_rows.append(row)
    (hf / "compaction.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in compaction_rows), encoding="utf-8"
    )
    title_rows = []
    for item_id in items:
        row = {"item_id": item_id}
        for arm in CONTESTANTS:
            answer = next(
                (r for r in outputs.get(arm, []) if r["kind"] == "title" and r["item_id"] == item_id), {}
            )
            row[f"{arm}_title"] = title_of(answer.get("text", ""))
            row[f"{arm}_raw"] = answer.get("text", "")
            row[f"{arm}_time_s"] = (float(answer["total_ms"]) / 1000) if answer.get("total_ms") else None
        row["microtensor_route"] = (
            "microtensor/session_title, stand in (Qwen2.5 1.5B Instruct), not certified"
        )
        title_rows.append(row)
    (hf / "titles.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in title_rows), encoding="utf-8"
    )
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
