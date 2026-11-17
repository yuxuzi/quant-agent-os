# Tutorial settings for bash or zsh. Source it in every terminal you use, from any folder:
#   source /path/to/quant-agent-os/examples/tutorial/env.sh
# The tutorial has its own home, so everything it creates is in one folder: `rm -rf "$TUT"` removes it.

# REPO is the clone this file is in, wherever you source it from.
if [ -n "${BASH_SOURCE:-}" ]; then
  _qaos_env="${BASH_SOURCE[0]}"
else
  _qaos_env="${(%):-%x}"  # zsh
fi
export REPO="$(cd "$(dirname "$_qaos_env")/../.." && pwd)"
unset _qaos_env
export TUT="$HOME/qaos-tutorial"
export QUANT_AGENT_OS_HOME="$TUT"
mkdir -p "$TUT"

# Labs 5 to 7 call the tutorial server at port 8765 as the holder of a key, and print the answer
# readably: `call KEY TOOL '{"name": value}'` calls a tool, and `get KEY /v1/...` reads a route.
# Both work from any folder.
call() {
  local arguments="${3:-}"
  [ -n "$arguments" ] || arguments='{}'
  curl -sS -H "Authorization: Bearer $1" -H 'content-type: application/json' -X POST \
    "http://127.0.0.1:8765/v1/tools/$2/call" -d "{\"arguments\": $arguments}" \
    | uv run --project "$REPO" python "$REPO/examples/tutorial/show.py"
}
get() {
  curl -sS -H "Authorization: Bearer $1" "http://127.0.0.1:8765$2" \
    | uv run --project "$REPO" python "$REPO/examples/tutorial/show.py"
}

echo "REPO=$REPO  (your clone)"
echo "TUT=$TUT  (everything the tutorial creates; QUANT_AGENT_OS_HOME points here)"
echo "In a new terminal, first run: source \"$REPO/examples/tutorial/env.sh\""
