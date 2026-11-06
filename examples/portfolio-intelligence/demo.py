"""Portfolio Intelligence: one firm's tools, data, research, and access rules, over real MCP.

Every figure comes from a tool, and no model is involved. A separate server process serves a
fictional firm over HTTP, and this script is its client, with one printed step each. `--serve` only
runs that server, for a real client to use (docs/demo.md).

Usage:
  uv run python examples/portfolio-intelligence/demo.py
  uv run python examples/portfolio-intelligence/demo.py --serve [--home DIR] [--port 8000]
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from book_tools import firm_tools
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from quant_agent_os.api import add_key
from quant_agent_os.data import DirectoryDatasetStore, GuardedDatasetStore
from quant_agent_os.tools import FileAccessPolicy, PermissionPolicy
from quant_platform import Catalog, compose, serve_http
from quant_research import open_research
from quant_research.application.retrieval import ResearchService

HERE = Path(__file__).resolve().parent
FACTORS = ["market", "size", "value", "momentum"]
UNANSWERABLE = "What is the duration of our municipal bond holdings?"
INSTRUCTIONS = (
    "book_var gives a book's value at risk by the firm's convention, and its weights for "
    "analyze_risk. Prefer it to any other VaR figure, and report the number as computed."
)
STEPS = 8

Say = Callable[[str], None]


@dataclass(frozen=True)
class Keys:
    """One fictional API key each for the two desks and the auditor."""

    growth: str
    value: str
    auditor: str


def prepare(home: Path) -> Keys:
    """Do what an operator does once: add the datasets, ingest the notes, and issue the keys."""
    store = DirectoryDatasetStore(home / "datasets")
    for path in sorted((HERE / "data").glob("*.csv")):
        store.put(path.stem, path)
    with open_research(home / "kb.db") as research:
        research.ingest.ingest(HERE / "notes")
    keys = home / "keys.toml"
    return Keys(
        growth=add_key(keys, "growth-pm", ["tools:list", "tools:call"], groups=["growth-desk"]),
        value=add_key(keys, "value-analyst", ["tools:list", "tools:call"], groups=["value-desk"]),
        auditor=add_key(keys, "auditor", ["runs:read"]),
    )


def build_catalog(home: Path, research: ResearchService) -> Catalog:
    """Return what the firm serves, with its own tool and data behind its access rules."""
    access = FileAccessPolicy(HERE / "access.toml")
    guarded = GuardedDatasetStore(DirectoryDatasetStore(home / "datasets"), access)
    return compose(
        policy=PermissionPolicy(),
        research=research,
        datasets=guarded,
        extra_tools=firm_tools(guarded),
        extra_instructions=INSTRUCTIONS,
        access=access,
    )


def connect_instructions(keys: Keys, port: int) -> str:
    """Return how to point a real client at the server, with the fictional keys to use."""
    url = f"http://127.0.0.1:{port}"
    return "\n".join(
        [
            f"Serving the fictional firm at {url}/mcp. These keys reach only this demonstration.",
            "",
            "Connect Claude Code as the growth desk's portfolio manager:",
            f'  claude mcp add --transport http qaos-demo {url}/mcp --header "Authorization: '
            f'Bearer {keys.growth}"',
            "",
            f"The value desk's analyst:  {keys.value}",
            f"The auditor (REST only):   {keys.auditor}",
            f'  curl -H "Authorization: Bearer {keys.auditor}" "{url}/v1/runs?limit=5"',
            "",
        ]
    )


def serve(home: Path, port: int) -> None:
    """Serve until interrupted, preparing ``home`` first if it is new."""
    if not (home / "keys.toml").exists():
        home.mkdir(parents=True, exist_ok=True)
        print(connect_instructions(prepare(home), port), flush=True)
    with open_research(home / "kb.db", read_only=True) as research:
        serve_http(
            build_catalog(home, research.research),
            keys=home / "keys.toml",
            runs=home / "runs.db",
            port=port,
        )


@asynccontextmanager
async def client_as(url: str, key: str) -> AsyncIterator[ClientSession]:
    """Yield an initialised MCP session that authenticates with ``key``."""
    headers = {"Authorization": f"Bearer {key}"}
    async with (
        httpx.AsyncClient(headers=headers, timeout=60) as client,
        streamable_http_client(f"{url}/mcp", http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


async def _call(session: ClientSession, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a tool's structured result, which on failure holds its status and hint."""
    result = await session.call_tool(tool, arguments)
    return dict(result.structuredContent or {}) | {"is_error": bool(result.isError)}


