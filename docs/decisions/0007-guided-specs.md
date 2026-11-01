# 0007: Guided specs are stateless; the spec file is the draft

- Status: accepted (implemented in B-24, 2026-10-31)
- Date: 2026-10-31

## Context

Writing an `AppSpec` by hand needs the user to know every profile, component, and question in
advance. Users want to be guided until there is enough information to build an app. pytemplate-uv
packs already declare that information through `pytemplate.api.open_catalog`: profiles,
components, Copier questions, and the components' runtime settings.

A first design added a deterministic next-question engine and a draft store with its own
states. Both are unnecessary:
- **The client already converses.** An MCP client such as Claude, or a terminal loop, can
  hold the conversation.
- **Copier's rules are hard to copy.** Re-evaluating them (Jinja `when`, defaults, validators,
  and their filters) outside Copier would drift from what pytemplate-uv actually accepts.

## Decision

- **Two read-only tools.**
  - `describe_template` returns what a template asks: profiles, components, questions with
    their `when` text, and settings, with `secret` marked.
  - `check_spec` returns `ready`, with the exact files, or the problems.
- **pytemplate-uv is the judge.** A spec is ready only when `pytemplate.api.plan` accepts it.
  Our own checks add only what pytemplate-uv cannot know: required spec fields, and secrets
  given as values.
- **The spec file is the draft.** A guided session, whether `quant-factory new` or a
  conversation in Claude, produces an ordinary `AppSpec` TOML file. There is no draft table,
  no session state, and no resume logic.
- **Secrets only as references.** A setting marked `secret`, or a value under a secret-named
  key, must be `env:NAME`.
- **Creation stays approval-gated.** `create_app` needs an approval per call (decision 0005).
  Until MCP clients can approve (O-13), people converse in Claude and confirm with
  `quant-factory create spec.toml`.

## Consequences

- One guide works for the bundled profiles, quant templates, and user packs, with no new state
  to persist or migrate.
- The terminal skips questions whose simple `when` is false. Anything subtler is caught by
  `check_spec`, at the cost of an extra round.
- Model-drafted specs (B-16) fill the same file and go through the same check.
