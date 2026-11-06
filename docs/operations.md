# Operations: backups, upgrades, and rollback

This is for the person who runs a [team server](deployment.md) after it is installed. Every command runs
from the repository root and is written for SQLite. For PostgreSQL, use `deploy/compose.postgres.yaml`,
and follow the extra PostgreSQL steps.

## Where things live

| What | Where | If you lose it |
| --- | --- | --- |
| Run store and audit trail | `runs` volume, `runs.db` | **Gone for good.** Nothing else holds the audit trail |
| API-key hashes | `keys` volume, `api-keys.toml` | Issue new keys to everyone |
| Datasets | `datasets` volume | Add them again from your source files, and results show new data versions only if the files changed |
| Knowledge store | `knowledge` volume, `kb.db`, or PostgreSQL's `pgdata` volume | Ingest the corpus again, so keep the corpus |
| Database passwords, `QAOS_IMAGE` | `deploy/.env`, never committed | Keep them in your secret store. New passwords need `ALTER ROLE` on the database |
| Access file, compose overrides, `firm_server.py` | Your own repository | Whatever your repository holds |

The image holds code only, so losing it costs a rebuild. Settings are in [configuration](configuration.md).

**A backup is as sensitive as the corpus.** The run store can hold retrieved passages and computed results,
so encrypt backups and restrict who can read them. API keys are stored only as hashes, but a stolen hash file
still shows who holds which rights.

