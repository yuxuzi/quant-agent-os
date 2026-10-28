# Quant AI Assistant (planned)

The flagship, user-facing application of QuantFoundry, built on QuantAgentOS. Its MVP workflow:

1. The user supplies permitted portfolio data, a benchmark, and research documents.
2. The orchestrator validates the request and creates a bounded plan.
3. Registered tools run deterministic analytics and research retrieval.
4. Outputs and evidence are validated.
5. The assistant presents a structured report with metrics, citations, assumptions, warnings,
   and provenance.

The application will become a uv workspace member here once milestone Q6 needs a user
interface. Until then, `examples/portfolio-research` demonstrates the workflow end to end.
