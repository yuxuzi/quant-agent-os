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
