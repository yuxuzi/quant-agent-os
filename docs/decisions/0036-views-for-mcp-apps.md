# 0036: Serve views for MCP Apps beside the tools that render them

- Status: proposed for 1.0; extends [0034](0034-mcp-2026-07-28.md) and [0002](0002-mcp-boundary.md)
- Date: 2026-11-24

## Context

MCP Apps (the `io.modelcontextprotocol/ui` extension, spec 2026-01-26) lets a tool name a `ui://` page in
`_meta.ui.resourceUri`. A client that shows MCP Apps fetches the page, renders it in a sandboxed iframe, and
pushes the tool's result into it. A client without the extension ignores the metadata and shows the tool's
text. Decision 0034 kept the SDK 2 server and listed MCP Apps as a later addition. For 1.0, a fund's
performance analysis should appear in the chat as a page, and every other client should see what it sees now.

## Decision

- **A view is a static page, not a computation.** Core gains `ViewSpec`: a `ui://` URI, the tools it renders,
  and its HTML. The adapter lists the page and returns it on `resources/read`. Reading a view calls no tool
  and records no run, and the tool itself still runs through the executor.
- **A view is served with its tools.** It is listed only while one of its tools is served to the caller, as a
  resource is (decision 0021). Each served tool it names carries `_meta.ui.resourceUri`, merged with the
  tool's other `_meta` keys. Results are unchanged.
- **The extension is advertised when a view is served.** The SDK sends `io.modelcontextprotocol/ui` on the
  2026-07-28 discovery result. Its 2025-11-25 `initialize` result has no `extensions` field, so the SDK does
  not send it there. A client on that handshake still gets the page and the link.
- **The first view is Quant Analytics' performance page.** It is one HTML file, shipped as package data and
  served with `analyze_performance` wherever the analytics toolset is composed. Its script draws the
  structured result, with no network and no external resource, and sets text with `textContent`. Its number
  formats follow Quant Toolbox, and a test holds its lists of percentages and counts to the toolbox's kinds.

## Consequences

- The link costs 61 bytes on the one tool it names. The agent-efficiency measure does not move: it counts
  name, description, and input schema for the model, and title, output schema, and annotations for the
  client, not `_meta`, so its recorded numbers are unchanged.
- Hosts that preload `ui://` resources will fetch a static page. Nothing about the page is per caller.
- The page has been tested by protocol tests and by rendering it in headless Chrome against a fake host. No
  real client has shown it yet, so the first look in one is still to do.
- The 2025-11-25 handshake cannot carry the extension flag through this SDK. Clients that rely on the flag
  rather than the link will not see the page on that handshake.
- Another tool gets a view the same way: its own page, its own tests, and its own check of formats. This is
  not a general UI framework, and views do not run code on the server.
- Revisit when the MCP Apps extension names a version after 2026-01-26, or when the SDK sends extensions
  on the 2025-11-25 handshake.
