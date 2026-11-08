from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator
from typing import Any

STOP_REASONS = {"stop": "end_turn", "length": "max_tokens", "content_filter": "end_turn"}


class StandInError(ValueError):
    pass


def _text(content: Any, where: str) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                kind = block.get("type") if isinstance(block, dict) else type(block).__name__
                raise StandInError(f"the stand in serves text only requests; {where} has a {kind} block")
            parts.append(str(block.get("text", "")))
        return "".join(parts)
    if content is None:
        return ""
    raise StandInError(f"{where} content must be text")


def to_chat(body: dict[str, Any], model: str) -> dict[str, Any]:
    if body.get("tools"):
        raise StandInError("the stand in serves text only requests; tools are not supported")
    messages: list[dict[str, str]] = []
    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": _text(system, "system")})
    for index, message in enumerate(body.get("messages") or []):
        role = message.get("role")
        if role not in ("user", "assistant"):
            raise StandInError(f"message {index} has role {role!r}")
        messages.append({"role": role, "content": _text(message.get("content"), f"message {index}")})
    chat: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": int(body.get("max_tokens") or 0) or 512,
        "stream": bool(body.get("stream")),
    }
    if body.get("temperature") is not None:
        chat["temperature"] = body["temperature"]
    if body.get("stop_sequences"):
        chat["stop"] = list(body["stop_sequences"])
    if chat["stream"]:
        chat["stream_options"] = {"include_usage": True}
    return chat


def message_id() -> str:
    return "msg_standin_" + secrets.token_hex(12)


def to_message(found: dict[str, Any], model: str) -> dict[str, Any]:
    choice = (found.get("choices") or [{}])[0]
    text = str(((choice.get("message") or {}).get("content")) or "")
    usage = found.get("usage") or {}
    return {
        "id": message_id(),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": STOP_REASONS.get(str(choice.get("finish_reason") or "stop"), "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


async def to_message_stream(lines: AsyncIterator[str], model: str) -> AsyncIterator[bytes]:
    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id(),
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )
    yield _sse(
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    )
    finish = "stop"
    usage: dict[str, Any] = {}
    async for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            piece = ((choice or {}).get("delta") or {}).get("content")
            if piece:
                yield _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": piece},
                    },
                )
            if (choice or {}).get("finish_reason"):
                finish = str(choice["finish_reason"])
    yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": STOP_REASONS.get(finish, "end_turn"), "stop_sequence": None},
            "usage": {
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            },
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})
