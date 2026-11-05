# Tools changelog

Every change to a served tool's version, with deprecations and removals
([decision 0015](decisions/0015-metering-quotas-and-entitlements.md), section 3). The platform
changelog is [CHANGELOG.md](../CHANGELOG.md).

## Versioning rules

- **Major:** a breaking change to the input schema (a new required field, a removed or renamed
  field, a narrowed type), to the output schema, to the permissions, or to the meaning of a
  result.
- **Minor:** additions only, such as an optional input, a new output field, or a new enum value.
- **Patch:** fixes that change neither schema, such as a clearer description.

`tool-versions.lock` records each tool's version, permissions, and schemas. The platform tests
fail when they change without a big enough version bump. After raising the version, run
`uv run python apps/quant-platform/tests/tool_lock.py --update` and add an entry here.

Clients read the served versions from `qaos://server/info`.

**No compatibility window yet.** While the project is young, a tool may change or be removed in any
release, under its existing name, with an entry here. The rule from
[decision 0015](decisions/0015-metering-quotas-and-entitlements.md) (a new tool name, and at least
90 days and a major release before removal) is suspended until the owner decides the project is
mature enough. Version bumps and the lock still apply, so every change stays visible.

## 0.1.2

| Tool | Version | Change |
| --- | --- | --- |
| `analyze_performance` | 2.0.0 | `periods_per_year` no longer defaults to 12: it is inferred from the dataset's dates, and refused when neither is available. Series from different datasets are joined on their dates, rows with missing values are left out and counted, and returns and benchmark must be passed the same way. |
| `analyze_risk` | 2.0.0 | The same, for assets and factors. |
| `research_report` | 2.0.0 | `periods_per_year` is inferred the same way. |
| `list_datasets` | 1.1.0 | Adds `periods_per_year` and `missing_cells` to each dataset. |

## 0.1.1

No tool version changes.

## 0.1.0

First versioned release. Every tool is at `1.0.0`:

| Tool | Toolset | Version |
| --- | --- | --- |
| `find_related_signals` | research | 1.0.0 |
| `get_source_passage` | research | 1.0.0 |
| `generate_strategy` | research | 1.0.0 |
| `research_report` | research | 1.0.0 |
| `analyze_performance` | analytics | 1.0.0 |
| `analyze_risk` | analytics | 1.0.0 |
| `list_datasets` | analytics | 1.0.0 |
| `describe_template` | factory | 1.0.0 |
| `check_spec` | factory | 1.0.0 |
| `plan_app` | factory | 1.0.0 |
| `server_info` | platform | 1.0.0 |

No tool is deprecated.
