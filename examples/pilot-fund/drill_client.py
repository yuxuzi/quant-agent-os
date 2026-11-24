"""The fund's people at work, as drill.sh runs them: sign in at the gateway, then call the server.

Each user gets a token from the gateway and calls the fund's server over REST (and one over MCP),
as their client would. Every check prints one line; the script exits 1 if any fails.

    python drill_client.py --server http://127.0.0.1:8000 --gateway http://127.0.0.1:9400 \
        --warehouse facility/warehouse/kestrel.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any

FAILURES: list[str] = []


def check(user: str, what: str, passed: bool, detail: str = "") -> None:
    """Print one check's verdict, and remember a failure."""
    print(f"  {'ok  ' if passed else 'FAIL'}  {user:<15} {what}{': ' + detail if detail else ''}")
    if not passed:
        FAILURES.append(f"{user}: {what}")


def http(method: str, url: str, body: Any = None, token: str | None = None) -> tuple[int, Any]:
    """Send a JSON (or form) request; return the status and the decoded body."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if isinstance(body, dict) and body.get("grant_type"):
        data = urllib.parse.urlencode(body).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            return answer.status, json.loads(answer.read() or b"null")
    except urllib.error.HTTPError as error:
        text = error.read()
        try:
            return error.code, {"body": json.loads(text), "headers": dict(error.headers)}
        except ValueError:
            return error.code, {
                "body": text.decode(errors="replace"),
                "headers": dict(error.headers),
            }


class Session:
    """One signed-in user."""

    def __init__(self, server: str, gateway: str, user: str) -> None:
        self.server, self.user = server, user
        status, answer = http(
            "POST",
            f"{gateway}/token",
            {"grant_type": "password", "username": user, "password": f"kpc-{user}"},
        )
        if status != 200:
            raise RuntimeError(f"{user} got no token: {answer}")
        self.token: str = answer["access_token"]

    def tools(self) -> set[str]:
        status, answer = http("GET", f"{self.server}/v1/tools", token=self.token)
        return {item["name"] for item in answer} if status == 200 else set()

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        status, answer = http(
            "POST", f"{self.server}/v1/tools/{tool}/call", {"arguments": arguments}, self.token
        )
        if status != 200:
            return {"status": f"http {status}", "error": str(answer), "output": None}
        result: dict[str, Any] = answer
        return result


def latest_weights(warehouse: Path, desk: str) -> dict[str, float]:
    """A desk's book on the last day, as the fund's order system would hand it to a client."""
    with closing(sqlite3.connect(f"{warehouse.resolve().as_uri()}?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT instrument_id, weight FROM pos_eod WHERE desk = ? AND date = "
            "(SELECT MAX(date) FROM pos_eod)",
            (desk,),
        ).fetchall()
    return {name: weight for name, weight in rows}


def mcp_call(server: str, token: str, tool: str, arguments: dict[str, Any]) -> Any:
    """Call one tool over MCP with a bearer token, as Claude or Cursor would."""
    import anyio
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def main() -> Any:
        async with (
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60) as client,
            streamable_http_client(f"{server}/mcp", http_client=client) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await session.call_tool(tool, arguments)

    return anyio.run(main)


def main(argv: list[str] | None = None) -> int:
    """Run the fund's day against a running server; return 1 if any check fails."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--gateway", default="http://127.0.0.1:9400")
    parser.add_argument("--warehouse", type=Path, required=True)
    args = parser.parse_args(argv)
    server, gateway = args.server, args.gateway
    answers = json.loads(args.warehouse.with_name("known_answers.json").read_text())

    status, refused = http("GET", f"{server}/v1/tools")
    challenge = refused.get("headers", {}).get("www-authenticate", "") if refused else ""
    check(
        "(nobody)",
        "no credential is refused, and pointed to sign-in",
        status == 401 and "resource_metadata" in challenge,
        challenge,
    )
    status, _ = http(
        "POST",
        f"{gateway}/token",
        {"grant_type": "password", "username": "former-analyst", "password": "kpc-former-analyst"},
    )
    check("former-analyst", "a disabled user gets no token", status == 400, f"HTTP {status}")

    operator = Session(server, gateway, "operator")
    tools = operator.tools()
    check(
        "operator",
        "sees only server_info and list_datasets",
        tools == {"server_info", "list_datasets"},
        ", ".join(sorted(tools)),
    )

    pm = Session(server, gateway, "pm-equities")
    tools = pm.tools()
    check(
        "pm-equities",
        "reaches analysis and kpc_* but no risk_* tools",
        "run_backtest" in tools
        and "kpc_desk_pnl" in tools
        and not any(t.startswith("risk_") for t in tools),
        f"{len(tools)} tools",
    )
    listed = pm.call("list_datasets")
    ids = {item["id"] for item in (listed["output"] or {}).get("datasets", [])}
    check(
        "pm-equities",
        "lists own desk, not another's",
        "positions_eq_ls" in ids and "positions_macro" not in ids,
        f"{len(ids)} datasets",
    )
    other = pm.call(
        "analyze_performance", returns={"dataset": "desk_returns_macro", "column": "gross_return"}
    )
    missing = pm.call(
        "analyze_performance", returns={"dataset": "desk_returns_nowhere", "column": "gross_return"}
    )
    same = other["error"].replace("desk_returns_macro", "X") == missing["error"].replace(
        "desk_returns_nowhere", "X"
    )
    check(
        "pm-equities",
        "macro desk's returns refused, worded as missing",
        other["status"] != "ok" and same,
        other["status"],
    )
    pnl = pm.call("kpc_desk_pnl", desk="eq_ls")
    gross = (pnl["output"] or {}).get("gross_pnl")
    expected = answers["desks"]["eq_ls"]["gross_pnl_usd"]
    check(
        "pm-equities",
        "own desk P&L from the legacy report matches the books",
        gross is not None and abs(gross - expected) < 1.0,
        f"gross {gross:,.2f} USD" if gross is not None else pnl["error"],
    )
    refused_pnl = pm.call("kpc_desk_pnl", desk="macro")
    check(
        "pm-equities",
        "macro desk P&L refused",
        refused_pnl["status"] != "ok",
        refused_pnl.get("error") or "",
    )

    researcher = Session(server, gateway, "researcher")
    planted = answers["signals"]["momentum"]
    signal = researcher.call(
        "analyze_signal",
        signal={"dataset": "signals_momentum"},
        returns={"dataset": "px_returns"},
        lag=0,
    )
    summary = (signal["output"] or {}).get("summary", {})
    ic = summary.get("mean_rank_ic")
    check(
        "researcher",
        "analyze_signal recovers the planted momentum IC",
        ic is not None and abs(ic - planted["mean_ic"]) < 1e-9,
        f"rank IC {ic:.10f} vs planted {planted['mean_ic']:.10f}, HAC t "
        f"{summary.get('rank_ic_t_stat', float('nan')):.2f}"
        if ic is not None
        else signal["error"],
    )
    positions = researcher.call(
        "analyze_performance", returns={"dataset": "desk_returns_eq_ls", "column": "gross_return"}
    )
    check(
        "researcher",
        "refused every desk's returns",
        positions["status"] != "ok",
        positions["status"],
    )
    denied = researcher.call("kpc_desk_pnl", desk="eq_ls")
    check("researcher", "refused the desk P&L tool", denied["status"] == "denied", denied["status"])
    search = researcher.call(
        "find_related_signals", query="vendor factor model file format change", k=5
    )
    passages = (search["output"] or {}).get("passages", [])
    sources = [p["source"]["source_id"] for p in passages]
    check(
        "researcher",
        "cited search finds the vendor note, marked untrusted",
        "note-data-vendor-update.md" in sources
        and "untrusted" in json.dumps(search["output"]).lower(),
        ", ".join(dict.fromkeys(sources)),
    )

    risk = Session(server, gateway, "risk-manager")
    backtest = risk.call(
        "run_backtest",
        signal={"dataset": "signals_momentum"},
        returns={"dataset": "px_returns"},
        cost_bps=5,
    )
    head = (backtest["output"] or {}).get("summary", {})
    check(
        "risk-manager",
        "run_backtest of momentum at 5 bps",
        backtest["status"] == "ok",
        f"Sharpe {head.get('sharpe_ratio', float('nan')):.2f}, "
        f"{head.get('first_date')} to {head.get('last_date')}"
        if backtest["status"] == "ok"
        else backtest["error"],
    )
    weights = latest_weights(args.warehouse, "macro")
    found = risk.call(
        "analyze_risk",
        portfolio_id="macro",
        weights=weights,
        asset_returns={"dataset": "px_returns"},
        factor_model={
            "loadings": "fm_loadings",
            "factor_covariance": "fm_factor_cov",
            "specific_variance": {"dataset": "fm_specific", "column": "specific_var_annual"},
            "periods_per_year": 1,
        },
    )
    output = found["output"] or {}
    stress = output.get("historical_stress") or {}
    check(
        "risk-manager",
        "analyze_risk on the macro book, with stress and the vendor model",
        found["status"] == "ok" and output.get("factor_model") is not None,
        f"{len(stress)} stress windows; note: {output.get('historical_stress_note')}"
        if found["status"] == "ok"
        else found["error"],
    )
    var = risk.call("risk_desk_var", desk="macro", method="factor")
    value = (var["output"] or {}).get("var_usd")
    check(
        "risk-manager",
        "risk-engine service: macro 99% factor VaR",
        value is not None,
        f"{value:,.0f} USD" if value is not None else var["error"],
    )
    momentum = risk.call("kpc_momentum_signal", prices={"dataset": "px_close"})
    names = (momentum["output"] or {}).get("result") or {}
    check(
        "risk-manager",
        "legacy facade: momentum cross-section",
        len(names) == 40,
        f"{len(names)} instruments",
    )

    macro = Session(server, gateway, "pm-macro")
    denied = macro.call("risk_desk_var", desk="macro")
    check(
        "pm-macro",
        "the risk service is for the risk team only",
        denied["status"] == "denied",
        denied["status"],
    )
    try:
        answer = mcp_call(server, macro.token, "kpc_desk_pnl", {"desk": "macro"})
        check("pm-macro", "own desk P&L over MCP with a token", answer.is_error is False)
    except Exception as error:  # report it as a failed check, not a traceback
        check("pm-macro", "own desk P&L over MCP with a token", False, repr(error))

    if FAILURES:
        print(f"\n{len(FAILURES)} checks failed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
