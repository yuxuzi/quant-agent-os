# Team server: the research tools over REST, in Docker

This guide runs the Quant Research tools as a small team server. Each teammate, or each
application, calls them over REST with an API key. Every call is recorded with the caller's
name, and nobody but the operator can change the corpus.

What runs:
- **One container** runs `quant-research serve-api`
  ([decision 0011](decisions/0011-rest-api-and-api-keys.md)).
- **Three volumes** hold the data. `knowledge` holds the SQLite knowledge store, `keys` holds
  the API-key hashes, and `runs` holds the run store and audit trail.
- **The image holds code only.** It contains no corpus, keys, runs, or secrets.

PostgreSQL arrives with the PostgreSQL knowledge store (B-14). SQLite is enough here, because
serving only reads the store. The corpus changes only when the operator runs `ingest`.

Everything below runs from the repository root. You need Docker with Compose v2.

## 1. Build

```bash
docker compose -f deploy/compose.yaml build
```

The build installs the locked dependencies (`uv sync --locked`) for Quant Research with the
`api` and `pdf` extras, and nothing else: no dev tools, App Factory, or analytics. It uses a
pinned Python 3.13 base image and a pinned uv, both by digest.

## 2. Ingest your corpus (operator)

```bash
CORPUS=/path/to/notes-and-papers docker compose -f deploy/compose.yaml run --rm ingest
```

`CORPUS` is mounted read-only. Without `CORPUS`, the sample corpus is ingested. Ingestion runs
with no network, so hosted embedders are not available here. Run it again whenever the corpus
changes: unchanged files are skipped by content hash, and the server sees the new snapshot on
its next request.

## 3. Issue API keys (operator)

```bash
docker compose -f deploy/compose.yaml run --rm -T keys new --principal alice \
  --scope tools:list --scope tools:call
```

- **The key is printed once.** Give it to Alice over a channel you trust. Only its SHA-256
  hash is stored, in the `keys` volume.
- **Scopes:**
  - `tools:list` and `tools:call` list and call the tools;
  - `runs:read` reads every recorded run, including retrieved passages, so grant it like
    access to the corpus.
- **Revoking:** remove the key's entry, or add `disabled = true` under it, in
  `/keys/api-keys.toml`, then restart the server.
- **New keys** are read when the server starts, so restart it after issuing one:
  `docker compose -f deploy/compose.yaml restart server`.

## 4. Start the server

```bash
docker compose -f deploy/compose.yaml up -d server
curl http://127.0.0.1:8000/healthz      # {"status":"ok"}
```

The container runs as an unprivileged user (uid 10001):
- the root filesystem is read-only, all capabilities are dropped, and privilege escalation is
  blocked;
- the knowledge store and keys are mounted read-only, and only the run store is writable;
- the port is published on `127.0.0.1` only. The server's warning about binding `0.0.0.0`
  refers to its address inside the container.

Call a tool:

```bash
curl -s -H "Authorization: Bearer $KEY" -X POST \
  http://127.0.0.1:8000/v1/tools/find_related_signals/call \
  -H 'content-type: application/json' -d '{"arguments": {"query": "short-term reversal"}}'
```

The routes, status codes, and scopes are in [decision 0011](decisions/0011-rest-api-and-api-keys.md).
REST results equal MCP results for the same arguments.

## 5. See who called what (operator)

```bash
docker compose -f deploy/compose.yaml run --rm runs            # recent runs
docker compose -f deploy/compose.yaml run --rm runs <run-id>   # one run, its calls, its audit
```

REST runs show actor `rest` and the key's principal. Audit events are append-only.

## 6. Expose it to your team

Keep the published port on loopback, and put a reverse proxy in front. The proxy should:
- terminate TLS;
- apply rate limits;
- forward the `Authorization` header unchanged.

The server sends no CORS headers, so browsers on other origins cannot call it directly. Back
up the `knowledge`, `keys`, and `runs` volumes. A lost `knowledge` volume can be rebuilt by
ingesting again; the `runs` volume is your audit trail.

## Permissions

The server is read-only by default. The tools that need more (for example `network`, for a
hosted embedder) are refused unless the operator sets `QUANT_AGENT_OS_GRANTED_PERMISSIONS` on
the `server` service. Consequential permissions also need a per-call approval, which no API
key can give ([security](security.md)).

## Check it yourself

`deploy/smoke-test.sh` is what CI runs on every pull request. It:
- builds the image and checks that it runs as non-root and holds no data, keys, or
  secret-looking environment variables;
- ingests the sample corpus, issues a key, and runs `docker compose up`;
- checks health, a 401 without a key, a real call, the principal on the recorded run, and
  that the server cannot write its knowledge store.

It uses its own compose project and port 18000, and it removes its volumes afterwards.

```bash
deploy/smoke-test.sh
```
