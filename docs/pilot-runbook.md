# Pilot runbook: installing the platform at a fund

For a fund's engineer who will install Quant-Agent-OS on the fund's own machine, sign the fund's
people in through its identity provider, and serve its warehouse, documents, and existing Python code
to their AI clients. It follows the 0.8.0 pilot at **Kestrel Point Capital**, a fictional fund in
[examples/pilot-fund](../examples/pilot-fund/README.md), and every step below is one that
[`drill.sh`](../examples/pilot-fund/drill.sh) runs, timed. What the pilot found on the way is in
[part 5 of the plan](plans/proposals-0.8.md#5-what-the-pilot-found).

The general guides still apply: [deployment](deployment.md) for a server in Docker, and
[operations](operations.md) for backups and upgrades there. This runbook is the same work for a fund that
installs the wheel into its own Python project instead of running the image.

## The people and what they reach

Each person is a user in the fund's identity provider, with one group. The groups are what
`access.toml` reads ([decision 0021](decisions/0021-per-caller-access.md)), so the provider's directory,
not the platform, decides who is in which group.

| User | Group | Reaches |
| --- | --- | --- |
| `pm-equities`, `pm-macro`, `pm-commodities` | `desk-eq-ls`, `desk-macro`, `desk-commod` | Market data, signals, the vendor model, and their own desk's positions, weights, and returns; the analysis and research tools; the `kpc_*` legacy tools, with `kpc_desk_pnl` for their own desk only |
| `risk-manager` | `risk` | Every dataset and tool, including the `risk_*` tools of the risk-engine service |
| `researcher` | `research` | Market data, signals, and documents; no positions, weights, or desk returns, and no `kpc_desk_pnl` |
| `operator` | `operators` | `server_info` and `list_datasets`, which list nothing; runs the commands below on the server |

A refused dataset answers exactly as a missing one does, and a refused desk in `kpc_desk_pnl` answers as an
unknown desk. Approving a consequential call needs an approver key, never a token
([decision 0028](decisions/0028-oauth-sign-in-on-a-firms-server.md)).

## 1. Install

Build the platform's two wheels where the source is, then make the fund's own project depend on them. The
server needs the `server` and `analytics` extras; Quant Toolbox is its own wheel.

```bash
uv build --wheel --out-dir dist                           # in the platform's source
uv build --wheel --package quant-toolbox --out-dir dist
uv init --bare --name kestrel-site                       # in the fund's own folder
uv add dist/quant_toolbox-0.6.0-py3-none-any.whl "quant-agent-os[server,analytics] @ dist/quant_agent_os-0.7.0-py3-none-any.whl"
export UV_OFFLINE=1                                      # nothing is fetched after this
```

- **Network.** Only `uv add` needs it, to fetch the dependencies, or none if uv's cache holds them. Every later
  step runs with `UV_OFFLINE=1`, and the server talks only to loopback: the gateway, the risk service, and
  its clients. To install with no network at all, fill a uv cache on a connected machine and carry it across.
- **Commit `uv.lock`.** It pins every dependency, so an upgrade is a reviewed change to one file.

## 2. The home

The home holds the knowledge store, the run store, and API-key hashes. Set `QUANT_AGENT_OS_HOME`, or it is
`~/.quant-agent-os`.

```bash
uv run qaos init
uv run qaos doctor
```

`qaos doctor` checks the home, the stores, the extras, the access file, and the sign-in file and its secret,
without contacting the identity provider. With the fund's settings it warns about `network` being granted and
pre-approved, which the risk service needs (section 6).

## 3. Data in place

The warehouse stays where it is. The fund writes one adapter,
[`warehouse_store.py`](../examples/pilot-fund/site/warehouse_store.py), a `DatasetStore` that pivots the
warehouse's long tables into the wide datasets the tools read by id: `px_returns` and `px_close` (dates by
instruments), `bench_returns`, `adv_usd`, `signals_momentum|carry|value`, `positions_<desk>`,
`weights_<desk>`, `desk_returns_<desk>`, and the vendor model's latest snapshot as `fm_loadings`,
`fm_factor_cov`, and `fm_specific`.

- **Complete to the last row.** Every dated dataset has every business day for every instrument, so no
  instrument looks delisted to `analyze_signal` or `run_backtest`. The first day's returns are missing values,
  never zero.
- **Fractions.** Returns are simple daily total returns as fractions; the vendor model is annual, so a client
  passes `periods_per_year: 1` with it.
- **Fingerprints.** Each dataset goes through the platform's CSV parser, and its fingerprint is that CSV's
  SHA-256, so a result names exactly the data it read and changes when the warehouse does.
- **Read-only.** It opens the warehouse with `mode=ro`, a connection per call.

Nothing is copied into the home, so there is no `qaos data add` step. The server passes the store to
`serve_apps`, and the access file guards every read.

## 4. Documents

```bash
uv run qaos ingest facility/research
```

Ingesting is an operator command, never a tool. Search results cite each passage's file and location, and
passage text is marked untrusted: Kestrel's corpus holds a note with an injected instruction, which the drill
retrieves and nothing acts on. A caller who may search may search the whole corpus; keep documents a group
must not read out of it.

## 5. Legacy code

