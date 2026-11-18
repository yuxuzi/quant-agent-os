---
name: quant-legacy
description: Turn a fund's existing Python into Quant-Agent-OS tools with a developer, on their machine, through the qaos legacy commands - scan, review, wrap, run the generated contract test, fix, repeat. Use when the user wants to serve legacy functions or classes (pandas signals, risk engines, report scripts) as tools.
license: MIT
compatibility: Needs the qaos command on the developer's machine; drafting a service needs fastapi and uvicorn. Uses no server tools - the server never reads or converts code.
metadata:
  min-server-version: "0.7.0"
---

# Converting legacy code into tools

You work with a developer in their repository. `qaos legacy` drafts the code; the developer
decides, and the generated test says when it works. The draft is a first version: it will not
pass first time on real code, and you never report it done until the test passes.

## Workflow

1. **Scan.** Run `qaos legacy scan PATH` on the legacy package, then
   `qaos legacy scan PATH --write legacy.toml`. It reads the code without importing it, and
   classifies each public function and method:
   - *expose*: typed, documented, and pure; registered as it is.
   - *facade*: wrapped in a thin typed function (untyped or pandas inputs, module settings,
     printing, files, a database).
   - *service*: keeps state, starts slowly, or caches; served from one process over HTTP.
2. **Review with the developer.** Go through `legacy.toml` together. For each entry confirm
   `kind`, `name`, and `data` (which parameters take a dataset reference, as series or
   frame), set `include = false` for what should not be served, and fill `convention` when
   the developer knows it. The scan's reasons are above each entry; it guesses from the code
   and misses what only running would show.
3. **Wrap.** Run `qaos legacy wrap legacy.toml --out legacy_tools`. It writes `facades.py`,
   `server.py`, and for services `service/app.py` and `stubs.py`, plus `conftest.py` and
   `test_tools.py`. It never overwrites without `--force`, so rerun it only before the
   developer edits the draft; after that, edit the files.
4. **Run the test.** `uv run pytest legacy_tools/test_tools.py`. It runs check_app() on every
   tool with an example call, through the executor and MCP, and lists every open TODO.
5. **Read each failure and fix it**, smallest change first: a hint, a data marker, an example
   value in `test_tools.py`, a missing value the legacy code returns as NaN. Then run the
   test again. Repeat until both tests pass.

## Rules

- **Ask about every TODO on conventions and units.** `TODO(convention)`, `TODO(unit)`, and
  `TODO(config)` are the developer's decisions: annualisation, windows, return units, which
  module settings a caller may choose. Never invent them; ask, then delete the TODO.
- **Keep facades thin.** A facade adapts types and calls the legacy code unchanged. Add no
  business logic, and do not rewrite the calculation; if it is wrong, tell the developer.
- **Declare permissions honestly.** Keep every permission the draft declares unless the
  developer shows the code does not need it, and add one for anything else the code does,
  such as writing files or calling out. Declared permissions are checked, not sandboxed:
  never describe them as limiting what the code can do.
- **Read code, do not run it to find out.** Do not import the legacy package to explore it;
  it may need the fund's database. The generated test is the place it runs.
- **Done means the test passes.** Report done only when `test_tools.py` passes, with its real
  output. Otherwise say what still fails and what is left to decide.
- **Nothing on the server.** The draft is code the firm commits and serves with its own
  server file; no tool reads, converts, or registers code.

## Related

Once the tools pass, the firm serves them with `server.py`; the `quant-analytics` skill covers
the platform's own metrics, which can read the same datasets.
