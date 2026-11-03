---
name: quant-app-factory
description: Guide the user to a ready Quant-Agent-OS App Factory spec (quant research projects, agent tools) that they create locally, through the Quant-Agent-OS MCP server. Use when the user wants to generate a Python project, a backtest or research project, or a tool package from a template.
license: MIT
compatibility: Needs the Quant-Agent-OS MCP server (quant-platform) with the factory toolset; creating the project needs the quant-factory CLI on the user's machine.
metadata:
  min-server-version: "0.1.0"
---

# Drafting an App Factory spec

The server checks specs; the user creates the project on their own machine. Nothing here
writes files, runs code, or touches the network.

## Workflow

1. **Learn the template.** Call `describe_template` with the `template` (and a `profile`, if
   it has several), or read `qaos://template/{template}` or
   `qaos://template/{template}/{profile}`. It lists the profiles, the optional components,
   and the questions with their help, choices, defaults, and conditions.
2. **Ask only what applies.** Skip questions whose conditions are not met, and offer the
   defaults. Do not ask for anything the template does not need.
3. **Check until ready.** Send the draft as `spec` to `check_spec`, fix every problem it
   reports, and repeat until it says ready. It returns the exact files and the spec as TOML.
4. **Optionally preview.** `plan_app` shows the profile, components, reasons, dependencies,
   checks, and files. It writes nothing.
5. **Hand over.** Give the user the TOML to save, and tell them to run
   `quant-factory create FILE` themselves. Creating a project writes files and runs its checks,
   so it needs their confirmation on their machine; never claim you created it.

## Rules

- **Secrets are references.** Use `env:NAME` (for example `env:DATABASE_PASSWORD`). A value
  that looks like a password or key is refused by `check_spec`, and you must never put one
  in a spec.
- **The template decides.** Use the template's names for profiles and components; do not
  invent options.
- **Follow the errors.** Failures carry `hint` and `retryable`; on `denied` or
  `approval_required`, tell the user and do not retry.

## Server version

Read `qaos://server/info` (`server_info`) once per session. If its `platform_major` is not 0,
warn the user that this skill may be out of date. Use a deprecated tool's named replacement.

## Related

The `draft_app_spec` prompt runs this workflow in clients without skills. A strategy
specification from the `quant-strategy` skill is a good source of answers for a quant
research project.
