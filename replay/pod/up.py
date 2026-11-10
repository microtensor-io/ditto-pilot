from __future__ import annotations

import base64
import io
import json
import os
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

REST = "https://rest.runpod.io/v1"
IMAGE = "vllm/vllm-openai:v0.32.0"
ROOT = Path(__file__).resolve().parents[2]
SHIPPED = (
    "relay",
    "bench",
    "replay/build.py",
    "replay/run.py",
    "replay/claude_code_compact_instruction.txt",
    "replay/pod/boot.sh",
    "live/run.py",
    "live/tasks.json",
    "catalog.json",
    "requirements.txt",
)
CHUNK = 4000
LIVE_KEYS = (
    "MODE",
    "LIVE_MODEL",
    "LIVE_TASKS",
    "LIVE_CAP_USD",
    "LIVE_HARD_USD",
    "LIVE_SWITCH_USD",
    "LIVE_MAX_TURNS",
    "LIVE_SESSION_SECONDS",
    "LIVE_COMPACT_WINDOW",
    "LIVE_COMPACT_PCT",
    "LIVE_SKIP",
)
START = (
    "mkdir -p /opt/pilot && "
    'for i in $(seq 0 $((PILOT_PARTS-1))); do v="PILOT_$i"; printf %s "${!v}"; done '
    "| base64 -d | tar xz -C /opt/pilot && bash /opt/pilot/replay/pod/boot.sh"
)


def pilot_tarball() -> str:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in SHIPPED:
            archive.add(
                ROOT / name,
                arcname=name,
                filter=lambda info: (
                    None if "__pycache__" in info.name or info.name.endswith(".pyc") else info
                ),
            )
    return base64.b64encode(buffer.getvalue()).decode()


def call(method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        REST + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}",
            "Content-Type": "application/json",
            "User-Agent": "ditto-pilot-replay/1",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{path} answered {exc.code}: {exc.read()[:300]!r}") from exc
    return json.loads(raw) if raw else {}


def main() -> int:
    packed = pilot_tarball()
    parts = [packed[i : i + CHUNK] for i in range(0, len(packed), CHUNK)]
    env = {
        **{f"PILOT_{i}": part for i, part in enumerate(parts)},
        "PILOT_PARTS": str(len(parts)),
        "RAW_REF": os.environ["RAW_REF"],
        "SUBNET_REF": os.environ["SUBNET_REF"],
        "MT_LABEL": os.environ.get("MT_LABEL", "loadtest 4090"),
        "MT_WORKER": "pod",
        "MT_HOTKEY_MNEMONIC": os.environ["MT_HOTKEY_MNEMONIC"],
        "MT_COLDKEY_SS58": os.environ["MT_COLDKEY_SS58"],
        "MT_HOTKEY_SS58": os.environ["MT_HOTKEY_SS58"],
        "ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"],
        "MICROTENSOR_API_KEY": os.environ["MICROTENSOR_API_KEY"],
        "VLLM_NO_USAGE_STATS": "1",
        **({"RERUN_IDS": os.environ["RERUN_IDS"]} if os.environ.get("RERUN_IDS") else {}),
        **{k: os.environ[k] for k in LIVE_KEYS if os.environ.get(k)},
    }
    print("payload bytes", len(packed), "in", len(parts), "parts", file=sys.stderr)
    if len(sys.argv) > 1:
        found = call("POST", f"/pods/{sys.argv[1]}/update", {"env": env, "dockerStartCmd": [START]})
        print(json.dumps({k: found.get(k) for k in ("id", "desiredStatus", "costPerHr")}))
        return 0
    found = call(
        "POST",
        "/pods",
        {
            "name": "ditto-pilot-replay",
            "imageName": IMAGE,
            "gpuTypeIds": os.environ.get("RUNPOD_GPUS", "NVIDIA GeForce RTX 4090").split(","),
            "gpuCount": 1,
            "gpuTypePriority": "custom",
            "allowedCudaVersions": ["13.0"],
            "cloudType": os.environ.get("RUNPOD_CLOUD", "SECURE"),
            "computeType": "GPU",
            "containerDiskInGb": 80,
            "volumeInGb": 0,
            "ports": ["8070/http"],
            "dockerEntrypoint": ["/bin/bash", "-c"],
            "dockerStartCmd": [START],
            "env": env,
        },
    )
    print(json.dumps({k: found.get(k) for k in ("id", "desiredStatus", "costPerHr")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
