# 0003: Quant-Agent-OS is the brand and platform, with two products

- Status: proposed
- Date: 2026-10-30
- Supersedes: the QuantFoundry umbrella and the Quant AI Assistant app in the earlier plan

## Context

The earlier plan placed QuantAgentOS under a "QuantFoundry" umbrella. It also planned a
flagship chat-style application, "Quant AI Assistant", and split research retrieval
("Quant Search") into a separate package. The new product baseline makes three changes:
- Quant-Agent-OS is the primary brand and platform.
- It is consumed by external AI clients rather than being a chatbot.
- It has two products: **Quant Research** (Graph RAG) and **Quant App Factory**.

`pytemplate-uv` 3.0 is published and stays a standalone tool.

## Decision

- Quant-Agent-OS is the brand, product, and platform. QuantFoundry and Quant AI Assistant are
  retired as names. The assistant's portfolio-research workflow continues as a platform
  workflow that external clients call.
- Quant Research absorbs the planned Quant Search package.
- Quant analytics stays a separate, LLM-free package in its own repository.
- Repository strategy: one repository and one uv workspace. Core stays at the repository root,
  unchanged. `packages/quant-research` and `packages/quant-app-factory` become workspace
  members when their first code lands. Each member has its own `pyproject.toml`, tests,
  version, and changelog section.
- Dependencies point one way. Core imports no product, and products do not import each other.
  A test enforces this.
- `pytemplate-uv` is used only through a `ScaffoldProvider` adapter, and this repository holds
  no plans for it ([AD-8](../ENGINEERING_REVIEW_PLAN.md#architecture-decisions)).
  - *Superseded in part by [decision 0006](0006-pytemplate-uv-as-a-library.md):* App Factory
    now imports `pytemplate.api` as a library, in that adapter only, instead of running the
    pinned CLI.

## Consequences

- Users can install Core alone, or Core plus one product.
- A single repository keeps coordinated changes to the tool contract reviewable in one pull
  request. Separate distributions keep releases independent.
- Moving to separate repositories later stays possible, because packages share only Core's
  public API.
- The names still need availability and trademark checks before any public launch or
  commercial use.
