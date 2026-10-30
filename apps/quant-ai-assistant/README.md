# Quant AI Assistant (superseded)

This placeholder planned a chat-style flagship application. [Decision
0003](../../docs/decisions/0003-brand-and-product-structure.md) supersedes it: Quant-Agent-OS is
a platform that external AI clients (Claude, Copilot, Cursor) consume through MCP and the Python
SDK, so it does not ship its own chat app.

The workflow this app described is now a platform workflow that clients call: validated,
bounded plans; deterministic analytics; research retrieval with citations; and an
evidence-linked report. `examples/portfolio-research` demonstrates it, and the MVP in
[docs/ENGINEERING_REVIEW_PLAN.md](../../docs/ENGINEERING_REVIEW_PLAN.md) exposes it over MCP.

This directory can be removed once nothing links to it.
