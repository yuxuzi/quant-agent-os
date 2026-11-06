# 0020: A firm serves its own tools from its own server

- Status: accepted (approved by the owner, 2026-11-06)
- Date: 2026-11-06

## Context

A firm's main reason to use the platform is to reach its own Python tools and data from an AI client. The SDK
could call such a tool, but no server could serve one: `Toolset` is a closed list, `compose()` took no tools, and
the serving functions were private to the command line.

## Decision

- **The firm writes a small server in its own repository.** It imports its tools, passes them to
  `compose(extra_tools=...)`, and calls `serve_http`, the same function `qaos serve` calls.
- **Tools join as the toolset `firm`,** through the same executor as the platform's.
- **A served name cannot be replaced.** A clash stops the server at start-up.
- **A catalog carries its policy,** so `serve_http` cannot apply a different policy from the one it was composed under.
- **A firm's data needs no connector.** `compose(datasets=...)` already takes any `DatasetStore`.

## Alternatives considered

- **`qaos serve --tools-module firm_quant.tools`.** It is more convenient, but a setting would choose what code
  runs in the server, against the rule of no dynamic code loading. The security gain from refusing it is modest, since
  someone who controls a deployment's settings often controls more. The firmer reasons are that the tool list stays reviewed in the
  firm's repository and pinned in its image, and the server gains no option to secure.
- **Entry-point plugins.** Rejected for the same reason, and they add discovery.

## Consequences

- A firm maintains about twenty lines and a Dockerfile.
- The skills and the tool lock cover only the platform's tools. A firm tests its own tools with `check_tool`, and
  describes them with `extra_instructions`.
- `research_report` does not call a firm's tools.
