# Plan: quant templates and a guided App Factory (simplified)

Scope: what users asked for, with as little new machinery as possible.
1. Bring your own templates.
2. Make templates, from a quant baseline.
3. Quant baseline templates for alpha research, backtests, and reports.
4. Be guided until there is enough information to build.
5. Be guided through setting up a database.
6. Keep frequent settings somewhere.

The design follows three rules:
- **The spec file is the draft.** A guided session produces an ordinary `AppSpec` TOML file,
  which is reviewable, reusable, and diffable. Nothing else is stored.
- **pytemplate-uv is the judge.** Whether a spec is complete and valid is decided by
  `pytemplate.api.plan`, not by our own reading of Copier's rules.
- **Templates come first.** They are the part only Quant-Agent-OS has, and they already work
  with today's `quant-factory create`.

Decisions:
- [0007 guided specs](../decisions/0007-guided-specs.md);
- [0008 factory settings](../decisions/0008-factory-settings.md);
- [0009 quant baseline templates](../decisions/0009-quant-baseline-templates.md).

Backlog: B-23 to B-26 in [ENGINEERING_REVIEW_PLAN.md](../ENGINEERING_REVIEW_PLAN.md#remaining-backlog).

## Starting point (`e0cbc71`, 2026-10-31)

- **App Factory today.** It has:
  - an `AppSpec` (`kind`: `agent-tool`, `python-cli`, or `fastapi-service`);
  - the deterministic `ArchitecturePlanner`;
  - `PytemplateUvScaffolder`, the only importer of `pytemplate.api`;
  - `plan_app` (read-only, with the exact file preview);
  - `create_app` (one approval per call);
  - `quant-factory plan` and `quant-factory create`;
  - the `agent-tool` template pack.
- **What `pytemplate.api.open_catalog` exposes for a pack:**
  - profiles, with default and optional components;
  - components, with `requires`, `conflicts`, and runtime `settings`. Each setting has a
    name, a type, a description, and a `secret` flag, for example `DATABASE_URL`;
  - the parsed Copier questions: type, help, choices, default, `when`, and validator.
- **The gap:** pytemplate-uv's skill guides the creation of projects, not of templates.
  Generic template authoring is pytemplate-uv's own roadmap.

## Steps

Each step ends with every quality gate passing and a commit. Tests run offline, and anything
that installs or connects is in the opt-in smoke test (`QUANT_FACTORY_SMOKE=1`).

### Q1. Quant baseline templates and user packs (B-23), covering points 1 and 3

- **`AppSpec` gains** these fields, and `kind` stays as a shortcut, so existing specs keep
  working:
  - `template`: a built-in pack name, `bundled`, or a path to a local pack directory;
  - `profile`;
  - `answers`: template answers, passed to pytemplate-uv as data;
  - `settings`: runtime settings, each a value or an `env:NAME` reference (Q2).
- **A new built-in pack, `quant-baseline`**, ships in `quant_app_factory/packs/`. Its
  profiles are portable and do not depend on Quant-Agent-OS:
  - **`alpha-research`:**
    - a data loader;
    - signal functions on a price panel;
    - evaluation: IC, rank IC, quantile spreads, turnover, and decay;
    - a `run-study` CLI that writes results.
  - **`backtest`:**
    - signals become target weights, and trades are lagged one bar by default;
    - a daily vectorised backtest with costs in basis points;
    - walk-forward splits;
    - a `run-backtest` CLI;
    - no orders and no broker code.
  - **`report`:** turns `run-study` or `run-backtest` results into Markdown. It keeps
    computed figures apart from interpretation, and states each metric's convention and the
    data period.
- **The pack's components:**
  - **`metrics`** is required by all three profiles. It is the one place the metric code
    lives (return, volatility, Sharpe, max drawdown, turnover, IC), with documented
    conventions and known-answer tests.
  - **`data-csv`** is the default data source, and **`data-parquet`** is optional.
  - **`agent-tools`** is optional. It adds typed `@tool` wrappers, `check_tool` tests, and a
    deliberate dependency on `quant-agent-os`.
- **Bias tests in every generated project:**
  - shifting a signal forward must change the result, which catches look-ahead;
  - a known path must give its known drawdown and Sharpe;
  - zero costs and positive costs must order returns correctly.
  - A `LIMITATIONS.md` names what the template does not handle: survivorship, point-in-time
    fundamentals, corporate actions, and borrow.
- **User packs:** `template = "/path/to/pack"` renders any local pytemplate-uv pack.
  Registering git packs is part of Q4.
- **Accept:**
  - Offline tests render each profile, and check its file list and answers.
  - The smoke test builds each profile with its defaults, and each passes `uv sync`, ruff,
    mypy, and pytest, including the bias tests.
  - A local user pack renders.
  - The existing specs and tests are unchanged.

### Q2. Guided specs and factory settings (B-24), covering points 4 and 6

- **Two read-only tools** do the guiding, so an MCP client such as Claude holds the
  conversation:
  - **`describe_template(template, profile?)`** returns the profiles, the components (with
    `requires` and `conflicts`), each question (help, type, choices, default, and its `when`
    condition as text), and each component's runtime settings, with `secret` marked.
  - **`check_spec(spec)`** returns `ready` or the problems:
    - missing required fields;
    - invalid answers;
    - a secret given as a value instead of an `env:` reference;
    - pytemplate-uv's own errors, from `api.plan`;
    - when ready, the exact files.
  - The client asks the person, and fills the spec until `check_spec` is ready.
- **`quant-factory new [--template T] [--profile P] [-o spec.toml]`** runs the same loop in
  the terminal:
  - it asks for the profile, then the components, then the questions, then the settings;
  - it skips a question whose simple `when` is false, but `check_spec` is still the final
    word;
  - it writes the spec file, then offers to run `quant-factory create`.
- **Creation in this phase.** The person converses in Claude and confirms in the terminal.
  `create_app` needs an approval, and MCP clients cannot approve yet (decision 0005).
  - Claude produces or checks the spec.
  - The person runs one command: `quant-factory create spec.toml`.
  - An MCP approval path is open question O-13.
- **The factory settings file** is `~/.quant-agent-os/factory.toml`, and only App Factory
  reads it (decision 0008):
  - **`[identity]`:** author, email, and license. Missing values fall back to `git config`.
  - **`[connections.<name>]`:** a database's kind, host, port, database, user, schema,
    `read_only`, and `url_env` or `password_env`. These hold environment-variable names,
    never values.
  - **`[templates.<name>]`:** registered user packs (Q4).
  - Specs can say `connection = "research-pg"` instead of repeating the details.
  - Values that look like secrets are refused.
  - It is written by `quant-factory settings set|remove`, or by editing the file by hand.
- **Accept:**
  - `check_spec` reports each kind of problem, tested on the `agent-tool`, `fastapi`, and
    `quant-baseline` profiles.
  - `quant-factory new` is tested with scripted input, and writes a spec that `create`
    accepts.
  - A secret-looking value in a spec or the settings file is refused.
  - Identity falls back to git config.

### Q3. Database guidance (B-25), covering point 5

- **The `data-database` component** in `quant-baseline`:
  - questions: `db_kind` (`postgres`, `sqlite`, or `duckdb`) and `db_read_only` (default
    `true`), both guarded by `when` on the component;
  - settings: `DATABASE_URL`, which is secret, so it takes a reference.
- **Guidance:**
  - `quant-factory new` asks for the database kind, then whether to use a saved connection
    or create one;
  - for a new one it asks for the host, database, user, and schema, then the environment
    variable that holds the password or URL;
  - it offers to save the connection to `factory.toml`.
  - The questions are the same through `describe_template`.
- **The generated app gets:**
  - a `settings.py` (pydantic-settings) that reads `DATABASE_URL`;
  - a `.env.example` that names the variable and holds no value;
  - a read-only SQLAlchemy loader, with the driver chosen by `db_kind`;
  - a `check-connection` command.
- **The optional connection check.** `quant-factory create --check-connection` runs
  `uv run <app> check-connection` after the other checks, and passes only the referenced
  variable through `run_command`'s `extra_env`.
  - It is part of the operator's approved create, and is shown in the confirmation.
  - The resolved value never appears in the report, the run store, or logs.
- **Claude Desktop.** A server Desktop starts does not see the shell's environment. Running
  `create` in the terminal avoids the problem. The docs say so.
- **Accept:**
  - The branch is tested.
  - A scan test finds no secret values in the generated files.
  - In the smoke test, `check-connection` succeeds against a SQLite or DuckDB file.

### Q4. Template forks, git packs, and the walkthrough (B-26), covering point 2

- **`quant-factory template fork <pack>/<profile> <dest> [--name ID]`** copies a built-in
  profile, its questions, and the components it uses into a new user pack. It renames the
  pack and the profile ids, then validates the result with `open_catalog` and `api.plan`.
  - The user edits the pack: by hand, or with Claude Code, through the pack layout and
    `template check`.
  - That is how templates are made by conversation, with no new engine.
- **`quant-factory template check <dir>`** validates a pack and plans its profiles with
  default answers.
- **`quant-factory template add <dir | gh:org/repo> --ref TAG --name NAME`** registers a pack
  in `factory.toml`. A git pack is cloned once at that ref into
  `~/.quant-agent-os/packs/NAME/TAG/`, so later plans are offline and the tools stay
  read-only.
- **Handoff:** if pytemplate-uv ships generic pack authoring, `fork` hands the generic layout
  to it (open question O-12).
- **Walkthrough section:**
  1. Save an identity and a connection.
  2. Run `quant-factory new` for `alpha-research` with `data-database`, then `create`.
  3. Run `backtest`, then `report`, on its output.
  4. Fork `backtest` and add one question.
- **Accept:**
  - A forked pack checks, renders, and passes its smoke checks.
  - A git pack (a local bare repository in tests) registers and plans offline.
  - The walkthrough is reproduced from a clean checkout.

## Gate

After Q1 and Q2, put the walkthrough in front of two or three design partners before starting
Q3 and Q4. Their use decides whether database guidance and forking come next, or Quant
Research's gaps do (a semantic embedder, typed relations).

Measure:
- **Generated apps:** the share that pass their checks, by profile and component mix.
- **Guidance:** the number of `check_spec` rounds to reach ready.
- **Bias tests:** whether every template's bias tests pass.

## Deliberately not built

- **A draft store or next-question engine.** The spec file and `check_spec` replace them.
- **A hub in Core.** `factory.toml` moves to Core only when a second product needs it.
- **Keychain secret backends.** `env:` comes first.
- **Approvals through MCP clients** (O-13).
- **Model-drafted specs** (B-16). These later fill the same spec file, which `check_spec`
  checks and a person confirms.
- **Code generation** (B-20), trading, orders, and broker connectivity.
- **Any change to pytemplate-uv.**
