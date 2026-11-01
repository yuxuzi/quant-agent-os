# quant-agent-os-factory

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
| `alpha-research` | the `quant-baseline` pack: signals, IC, rank IC, quantile spreads, decay | No (optional `agent-tools`) |
| `backtest` | the `quant-baseline` pack: dollar-neutral weights, lagged trades, costs, walk-forward | No (optional `agent-tools`) |
| `report` | the `quant-baseline` pack: Markdown from study or backtest results, facts apart from interpretation | No (optional `agent-tools`) |
| your own pack | `template = "path/to/pack"` with `profile = "..."` | Up to the pack |

Every quant baseline project shares one `metrics` module, with documented conventions and
known-answer tests. Each also has bias tests (no look-ahead, a real trade lag, costs that only
cost) and a `LIMITATIONS.md`. Optional components are `data-parquet`, `data-database`
(PostgreSQL, SQLite, or DuckDB, read-only, with `--check-connection`), and `agent-tools`. A
spec can name a connection saved in `factory.toml` (`connection = "research-pg"`).

## Use

```bash
uv run quant-factory new                # answer the template's questions; writes a ready spec
uv run quant-factory plan app.toml      # writes nothing
uv run quant-factory create app.toml    # asks; your yes is the recorded, single-use approval
uv run quant-factory check app.toml     # re-run an existing project's checks
uv run quant-factory settings set identity.author "Ada Lovelace"   # ~/.quant-agent-os/factory.toml
uv run quant-factory serve-mcp          # describe_template, check_spec, plan_app for Claude
uv run quant-factory template fork quant-baseline/backtest packs/mine   # make a template yours
uv run quant-factory template add gh:org/packs --name firm --ref v1.0.0 # pinned git pack
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

The design is in [docs/architecture.md §8–9](../../docs/architecture.md), and the plan and
results are in [docs/plans/approvals-and-app-factory.md](../../docs/plans/approvals-and-app-factory.md).