`qaos legacy` drafts tools from existing Python on the developer's machine
([legacy code as tools](products/quant-app-factory.md#legacy-code-as-tools)). On Kestrel's `kestrel_quant`:

```bash
uv run qaos legacy scan facility/kestrel_quant --write site/legacy.toml
uv run qaos legacy wrap site/legacy.toml --out site/legacy_tools
```

1. **Review the inventory.** The scan found 26 callables and called 20 of them services, because every
   signal reaches the module-level query cache in `db`. Kestrel's developer left out the database helpers and
   the engine's `load` and `refresh`, made the signals and the P&L report facades, and named each tool
   `kpc_*` or `risk_*`: 38 lines of the inventory.
2. **Wrap.** The draft had 13 tools and 110 open decisions. Its contract test passed for 5 tools first time;
   3 signal facades returned whole panels over the 100 KB output limit, and 5 risk calls had placeholder
   example arguments.
3. **Finish it.** Decide every TODO, keep facades thin, and run `pytest test_tools.py` in the draft's folder
   until both tests pass. Kestrel's finished draft differs from the fresh one by 342 added and 312 removed
   lines across six files: types and docstrings, one date's cross-section from each signal facade, desk
   scoping in `kpc_desk_pnl`, the engine's settings, typed service answers, and a test against a throwaway
   warehouse. Commit the fresh draft first, then the edits, so the diff shows what a person decided.
4. **Serve it** from the fund's own server module, beside the platform's toolsets:
   [`kestrel_server.py`](../examples/pilot-fund/site/kestrel_server.py). Stateful code runs as a service on
   loopback, `uvicorn service.app:app --port 8100` in the draft's folder, and its endpoints are tools through
   `FirmApp.call_service`.

A tool that reads the warehouse itself, as `kpc_desk_pnl` and the risk engine do, bypasses the access file's
dataset rules. The facade checks `current_caller()` against the same rules, and the risk tools are granted to
the risk group alone, since a service cannot see who called.

## 6. Sign-in and entitlements

The fund's provider registers an introspection client for the server and issues tokens whose audience is the
server's MCP URL, with a groups claim ([deployment, section 10](deployment.md#10-sign-in-through-your-identity-provider)).
Kestrel's [`sign-in.toml`](../examples/pilot-fund/site/sign-in.toml) names its gateway, with
`tenant_claim_value = "kestrel"` because its tokens name the firm. Its settings are in
[`kestrel.env`](../examples/pilot-fund/site/kestrel.env):

| Setting | Kestrel's value | Why |
| --- | --- | --- |
| `QUANT_AGENT_OS_SIGN_IN_FILE` | `site/sign-in.toml` | Turns sign-in on |
| `KESTREL_INTROSPECTION_SECRET` | the introspection client's secret | Named by the sign-in file; never in it |
| `QUANT_AGENT_OS_ACCESS_FILE` | `facility/access.toml` | Groups decide tools and datasets |
| `QUANT_AGENT_OS_GRANTED_PERMISSIONS` | `["read_local_data", "network"]` | The risk tools call a service |
| `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS` | `["network"]` | Clients cannot approve each call; this applies to every tool that needs `network` |
| `KPC_WAREHOUSE`, `KPC_RISK_SERVICE_URL` | the warehouse file, `http://127.0.0.1:8100` | Read by the store, the legacy code, and the server |

Keys keep working beside tokens, for batch jobs and teams without the provider:

```bash
uv run qaos keys new --principal batch-research --scope tools:list --scope tools:call --group research
```

## 7. Start

Start the risk service, then the server. Both bind loopback; put TLS and rate limits in a reverse proxy before
binding anything else.

```bash
cd site/legacy_tools && uv run uvicorn service.app:app --host 127.0.0.1 --port 8100 &
uv run python site/kestrel_server.py
```

The server prints how many tools it serves and the issuer it trusts. A sign-in file that is invalid, names an
unset secret, or a tenant the server does not serve stops it, with the fix. Clients add
`http://127.0.0.1:8000/mcp` (the public URL, behind the proxy) and sign in; REST callers send the token as a
bearer token to `/v1/tools/<name>/call`.

## 8. Operate

**Agreed cases.** Before people rely on it, agree the numbers with the desks and the risk team and keep them
in [`acceptance.toml`](../examples/pilot-fund/site/acceptance.toml): the planted momentum IC, carry's lack of
one, a desk's P&L from the legacy report, and the documents that must answer, or abstain. Run them as the
operator after every upgrade:

```bash
uv run python site/kestrel_server.py acceptance site/acceptance.toml
```

`qaos eval` does the same for the platform's own tools; the fund's server runs `run_acceptance` with its own
catalog, so its legacy tools and its warehouse are in the cases too.

**Who called what.** Each signed-in call is recorded under the token's subject, each key's under its principal:

```bash
uv run qaos runs show
uv run qaos runs show RUN_ID
uv run qaos runs export > audit.jsonl
```

**Back up** with the server stopped: copy the home's `runs.db` (the only copy of the audit trail),
`api-keys.toml`, and `knowledge.db`, and keep the fund's repository (site code, `uv.lock`, access and sign-in
files). The warehouse is the fund's own and is backed up as it already is.

**Upgrade** by building the new wheels, replacing them with `uv add`, and reviewing the `uv.lock` change. Then
run `qaos doctor`, the agreed cases, and the legacy draft's `test_tools.py` before restarting. To roll back,
restore the previous `uv.lock` and run `uv sync`. Read the [changelog](../CHANGELOG.md) for tool version
changes first; the stores' upgrade rules are in [operations](operations.md#upgrade).

## The drill

```bash
examples/pilot-fund/drill.sh        # from the repository root; KEEP=1 keeps the work folder
```

It runs every step above in a fresh folder with its own `HOME`, works as each user through
[`drill_client.py`](../examples/pilot-fund/drill_client.py), over REST and one MCP call, then runs the agreed
cases and lists the runs. It needs `uv`, `curl`, and free ports 8000, 8100, and 9400, and cleans up its processes
on exit. [`tests/integration/test_pilot_fund.py`](../tests/integration/test_pilot_fund.py) tells the same story
in process, offline, in CI.
