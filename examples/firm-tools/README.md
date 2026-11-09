# A firm's own tools on the server

This example serves a firm's own Python function and data beside the platform's toolsets:

- `firm_tools.py`: `desk_var`, an ordinary typed function that knows nothing about the platform.
- `firm_data.py`: a `DatasetStore` over the firm's database, so one data source needs no connector.
- `firm_server.py`: the firm's server, which registers `desk_var` with `FirmApp.expose` and calls
  `serve_apps`.
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
  -d '{"arguments": {"returns": {"dataset": "desks", "column": "growth"}}}'
```

The result carries the figure, the convention, the fingerprint of the data it read, and `computed`
evidence. The function's file has no platform code: registering it took two statements.

`check_app` is the test to put in your own CI; see
`tests/integration/test_firm_tools_example.py`. The guide is in
[deployment](../../docs/deployment.md#8-add-your-own-tools), and why it works this way is in
decisions [0020](../../docs/decisions/0020-a-firms-own-server.md) and
[0024](../../docs/decisions/0024-registering-a-firms-functions.md).
