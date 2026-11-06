# The Portfolio Intelligence demonstration

A guide to showing the platform to someone in about ten minutes, with a real AI client. Everything in
it is [fictional](../examples/portfolio-intelligence/README.md): one firm, two desks, their data and
research notes, and their access rules.

The audience should leave having seen four things: answers come from the firm's own tools and data,
each person reaches only what the firm allows, the system says when it doesn't know, and every call
can be audited later.

## Try it without a client

This runs the same story with no model, so it is also the fallback if a client or network fails on the
day. Each step prints what the tools returned:

```bash
uv run python examples/portfolio-intelligence/demo.py
```

## Set up beforehand

You need [uv](https://docs.astral.sh/uv/), a checkout of this repository, and
[Claude Code](https://code.claude.com/docs). Any MCP client that sends a bearer header over HTTP works,
but the steps below use Claude Code. Start the server, keeping its state so the keys last between
rehearsals:

```bash
uv run python examples/portfolio-intelligence/demo.py --serve --home ~/qaos-demo
```

The first start prints the connection command and three keys, and later starts don't print them again,
so copy them now. These keys reach only this demonstration. To begin again, stop the server and delete
that directory. In a second terminal, run the `claude mcp add` command it printed, which connects you
as the growth desk's portfolio manager, then start `claude` and allow the `qaos-demo` tools when asked.

Rehearse it and time yourself. The timings below are a target and have not been measured with a real
client.

## The script

| Time | What you ask or do | What the audience sees | Where it comes from |
| --- | --- | --- | --- |
| 0:00 | Introduce it: one firm, two desks, and an AI client connected as the growth desk's portfolio manager | | |
| 1:00 | *What can you reach on this server?* | The growth desk's tools and four datasets, and none of the value desk's | `tools/list` and `list_datasets` |
| 2:00 | *What is the growth book's 95% VaR by our convention, and what are its factor exposures?* | The firm's own `book_var`, then `analyze_risk`: VaR 7.72%, volatility 17.03%, momentum exposure +0.51 | Tools. The client sends a book's name, its weights, and dataset ids, and no return passes through it |
| 4:00 | *Does our research say anything about that momentum exposure? Cite it.* | Passages from `momentum-crash-risk.md` with line numbers, explained | Passages from `find_related_signals`. The explanation is the model's |
| 5:00 | *Give me a report on momentum, with the growth book's performance against its benchmark.* | One `research_report` call: a cited specification and metrics, including a Sharpe ratio of -0.05 | The tool, in one deterministic call. Any summary around it is the model's |
| 6:30 | *Now show me the value book's performance.* | The client finds no such dataset | The tool's refusal, which looks like a missing dataset |
| 7:30 | *What is the duration of our municipal bond holdings?* | No passages, and the client should say the research doesn't cover it | `find_related_signals` returns nothing. The wording is the model's |
| 8:30 | Show the audit trail, below | Each call under its caller's name, including the refused read | The run store, over REST |

To show the trail, use the auditor's key, which can read runs and call no tool:

```bash
curl -s -H "Authorization: Bearer $AUDITOR_KEY" "http://127.0.0.1:8000/v1/runs?limit=3" | python3 -m json.tool
curl -s -H "Authorization: Bearer $AUDITOR_KEY" "http://127.0.0.1:8000/v1/runs/$RUN_ID" | python3 -m json.tool
```

Find the refused read in the second command's output, under `calls`: its `refused` field says
`dataset.read value-book`, and the portfolio manager never sees that field. The call also records the
tool's version, a hash of its input, its status, and the versions of the data it used.

## What to say about the model

Be exact about this, because a careful audience will ask:

- **Tools produce** every figure, citation, refusal, and audit record, and these are the same on every
  run. `tests/integration/test_portfolio_demo.py` checks them, with the risk figures compared to a
  separate numpy calculation.
- **The client's model produces** the choice of tool and all the wording around the results. It can
  vary between runs and can be wrong. It could still answer the municipal bond question from general
  knowledge after the search returns nothing, so the guarantee is that the tool finds no evidence, not
  that a model never speculates. Showing this if it happens is more convincing than hiding it.
- **The platform calls no model.** The firm chooses its client and the model behind it.

## What it doesn't show

Keep claims to what the demonstration shows. If asked:

- **Fictional data, small corpus.** Three short notes prove the mechanism, not search quality. The
  [measured results on real papers](products/quant-research.md#how-well-it-works) are the ones to cite.
- **Scale and production.** This is one process on a laptop, with API keys over loopback. The
  [deployment guide](deployment.md) covers a real server.
- **Limits of per-caller access.** The research corpus is all or nothing per caller, and a firm's own
  tools must read through the guard. [Per-caller access](security.md#per-caller-access) lists them all.
- **Advice.** The figures and citations are evidence for a person's decision, not investment advice.

## Clean up

Stop the server, then run:

```bash
claude mcp remove qaos-demo
rm -rf ~/qaos-demo
```
