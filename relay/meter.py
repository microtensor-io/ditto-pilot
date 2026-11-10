from __future__ import annotations

import json
import time
from typing import Any

CONTENT_EVENTS = ("content_block_delta",)


class Meter:
    def __init__(self, started: float) -> None:
        self.started = started
        self.first_byte: float | None = None
        self.first_token: float | None = None
        self.input_tokens = 0
        self.cached_tokens = 0
        self.written_tokens = 0
        self.written_long_tokens = 0
        self.output_tokens = 0
        self.stream = False
        self.buffer = b""
        self.body = bytearray()

    def feed(self, chunk: bytes) -> None:
        now = time.perf_counter()
        if self.first_byte is None and chunk:
            self.first_byte = now
        self.body.extend(chunk)
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            self._line(line.strip(), now)

    def _line(self, line: bytes, now: float) -> None:
        if not line.startswith(b"data:"):
            return
        self.stream = True
        payload = line[5:].strip()
        if not payload or payload == b"[DONE]":
            return
        try:
            event = json.loads(payload)
        except ValueError:
            return
        if isinstance(event, dict):
            self._event(event, now)

    def _event(self, event: dict[str, Any], now: float) -> None:
        kind = event.get("type")
        if kind == "message_start":
            self._anthropic_usage((event.get("message") or {}).get("usage") or {})
        elif kind == "message_delta":
            self._anthropic_usage(event.get("usage") or {})
        elif kind in CONTENT_EVENTS:
            self._mark(now)
        choices = event.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                delta = (choice or {}).get("delta") or {}
                if delta.get("content") or delta.get("tool_calls"):
                    self._mark(now)
        if isinstance(event.get("usage"), dict) and "prompt_tokens" in event["usage"]:
            self._openai_usage(event["usage"])
        if kind == "response.completed":
            self._responses_usage(((event.get("response") or {}).get("usage")) or {})

    def _mark(self, now: float) -> None:
        if self.first_token is None:
            self.first_token = now

    def _anthropic_usage(self, usage: dict[str, Any]) -> None:
        if "input_tokens" in usage:
            self.input_tokens = int(usage.get("input_tokens") or 0)
        written = int(usage.get("cache_creation_input_tokens") or 0)
        cached = int(usage.get("cache_read_input_tokens") or 0) + written
        if cached:
            self.cached_tokens = cached
            self.written_tokens = written
            split = usage.get("cache_creation") or {}
            self.written_long_tokens = min(written, int(split.get("ephemeral_1h_input_tokens") or 0))
        if "output_tokens" in usage:
            self.output_tokens = int(usage.get("output_tokens") or 0)

    def _openai_usage(self, usage: dict[str, Any]) -> None:
        cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        self.input_tokens = int(usage.get("prompt_tokens") or 0) - cached
        self.cached_tokens = cached
        self.output_tokens = int(usage.get("completion_tokens") or 0)

    def _responses_usage(self, usage: dict[str, Any]) -> None:
        cached = int(((usage.get("input_tokens_details") or {}).get("cached_tokens")) or 0)
        self.input_tokens = int(usage.get("input_tokens") or 0) - cached
        self.cached_tokens = cached
        self.output_tokens = int(usage.get("output_tokens") or 0)

    def finish(self) -> None:
        if self.buffer:
            self._line(self.buffer.strip(), time.perf_counter())
            self.buffer = b""
        if self.stream:
            return
        try:
            found = json.loads(bytes(self.body))
        except ValueError:
            return
        if not isinstance(found, dict):
            return
        usage = found.get("usage") or {}
        if "prompt_tokens" in usage:
            self._openai_usage(usage)
        elif "input_tokens" in usage and "output_tokens" in usage and found.get("object") == "response":
            self._responses_usage(usage)
        elif usage:
            self._anthropic_usage(usage)
        elif "input_tokens" in found:
            self.input_tokens = int(found.get("input_tokens") or 0)

    def ms(self, moment: float | None) -> float | None:
        return None if moment is None else round((moment - self.started) * 1000.0, 1)