async def drive(url: str, keys: Keys, say: Say = print) -> dict[str, Any]:
    """Walk through the eight steps against the server at ``url``, and return what each returned."""
    seen: dict[str, Any] = {}

    def step(number: int, title: str) -> None:
        say(f"\n[{number}/{STEPS}] {title}")

    step(1, "Authenticate: no key, no service")
    async with httpx.AsyncClient(timeout=60) as anonymous:
        seen["no_key"] = (await anonymous.get(f"{url}/v1/tools")).status_code
    say(f"  A request without a key gets HTTP {seen['no_key']}.")

    async with client_as(url, keys.growth) as growth, client_as(url, keys.value) as value:
        step(2, "Discover: each caller sees only what the firm lets it use")
        seen["growth_tools"] = sorted(t.name for t in (await growth.list_tools()).tools)
        seen["value_tools"] = sorted(t.name for t in (await value.list_tools()).tools)
        say(f"  growth-pm:     {', '.join(seen['growth_tools'])}")
        say(f"  value-analyst: {', '.join(seen['value_tools'])}")
        listed = await _call(growth, "list_datasets", {})
        seen["growth_datasets"] = [dataset["id"] for dataset in listed["datasets"]]
        say(f"  growth-pm's datasets: {', '.join(seen['growth_datasets'])}")

        step(3, "Compute risk and exposures, by reference: no return passes through a client")
        var = await _call(growth, "book_var", {"book": "growth"})
        seen["var"] = var
        say(f"  The firm's own tool: 95% one-month VaR {var['var']:.2%} ({var['convention']})")
        risk = await _call(
            growth,
            "analyze_risk",
            {
                "portfolio_id": "growth",
                "weights": var["weights"],
                "asset_returns": {"dataset": "growth-assets"},
                "factor_returns": {"dataset": "factors", "columns": FACTORS},
            },
        )
        seen["risk"] = risk
        say(f"  Annualised volatility {risk['volatility']:.2%}")
        exposures = risk["factors"]["exposures"]
        say("  Exposures: " + ", ".join(f"{name} {exposures[name]:+.2f}" for name in FACTORS))
        say(f"  From data versions: {', '.join(sorted(risk['data_versions']))}")

        step(4, "Retrieve research, with citations")
        question = "momentum crash risk in growth portfolios"
        found = await _call(growth, "find_related_signals", {"query": question})
        seen["research"] = found
        say(f"  Q: {question}")
        for passage in found["passages"][:2]:
            source = passage["source"]
            where = f"{source['source_id']}, {source['location']}".replace("§", "section")
            text = " ".join(passage["text"].split()).replace("…", "...")
            say(f"  - {where}: {text[:90]}...")

        step(5, "Report: computed and retrieved evidence, kept apart")
        report = await _call(
            growth,
            "research_report",
            {
                "focus": "momentum",
                "returns": {"dataset": "growth-book", "column": "book"},
                "benchmark": {"dataset": "growth-book", "column": "benchmark"},
            },
        )
        seen["report"] = report
        kinds = Counter(item["kind"] for item in report["evidence"])
        say("  Evidence: " + ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items())))
        say(
            f"  Sharpe ratio {report['metrics']['sharpe_ratio']:.2f}, "
            f"maximum drawdown {report['metrics']['max_drawdown']:.2%}"
        )

        step(6, "Refuse: another desk's data looks missing, and another desk's tool is denied")
        refused = await _call(
            growth, "analyze_performance", {"returns": {"dataset": "value-book", "column": "book"}}
        )
        seen["refused_dataset"] = refused
        say(f"  growth-pm reads value-book: {refused['status']}: {refused['error']}")
        denied = await _call(value, "book_var", {"book": "value"})
        seen["denied_tool"] = denied
        say(f"  value-analyst calls book_var: {denied['status']}")

        step(7, "Abstain: a question the research cannot answer")
        abstained = await _call(growth, "find_related_signals", {"query": UNANSWERABLE})
        seen["abstained"] = abstained
        say(f"  Q: {UNANSWERABLE}")
        say(f"  {len(abstained['passages'])} passages. {abstained['warnings'][-1]}")

    step(8, "Audit: every call above is on record, under the caller's name")
    seen["audit"] = await _audit(url, keys.auditor)
    for call in seen["audit"]:
        refusal = f", refused {', '.join(call['refused'])}" if call["refused"] else ""
        say(
            f"  {call['principal']:<14} {call['tool']:<21} v{call['version']:<6} "
            f"{call['status']:<7} input {call['input'][:8]}, "
            f"data versions: {len(call['data_versions'])}{refusal}"
        )
    return seen


async def _audit(url: str, key: str) -> list[dict[str, Any]]:
    """Return the recorded calls, oldest first, as the auditor reads them over REST."""
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(base_url=url, headers=headers, timeout=60) as client:
        runs = (await client.get("/v1/runs", params={"limit": 100})).raise_for_status().json()
        stored = [
            (await client.get(f"/v1/runs/{run['run_id']}")).raise_for_status().json()
            for run in runs
        ]
    calls = [
        {
            "principal": item["run"]["principal"],
            "tool": call["tool"],
            "version": call["tool_version"],
            "status": call["status"],
            "input": call["input_sha256"],
            "data_versions": call["data_versions"],
            "refused": call["refused"],
            "at": call["started_at"],
        }
        for item in stored
        for call in item["calls"]
    ]
    return sorted(calls, key=lambda call: call["at"])


def _wait_until_healthy(url: str, server: subprocess.Popen[bytes], log: Path) -> None:
    """Return once the server answers, or raise with its log if it stops or never does."""
    for _ in range(300):
        if server.poll() is not None:
            break
        try:
            if httpx.get(f"{url}/healthz", timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"The demonstration's server did not start:\n{log.read_text('utf-8')}")


def run_demo(say: Say = print) -> dict[str, Any]:
    """Prepare a temporary home, start the server on it, drive it, and stop it."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        home = Path(tmp)
        keys = prepare(home)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        log = home / "server.log"
        command = [sys.executable, str(Path(__file__).resolve()), "--serve"]
        command += ["--home", str(home), "--port", str(port)]
        with log.open("wb") as output:
            server = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
            try:
                _wait_until_healthy(url, server, log)
                say(f"A fictional firm is served at {url}/mcp, by a separate process.")
                return asyncio.run(drive(url, keys, say))
            finally:
                server.terminate()
                server.wait(timeout=30)


def main() -> None:
    """Run the scripted walk-through, or with ``--serve`` only the server."""
    parser = argparse.ArgumentParser(description="The Portfolio Intelligence demonstration.")
    parser.add_argument("--serve", action="store_true", help="Only run the server, for a client")
    parser.add_argument("--home", type=Path, help="Keep its state here; default: a temporary one")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not args.serve:
        run_demo()
    elif args.home is not None:
        serve(args.home, args.port)
    else:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            serve(Path(tmp), args.port)


if __name__ == "__main__":
    main()
