#!/usr/bin/env bash
# Kestrel Point Capital's day (fictional), outside in, as the fund's engineer would run it:
# build the wheels, install them into a clean folder with its own HOME, build the warehouse,
# set up the home, ingest the documents, draft the legacy tools, start the gateway, the
# risk-engine service, and the fund's server with sign-in, then work as each user, run the
# agreed cases, and read the audit trail. Every phase is timed. See docs/pilot-runbook.md.
#
#   examples/pilot-fund/drill.sh            # from the repository root; needs uv and curl
#   KEEP=1 examples/pilot-fund/drill.sh     # keep the work folder afterwards
#
# Only the install phase may use the network (to fetch the dependencies, unless uv's cache has
# them); every later phase runs with UV_OFFLINE=1 and talks only to loopback. Ports 9400 (gateway),
# 8100 (risk service), and 8000 (server) must be free: the gateway's audience names port 8000.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PILOT="$REPO/examples/pilot-fund"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kestrel-drill.XXXXXX")"
FUND="$WORK/fund"
LOGS="$WORK/logs"
SERVER=http://127.0.0.1:8000
GATEWAY=http://127.0.0.1:9400
RISK=http://127.0.0.1:8100
PIDS=()
PHASES=()
mkdir -p "$LOGS"

# Reuse the caller's uv cache and Python installs, so the clean HOME does not download them again.
UV_CACHE_DIR="$(uv cache dir)"
export UV_CACHE_DIR
if PYTHONS="$(uv python dir 2>/dev/null)"; then export UV_PYTHON_INSTALL_DIR="$PYTHONS"; fi
unset VIRTUAL_ENV QUANT_AGENT_OS_HOME

now() {
  if [ -n "${EPOCHREALTIME:-}" ]; then echo "${EPOCHREALTIME/,/.}"; else python3 -c 'import time; print(time.time())'; fi
}

