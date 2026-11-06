#!/usr/bin/env bash
# Build the firm's image from examples/firm-tools and prove it serves the firm's tool with the
# platform's restrictions. CI runs this after deploy/smoke-test.sh; so can you.
#   deploy/firm-image-test.sh
# It uses its own container, port, and volumes, and removes them when it finishes.
set -euo pipefail

cd "$(dirname "$0")/.."
image="firm-qaos:smoke"
name="qaos-firm-smoke"
keys="qaos-firm-smoke-keys"
runs="qaos-firm-smoke-runs"
port="${QAOS_PORT:-18001}"
base="http://127.0.0.1:${port}"

cleanup() {
  docker logs "$name" || true
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$keys" "$runs" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

docker image inspect quant-agent-os:local >/dev/null 2>&1 \
  || docker compose -f deploy/compose.yaml build server
docker build -t "$image" examples/firm-tools

echo "== issue a key"
key="$(docker run --rm -v "$keys:/keys" --entrypoint qaos "$image" keys new --principal smoke \
  --scope tools:list --scope tools:call --scope runs:read 2>/dev/null | tail -n 1)"
[[ "$key" == qaos_* ]] || fail "no API key was printed"

echo "== start it with the restrictions the compose file sets"
docker run -d --name "$name" -p "127.0.0.1:${port}:8000" \
  -v "$keys:/keys:ro" -v "$runs:/data/runs" \
  -e FIRM_DB=/tmp/firm.db \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  "$image"
for _ in $(seq 1 60); do
  curl -fsS "$base/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "$base/healthz" | grep -q '"ok"' || fail "the server never became healthy"

echo "== the firm's tool is served, called, and recorded"
auth=(-H "Authorization: Bearer $key")
curl -fsS "${auth[@]}" "$base/v1/tools" | grep -q '"desk_var"' || fail "desk_var is not listed"
result="$(curl -fsS "${auth[@]}" -X POST -H 'content-type: application/json' \
  -d '{"arguments": {"dataset": "desks", "column": "growth"}}' \
  "$base/v1/tools/desk_var/call")"
grep -q '"status":"ok"' <<<"$result" || fail "the call did not succeed: $result"
run_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<<"$result")"
curl -fsS "${auth[@]}" "$base/v1/runs/$run_id" | grep -q '"principal":"smoke"' \
  || fail "the run does not record the key's principal"

echo "PASS: the firm's image"
