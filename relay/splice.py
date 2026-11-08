from __future__ import annotations

import json


def model_of(raw: bytes) -> str | None:
    try:
        found = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(found, dict):
        return None
    model = found.get("model")
    return model if isinstance(model, str) else None


def splice_model(raw: bytes, old: str, new: str) -> bytes | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    key = text.find('"model"')
    if key < 0:
        return None
    colon = text.find(":", key + len('"model"'))
    if colon < 0:
        return None
    start = colon + 1
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    if start >= len(text) or text[start] != '"':
        return None
    cursor = start + 1
    while cursor < len(text):
        if text[cursor] == "\\":
            cursor += 2
            continue
        if text[cursor] == '"':
            break
        cursor += 1
    if cursor >= len(text):
        return None
    end = cursor + 1
    try:
        current = json.loads(text[start:end])
    except ValueError:
        return None
    if current != old:
        return None
    return (text[:start] + json.dumps(new) + text[end:]).encode("utf-8")