**Changing a secret:**
- **An API key.** Issue a new one with `keys new`, hand it over, then revoke the old one, as in
  [issue API keys](deployment.md#3-issue-api-keys-operator). The server needs no restart.
- **A database password.** The file `deploy/.env` sets a role's password only when the volume is first
  created. Run `ALTER ROLE` with `psql` in the `postgres` service, update `deploy/.env`, then start the server again.

## Back up

Stop the server first, so the SQLite files are at rest. Backups are written to stdout, and `-T` keeps the
stream intact:

```bash
docker compose -f deploy/compose.yaml stop server
docker compose -f deploy/compose.yaml run --rm -T backup > qaos-backup.tar
docker compose -f deploy/compose.yaml up -d server
```

To save one volume, name it, using `data/knowledge`, `data/datasets`, `data/runs`, or `keys`:

```bash
docker compose -f deploy/compose.yaml run --rm -T backup data/runs > qaos-runs.tar
```

**On PostgreSQL,** the knowledge store is not in the archive, so dump it too, while the `postgres` service runs:

```bash
docker compose -f deploy/compose.postgres.yaml exec -T postgres pg_dump -U postgres -d qaos -Fc > qaos-knowledge.dump
```

Keep `deploy/.env` with the backup, in your secret store. To check a backup, restore it on another machine and
run `doctor`. A backup you have never restored is a hope.

## Restore

`restore` reads a backup from stdin and **replaces** each volume the archive holds, so anything newer in those
volumes is lost. It reads the whole archive first, and an archive it cannot read changes nothing.

```bash
docker compose -f deploy/compose.yaml stop server
docker compose -f deploy/compose.yaml run --rm -T restore < qaos-backup.tar
docker compose -f deploy/compose.yaml up -d server
docker compose -f deploy/compose.yaml run --rm -T doctor
```

An archive of one volume replaces only that volume.

**On PostgreSQL,** with the `postgres` service running, also restore the dump:

```bash
docker compose -f deploy/compose.postgres.yaml exec -T postgres pg_restore -U postgres -d qaos --clean --if-exists --single-transaction < qaos-knowledge.dump
```

If `pgdata` itself was lost, start `postgres` first with the same `deploy/.env`. It creates the roles again, and
the dump holds what they may read.

## Upgrade

1. **Read the [changelog](../CHANGELOG.md)** for every version you cross, and check that this server is healthy:

   ```bash
   docker compose -f deploy/compose.yaml run --rm -T doctor
   ```

2. **Keep the image you run now,** so that you can go back to it without a rebuild:

   ```bash
   docker tag quant-agent-os:local quant-agent-os:before-upgrade
   ```

   If you serve your own tools, keep your own image the same way.

3. **Stop the server and [back up](#back-up).** Do not skip this: a rollback is a restore of this backup.
4. **Build the new version,** from its tag, and start it:

   ```bash
   git fetch --tags
   git checkout vX.Y.Z
   docker compose -f deploy/compose.yaml build
   docker compose -f deploy/compose.yaml up -d server
   ```

   If you serve [your own tools](deployment.md#8-add-your-own-tools), rebuild your image on the new one now, and run
   your tool's tests against it.
5. **Check it.** `doctor` must pass, the old runs must still be listed, and a question you know the answer to must
   still get it:

   ```bash
   docker compose -f deploy/compose.yaml run --rm -T doctor
   docker compose -f deploy/compose.yaml run --rm runs
   ```

Each store upgrades itself or tells you what to do:
- **The run store** upgrades in place when the server first opens it.
- **The knowledge store** does not, because the server may only read it. If the new version needs a newer schema,
  the server does not start until you run `ingest` once, with the same `CORPUS`:

  ```bash
  CORPUS=/path/to/notes-and-papers docker compose -f deploy/compose.yaml run --rm ingest
  ```

**From v0.2.0** no store changes: the knowledge store stays at schema 3, the run store at 5, and PostgreSQL at 1.

To rehearse all of this first, including the rollback, on a machine with Docker, run `deploy/upgrade-test.sh` or
`deploy/upgrade-test.sh postgres`. It starts from a real tagged release, and cleans up after itself.

## Roll back

**A rollback is a restore of the backup you took before the upgrade.** Do not point the old version at volumes the
new one has used: an older version refuses a store with a newer schema, and v0.2.0 does not refuse when it ingests,
so it can damage one.

A restore also drops what happened since the upgrade, including the audit trail of those calls. Keep that first, by
taking a backup under another name.

```bash
docker compose -f deploy/compose.yaml stop server
docker compose -f deploy/compose.yaml run --rm -T backup > qaos-after-upgrade.tar
docker compose -f deploy/compose.yaml run --rm -T restore < qaos-backup.tar
echo 'QAOS_IMAGE=quant-agent-os:before-upgrade' >> deploy/.env
docker compose -f deploy/compose.yaml up -d server
docker compose -f deploy/compose.yaml run --rm -T doctor
```

On PostgreSQL, also restore the dump, as in [restore](#restore). Remove the `QAOS_IMAGE` line when you upgrade again.

## Check it against agreed cases

Write the cases you and the people accepting this server agree on, in a TOML file. Take expected numbers from your own
calculation, not from these tools. [The demonstration's cases](../examples/portfolio-intelligence/acceptance.toml)
are a complete example:

```toml
[[numeric]]
name = "growth book volatility"
tool = "analyze_performance"
arguments = { returns = { dataset = "growth-book", column = "book" } }
value = "metrics.annualized_volatility"   # where in the tool's output
expected = 0.17029039110268307
tolerance = 1e-9                      # absolute, and required

[[retrieval]]
name = "momentum crash risk is in the research"
question = "When do momentum strategies crash?"
source = "momentum-crash-risk.md"     # must be among the top k passages (k = 5 unless you set it)

[[retrieval]]
name = "the research says nothing on municipal bonds"
question = "What is the duration of our municipal bond holdings?"
abstain = true                        # must find no qualifying passage
```

```bash
GOLD=/path/to/cases.toml docker compose -f deploy/compose.yaml run --rm -T eval > acceptance.md
GOLD=/path/to/cases.toml docker compose -f deploy/compose.yaml run --rm -T eval --format json > acceptance.json
```

It runs the tools a client would get, under the same permissions, and changes nothing. The report gives each case's
verdict, what it expected and got, the platform version, the SHA-256 of the cases file, and the data versions, so
it can be traced later. It names sources and never quotes retrieved text. The command exits 0 when every case passes, 1
when any fails, and 2 when the cases cannot be read, so scripts can use it. Without a compose file it is
`qaos eval --gold cases.toml`. It runs the platform's own tools: to include your tools, call `run_acceptance` with your
catalog from your own server code.

## Retention and export

**Runs are kept until you remove them.** Nothing removes them on its own, and they can hold retrieved passages and
computed results, so decide how long you keep them.

```bash
docker compose -f deploy/compose.yaml run --rm runs prune --older-than 90d
```

- **What goes:** ended runs started before then, with their calls, arguments, outputs, and retrieved text. The freed
  space in the file is overwritten.
- **What stays:** audit events and usage events, which hold ids, hashes, and sizes rather than content. So you can
  still show who called what and when, not what came back. The removal itself adds a `runs.pruned` audit event.
- **What it cannot reach:** backups you already took, and what you already exported. Stop the server first if you need
  the removed text gone at once, or the write-ahead log can hold it until the next checkpoint, and the command warns you.
- **Scripts** add `--yes` and `-T`. It asks first otherwise.

To send the audit trail to your own log system:

```bash
docker compose -f deploy/compose.yaml run --rm -T runs export --from 2026-11-01 --to 2026-12-01 > audit.jsonl
```

Each line is one call or one audit event. A call has its time, run and call ids, tenant, actor, principal, platform
version, tool and tool version, input hash, data versions, status, duration, approval, and anything an access policy
refused. It leaves out arguments, outputs, and internal error text, so your log system learns no content. Export before
you prune: a pruned call is not exported, though its audit events are.

## Install with no network

Build on a machine with network, at the release you want, and carry the images across:

```bash
git checkout vX.Y.Z
docker compose -f deploy/compose.yaml build
docker tag quant-agent-os:local quant-agent-os:X.Y.Z
docker save quant-agent-os:X.Y.Z | gzip > quant-agent-os-X.Y.Z.tar.gz
git archive vX.Y.Z | gzip > quant-agent-os-source-X.Y.Z.tar.gz
```

For PostgreSQL, carry its image too. The compose file pins it by digest, and `docker load` can drop the digest, so
give it a local name:

```bash
postgres_image="$(grep -o 'pgvector/pgvector:[^}]*' deploy/compose.postgres.yaml)"
docker pull "$postgres_image"
docker tag "$postgres_image" qaos-postgres:pg17
docker save qaos-postgres:pg17 | gzip > qaos-postgres-pg17.tar.gz
```

On the server, check each file against a checksum you took on the first machine, then load the images and unpack
the source. Name the images in `deploy/.env`, so that nothing tries to build or pull them:

```bash
docker load < quant-agent-os-X.Y.Z.tar.gz
tar -xzf quant-agent-os-source-X.Y.Z.tar.gz
echo 'QAOS_IMAGE=quant-agent-os:X.Y.Z' >> deploy/.env
docker compose -f deploy/compose.yaml run --rm -T doctor
```

For PostgreSQL, also load its archive, and add `QAOS_POSTGRES_IMAGE=qaos-postgres:pg17` to `deploy/.env`. The rest of
[the guide](deployment.md) applies, without its build step. To upgrade, carry a new image the same way, and follow
[upgrade](#upgrade), naming the new image in `deploy/.env` in place of its build step. Your old image is the one to
go back to.

| Step | Needs network? |
| --- | --- |
| Building the image: base images, then packages locked in `uv.lock` | **Yes,** on the build machine |
| Pulling the PostgreSQL image | **Yes,** on the build machine |
| Ingesting, adding datasets, issuing keys, backups, `doctor` | No. These services run with no network, or on PostgreSQL's internal one |
| Serving keyword search and analytics | No outgoing connections. Clients reach the published port |
| A hosted embedder (`--embedder voyage` or `openai`) | **Yes,** and it needs the `network` permission. Leave it off |
| A local embedder (`--embedder local`) | Not in this image. It is an optional extra, and downloads its model on first use |

## When a store refuses to open

Start with what the server said, and with `doctor`, which opens each store without changing it and holds no
secrets, so you can paste its output in a support ticket:

```bash
docker compose -f deploy/compose.yaml logs server
docker compose -f deploy/compose.yaml run --rm -T doctor
```

| It says | What happened | What to do |
| --- | --- | --- |
| The knowledge store has a schema version, and "Run qaos ingest on it once" | The store is older than this version | Run `ingest` once with the same `CORPUS` |
| The knowledge store is "newer than" this version | The image is older than the store, usually after a partial rollback | **Do not ingest.** Run the newer image, or restore the backup |
| The run store has a newer "run-store schema" | The same, for the audit trail | Run the newer image, or restore the backup. The message also offers a new runs file, which leaves a gap in the audit trail |
| "No knowledge store" | The volume is empty or missing | Ingest, or restore. Check the compose project name: volumes belong to a project |
| "Cannot open the run store" | The file is damaged | Restore the backup |
| "no keyword index" | The Python running the server has no SQLite FTS5, which does not happen in this image | Use this image, or see the message |
| The server accepts no key | The keys file is missing or invalid, and the log says why | Fix the file or restore `keys`. Nothing is accepted until then, on purpose |
| Nothing is allowed for anyone | The access file is missing or invalid | Fix the file. Nothing is allowed until then, on purpose |
