# 0021: Per-caller access to tools and datasets

- Status: accepted (design approved by the owner, 2026-11-06)
- Date: 2026-11-06

## Context

Any key with `tools:call` reached every tool and dataset on its server. The tenant was the only boundary, and the
caller stopped at the recorder: the executor, the tools, and the stores never saw it. A firm needs to say which
person may use which tool and read which data, and needs to connect that to its own entitlement system.

## Decision

- **An `AccessPolicy` port in Core:** `allows(caller, action, resource)`, with actions `tool.call` and `dataset.read`.
  `FileAccessPolicy` is the built-in one, from a TOML file of groups; a firm can implement its own.
- **No policy, no change.** A server without one behaves exactly as before, so an upgrade breaks nothing.
- **Deny what is not listed,** once a policy exists.
- **Fail closed.** No bound caller, a policy that raises or does not return `True`, or a file that becomes missing or
  invalid is a refusal. A server will not start on a file that is already bad.
- **The caller is bound once per request,** by REST and HTTP MCP, from the verified credential, and read through
  a context variable that the executor copies onto the tool's thread. Passing it as an argument would have meant
  threading it through the recorder, approvals, and nested executors, each one a place to forget it. Forgetting here
  gives no caller, which is a refusal.
- **Tools are checked in the executor, and datasets where they are read,** in `GuardedDatasetStore`, because references
  sit inside tool arguments and nested calls such as `research_report`.
- **A caller sees only what it may reach:** in tool lists, resources, `list_datasets`, `server_info`, and error messages.
- **A refused dataset answers as a missing one.** A refused tool answers `denied`, because a client needs a clear
  refusal, so a caller can tell that a tool name it guessed exists.
- **Refusals go on the call's record** (`refused`) and its audit event, including those inside a nested call.
  Interfaces do not return them to callers, so an auditor can see what a caller cannot.

## Alternatives considered

- **Per-document access to the corpus.** Keyword, vector, and graph search would each need a filter, and a missed one is
  a leak: the reasoning of [decision 0013](0013-identity-and-tenancy.md). A firm that needs it runs a server per corpus.
- **Using tenants for desks.** Tenants isolate stores, not rights over shared data, and they sit behind `qaos hosted`.
- **A `caller=` argument on `ToolExecutor.execute`.** See above.

## Consequences

- A firm's tools must read through a `GuardedDatasetStore`. Nothing can enforce that in the firm's own code.
- `runs:read` still reads every caller's runs, so it is for auditors.
- Prompts and server instructions go to every caller.
- `qaos hosted serve` does not read an access file, and OAuth tokens carry no groups yet. That comes with single
  sign-on, if a firm needs it.
- stdio and the SDK are unchanged, and `serve_stdio` refuses a catalog with an access policy.
