#!/usr/bin/env bash
# Rehearse docs/operations.md from a real tagged release: seed it, back up, upgrade to this commit,
# confirm that old runs and answers survive, roll back, restore, and confirm again.
#   deploy/upgrade-test.sh            # the SQLite store (compose.yaml)
#   deploy/upgrade-test.sh postgres   # PostgreSQL with pgvector (compose.postgres.yaml)
#   QAOS_UPGRADE_FROM=v0.3.0 deploy/upgrade-test.sh
# It uses its own compose project, port, and images, and removes them when it finishes.
set -euo pipefail

cd "$(dirname "$0")/.."
from="${QAOS_UPGRADE_FROM:-v0.2.0}"
variant="${1:-sqlite}"
export QAOS_PORT="${QAOS_PORT:-18002}"
old="quant-agent-os:drill-old"
new="quant-agent-os:drill-new"
workspace="$(mktemp -d)"
case "$variant" in
  sqlite) compose=(docker compose -p qaos-upgrade-sqlite -f deploy/compose.yaml) ;;
  postgres)
    # Throwaway passwords for this run only, outside the repository.
    for name in QAOS_POSTGRES_PASSWORD QAOS_INGEST_PASSWORD QAOS_READER_PASSWORD; do
      echo "$name=$(python3 -c 'import secrets; print(secrets.token_hex(16))')" >>"$workspace/env"
    done
    compose=(docker compose -p qaos-upgrade-postgres -f deploy/compose.postgres.yaml
             --env-file "$workspace/env")
    ;;
  *) echo "usage: $0 [sqlite|postgres]" >&2; exit 2 ;;
esac
base="http://127.0.0.1:${QAOS_PORT}"
export QAOS_IMAGE="$old"
cleanup() {
  "${compose[@]}" logs server || true
  "${compose[@]}" down -v --remove-orphans
  docker image rm "$old" "$new" >/dev/null 2>&1 || true
  rm -rf "$workspace"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

start() {  # Start the given image, and check that it is what runs.
  export QAOS_IMAGE="$1"
  "${compose[@]}" up -d --no-build server
  for _ in $(seq 1 60); do
    curl -fsS "$base/healthz" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS "$base/healthz" | grep -q '"ok"' || fail "$1 never became healthy"
  running="$(docker inspect --format '{{.Image}}' "$("${compose[@]}" ps -q server)")"
  [[ "$running" == "$(docker image inspect --format '{{.Id}}' "$1")" ]] || fail "$1 is not running"
}

call() {  # A tool's whole response, from a name and its JSON arguments.
  curl -fsS -H "Authorization: Bearer $key" -X POST -H 'content-type: application/json' \
    -d "{\"arguments\": $2}" "$base/v1/tools/$1/call"
}

field() { python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['$1'], sort_keys=True))"; }

answers() {  # What the tools answer, from the corpus and from a dataset, with no run ids or times.
  call find_related_signals '{"query": "short-term reversal"}' | field output
  call analyze_performance \
    '{"returns": {"dataset": "monthly", "column": "strategy"}, "periods_per_year": 12}' | field output
}

still_holds() {  # Every field the older answer had is unchanged. A release may add fields.
  python3 - "$1" "$2" <<'PY'
import json
import sys


def differences(old, new, path=""):
    if isinstance(old, dict) and isinstance(new, dict):
        for key, value in old.items():
            if key in new:
                yield from differences(value, new[key], f"{path}.{key}")
            else:
                yield f"{path}.{key} is gone"
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            yield f"{path} had {len(old)} items and now has {len(new)}"
        for index, pair in enumerate(zip(old, new)):
            yield from differences(*pair, f"{path}[{index}]")
    elif old != new:
        yield f"{path}: {old!r} became {new!r}"


with open(sys.argv[1]) as first, open(sys.argv[2]) as second:
    pairs = zip(map(json.loads, first), map(json.loads, second), strict=True)
    found = [text for old, new in pairs for text in differences(old, new)]
print("; ".join(found))
sys.exit(1 if found else 0)
PY
}

check_survived() {  # The seeded run is there under its principal, and the answers have not changed.
  "${compose[@]}" run --rm -T doctor >"$workspace/doctor" \
    || fail "doctor failed on $1: $(cat "$workspace/doctor")"
  curl -fsS -H "Authorization: Bearer $key" "$base/v1/runs/$seeded_run" \
    | grep -q '"principal":"drill"' || fail "the seeded run did not survive on $1"
  answers >"$workspace/answers"
  changed="$(still_holds "$workspace/seeded" "$workspace/answers")" \
    || fail "the answers changed on $1: $changed"
}

echo "== build $from and this commit"
git rev-parse -q --verify "refs/tags/$from" >/dev/null \
  || git fetch --depth 1 origin "refs/tags/$from:refs/tags/$from"
mkdir "$workspace/source"
git archive "$from" | tar -x -C "$workspace/source"
docker build -q -f "$workspace/source/deploy/Dockerfile" -t "$old" "$workspace/source"
QAOS_IMAGE="$new" "${compose[@]}" build server

echo "== seed $from"
"${compose[@]}" run --rm -T ingest
key="$("${compose[@]}" run --rm -T keys new --principal drill \
  --scope tools:list --scope tools:call --scope runs:read 2>/dev/null | tail -n 1)"
[[ "$key" == qaos_* ]] || fail "no API key was printed"
"${compose[@]}" run --rm -T datasets add /import/monthly-returns.csv --id monthly >/dev/null
start "$old"
seeded_run="$(call find_related_signals '{"query": "momentum"}' | field run_id | tr -d '"')"
answers >"$workspace/seeded"

echo "== back up, as operations.md says"
"${compose[@]}" stop server
"${compose[@]}" run --rm -T backup >"$workspace/backup.tar"
if [[ "$variant" == postgres ]]; then
  "${compose[@]}" exec -T postgres pg_dump -U postgres -d qaos -Fc >"$workspace/knowledge.dump"
fi

echo "== upgrade to this commit"
start "$new"
check_survived "this commit"
upgraded_run="$(call find_related_signals '{"query": "momentum"}' | field run_id | tr -d '"')"

echo "== roll back: restore the backup, and run $from"
"${compose[@]}" stop server
"${compose[@]}" run --rm -T restore <"$workspace/backup.tar"
if [[ "$variant" == postgres ]]; then
  "${compose[@]}" exec -T postgres pg_restore -U postgres -d qaos --clean --if-exists \
    --single-transaction <"$workspace/knowledge.dump"
fi
start "$old"
check_survived "$from"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $key" \
  "$base/v1/runs/$upgraded_run")"
[[ "$code" == "404" ]] || fail "the run from after the upgrade should be gone, and got $code"

echo "PASS: upgrade from $from and rollback ($variant)"
