# Plan: questionnaire-driven App Factory, quant baseline templates, configuration hub

Scope: turn App Factory from "write an `AppSpec` by hand" into a guided product. A user
chooses a template: a built-in quant baseline, pytemplate-uv's bundled profiles, or their own
pack. A questionnaire then refines a draft until there is enough information to generate the
app. Settings used often, such as database connections, live in a configuration hub, and
secrets are kept only as references.

Decisions:
- [0007 questionnaire and drafts](../decisions/0007-questionnaire-and-drafts.md);
- [0008 configuration hub](../decisions/0008-configuration-hub.md);
- [0009 quant baseline templates](../decisions/0009-quant-baseline-templates.md).

Backlog: B-23 to B-29 in [ENGINEERING_REVIEW_PLAN.md](../ENGINEERING_REVIEW_PLAN.md#remaining-backlog).

## Starting point (`e0cbc71`, 2026-10-31)

- **App Factory today.** It has:
  - an `AppSpec` (`kind`: `agent-tool`, `python-cli`, or `fastapi-service`);
  - the deterministic `ArchitecturePlanner`;
  - the `ScaffoldProvider` port and `PytemplateUvScaffolder`, the only importer of
    `pytemplate.api`;
  - `AppFactory.preview`, `create`, and `check`;
  - the `plan_app` (read-only) and `create_app` (one approval per call) tools;
  - `quant-factory plan` and `quant-factory create`;
  - the `agent-tool` template pack.
- **What pytemplate-uv 3.0 already gives us,** through `pytemplate.api`:
  - **User packs:** a local directory, or a git source with a ref.
  - **`open_catalog(template, ref)`** returns:
    - the profiles, with default and optional components;
    - the components, with `requires`, `requires_any`, `conflicts`, and runtime `settings`;
    - the parsed Copier `questions`: type, choices, default, `when`, validator, and `secret`.
  - **Runtime settings.** `Component.settings` declares the environment settings an app reads
    at runtime (name, type, description, `required_in_production`, `secret`), for example
    `DATABASE_URL`. These are never stored values.
- **What it does not give us:** its skill guides the creation of *projects*, not of templates.
  Generic template authoring belongs on pytemplate-uv's own roadmap and is not planned here.
  [Q6](#q6-quant-template-authoring-b-28) records what this repository would use if it ships.

Two kinds of input follow from this, and the design keeps them apart:

| Input | Comes from | Used at | Stored in the hub? |
| --- | --- | --- | --- |
| Template answers (description, `db_kind`, frequency, ...) | the pack's Copier questions | render time | Reusable defaults only, such as identity or the default frequency |
| Runtime settings (`DATABASE_URL`, `VENDOR_API_KEY`, ...) | the selected components' `settings` | run time, by the generated app | Yes, as connection profiles, with secrets as references only |

## Steps

Each step ends with every quality gate passing (pytest, ruff, ruff format, mypy) and a commit.
Tests run offline, and anything that installs or connects is in an opt-in smoke test.

### Q1. Template catalog and user packs (B-23)

- **`TemplateCatalog`** (App Factory, application layer) lists every profile it can build
  from:
  - the built-in packs in `quant_app_factory/packs/` (`agent-tool`, and `quant-baseline`
    from Q4);
  - pytemplate-uv's bundled profiles (`python`, `fastapi`);
  - the user packs registered in the hub (Q2).
- **Each entry** gives the catalog name, source, ref, profile ids, components, and
  descriptions. It is read through `open_catalog`, and only by the scaffolder adapter.
- **`quant-factory template add <dir | gh:org/repo> [--ref TAG] [--name NAME]`** is an
  operator command:
  - it validates the pack with `open_catalog`;
  - it copies a git pack, pinned to its ref, into `~/.quant-agent-os/packs/<name>/<ref>/`;
  - it registers the pack in the hub.
  - Because later plans render from that cache, they need no network access, so the agent
    tools stay read-only.
- **Other commands:** `quant-factory template list` and
  `quant-factory template show NAME [PROFILE]`.
- **`AppSpec` generalises:**
  - `template` (a catalog name; the default comes from `kind`);
  - `profile`;
  - `components`;
  - `answers`, which are template answers;
  - `settings`, which are runtime settings as values or references.
  - `kind` stays as a shortcut for the built-in profiles, so every existing spec keeps
    working.
- **Files:**
  - `domain/spec.py`, `domain/planner.py`, and `application/catalog.py` (new);
  - `infrastructure/pytemplate_uv.py`, for catalog reads;
  - `cli.py`.
- **Accept:**
  - A local pack and a git pack (a local bare repository in tests) register, then plan
    offline from the cache.
  - Unknown templates, profiles, or components fail with the available names.
  - Every existing factory test passes unchanged.

### Q2. Configuration hub in Core (B-24)

- **Module:** `quant_agent_os.config.hub`. Core, Quant Research, and App Factory all read it.
  Only operators, or approved calls, write it.
- **Layers:** `~/.quant-agent-os/hub.toml` (user), then `./.quant-agent-os/hub.toml`
  (project), then explicit overrides. A later layer wins key by key. A team-server backend
  comes later (B-18, B-19).
- **Typed sections** (pydantic):
  - `identity`: author, email, license, Python version;
  - `connections.<name>`: `kind` (`postgres`, `sqlite`, `duckdb`, or `snowflake`), host,
    port, database, user, schema, `read_only` (default `true`), and `password_ref` or
    `url_ref`;
  - `data_sources.<name>`: `kind` (`files` or `vendor`), a path or bucket, and a
    `credentials_ref`;
  - `templates.<name>`: source, ref, and cache path (written by Q1);
  - `defaults`: components, data frequency, and calendar.
- **Secret references:** `env:NAME`, or `keyring:service/name` later.
  - The hub refuses to save anything that looks like a secret: any value under a secret-named
    key, or any field declared secret, that is not a reference. It reuses the `redact` key
    patterns from `runs/redaction.py`.
  - `resolve(ref)` runs only inside the process that needs the value, and returns a
    `SecretStr`.
- **Operator CLI:** `quant-agent-os hub list|show|set|remove|check`. `show` prints references
  and never resolves them. `check` reports which references resolve in this environment, as
  yes or no.
- **Agent tools:** a read-only `list_hub_entries` returns names, kinds, and non-secret fields.
  Writes from agents go through approved calls (Q3).
- **Files:** `src/quant_agent_os/config/hub.py` (new), `config/__init__.py`, and `cli.py`.
- **Accept:**
  - A secret-looking value is refused, by a test for each secret-named key.
  - The project layer overrides the user layer.
  - `show` and the tool output never contain resolved values.
  - A missing file reads as empty.
  - A file is written atomically, with mode 0600 on POSIX.

### Q3. Questionnaire and drafts (B-25)

- **`Questionnaire`** (App Factory, domain layer) is deterministic: no model and no network.
  Given a catalog profile and a draft, it returns the next questions in order:
  1. **The profile and its components**, with each component's `requires`, `requires_any`,
     and `conflicts` checked and explained.
  2. **The template questions** from the pack's Copier `questions`:
     - only those the profile lists, and only those whose `when` is true;
     - defaults are rendered, and validators run, with Jinja (already installed through
       copier);
     - questions already answered from the hub (identity, defaults) are prefilled and
       marked so.
  3. **The runtime settings** the selected components declare:
     - each setting is bound to a value, to a reference (`env:NAME`), or to a hub connection
       field;
     - a setting declared `secret` accepts only a reference.
- **`AppDraft`:**
  - its fields are id, catalog name, profile, answers, settings bindings, history, and
    status;
  - status is `collecting`, then `complete`, then `created`;
  - `complete` means every required question is answered and valid, and both the planner and
    `api.plan` accept the result;
  - `to_spec()` returns an `AppSpec`.
- **Storage:** a `drafts` table in the factory's SQLite store (`factory-runs.db`). Drafts are
  platform bookkeeping, like run records (decision 0007).
- **Tools:**
  - `start_app_draft(template, profile?, use_hub=true)`: read-only toward the user's files;
  - `answer_app_draft(draft_id, answers)`: validates the answers and returns the next
    questions, or `complete` with the planned files;
  - `get_app_draft(draft_id)`;
  - `plan_app(draft_id | spec)`;
  - `create_app` still needs an approval per call;
  - `save_to_hub(draft_id, what)` needs `write_local_data`, so it needs an approval.
- **CLI:** `quant-factory new [--template NAME] [--profile ID]` runs the same questionnaire
  in the terminal. When the draft is complete, it goes on to plan, confirm, and create.
- **Clients:** an MCP client such as Claude drives the conversation, asking the human each
  question. The questionnaire never sees model output except as answers, which are validated
  like any other input. B-16 (model-drafted specs) later fills drafts through
  `answer_app_draft`, and a person still confirms.
- **Files:** `domain/questionnaire.py`, `domain/draft.py`, `infrastructure/draft_store.py`,
  `tools.py`, and `cli.py`.
- **Accept:**
  - `when` branches, defaults, and validators are tested on the `agent-tool` pack and on the
    bundled `fastapi` profile with `database` and `auth`.
  - `complete` is reached only when `api.plan` accepts the draft.
  - A secret entered as a value is refused.
  - Drafts survive a restart.
  - The CLI questionnaire is tested with scripted input.

### Q4. `quant-baseline` pack: alpha research, backtest, report (B-26)

The pack lives at `quant_app_factory/packs/quant-baseline` and has three profiles. All of them
are portable and do not depend on Quant-Agent-OS (decision 0009).
- **`alpha-research`:**
  - a data port with a loader;
  - signal and factor definitions (typed functions on a panel of prices and fundamentals);
  - evaluation: IC, rank IC, quantile spreads, turnover, and decay;
  - a `run-study` CLI that writes results (Parquet or JSON, plus a summary).
- **`backtest`:**
  - a strategy spec that turns signals into target weights;
  - a vectorised daily backtester with costs and slippage, and walk-forward splits;
  - metrics: return, volatility, Sharpe, max drawdown, turnover, and hit rate, with their
    conventions documented (annualisation, risk-free rate, compounding);
  - no live trading, orders, or broker code.
- **`report`:**
  - reads research or backtest results;
  - renders Markdown or HTML;
  - keeps computed facts apart from interpretation, and states each metric's convention and
    the data period.

Components shared by the profiles:
- `data-csv` (the default) and `data-parquet` (with `pyarrow`);
- `data-database` (Q5);
- `agent-tools`: a `tools.py` with typed `@tool` wrappers, `check_tool` tests, and a
  dependency on `quant-agent-os`;
- `container`.

Each profile's questions:
- **Universe, frequency, and calendar.** The default is daily.
- **The benchmark** (`backtest`).
- **The cost model, in basis points** (`backtest`).
- **The evaluation horizon** (`alpha-research`).

Shared rules:
- **Known answers:** every metric has known-answer tests on synthetic data, for example a
  constant return gives a known Sharpe, and a known drawdown path gives its drawdown.
- **Defaults pass:** each profile, with its default components, passes its own lifecycle
  commands.

Accept:
- The smoke test (`QUANT_FACTORY_SMOKE=1`) builds each profile, and each passes `uv sync`,
  ruff, mypy, and pytest.
- Offline tests render each profile and check the file lists and answers.

### Q5. Database guidance (B-27)

- **The `data-database` component** declares:
  - settings: `DATABASE_URL` (secret) and `DATABASE_SCHEMA`;
  - questions: `db_kind`, `db_read_only`, and `db_table_prefix`, each guarded by `when`.
- **The questionnaire branch** asks, in order:
  1. Which database kind?
  2. Use an existing hub connection, or create one?
  3. For a new one: host, port, database, and user.
  4. The password or URL, as a reference such as `env:RESEARCH_PG_PASSWORD`.
  5. The schema.
  6. Read-only access? (Default: yes.)
  7. Remember this connection? This uses `save_to_hub`, which needs an approval.
- **The generated app gets:**
  - a `settings.py` (pydantic-settings) that reads the environment;
  - a `.env.example` that lists the variable names, never values;
  - a repository adapter behind the data port, using SQLAlchemy, with the driver chosen by
    `db_kind`;
  - a `check-connection` command.
- **No database drivers in quant-agent-os.** The optional post-create
  `uv run <app> check-connection` needs `execute_code` and `network`, so it needs an approval.
  - `run_command` receives only the referenced variables, resolved from the operator's
    environment through `extra_env`.
  - The resolved values never appear in the report, the run store, or logs.
- **Accept:**
  - The questionnaire branch is covered by tests.
  - The generated files contain no secret values, scanned by a test.
  - In the smoke test, `check-connection` succeeds against a SQLite or DuckDB file.

### Q6. Quant template authoring (B-28)

`quant-factory template new` is a questionnaire that writes a user pack. Its steps:
1. Fork a `quant-baseline` profile, or start from an empty profile.
2. Name the pack and the profile.
3. Choose which components to keep, add, or drop.
4. Add questions: name, type, help, choices, default, and `when`.
5. Declare runtime settings, with `secret` marking those that take references.
6. Choose starter files.

After that:
- **Validation:** `open_catalog` checks the result, then `api.plan` with default answers.
- **Registration:** the pack is registered in the hub.
- **Tool form:** `create_template` writes files, so it needs `write_local_data` and an
  approval.

Handoff (an external expectation, not a task here): if pytemplate-uv ships generic pack
authoring, for example `api.new_pack(...)` or `pytemplate pack new --json`, Q6 hands it the
generic manifest and layout. Q6 keeps the quant-specific forks, the questions, and the hub
registration.

Accept: a forked pack validates, registers, and generates an app that passes its checks
(smoke test).

### Q7. Walkthrough (B-29)

Add a walkthrough section, reproduced from a clean checkout. It goes through:
1. Save an identity and a connection in the hub (CLI).
2. `quant-factory new --template quant-baseline --profile alpha-research`, answering with
   `data-database` and the saved connection.
3. Create the app; its checks pass.
4. Do the same for `backtest` and `report`.
5. Fork `backtest` into a user pack with an extra question.

## Out of scope

- Model-drafted answers, which is B-16 and uses the same `answer_app_draft` path.
- Code generation beyond templates (B-20).
- A keychain backend for secret references; `env:` comes first.
- A team-hub backend (B-18, B-19).
- Approvals through an MCP client's confirmation step.
- Trading, orders, and broker connectivity.
- Any change to pytemplate-uv.
