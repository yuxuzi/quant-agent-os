# 0025: Templates are starting points, named one way, and pinned when fetched

- Status: accepted (direction from the owner, 2026-11-12)
- Date: 2026-11-12
- Amends: [0009](0009-quant-baseline-templates.md), [0022](0022-one-application-per-stage.md)

## Context

Decisions 0009 and 0022 left one question open: how a fix to a template reaches a project already generated
from it. Both waited on `pytemplate update`. pytemplate-uv 3.1 removed that command, because nobody will risk
merging a template release into a project they have shipped and changed.

The `quant-apps` projects still said that later template updates would leave `config.toml` and `firm/` alone.
Template names were also uneven: `qaos app new` took a short name, a spec took a template and a profile, and
`template list` printed neither form with a version. A git pack was pinned to a tag, which its author can move.

## Decision

- **A generated project is a starting point, and it belongs to the firm from the first commit.** No command
  upgrades it. Its `.pytemplate.toml` records the pack, the pack's version, and the answers. To take a later fix,
  the firm generates a fresh project from the newer template with the same answers and compares. Keeping changes
  in `config.toml` and `firm/` keeps that comparison small, which is now the only reason for the convention.
- **Every template has one name, `pack/profile`.** `qaos app new` takes it, and `qaos app template list` prints
  it for every source with the pack's version. A built-in profile's bare name remains a shortcut. Specs keep
  `template` and `profile`.
- **A template comes from one of three sources:** built into the wheel, the firm's own pack, or a git pack.
  A built-in pack changes only when the firm upgrades `quant-agent-os`.
- **A git pack is held to its content.** `template add` fetches once at a tag and records the commit and a
  digest of the files. `--commit` and `--digest` refuse a pack that differs. The digest is computed as
  pytemplate-uv computes it, and a test holds the two equal, so a value published for one tool works in the other.
- **No pack is renamed.** Renaming `bundled`, or a pack whose id differs from its folder, would break specs
  for no gain.

## Consequences

- The backlog item on updating generated projects closes. If a firm later needs fixes delivered, that is a new
  decision with a real case behind it.
- `factory.toml` gains `commit` and `digest` under each registered git pack. An older `qaos` refuses a file that
  has them, because it rejects unknown fields.
- A cached pack is checked against its digest by `template check`, not on every plan. The cache is in the
  firm's own home directory.
- A pin proves the files are the ones someone named. It does not make them safe: a generated project's
  dependencies and tests still run code.
