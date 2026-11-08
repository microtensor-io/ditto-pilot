from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from relay import standin
from relay.catalog import Catalog, Route
from relay.meter import Meter
from relay.splice import model_of, splice_model

ROUTES = ("/v1/messages", "/v1/messages/count_tokens", "/v1/chat/completions", "/v1/responses")
STEP_HEADER = "x-dittobench-step"
ANTHROPIC_VERSION = "2023-06-01"
PASS_HEADERS = ("anthropic-version", "anthropic-beta", "accept")
HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
}
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"type": "error", "error": {"type": code, "code": code, "message": message}}, status_code=status
    )


def _chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(len(str(block.get("text", ""))) for block in value if isinstance(block, dict))
    return 0


def shape_of(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    last_user = next((m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), {})
    tools = body.get("tools") if isinstance(body.get("tools"), list) else []
    after_user = []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            break
        after_user.append(message)
    content = last_user.get("content")
    final = content[-1] if isinstance(content, list) and content else None
    final_text = (
        len(content)
        if isinstance(content, str)
        else len(str(final.get("text") or ""))
        if isinstance(final, dict) and final.get("type") == "text"
        else 0
    )
    return {
        "final_text_chars": final_text,
        "only_system_after_user": all(isinstance(m, dict) and m.get("role") == "system" for m in after_user),
        "tools": len(tools),
        "tool_choice": body.get("tool_choice") is not None,
        "max_tokens": body.get("max_tokens"),
        "messages": len(messages),
        "last_user_chars": _chars(last_user.get("content")),
        "system_chars": _chars(body.get("system")),
        "stream": bool(body.get("stream")),
        "thinking": bool(body.get("thinking")),
    }


class Relay:
    def __init__(
        self,
        catalog: Catalog,
        ticket: str,
        log_path: Path,
        capture_dir: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 900.0,
    ) -> None:
        self.catalog = catalog
        self.ticket = ticket
        self.log_path = log_path
        self.capture_dir = capture_dir
        self.env = dict(os.environ if env is None else env)
        self.client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(timeout, connect=30.0))
        self.labels = {"run_id": self.env.get("DITTO_RUN_ID", ""), "arm": self.env.get("DITTO_ARM", "")}
        self.captured = 0

    def authorised(self, request: Request) -> bool:
        if not self.ticket:
            return False
        bearer = request.headers.get("authorization", "")
        if bearer.lower().startswith("bearer ") and bearer[7:].strip() == self.ticket:
            return True
        return request.headers.get("x-api-key", "") == self.ticket

    def log(self, entry: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def capture(self, step: str, route_id: str, path: str, raw: bytes) -> None:
        if self.capture_dir is None:
            return
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.captured += 1
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%f")
        name = SAFE_NAME.sub("_", f"{stamp}_{self.captured:05d}_{step or 'nostep'}_{route_id}")
        (self.capture_dir / f"{name}.json").write_bytes(raw)
        meta = {"path": path, "route_id": route_id, "step": step, "bytes": len(raw), **self.labels}
        (self.capture_dir / f"{name}.meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def entry(
        self,
        *,
        path: str,
        step: str,
        route: Route | None,
        route_id: str,
        raw: bytes,
        status: int,
        meter: Meter | None,
        started: float,
        error: str = "",
    ) -> dict[str, Any]:
        input_tokens = meter.input_tokens if meter else 0
        cached = meter.cached_tokens if meter else 0
        output = meter.output_tokens if meter else 0
        cost = route.cost_micros(input_tokens, cached, output) if route and status < 400 else 0
        return {
            "ts": dt.datetime.now(dt.UTC).isoformat(),
            "run_id": self.labels["run_id"],
            "arm": self.labels["arm"],
            "step": step,
            "path": path,
            "route_id": route_id,
            "upstream_model": route.model if route else "",
            "provider_api": route.provider_api if route else "",
            "request_type": route.request_type if route else "",
            "certified": route.certified if route else False,
            "stand_in": route.stand_in if route else False,
            "input_tokens": input_tokens,
            "cached_tokens": cached,
            "output_tokens": output,
            "cost_usd_micros": cost,
            "first_byte_ms": meter.ms(meter.first_byte) if meter else None,
            "ttft_ms": meter.ms(meter.first_token) if meter else None,
            "total_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "status": status,
            "error": error[:300],
            "request_bytes": len(raw),
            "shape": shape_of(raw),
        }

    def upstream(
        self, route: Route, path: str, raw: bytes, request: Request
    ) -> tuple[str, bytes, dict[str, str]]:
        headers = {"content-type": "application/json", "accept-encoding": "identity"}
        for name in PASS_HEADERS:
            if request.headers.get(name):
                headers[name] = request.headers[name]
        key = self.env.get(route.key_env, "") if route.key_env else ""
        if route.key_env and not key:
            raise KeyError(f"the key env {route.key_env} is not set")
        if route.provider_api == "anthropic":
            headers.setdefault("anthropic-version", ANTHROPIC_VERSION)
            if key:
                headers["x-api-key"] = key
        elif key:
            headers["authorization"] = f"Bearer {key}"
        body = raw
        if route.model != route.route_id:
            body = splice_model(raw, route.route_id, route.model) or raw
        return route.base_url + path, body, headers

    async def handle(self, path: str, request: Request) -> Response:
        started = time.perf_counter()
        raw = await request.body()
        step = request.headers.get(STEP_HEADER, "")
        if not self.authorised(request):
            return _error(401, "authentication_error", "the relay ticket is missing or wrong")
        route_id = model_of(raw)
        if route_id is None:
            self.log(
                self.entry(
                    path=path,
                    step=step,
                    route=None,
                    route_id="",
                    raw=raw,
                    status=400,
                    meter=None,
                    started=started,
                    error="no model",
                )
            )
            return _error(400, "invalid_request_error", "the request names no model")
        route = self.catalog.get(route_id)
        if route is None:
            message = f"{route_id} is not in the frozen catalog"
            self.log(
                self.entry(
                    path=path,
                    step=step,
                    route=None,
                    route_id=route_id,
                    raw=raw,
                    status=400,
                    meter=None,
                    started=started,
                    error="route_not_in_catalog",
                )
            )
            return _error(400, "route_not_in_catalog", message)
        self.capture(step, route_id, path, raw)
        if route.provider_api == "openai_compat":
            return await self.stand_in(route, path, raw, request, step, started)
        try:
            url, body, headers = self.upstream(route, path, raw, request)
        except KeyError as exc:
            self.log(
                self.entry(
                    path=path,
                    step=step,
                    route=route,
                    route_id=route_id,
                    raw=raw,
                    status=500,
                    meter=None,
                    started=started,
                    error=str(exc),
                )
            )
            return _error(500, "relay_misconfigured", str(exc))
        return await self.passthrough(route, path, raw, url, body, headers, step, started)

    async def passthrough(
        self,
        route: Route,
        path: str,
        raw: bytes,
        url: str,
        body: bytes,
        headers: dict[str, str],
        step: str,
        started: float,
    ) -> Response:
        meter = Meter(started)
        try:
            response = await self.client.send(
                self.client.build_request("POST", url, content=body, headers=headers), stream=True
            )
        except httpx.HTTPError as exc:
            self.log(
                self.entry(
                    path=path,
                    step=step,
                    route=route,
                    route_id=route.route_id,
                    raw=raw,
                    status=502,
                    meter=meter,
                    started=started,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            return _error(502, "upstream_unreachable", f"{type(exc).__name__}: {exc}")
        kept = {k: v for k, v in response.headers.items() if k.lower() not in HOP}

        async def relay_bytes() -> AsyncIterator[bytes]:
            error = ""
            try:
                async for chunk in response.aiter_bytes():
                    meter.feed(chunk)
                    yield chunk
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                await response.aclose()
                meter.finish()
                if response.status_code >= 400 and not error:
                    error = bytes(meter.body[:300]).decode("utf-8", "replace")
                self.log(
                    self.entry(
                        path=path,
                        step=step,
                        route=route,
                        route_id=route.route_id,
                        raw=raw,
                        status=response.status_code,
                        meter=meter,
                        started=started,
                        error=error,
                    )
                )

        return StreamingResponse(relay_bytes(), status_code=response.status_code, headers=kept)

    async def stand_in(
        self, route: Route, path: str, raw: bytes, request: Request, step: str, started: float
    ) -> Response:
        meter = Meter(started)
        key = self.env.get(route.key_env, "") if route.key_env else ""
        headers = {"content-type": "application/json", "accept-encoding": "identity"}
        if key:
            headers["authorization"] = f"Bearer {key}"
        if path == "/v1/chat/completions":
            body = splice_model(raw, route.route_id, route.model) or raw
            return await self.passthrough(
                route, path, raw, route.base_url + path, body, headers, step, started
            )
        if path != "/v1/messages":
            self.log(
                self.entry(
                    path=path,
                    step=step,
                    route=route,
                    route_id=route.route_id,
                    raw=raw,
                    status=404,
                    meter=None,
                    started=started,
                    error="not served by the stand in",
                )
            )
            return _error(404, "not_found_error", f"the stand in {route.route_id} does not serve {path}")
        try:
            chat = standin.to_chat(json.loads(raw), route.model)
        except (standin.StandInError, ValueError) as exc:
            self.log(
                self.entry(
                    path=path,
                    step=step,
                    route=route,
                    route_id=route.route_id,
                    raw=raw,
                    status=400,
                    meter=None,
                    started=started,
                    error=str(exc),
                )
            )
            return _error(400, "invalid_request_error", str(exc))
        url = route.base_url + "/v1/chat/completions"
        try:
            response = await self.client.send(
                self.client.build_request("POST", url, content=json.dumps(chat).encode(), headers=headers),
                stream=True,
            )
        except httpx.HTTPError as exc:
            self.log(
                self.entry(
                    path=path,
                    step=step,
                    route=route,
                    route_id=route.route_id,
                    raw=raw,
                    status=502,
                    meter=meter,
                    started=started,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            return _error(502, "upstream_unreachable", f"{type(exc).__name__}: {exc}")
        if response.status_code >= 400 or not chat["stream"]:
            data = await response.aread()
            await response.aclose()
            if response.status_code >= 400:
                meter.feed(data)
                meter.finish()
                self.log(
                    self.entry(
                        path=path,
                        step=step,
                        route=route,
                        route_id=route.route_id,
                        raw=raw,
                        status=response.status_code,
                        meter=meter,
                        started=started,
                        error=data[:300].decode("utf-8", "replace"),
                    )
                )
                return Response(content=data, status_code=response.status_code, media_type="application/json")
            message = standin.to_message(json.loads(data), route.route_id)
            out = json.dumps(message).encode()
            meter.feed(out)
            meter.finish()
            self.log(
                self.entry(
                    path=path,
                    step=step,
                    route=route,
                    route_id=route.route_id,
                    raw=raw,
                    status=200,
                    meter=meter,
                    started=started,
                )
            )
            return Response(content=out, status_code=200, media_type="application/json")

        async def events() -> AsyncIterator[bytes]:
            try:
                async for chunk in standin.to_message_stream(response.aiter_lines(), route.route_id):
                    meter.feed(chunk)
                    yield chunk
            finally:
                await response.aclose()
                meter.finish()
                self.log(
                    self.entry(
                        path=path,
                        step=step,
                        route=route,
                        route_id=route.route_id,
                        raw=raw,
                        status=200,
                        meter=meter,
                        started=started,
                    )
                )

        return StreamingResponse(events(), status_code=200, media_type="text/event-stream")


def create_app(relay: Relay) -> FastAPI:
    app = FastAPI(title="ditto-pilot relay")
    app.state.relay = relay

    for route_path in ROUTES:

        async def endpoint(request: Request, _path: str = route_path) -> Response:
            return await relay.handle(_path, request)

        app.add_api_route(route_path, endpoint, methods=["POST"])

    @app.get("/relay/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "routes": sorted(relay.catalog.routes),
            "stand_in": sorted(r.route_id for r in relay.catalog.routes.values() if r.stand_in),
            "capture": relay.capture_dir is not None,
        }

    @app.post("/relay/label")
    async def label(request: Request) -> Response:
        if not relay.authorised(request):
            return _error(401, "authentication_error", "the relay ticket is missing or wrong")
        found = await request.json()
        relay.labels = {"run_id": str(found.get("run_id", "")), "arm": str(found.get("arm", ""))}
        return JSONResponse(relay.labels)

    return app
