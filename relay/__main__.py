from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from relay.app import Relay, create_app
from relay.catalog import load


def main() -> None:
    parser = argparse.ArgumentParser(prog="ditto-pilot-relay")
    parser.add_argument("--catalog", default=os.environ.get("DITTO_RELAY_CATALOG", "catalog.json"))
    parser.add_argument("--log", default=os.environ.get("DITTO_RELAY_LOG", "logs/relay.jsonl"))
    parser.add_argument("--capture-dir", default=os.environ.get("DITTO_RELAY_CAPTURE_DIR", ""))
    parser.add_argument("--host", default=os.environ.get("DITTO_RELAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DITTO_RELAY_PORT", "8090")))
    args = parser.parse_args()
    ticket = os.environ.get("DITTOBENCH_RELAY_TICKET", "")
    if not ticket:
        raise SystemExit("set DITTOBENCH_RELAY_TICKET; the relay refuses every call without one")
    relay = Relay(
        load(args.catalog),
        ticket,
        Path(args.log),
        Path(args.capture_dir) if args.capture_dir else None,
    )
    uvicorn.run(create_app(relay), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
