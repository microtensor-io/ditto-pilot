from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROVIDERS = ("anthropic", "microtensor", "openai_compat")


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Route:
    route_id: str
    model: str
    provider_api: str
    base_url: str
    key_env: str = ""
    label: str = ""
    request_type: str = ""
    arena: str = ""
    certified: bool = False
    stand_in: bool = False
    price_in_micros_per_mtok: float = 0.0
    price_cached_micros_per_mtok: float | None = None
    price_out_micros_per_mtok: float = 0.0
    price_per_request_micros: float = 0.0
    price_long_above_tokens: int = 0
    price_long_in_micros_per_mtok: float = 0.0
    price_long_out_micros_per_mtok: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def cost_micros(self, input_tokens: int, cached_tokens: int, output_tokens: int) -> int:
        cached_price = (
            self.price_in_micros_per_mtok
            if self.price_cached_micros_per_mtok is None
            else self.price_cached_micros_per_mtok
        )
        prompt = input_tokens + cached_tokens
        long = bool(self.price_long_above_tokens) and prompt > self.price_long_above_tokens
        price_in = self.price_long_in_micros_per_mtok if long else self.price_in_micros_per_mtok
        price_out = self.price_long_out_micros_per_mtok if long else self.price_out_micros_per_mtok
        total = (
            input_tokens * price_in
            + cached_tokens * cached_price
            + output_tokens * price_out
        ) / 1_000_000 + self.price_per_request_micros
        return round(total)


KNOWN = {
    "model",
    "provider_api",
    "base_url",
    "key_env",
    "label",
    "request_type",
    "arena",
    "certified",
    "stand_in",
    "price_in_micros_per_mtok",
    "price_cached_micros_per_mtok",
    "price_out_micros_per_mtok",
    "price_per_request_micros",
    "price_long_above_tokens",
    "price_long_in_micros_per_mtok",
    "price_long_out_micros_per_mtok",
}


def route_of(route_id: str, raw: dict[str, Any]) -> Route:
    provider = str(raw.get("provider_api", ""))
    if provider not in PROVIDERS:
        raise CatalogError(f"{route_id}: provider_api must be one of {', '.join(PROVIDERS)}")
    if not raw.get("base_url"):
        raise CatalogError(f"{route_id}: base_url is required")
    if provider == "openai_compat" and not raw.get("stand_in"):
        raise CatalogError(f"{route_id}: an openai_compat route is a stand in and must say so")
    cached = raw.get("price_cached_micros_per_mtok")
    return Route(
        route_id=route_id,
        model=str(raw.get("model") or route_id),
        provider_api=provider,
        base_url=str(raw["base_url"]).rstrip("/"),
        key_env=str(raw.get("key_env", "")),
        label=str(raw.get("label", "")),
        request_type=str(raw.get("request_type", "")),
        arena=str(raw.get("arena", "")),
        certified=bool(raw.get("certified", False)),
        stand_in=bool(raw.get("stand_in", False)),
        price_in_micros_per_mtok=float(raw.get("price_in_micros_per_mtok", 0)),
        price_cached_micros_per_mtok=None if cached is None else float(cached),
        price_out_micros_per_mtok=float(raw.get("price_out_micros_per_mtok", 0)),
        price_per_request_micros=float(raw.get("price_per_request_micros", 0)),
        price_long_above_tokens=int(raw.get("price_long_above_tokens", 0)),
        price_long_in_micros_per_mtok=float(raw.get("price_long_in_micros_per_mtok", 0)),
        price_long_out_micros_per_mtok=float(raw.get("price_long_out_micros_per_mtok", 0)),
        extra={k: v for k, v in raw.items() if k not in KNOWN},
    )


@dataclass(frozen=True)
class Catalog:
    routes: dict[str, Route]
    meta: dict[str, Any]

    def get(self, route_id: str) -> Route | None:
        return self.routes.get(route_id)


def load(path: str | Path) -> Catalog:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = raw.get("routes")
    if not isinstance(entries, dict) or not entries:
        raise CatalogError("the catalog needs a non empty routes object")
    routes = {route_id: route_of(route_id, entry) for route_id, entry in entries.items()}
    return Catalog(routes=routes, meta={k: v for k, v in raw.items() if k != "routes"})
