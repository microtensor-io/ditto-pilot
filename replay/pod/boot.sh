#!/usr/bin/env bash
set -uo pipefail
mkdir -p /work /opt/pilot /opt/standin
exec > >(tee -a /work/replay.log) 2>&1
echo "==> $(date -u +%FT%TZ) replay pod on $(hostname)"

echo "==> gpu check: $(python3 -c 'import torch; torch.cuda.init(); x = torch.ones(1024, device="cuda"); print("ok", torch.cuda.get_device_name(0), float(x.sum()))' 2>&1 | tail -1)"

echo "==> operator"
curl -fsSL "https://raw.githubusercontent.com/microtensor-io/microtensor-comparisons/${RAW_REF}/ctx_compress/loadtest1/pod/bootstrap.sh" -o /tmp/operator.sh
sed -i 's#git+https://github.com/microtensor-io/microtensor-subnet@\${SUBNET_REF}#https://github.com/microtensor-io/microtensor-subnet/archive/${SUBNET_REF}.tar.gz#' /tmp/operator.sh
grep -n "microtensor-subnet" /tmp/operator.sh
echo "compilers: cc=$(command -v cc) gcc=$(command -v gcc) git=$(command -v git)"
(MT_SHIM_WORKERS=4 SUBNET_REF="${SUBNET_REF}" RAW="https://raw.githubusercontent.com/microtensor-io/microtensor-comparisons/${RAW_REF}/ctx_compress/loadtest1/pod" bash /tmp/operator.sh > /work/operator.log 2>&1 &)

echo "==> pilot code"
cd /opt/pilot
python3 -m venv /opt/venv
. /opt/venv/bin/activate
pip install -q -r requirements.txt huggingface_hub 2>&1 | tail -2

echo "==> stand in"
pip install -q "llama-cpp-python[server]" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu 2>&1 | tail -2
python3 -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('Qwen/Qwen2.5-1.5B-Instruct-GGUF', 'qwen2.5-1.5b-instruct-q8_0.gguf', local_dir='/opt/standin'))"
(python3 -m llama_cpp.server --model /opt/standin/qwen2.5-1.5b-instruct-q8_0.gguf --model_alias slm --host 127.0.0.1 --port 8100 --n_ctx 8192 --n_threads 8 > /work/standin.log 2>&1 &)

echo "==> router fork"
if ! command -v cc > /dev/null 2>&1; then
  sleep 60
  apt-get update > /work/apt.log 2>&1
  apt-get install -y build-essential >> /work/apt.log 2>&1
  tail -3 /work/apt.log
fi
echo "cc $(command -v cc)"
curl -fsSL https://sh.rustup.rs -o /tmp/rustup.sh && sh /tmp/rustup.sh -y --profile minimal > /dev/null 2>&1
. "$HOME/.cargo/env"
mkdir -p /opt/ditto-subnet
curl -fsSL https://codeload.github.com/microtensor-io/ditto-subnet/tar.gz/refs/heads/microtensor-route-table | tar xz -C /opt/ditto-subnet --strip-components=1
cd /opt/ditto-subnet/miners/dittobench-router-starter-kit
cargo build --release --locked 2>&1 | tail -2
ROUTER=/opt/ditto-subnet/miners/dittobench-router-starter-kit/target/release/dittobench-router-miner

echo "==> relay and routers"
TICKET="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
cd /opt/pilot
(DITTOBENCH_RELAY_TICKET="$TICKET" python3 -m relay --catalog catalog.json --log /work/relay.jsonl --port 8090 > /work/relay.out 2>&1 &)
(DITTOBENCH_RELAY_BASE_URL=http://127.0.0.1:8090 DITTOBENCH_RELAY_TICKET="$TICKET" "$ROUTER" --local-practice --port 8081 > /work/router-off.log 2>&1 &)
(DITTOBENCH_RELAY_BASE_URL=http://127.0.0.1:8090 DITTOBENCH_RELAY_TICKET="$TICKET" "$ROUTER" --local-practice --port 8082 --reroute --route-table "compaction=microtensor/ctx_compress,aside=microtensor/session_title" > /work/router-on.log 2>&1 &)
sleep 5
for port in 8081 8082 8090 8100; do
  printf 'port %s: ' "$port"; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:${port}/$( [ "$port" = 8090 ] && echo relay/health || ([ "$port" = 8100 ] && echo v1/models || echo router/health))"
done

echo "==> requests"
curl -fsSL "https://raw.githubusercontent.com/microtensor-io/microtensor-comparisons/main/ctx_compress/exp1/data/items.jsonl" -o /work/items.jsonl
python3 -m replay.build --items /work/items.jsonl --instruction replay/claude_code_compact_instruction.txt --out /work/requests.jsonl

echo "==> waiting for the ctx_compress card"
for _ in $(seq 1 240); do
  if curl -fs "https://api.microtensor.cloud/v1/public/inference/providers/${MT_HOTKEY_SS58}" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if any(g.get('status')=='serving' and g.get('worker')=='pod' for g in d.get('gpus',[])) else 1)"; then
    echo "card serving at $(date -u +%T)"; break
  fi
  sleep 15
done

echo "==> smoke"
python3 -m replay.run --requests /work/requests.jsonl --router-off http://127.0.0.1:8081 --router-on http://127.0.0.1:8082 --limit 1 --out /work/smoke
SMOKE_OK="$(python3 - <<'EOF'
import json
from pathlib import Path
rows = [json.loads(l) for f in Path("/work/smoke").glob("*.jsonl") for l in f.read_text().splitlines() if l.strip()]
ok = rows and all(r["status"] == 200 and not r["error"] and (r["text"] or r["kind"] == "turn") for r in rows)
print("yes" if ok else "no")
EOF
)"
echo "smoke ok: ${SMOKE_OK}"
if [ "${SMOKE_OK}" != "yes" ]; then
  echo "==> stopped after the smoke run; read /work/smoke and /work/relay.jsonl"
  sleep infinity
fi

echo "==> replay $(date -u +%T)"
python3 -m replay.run --requests /work/requests.jsonl --router-off http://127.0.0.1:8081 --router-on http://127.0.0.1:8082 --out /work/outputs
echo "==> done $(date -u +%FT%TZ)"
touch /work/DONE
sleep infinity
