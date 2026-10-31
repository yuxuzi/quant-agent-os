# Contributing

Thanks for helping build Quant-Agent-OS. Please be respectful and constructive.

## Setup and checks

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

CI runs the same commands on Windows, macOS, and Linux for Python 3.11–3.15.

## Adding a tool

1. Write a plain function `def fn(params: InputModel) -> OutputModel` in the package that owns
   the logic (for example the analytics package), with unit and known-answer tests there.
2. Decorate it with `@tool(...)`, declaring every `Permission` it needs.
3. Register it explicitly in a `ToolRegistry`.
4. Contract tests must pass: complete schemas, and MCP output equal to the direct call.

## Adding a model provider

Implement `ModelProvider` in an adapter module with an optional dependency. Add offline tests
with recorded responses; live tests must be opt-in.

## Architecture changes

Record significant decisions as a new file in `docs/decisions/` (`NNNN-short-title.md`: context,
decision, consequences). Read [AGENTS.md](AGENTS.md) for the invariants.

## Pull requests

Keep changes focused, add tests, update `CHANGELOG.md` under "Unreleased", and describe how you
verified the change.