cleanup() {
  local code=$?
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || true
  done
  if [ "$code" -ne 0 ]; then
    echo "drill failed (exit $code); logs in $LOGS" >&2
    for log in "$LOGS"/*.log; do [ -f "$log" ] && { echo "--- $log" >&2; tail -20 "$log" >&2; }; done
  fi
  if [ "${KEEP:-0}" = 1 ] || [ "$code" -ne 0 ]; then
    echo "work folder kept: $WORK" >&2
  else
    rm -rf "$WORK"
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

STARTED=""
phase() {
  finish
  STARTED="$(now)"
  CURRENT="$1"
  printf '\n== %s\n' "$1"
}
finish() {
  if [ -n "$STARTED" ]; then
    local seconds
    seconds="$(awk -v a="$STARTED" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }')"
    PHASES+=("$(printf '%-44s %7ss' "$CURRENT" "$seconds")")
    STARTED=""
  fi
}

wait_for() {  # url, name, log, [curl options...]
  local url="$1" name="$2" log="$3" deadline=$((SECONDS + ${START_TIMEOUT:-180}))
  shift 3
  until curl -fsS -m 5 -o /dev/null "$@" "$url" 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then echo "$name did not start; see $log" >&2; return 1; fi
    sleep 0.2
  done
}

for port in 8000 8100 9400; do
  if curl -s -m 5 -o /dev/null "http://127.0.0.1:$port/" 2>/dev/null; then
    echo "port $port is in use; stop what is listening there first" >&2
    exit 1
  fi
done

phase "1. build the wheels"
(cd "$REPO" && uv build --wheel --quiet --out-dir "$WORK/dist" \
  && uv build --wheel --quiet --package quant-toolbox --out-dir "$WORK/dist")
ls "$WORK/dist"

phase "2. install into a clean folder (network allowed)"
export HOME="$WORK/home"
mkdir -p "$HOME" "$FUND"
cp -R "$PILOT/facility" "$PILOT/site" "$PILOT/drill_client.py" "$FUND/"
find "$FUND" -name __pycache__ -prune -exec rm -rf {} +
rm -f "$FUND/facility/warehouse/kestrel.db" "$FUND/facility/warehouse/known_answers.json"
cd "$FUND"
uv init --bare --quiet --name kestrel-site --no-workspace
PLATFORM_WHEEL="$(ls "$WORK"/dist/quant_agent_os-*.whl)"
TOOLBOX_WHEEL="$(ls "$WORK"/dist/quant_toolbox-*.whl)"
uv add --quiet "$TOOLBOX_WHEEL" "quant-agent-os[server,analytics] @ $PLATFORM_WHEEL"
uv add --quiet --dev pytest
uv run qaos --version

export UV_OFFLINE=1  # from here on, nothing is fetched
export QUANT_AGENT_OS_HOME="$HOME/.quant-agent-os"
export KPC_WAREHOUSE="$FUND/facility/warehouse/kestrel.db"
export KPC_OUTPUT="$WORK/reports"
export KPC_RISK_SERVICE_URL="$RISK"
export QUANT_AGENT_OS_ACCESS_FILE="$FUND/facility/access.toml"
export QUANT_AGENT_OS_SIGN_IN_FILE="$FUND/site/sign-in.toml"
export KESTREL_INTROSPECTION_SECRET=kpc-introspection-fixture
export QUANT_AGENT_OS_GRANTED_PERMISSIONS='["read_local_data", "network"]'
export QUANT_AGENT_OS_PREAPPROVED_TOOLS='{"network": ["risk_desks", "risk_exposure", "risk_desk_var", "risk_stress", "risk_stress_all", "risk_factor_decomposition"]}'
export QUANT_AGENT_OS_LOG_FORMAT=json
# The server signs each risk call with a secret the risk service checks, named by a file (decision 0030).
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "$WORK/risk-caller.secret"
export KPC_RISK_CALLER_SECRET_FILE="$WORK/risk-caller.secret"
mkdir -p "$KPC_OUTPUT"

phase "3. build the warehouse"
uv run python facility/warehouse/build_warehouse.py "$KPC_WAREHOUSE"

phase "4. set up the home, and check it"
uv run qaos init
uv run qaos doctor 2>&1 | tee "$LOGS/doctor.log"

phase "5. ingest the fund's documents"
uv run qaos ingest facility/research

phase "6. draft the legacy tools and test the finished ones"
uv run qaos legacy scan facility/kestrel_quant --write "$WORK/legacy.scanned.toml" | tail -1
uv run qaos legacy wrap site/legacy.toml --out "$WORK/fresh_draft" | grep -E "^Drafted"
TODOS="$(cat "$WORK"/fresh_draft/{facades,server,stubs}.py "$WORK/fresh_draft/service/app.py" | grep -c TODO || true)"
EDITED="$(diff -r -x __pycache__ -x conftest.py -x '*.pyc' "$WORK/fresh_draft" site/legacy_tools | grep -c '^[<>]' || true)"
echo "fresh draft: $TODOS open decisions (TODO lines); the finished draft differs in $EDITED lines"
(cd site/legacy_tools && uv run --project "$FUND" pytest -q -p no:cacheprovider test_tools.py 2>&1 | tail -1)

phase "7. start the gateway, the risk service, and the server"
# --no-sync: the environment was synced at install, and three processes need not race to check it.
uv run --no-sync python facility/gateway/idp.py --port 9400 </dev/null >"$LOGS/gateway.log" 2>&1 &
PIDS+=($!)
(cd site/legacy_tools && PYTHONPATH="$FUND/facility" exec uv run --no-sync --project "$FUND" \
  uvicorn service.app:app --host 127.0.0.1 --port 8100 --log-level warning) </dev/null >"$LOGS/risk.log" 2>&1 &
PIDS+=($!)
uv run --no-sync python site/kestrel_server.py </dev/null >"$LOGS/server.log" 2>&1 &
PIDS+=($!)
wait_for "$GATEWAY/.well-known/oauth-authorization-server" gateway "$LOGS/gateway.log"
wait_for "$SERVER/healthz" server "$LOGS/server.log"
wait_for "$RISK/openapi.json" "risk service" "$LOGS/risk.log"
grep -E "^Serving" "$LOGS/server.log" || true

phase "8. the fund's day, as each user"
uv run python drill_client.py --server "$SERVER" --gateway "$GATEWAY" --risk "$RISK" --warehouse "$KPC_WAREHOUSE"

phase "9. the agreed cases"
uv run python site/kestrel_server.py acceptance site/acceptance.toml | tee "$LOGS/acceptance.md" | head -3

phase "10. who called what"
uv run qaos runs show | tee "$LOGS/runs.log" | head -40
# The AI-use report for the day, UTC: today's calls, by principal, tool, and dataset, with no content.
read -r FROM TO < <(python3 -c 'import datetime as d; t = d.datetime.now(d.timezone.utc).date(); print(t, t + d.timedelta(days=1))')
uv run qaos runs report --from "$FROM" --to "$TO" > "$LOGS/ai-use.md"
head -12 "$LOGS/ai-use.md"
finish

printf '\n== summary\n'
printf '%s\n' "${PHASES[@]}"
TOTAL="$(awk '{ gsub("s$", "", $NF); total += $NF } END { printf "%.1f", total }' < <(printf '%s\n' "${PHASES[@]}"))"
printf '%-44s %7ss\n' "total" "$TOTAL"
echo "legacy draft: $TODOS TODO lines fresh; $EDITED lines differ in the finished draft"
echo "drill passed"
