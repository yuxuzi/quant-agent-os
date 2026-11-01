# 0008: A small factory settings file instead of a configuration hub

- Status: proposed
- Date: 2026-10-31

## Context

Users repeat the same settings across apps: author details, database connections, and the
template packs they use. A first design put a layered configuration hub in Core, with five
typed sections, a team backend, and its own tools. That is a product in itself, and mature
tools already cover most of it: `.env` files, direnv, password-manager command-line tools,
and cloud secret managers. Only App Factory needs the settings today.

## Decision

- **One file, owned by App Factory:** `~/.quant-agent-os/factory.toml`.
  - `[identity]` holds author, email, and license. Missing values fall back to `git config`.
  - `[connections.<name>]` holds a database's kind, host, port, database, user, schema, and
    `read_only`, plus `url_env` or `password_env`.
  - `[templates.<name>]` holds registered user packs: source, ref, and cache path.
- **Environment-variable names only.** Secrets are never stored. A field ending in `_env`
  holds the name of an environment variable. A value that looks like a secret is refused.
- **Resolved only where needed.** A name resolves only inside the process that needs the
  value, and is passed to a program only through `run_command`'s `extra_env`, for that name.
- **Who writes it.** `quant-factory settings set|remove`, `quant-factory template add`, and
  people editing the file. No agent tool writes it.
- **Moving to Core** happens only when a second product needs these settings. The file
  format can move as it is.

## Consequences

- Specs can name a connection (`connection = "research-pg"`) instead of repeating it, and
  questionnaires can offer saved connections.
- **Claude Desktop.** A server started by Claude Desktop does not inherit the shell's
  environment, so creation that needs a referenced variable runs from the terminal. The docs
  say so.
- **Team sharing** (shared connections and packs, with access control) stays a later,
  commercial feature (B-18, B-19).
