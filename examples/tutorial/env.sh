# Tutorial settings. From the repository root, in each new terminal: source examples/tutorial/env.sh
# Everything the tutorial creates goes in one folder, so `rm -rf "$TUT"` removes nearly all of it.
export REPO="$PWD"
export TUT="$HOME/qaos-tutorial"
export KB="$TUT/kb.db"
export QUANT_AGENT_OS_HOME="$TUT"
export QUANT_AGENT_OS_STORE="$KB"
export QUANT_AGENT_OS_DATASETS_DIR="$TUT/datasets"
export QUANT_AGENT_OS_API_KEYS_FILE="$TUT/keys.jsonl"
mkdir -p "$TUT"
