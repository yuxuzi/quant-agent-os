# Quant App Factory

**Quant App Factory** is the application-engineering product of Quant-Agent-OS. It turns a
typed application spec into a scaffolded, checked, packaged Python project. It uses
[`pytemplate-uv`](https://pypi.org/project/pytemplate-uv/) 3 as a library (`pytemplate.api`),
behind the replaceable `ScaffoldProvider` port. Only one adapter module imports it.

```text
AppSpec (TOML) ─▶ ArchitecturePlanner ─▶ ScaffoldProvider.plan ─▶ operator approval
               ─▶ ScaffoldProvider.create ─▶ checks (uv sync, ruff, mypy, pytest) ─▶ FactoryReport
```

| Kind | Built from | Depends on Quant-Agent-OS |
| --- | --- | --- |
| `agent-tool` | this package's template pack (`quant_app_factory/packs/agent-tool`): one typed tool, a registry, and `check_tool` contract tests | Yes, on purpose |
| `python-cli` | pytemplate-uv's bundled `python` profile | No |
| `fastapi-service` | pytemplate-uv's bundled `fastapi` profile (`database`, `worker`, `auth`, ...) | No |
| `quant-research` | the `quant-baseline` pack: one project with `study` (IC, rank IC, spreads, decay), `backtest` (lagged trades, drift, rebalancing, costs, delistings), and `report` commands, sharing one `config.toml` | No (optional `agent-tools`) |
| your own pack | `template = "path/to/pack"` with `profile = "..."` | Up to the pack |

The kinds `alpha-research`, `backtest`, and `report` from quant-baseline 1.x still work for
one release: each builds a `quant-research` project and warns that it is an alias.

A quant-research project has one `metrics` module, with documented conventions and
known-answer tests. Each also has bias tests (no look-ahead, a real trade lag, costs that only
cost) and a `LIMITATIONS.md`. Optional components are `data-parquet`, `data-database`
(PostgreSQL, SQLite, or DuckDB, read-only, with a `check-connection` command), and `agent-tools`. A
spec can name a connection saved in `factory.toml` (`connection = "research-pg"`).

**Generated metrics and the platform's.** A quant-research project owns a numpy copy of its metrics,
so it stays portable ([decision 0009](../decisions/0009-quant-baseline-templates.md)).
`tests/factory/test_metric_parity.py` holds that copy to `quant_analytics.metrics` on annualised
return, volatility, Sharpe, maximum drawdown, hit rate, beta, active return, tracking error, and
information ratio. They differ in three ways:
- An undefined metric is NaN in a generated project and None on the platform.
- A generated metric drops missing values itself, one series at a time, except `active_metrics` and
  `beta`, which use bars where both are present. The platform's tools leave out and count missing
  rows first, so its metric functions never see them.
- Each has metrics the other lacks: Newey-West t-statistics, information coefficients, and turnover
  only in generated projects; Sortino, Calmar, omega, tail ratio, stability, value at risk, and alpha
  only on the platform.

A generated project does not receive later fixes by itself. Its `LIMITATIONS.md` says so, and
`pytemplate update` merges a newer template when you choose to.

## Use

```bash
uv run qaos app new quant-research my-lab --defaults   # one command: spec, project, checks
uv run qaos app new                # answer the template's questions; writes a ready spec
uv run qaos app plan app.toml      # writes nothing; --verbose lists every file
uv run qaos app create app.toml    # asks; your yes is the recorded, single-use approval
uv run qaos app check app.toml     # re-run an existing project's checks
uv run qaos app settings set identity.author "Ada Lovelace"   # ~/.quant-agent-os/factory.toml
uv run qaos serve --toolsets factory          # describe_template, check_spec, plan_app for Claude
uv run qaos app template fork quant-baseline/quant-research packs/mine   # make it yours
uv run qaos app template add gh:org/packs --name firm --ref v1.0.0 # pinned git pack
```

Secrets never go in a spec or `factory.toml`. Use `env:NAME` in a spec's `[settings]`, and
`password_env` or `url_env` for a connection.

From Python, `plan_app` is read-only: pytemplate-uv renders its file preview into a
temporary directory. `create_app` declares `write_local_data`,
`execute_code`, and `network`, so each call waits for an operator approval:

```python
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import READ_ONLY, PermissionPolicy
from quant_app_factory import AppFactory, PytemplateUvScaffolder, factory_tools
from quant_app_factory.tools import CREATE_PERMISSIONS

policy = PermissionPolicy(granted=READ_ONLY | CREATE_PERMISSIONS)
with Platform(factory_tools(AppFactory(PytemplateUvScaffolder())), policy=policy) as platform:
    waiting = platform.call("create_app", spec={...})  # status "approval_required"
    platform.approve(waiting.run_id)  # the operator says yes
    (created,) = platform.resume(waiting.run_id)  # runs once, in the same run
```

The design is in [docs/architecture.md §8–9](../architecture.md), and the plan and
results are in [docs/plans/approvals-and-app-factory.md](../plans/approvals-and-app-factory.md).
