# quant-agent-os-factory

**Quant App Factory** is the application-engineering product of Quant-Agent-OS. It turns a
typed application spec into a scaffolded, checked, packaged Python project. It uses
[`pytemplate-uv`](https://pypi.org/project/pytemplate-uv/) through a replaceable
`ScaffoldProvider` adapter and never imports it.

Status: package skeleton only. The foundation (`AppSpec`, `ArchitecturePlanner`,
`ScaffoldProvider`, the `agent-tool` pack, and the `plan_app` and `create_app` tools) is
backlog item B-10. It waits on the approval gate in decision 0005 (B-09). See
[docs/architecture.md §8–9](../../docs/architecture.md).
