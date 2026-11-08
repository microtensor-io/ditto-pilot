from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from relay.app import Relay, create_app
from relay.catalog import load

ROOT = Path(__file__).resolve().parents[1]
TICKET = "ticket-1"


def make(tmp_path: Path, handler) -> tuple[TestClient, Path]:
    log = tmp_path / "relay.jsonl"
    relay = Relay(
        load(ROOT / "catalog.json"),
        TICKET,
        log,
        transport=httpx.MockTransport(handler),
        env={"MICROTENSOR_API_KEY": "mt-key", "ANTHROPIC_API_KEY": "an-key"},
    )
    return TestClient(create_app(relay)), log


def lines(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def test_a_wrong_ticket_is_refused(tmp_path: Path) -> None:
    client, _ = make(tmp_path, lambda request: httpx.Response(500))
    body = {"model": "microtensor/ctx_compress", "max_tokens": 8, "messages": []}
    assert client.post("/v1/messages", json=body).status_code == 401
    assert client.post("/v1/messages", json=body, headers={"authorization": "Bearer nope"}).status_code == 401


def test_a_route_outside_the_catalog_is_refused_and_logged(tmp_path: Path) -> None:
    seen = []
    client, log = make(tmp_path, lambda request: seen.append(request) or httpx.Response(200, json={}))
    found = client.post(
        "/v1/messages",
        json={"model": "gpt-unknown", "max_tokens": 8, "messages": []},
        headers={"authorization": f"Bearer {TICKET}"},
    )
    assert found.status_code == 400 and found.json()["error"]["type"] == "route_not_in_catalog"
    assert not seen
    assert lines(log)[-1]["error"] == "route_not_in_catalog"


def test_the_microtensor_route_rewrites_only_the_model_and_logs_cost(tmp_path: Path) -> None:
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["body"] = request.content
        sent["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "content": [{"type": "text", "text": "summary"}],
                "usage": {"input_tokens": 90000, "output_tokens": 700},
            },
        )

    client, log = make(tmp_path, handler)
    raw = '{"model": "microtensor/ctx_compress", "max_tokens": 4096, "messages": [{"role": "user", "content": "x"}]}'
    found = client.post(
        "/v1/messages",
        content=raw,
        headers={
            "authorization": f"Bearer {TICKET}",
            "content-type": "application/json",
            "x-dittobench-step": "t1",
        },
    )
    assert found.status_code == 200
    assert sent["url"] == "https://api.microtensor.cloud/v1/messages"
    assert sent["body"].decode() == raw.replace('"microtensor/ctx_compress"', '"mt/ctx_compress"')
    assert sent["auth"] == "Bearer mt-key"
    entry = lines(log)[-1]
    for key in (
        "run_id",
        "arm",
        "step",
        "route_id",
        "input_tokens",
        "cached_tokens",
        "output_tokens",
        "cost_usd_micros",
        "ttft_ms",
        "total_ms",
        "status",
        "stand_in",
    ):
        assert key in entry
    assert entry["step"] == "t1" and entry["input_tokens"] == 90000 and entry["output_tokens"] == 700
    assert entry["cost_usd_micros"] == 5000 and entry["stand_in"] is False and entry["status"] == 200


def test_the_stand_in_translates_messages_to_chat_and_back(tmp_path: Path) -> None:
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["body"] = json.loads(request.content)
        stream = (
            'data: {"choices":[{"delta":{"content":"Fix slug"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":" dashes"},"finish_reason":"stop"}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":40,"completion_tokens":3}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=stream.encode(), headers={"content-type": "text/event-stream"})

    client, log = make(tmp_path, handler)
    body = {
        "model": "microtensor/session_title",
        "max_tokens": 32,
        "stream": True,
        "system": "Write a title.",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "fix the slug"}]}],
    }
    found = client.post("/v1/messages", json=body, headers={"authorization": f"Bearer {TICKET}"})
    assert found.status_code == 200
    assert sent["url"] == "http://127.0.0.1:8100/v1/chat/completions"
    assert sent["body"]["model"] == "slm" and sent["body"]["messages"][0] == {
        "role": "system",
        "content": "Write a title.",
    }
    events = [line[6:] for line in found.text.splitlines() if line.startswith("data: ")]
    kinds = [json.loads(e)["type"] for e in events]
    assert kinds == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    text = "".join(
        json.loads(e)["delta"].get("text", "")
        for e in events
        if json.loads(e)["type"] == "content_block_delta"
    )
    assert text == "Fix slug dashes"
    entry = lines(log)[-1]
    assert entry["stand_in"] is True and entry["input_tokens"] == 40 and entry["output_tokens"] == 3


def test_cost_follows_the_catalog_prices(tmp_path: Path) -> None:
    catalog = load(ROOT / "catalog.json")
    route = catalog.get("microtensor/ctx_compress")
    assert route is not None and route.cost_micros(120000, 0, 3000) == 5000
    main = catalog.get("claude-haiku-5-5")
    assert main is not None
    priced = type(main)(
        **{
            **main.__dict__,
            "price_in_micros_per_mtok": 1_000_000,
            "price_out_micros_per_mtok": 5_000_000,
            "price_cached_micros_per_mtok": 100_000,
        }
    )
    assert priced.cost_micros(2_000, 10_000, 1_000) == 2_000 + 1_000 + 5_000
