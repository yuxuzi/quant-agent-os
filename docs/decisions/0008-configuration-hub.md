# 0008: A configuration hub in Core, with secrets kept only as references

- Status: proposed
- Date: 2026-10-31

## Context

The same settings recur across apps and products: author details, database connections, data
vendors, template packs, and default frequencies. Typing them into every questionnaire is
slow and error-prone. Storing them anywhere risks storing secrets.

## Decision

- **Location.** `quant_agent_os.config.hub` lives in Core, so Quant Research, App Factory, and
  later products share it.
- **Layered TOML files.** `~/.quant-agent-os/hub.toml` (user) is overridden by
  `./.quant-agent-os/hub.toml` (project), key by key. A team-server backend comes later and
  sits behind the same model.
- **Typed sections:** `identity`, `connections.<name>`, `data_sources.<name>`,
  `templates.<name>`, and `defaults`.
- **References only.** Secret fields hold `env:NAME` (or `keyring:service/name` later).
  - Saving a value that looks like a secret is refused: one that is not a reference, under a
    secret-named key or in a field declared secret.
  - References resolve only inside the process that needs the value, as `SecretStr`. They
    never resolve in tool output, audit events, logs, or generated files.
- **Who writes.**
  - Operators write through `quant-agent-os hub set|remove` and through
    `quant-factory template add`.
  - An agent-initiated write (`save_to_hub`) needs `write_local_data`, so it needs an approval
    per call.
  - The read-only `list_hub_entries` tool returns names, kinds, and non-secret fields.
- **File safety.** Files are written atomically, with mode 0600 on POSIX.

## Consequences

- The questionnaire can offer saved connections and identities, and remember new ones with an
  approval.
- Credentials stay in the environment or a keychain, and the hub can be shown, shared, or
  committed at project level without leaking them.
- A hosted or team hub (B-18, B-19) needs authentication and an audit trail of who changed
  what. That work is deferred.
