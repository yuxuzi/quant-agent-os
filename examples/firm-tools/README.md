# A firm's own tools on the server

This example serves a firm's own Python tool and data beside the platform's toolsets:

- `firm_tools.py`: `desk_var`, an ordinary tool with typed input and output.
- `firm_data.py`: a `DatasetStore` over the firm's database, so one data source needs no connector.
- `firm_server.py`: the firm's server, which names what it serves and calls `serve_http`.
- `Dockerfile`: the platform's image plus the firm's code.

All the data is fictional. Run it after issuing a key:

```bash
uv run qaos keys new --principal alice --scope tools:list --scope tools:call
FIRM_DB=/tmp/firm.db uv run --extra analytics --extra server python examples/firm-tools/firm_server.py
```

Then call the tool, which goes through the same checks and recording as any other:

```bash
curl -s -H "Authorization: Bearer $KEY" -H 'content-type: application/json' -X POST \
  http://127.0.0.1:8000/v1/tools/desk_var/call \
  -d '{"arguments": {"dataset": "desks", "column": "growth"}}'
```

`check_tool` is the test to put in your tool package; see
`tests/integration/test_firm_tools_example.py`. The guide is in
[deployment](../../docs/deployment.md#8-add-your-own-tools), and why it works this way is in
[decision 0020](../../docs/decisions/0020-a-firms-own-server.md).
