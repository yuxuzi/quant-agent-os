# Portfolio Intelligence

One fictional firm with two desks, served over real HTTP and MCP. It brings together everything a
firm would deploy: its own tool, its own data, its research notes, per-caller access rules, and an
audit trail.

```bash
uv run python examples/portfolio-intelligence/demo.py
```

It needs no network, model, or account, and it starts from an empty temporary directory each time. To
present it with a real AI client, follow [the demonstration guide](../../docs/demo.md).

| File | What it is |
| --- | --- |
| `demo.py` | The operator's setup, the server, and a client that walks through eight steps |
| `book_tools.py` | `book_var`, the firm's own tool |
| `access.toml` | Who reaches which tools and datasets |
| `data/` | Weights and asset returns for two books, each book's returns, and shared factor returns |
| `notes/` | Three research notes |

All of it is invented, and none of it describes a real security, firm, or person. Each book's returns
are exactly its weights times its asset returns, and `tests/integration/test_portfolio_demo.py` checks
the demonstration's risk figures against a separate numpy calculation from these files.
