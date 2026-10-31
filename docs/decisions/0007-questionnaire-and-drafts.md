# 0007: Apps are specified by a questionnaire over the pack's own questions

- Status: proposed
- Date: 2026-10-31

## Context

Writing an `AppSpec` by hand needs the user to know every profile, component, and template
question in advance. Users want to be guided: answer questions until there is enough
information to generate the app. That includes guidance such as setting up a database
connection when the app queries a database.

pytemplate-uv packs already declare everything a questionnaire needs, through
`pytemplate.api.open_catalog`:
- the profiles and their components, with `requires`, `requires_any`, and `conflicts`;
- the components' runtime `settings`, with `secret` flags;
- the Copier `questions`, with type, choices, defaults, `when` conditions, and validators.

## Decision

- **The pack is the single source of questions.** App Factory's `Questionnaire` reads the
  questions a profile lists from the pack. It evaluates `when`, defaults, and validators with
  Jinja, then asks for the runtime settings of the selected components. There is no separate
  questionnaire format to keep in sync, and user packs get a questionnaire automatically.
- **Deterministic and model-free.** The questionnaire computes the next questions from the
  draft. A client, whether a person at the CLI or an MCP client asking a person, supplies
  answers, and they are validated like any other input. Model-drafted answers (B-16) go
  through the same path, and a person confirms them.
- **Drafts are platform bookkeeping.** An `AppDraft` is `collecting`, then `complete`, then
  `created`.
  - It is kept in the factory's SQLite store, like run records.
  - The draft tools are therefore read-only toward the user's files.
  - A draft is `complete` only when both the planner and `pytemplate.api.plan` accept it.
- **Secrets enter only as references.** A question or setting marked `secret`, or one under a
  secret-named key, accepts `env:NAME` (or `keyring:...` later) and refuses values.
- **Creation stays approval-gated.** `create_app` and `save_to_hub` need an approval per call
  (decision 0005).

## Consequences

- One questionnaire covers the bundled profiles, the quant baseline packs, and user packs.
- A pack author controls the questions, so a pack's quality decides how good its guided
  experience is.
- The questionnaire depends on Copier's question semantics, as exposed by `pytemplate.api`.
  Only pytemplate-uv's supported API is used (decision 0006).
