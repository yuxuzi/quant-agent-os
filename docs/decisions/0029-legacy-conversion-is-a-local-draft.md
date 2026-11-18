# 0029: Converting legacy code is a local command that writes a draft

- Status: accepted; builds on [0016](0016-clients-orchestrate.md), [0020](0020-a-firms-own-server.md), and
  [0024](0024-registering-a-firms-functions.md)
- Date: 2026-11-17

## Context

Decision 0024 made registering a typed, documented function about three lines. A fund's existing code is
rarely like that. Its signals take untyped pandas objects, read a module-level `CONFIG`, print as they go,
and open a global database connection. Its risk engine loads a model for a minute and caches answers. Before
`FirmApp.expose` can take any of it, someone must read each function, decide what it needs, and write a
wrapper or a service. That reading is the slow part, and most of it is mechanical.

A coding agent with the developer can do the rest of the work, if it starts from a draft that says what it
found and what it could not decide. The owner's framing for 0.8.0: "This is mainly a tool for vibe coding: a
developer debugs and steers it; it is not guaranteed to work on the first try."

## Decision

- **`qaos legacy` is a command the developer runs, in App Factory.** `scan PATH` inventories a package;
  `wrap INVENTORY --out DIR` drafts the code. No MCP tool reads, converts, or registers code, and the server
  takes no part (decision 0016, and writes to the user's machine stay local). It lives in App Factory, which
  already generates Python locally, as its own command module (`quant_app_factory/legacy_cli.py`).
- **The scan is static.** It parses the code with `ast` and never imports or runs it: legacy code may need
  the fund's database, network, or heavy dependencies just to import. Names are resolved through each
  module's imports, and a helper's behaviour passes to its callers within the scanned code. It is a
  heuristic, and its reasons say what it saw.
- **Three kinds, with reasons.** Each public function or method is *expose* (typed, documented, pure, with
  types `expose` accepts), *facade* (wrap it in a thin typed function), or *service* (it keeps state, starts
  slowly, or caches, so one process holds it behind HTTP and `FirmApp.call_service` registers each
  endpoint). The developer edits a small TOML inventory: kind, name, inclusion, data parameters,
  permissions, and convention.
- **The output is a draft, marked as one.** `wrap` writes facades that call the legacy code unchanged, a
  server file that names every tool with `FirmApp.expose` or `call_service`, a FastAPI service for stateful
  code, and a test that runs `check_app`. Every decision it could not make is a `TODO(...)` comment, and the
  test fails while one is left. It never overwrites a file without `--force`.
- **Permissions follow what the scan saw.** A function that writes files is drafted with
  `write_local_data`, one that calls out with `network`, one that runs programs with `execute_code`, and a
  service that changes anything with `external_action`, each flagged for review. Declared permissions are
  checked, not sandboxed (finding F-6): the draft does not make the legacy code safer, it makes what it does
  visible.
- **Explicit registration holds.** The draft is ordinary Python the firm commits and pins (decisions 0020
  and 0024). Nothing discovers code when the server starts.

## Alternatives considered

- **An MCP tool that converts code.** It would have the server read and write source, which 0016 and the
  local-writes rule keep off the server, and it would put retrieved text in charge of what code is written.
- **Import the code to inspect it.** Signatures and types would be exact, but importing runs the module:
  connections open, models load, and a missing dependency stops the scan. A static scan always finishes.
- **Generate finished code.** No tool can know a fund's conventions, units, or which settings a caller may
  choose. Pretending to would hide those decisions; a TODO puts them in front of the developer.
- **Rewrite the legacy code.** Facades leave it unchanged, so the fund's own tests and history still apply,
  and a facade adds no business logic.

## Consequences

- The measure is how far the draft gets and how few edits finish it, not that it needs none. On the test
  fixture, the generated `check_app` test passes as drafted, the service called over loopback; the open
  TODOs are what is left.
- The scan misses what only running would show: a method called on an object passed in, a file path built
  at run time, a connection opened by a library it does not know. The skill tells the agent to read the
  code with the developer rather than trust the classification.
- A rescan rewrites the inventory, so edits are kept only by writing it to another file and merging.
- The generated `conftest.py` puts the legacy package on the path until it is installed, and the drafted
  service binds to loopback; serving it elsewhere is the firm's deployment decision.
