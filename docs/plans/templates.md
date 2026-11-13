# Plan: templates as starting points, named one way, pinned when fetched

Scope: App Factory after pytemplate-uv 3.1.0 (2026-11-12), which removed `pytemplate update`, added
commit and digest pins for git packs, writes `AGENTS.md` into its own profiles, and gave the `python`
profile an `mcp` component. This plan brings App Factory in step, and makes where a template comes
from, what it is called, and how a fix reaches a project the same story everywhere.

Status key: `[x]` done and verified, `[ ]` open.

## 1. What is inconsistent today

| Area | Today | Problem |
| --- | --- | --- |
| Later fixes | The docs, decisions 0009 and 0022, and the backlog say `pytemplate update` cannot reach a project yet | The command no longer exists, so the route they wait for will not come |
| `quant-apps` projects | `AGENTS.md`, `config.toml`, and every `firm/` file say "later template updates leave this alone"; `copier.yml` has `_skip_if_exists` for them | They promise an update that never runs |
| Pack comments | Each `questions.yml` says to keep questions stable so `pytemplate update` can replay answers | Wrong reason |
| Git packs | `qaos app template add` pins a tag only | A tag can move. pytemplate-uv 3.1 refuses a pack whose commit or files changed; App Factory cannot |
| Listing | `qaos app template list` prints pack names and profiles, with no version, and not the name `app new` takes | A user cannot see what they would build from, or copy a name |
| The `python` profile | The planner allows only `container` | The new `mcp` component is refused |
| Dependency | `pytemplate-uv>=3.0,<4` | 3.0 has no pins |

## 2. Design

1. **A generated project is a starting point, and it is the firm's from the first commit.** No command
   upgrades it. To take a later fix, generate a fresh project from the newer template with the same answers
   and compare it with yours; the project's `.pytemplate.toml` records the pack, its version, and the answers.
   Keeping changes in `config.toml` and `firm/` keeps that comparison small. This closes the backlog item.
2. **One name for every template: `pack/profile`.** It works in `qaos app new`, and `template list` prints it
   for every source, with the pack's version. A profile's bare name stays as a shortcut for built-in ones.
   Specs keep `template` and `profile`.
3. **Three sources, one rule each.**

   | Source | Named | Changes when |
   | --- | --- | --- |
   | Built in (`quant-apps`, `quant-baseline`, `agent-tool`, and pytemplate-uv's `bundled`) | `quant-apps/risk-monitor` | You upgrade `quant-agent-os` |
   | Your own pack: a fork or a directory | the name you registered | You edit it |
   | A git pack | the name you registered | Never: it is fetched once at a tag, and held to a commit and a digest |

4. **Git packs are pinned to content.** `template add` records the commit and the digest of what it fetched.
   `--commit` and `--digest` refuse a pack that differs, so a value published by the author or by an index of
   packs can be checked. They are the values pytemplate-uv's `--template-commit` and `--template-digest` take,
   computed the same way, and a test holds the two digests equal. `template check NAME` fails when a cached
   pack no longer matches its recorded digest.
5. **`agent-tool` or `mcp`.** `agent-tool` builds tools for a Quant-Agent-OS server, behind its permissions and
   audit. `python` with `mcp` builds a standalone MCP server with no platform dependency. The product page says
   which to choose.

Not in this plan: renaming a pack or `bundled` (it would break specs for no gain), any upgrade command, and
any change to pytemplate-uv.

## 3. Steps

- [ ] 1. Require `pytemplate-uv>=3.1,<4`; refresh `uv.lock`; allow `mcp` on `bundled/python`.
- [ ] 2. Packs: drop the update wording and `_skip_if_exists`; say how to take a fix. Patch versions:
       `quant-apps` 1.0.1, `quant-baseline` 3.1.1, `agent-tool` 1.0.1.
- [ ] 3. `template add --commit --digest`; record both in `factory.toml`; `template check` verifies a cached
       pack; `template list` prints `pack/profile`, versions, and pins.
- [ ] 4. Docs: one "Where templates come from" section in the product page; decision 0025; decisions 0009
       and 0022 point to it; the backlog row closes; the changelog.
- [ ] 5. Verify: tests, lint, format, types, and the opt-in factory smoke test.

## 4. Acceptance criteria

- No file outside `CHANGELOG.md` and `docs/plans/archive/` mentions `pytemplate update` or a template update.
- A git pack whose tag moved, or whose files changed, is refused by `template add` with pins, and nothing is
  registered.
- `template list` shows a name that `qaos app new` accepts for every profile it lists.
- A spec with `template = "bundled"`, `profile = "python"`, and `components = ["mcp"]` plans.

## 5. Outside this repository

Each needs the owner's say, and none blocks the steps above: publishing the new pack versions to their public
repositories, a public repository for `quant-apps`, and listing them in an index.
