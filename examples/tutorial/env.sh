# Tutorial settings. From the repository root, in each new terminal: source examples/tutorial/env.sh
# The tutorial has its own home, so everything it creates is in one folder: `rm -rf "$TUT"` removes it.
export REPO="$PWD"
export TUT="$HOME/qaos-tutorial"
export KB="$TUT/knowledge.db"
export QUANT_AGENT_OS_HOME="$TUT"
mkdir -p "$TUT"

# Labs 5 to 7 call the tutorial server at port 8765 as the holder of a key, and print the answer
# readably: `call KEY TOOL '{"name": value}'` calls a tool, and `get KEY /v1/...` reads a route.
call() {
  local arguments="${3:-}"
  [ -n "$arguments" ] || arguments='{}'
  curl -s -H "Authorization: Bearer $1" -H 'content-type: application/json' -X POST \
    "http://127.0.0.1:8765/v1/tools/$2/call" -d "{\"arguments\": $arguments}" \
    | uv run --project "$REPO" python "$REPO/examples/tutorial/show.py"
}
get() {
  curl -s -H "Authorization: Bearer $1" "http://127.0.0.1:8765$2" \
    | uv run --project "$REPO" python "$REPO/examples/tutorial/show.py"
}
