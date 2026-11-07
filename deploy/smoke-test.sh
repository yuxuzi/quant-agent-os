#!/usr/bin/env bash
# Build the team-server image and prove it works end to end (B-19, B-33). CI runs this; so can
# you.
#   deploy/smoke-test.sh            # the SQLite store (compose.yaml)
#   deploy/smoke-test.sh postgres   # PostgreSQL with pgvector (compose.postgres.yaml)
# It uses its own compose project and port, and removes its volumes when it finishes.
set -euo pipefail

cd "$(dirname "$0")/.."
variant="${1:-sqlite}"
export QAOS_PORT="${QAOS_PORT:-18000}"
case "$variant" in
  sqlite) compose=(docker compose -p qaos-smoke-sqlite -f deploy/compose.yaml) ;;
  postgres)
    # Throwaway passwords for this run only, outside the repository.
    env_file="$(mktemp)"
    for name in QAOS_POSTGRES_PASSWORD QAOS_INGEST_PASSWORD QAOS_READER_PASSWORD; do
      echo "$name=$(python3 -c 'import secrets; print(secrets.token_hex(16))')" >>"$env_file"
    done
    compose=(docker compose -p qaos-smoke-postgres -f deploy/compose.postgres.yaml
             --env-file "$env_file")
    ;;
  *) echo "usage: $0 [sqlite|postgres]" >&2; exit 2 ;;
