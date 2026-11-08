from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_detectors(path: Path) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8")).get("detectors") or [])


def _holds(value: Any, rule: dict[str, Any]) -> bool:
    if "eq" in rule and value != rule["eq"]:
        return False
    if value is None and ("min" in rule or "max" in rule):
        return False
    if "min" in rule and value < rule["min"]:
        return False
    return not ("max" in rule and value > rule["max"])


def request_type(shape: dict[str, Any], detectors: list[dict[str, Any]]) -> str:
    if not shape:
        return ""
    for detector in detectors:
        if all(_holds(shape.get(name), rule) for name, rule in (detector.get("match") or {}).items()):
            return str(detector.get("name", ""))
    return "main"


def kind_of(entry: dict[str, Any], detectors: list[dict[str, Any]]) -> str:
    if entry.get("path") == "/v1/messages/count_tokens":
        return "count_tokens"
    if entry.get("request_type") and entry.get("request_type") != "main":
        return str(entry["request_type"])
    return request_type(entry.get("shape") or {}, detectors)
