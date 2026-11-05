# Tutorial settings. From the repository root, in each new terminal: source examples/tutorial/env.sh
# The tutorial has its own home, so everything it creates is in one folder: `rm -rf "$TUT"` removes it.
export REPO="$PWD"
export TUT="$HOME/qaos-tutorial"
export KB="$TUT/knowledge.db"
export QUANT_AGENT_OS_HOME="$TUT"
mkdir -p "$TUT"