esac
base="http://127.0.0.1:${QAOS_PORT}"
cleanup() {
  "${compose[@]}" logs server || true
  "${compose[@]}" down -v --remove-orphans
  [[ -z "${env_file:-}" ]] || rm -f "$env_file"
  rm -f "${backup:-}" "${dump:-}"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
wait_healthy() {
  for _ in $(seq 1 60); do
    curl -fsS "$base/healthz" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS "$base/healthz" | grep -q '"ok"' || fail "the server never became healthy"
}

"${compose[@]}" build
image="${QAOS_IMAGE:-quant-agent-os:local}"  # the server's image, not the database's

echo "== the image holds no data, keys, or secrets, and runs as a non-root user"
[[ "$(docker run --rm --entrypoint id "$image" -u)" == "10001" ]] || fail "image runs as root"
found="$(docker run --rm --entrypoint sh "$image" -c \
  'find / -xdev \( -name "*.db" -o -name "api-keys.toml" -o -name ".env" -o -name "*.toml.bak" \) \
     -not -path "/proc/*" 2>/dev/null' || true)"
[[ -z "$found" ]] || fail "files that belong in volumes are in the image: $found"
env_names="$(docker image inspect "$image" --format '{{range .Config.Env}}{{println .}}{{end}}')"
if grep -Ei '^[A-Z_]*(SECRET|TOKEN|PASSWORD|API_KEY)=' <<<"$env_names"; then
  fail "the image sets a secret-looking environment variable"
fi

echo "== operator commands: ingest the sample corpus and issue a key"
"${compose[@]}" run --rm -T ingest
key="$("${compose[@]}" run --rm -T keys new --principal smoke \
  --scope tools:list --scope tools:call --scope runs:read 2>/dev/null | tail -n 1)"
[[ "$key" == qaos_* ]] || fail "no API key was printed"
"${compose[@]}" run --rm -T datasets add /import/monthly-returns.csv --id monthly >/dev/null \
  || fail "the sample dataset was not added"

echo "== docker compose up"
"${compose[@]}" up -d server
wait_healthy

echo "== authentication and a real call"
code="$(curl -s -o /dev/null -w '%{http_code}' "$base/v1/tools")"
[[ "$code" == "401" ]] || fail "an unauthenticated request got $code, not 401"
auth=(-H "Authorization: Bearer $key")
curl -fsS "${auth[@]}" "$base/v1/tools" | grep -q '"research_report"' || fail "tools not listed"
result="$(curl -fsS "${auth[@]}" -X POST -H 'content-type: application/json' \
  -d '{"arguments": {"focus": "short-term reversal"}}' \
  "$base/v1/tools/research_report/call")"
grep -q '"status":"ok"' <<<"$result" || fail "the call did not succeed: $result"
run_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<<"$result")"
curl -fsS "${auth[@]}" "$base/v1/runs/$run_id" | grep -q '"principal":"smoke"' \
  || fail "the run does not record the key's principal"

echo "== MCP over HTTP at /mcp (decision 0012)"
rpc() { printf '{"jsonrpc":"2.0","id":1,"method":"%s","params":%s}' "$1" "$2"; }
mcp_headers=(-H 'content-type: application/json' -H 'accept: application/json, text/event-stream')
code="$(curl -s -o /dev/null -w '%{http_code}' "${mcp_headers[@]}" \
  -d "$(rpc tools/list '{}')" "$base/mcp")"
[[ "$code" == "401" ]] || fail "an unauthenticated MCP request got $code, not 401"
code="$(curl -s -o /dev/null -w '%{http_code}' "${auth[@]}" "${mcp_headers[@]}" \
  -H 'Host: evil.example' -d "$(rpc tools/list '{}')" "$base/mcp")"
[[ "$code" != "200" ]] || fail "a foreign Host header was accepted"
curl -fsS "${auth[@]}" "${mcp_headers[@]}" -d "$(rpc tools/list '{}')" "$base/mcp" \
  | grep -q '"research_report"' || fail "MCP tools/list lacks research_report"
called="$(curl -fsS "${auth[@]}" "${mcp_headers[@]}" -d "$(rpc tools/call \
  '{"name":"research_report","arguments":{"focus":"momentum","returns":{"dataset":"monthly","column":"strategy"},"periods_per_year":12}}')" \
  "$base/mcp")"
grep -q '"isError":false' <<<"$called" || fail "the MCP call failed: $called"
grep -q '"sharpe_ratio"' <<<"$called" || fail "the report has no metrics: $called"
curl -fsS "${auth[@]}" "$base/v1/runs" | grep -q '"actor":"mcp-http"' \
  || fail "the MCP call was not recorded with its caller"

echo "== doctor checks the deployed volumes, and its output holds no secrets"
report="$("${compose[@]}" run --rm -T doctor)" || fail "doctor failed: $report"
grep -q 'knowledge store .* ok' <<<"$report" || fail "doctor did not open the store: $report"
grep -q 'run store .* ok' <<<"$report" || fail "doctor did not open the run store: $report"
if grep -qF "$key" <<<"$report"; then fail "doctor printed an API key"; fi
if [[ "$variant" == postgres ]]; then
  while IFS='=' read -r _ password; do
    if grep -qF "$password" <<<"$report"; then fail "doctor printed a password"; fi
  done <"$env_file"
fi

echo "== back up, lose the data, restore"
"${compose[@]}" stop server
backup="$(mktemp)"
"${compose[@]}" run --rm -T backup >"$backup"
if [[ "$variant" == postgres ]]; then
  dump="$(mktemp)"
  "${compose[@]}" exec -T postgres pg_dump -U postgres -d qaos -Fc >"$dump"
fi
if "${compose[@]}" run --rm -T restore </dev/null 2>/dev/null; then
  fail "restore accepted an empty archive"
fi
"${compose[@]}" run --rm -T --entrypoint sh restore -c \
  'test -s /data/runs/runs.db && rm -f /data/runs/runs.db* /keys/api-keys.toml' \
  || fail "the empty archive changed the volumes, or there was nothing to remove"
"${compose[@]}" run --rm -T restore <"$backup"
if [[ "$variant" == postgres ]]; then
  "${compose[@]}" exec -T postgres psql -U postgres -d qaos -v ON_ERROR_STOP=1 \
    -c "DROP SCHEMA quant_research CASCADE" >/dev/null
  "${compose[@]}" exec -T postgres pg_restore -U postgres -d qaos --clean --if-exists \
    --single-transaction <"$dump"
fi
"${compose[@]}" up -d server
wait_healthy
curl -fsS "${auth[@]}" "$base/v1/runs/$run_id" | grep -q '"principal":"smoke"' \
  || fail "the restored run store lost the recorded run"
curl -fsS "${auth[@]}" -X POST -H 'content-type: application/json' \
  -d '{"arguments": {"focus": "short-term reversal"}}' "$base/v1/tools/research_report/call" \
  | grep -q '"status":"ok"' || fail "the restored server cannot answer"

echo "== the server cannot write its knowledge store"
if [[ "$variant" == sqlite ]]; then
  if "${compose[@]}" exec -T server sh -c 'touch /data/knowledge/x' 2>/dev/null; then
    fail "the knowledge store is writable from the server"
  fi
else
  # Refused by permission, not only by read-only transactions.
  denied="$("${compose[@]}" exec -T postgres psql -U qaos_reader -d qaos \
    -c "SET default_transaction_read_only = off" \
    -c "DELETE FROM quant_research.chunks" 2>&1 || true)"
  grep -q "permission denied" <<<"$denied" || fail "the server's role can write: $denied"
fi

echo "PASS: team server smoke test ($variant)"
