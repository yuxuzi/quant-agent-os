"""The approval page (decision 0014): where a person approves a waiting call in a browser.

``GET /approve/{id}`` shows what will run: tool, permissions, redacted arguments, input hash,
requester, and expiry. ``POST /approve/{id}`` takes an approver key, the form token, and the
decision. The page has no scripts, escapes everything it shows, and sends headers that forbid
framing, caching, referrers, and loading anything; the key is never stored or echoed.

Each client address may submit 30 forms a minute (decision 0015), counted in this process.
Behind a reverse proxy, run the server so it sees the client's address (for example uvicorn's
``--proxy-headers``), or every client shares the proxy's allowance.
"""

from __future__ import annotations

import html
import json
import math
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from quant_agent_os.approval_desk import ApprovalDesk, DecisionError
from quant_agent_os.runs.models import ApprovalRequest
from quant_agent_os.runtime.quotas import TokenBucket

if TYPE_CHECKING:
    from starlette.routing import Route

MAX_FORM_BYTES = 8 * 1024
SUBMISSIONS_PER_MINUTE = 30
MAX_TRACKED_ADDRESSES = 10_000
HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
STYLE = (
    "body{font:15px/1.5 system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem}"
    "dt{font-weight:600}dd{margin:0 0 .6rem}pre{background:#f4f4f4;padding:.6rem;"
    "overflow:auto}input{width:100%;padding:.4rem;margin:.3rem 0 .8rem}"
    "button{padding:.5rem 1rem;margin-right:.6rem}"
)


class AddressLimits:
    """A token bucket per client address, forgetting the least recently seen beyond a cap."""

    def __init__(self, rate: int, *, cap: int = MAX_TRACKED_ADDRESSES) -> None:
        self.rate = rate
        self.cap = cap
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()
        self._lock = threading.Lock()

    def take(self, address: str) -> float:
        """Take one submission for ``address``: 0, or the seconds until it may submit again."""
        with self._lock:
            bucket = self._buckets.pop(address, None) or TokenBucket(self.rate)
            self._buckets[address] = bucket
            while len(self._buckets) > self.cap:
                self._buckets.popitem(last=False)
        return bucket.take()


def approval_routes(
    desk: ApprovalDesk, *, submissions_per_minute: int = SUBMISSIONS_PER_MINUTE
) -> list[Route]:
    """Return the page's routes, served beside the REST and MCP routes."""
    from starlette.requests import Request
    from starlette.responses import HTMLResponse
    from starlette.routing import Route

    limits = AddressLimits(submissions_per_minute)

    def page(
        title: str, body: str, status: int = 200, headers: dict[str, str] | None = None
    ) -> HTMLResponse:
        document = (
            f"<!doctype html><html lang=en><head><meta charset=utf-8><title>{html.escape(title)}"
            f"</title><style>{STYLE}</style></head><body><h1>{html.escape(title)}</h1>{body}"
            "</body></html>"
        )
        return HTMLResponse(document, status_code=status, headers=HEADERS | (headers or {}))

    async def show(request: Request) -> HTMLResponse:
        request_id = request.path_params["request_id"]
        found = desk.view(request_id)
        if found is None:
            return page("Not found", "<p>There is no such approval request.</p>", 404)
        return page("Approve this call?", _details(found) + _form(found, desk))

    async def decide(request: Request) -> HTMLResponse:
        request_id = request.path_params["request_id"]
        wait = limits.take(request.client.host if request.client else "unknown")
        if wait > 0:
            seconds = math.ceil(wait)
            return page(
                "Too many attempts",
                f"<p>Wait {seconds} seconds, then submit the form again.</p>",
                429,
                {"Retry-After": str(seconds)},
            )
        declared = request.headers.get("content-length", "")
        if not declared.isdigit() or int(declared) > MAX_FORM_BYTES:
            return page("Refused", "<p>The form is too large.</p>", 413)
        form = await request.form()
        decision = str(form.get("decision", ""))
        try:
            decided = desk.decide(
                request_id,
                str(form.get("key", "")),
                approve=decision == "approve",
                form_token=str(form.get("token", "")),
            )
        except DecisionError as error:
            return page("Not decided", f"<p>{html.escape(str(error))}</p>", 403)
        if decided.status == "approved":
            return page(
                "Approved",
                "<p>Return to your client and ask it to run the same call again. "
                "It runs once, in the original run.</p>",
            )
        return page("Denied", "<p>The call will not run.</p>")

    return [
        Route("/approve/{request_id}", show, methods=["GET"]),
        Route("/approve/{request_id}", decide, methods=["POST"]),
    ]


def _details(request: ApprovalRequest) -> str:
    def row(name: str, value: Any) -> str:
        return f"<dt>{html.escape(name)}</dt><dd>{value}</dd>"

    arguments = json.dumps(request.arguments, indent=2, sort_keys=True, default=str)
    return (
        "<dl>"
        + row("Tool", html.escape(request.tool))
        + row("Permissions", html.escape(", ".join(request.permissions) or "none"))
        + row("Arguments", f"<pre>{html.escape(arguments)}</pre>")
        + row("Input SHA-256", f"<code>{html.escape(request.input_sha256)}</code>")
        + row("Requested by", html.escape(request.requested_by or "unknown"))
        + row("Expires", html.escape(request.expires_at.isoformat(timespec="seconds")))
        + row("Status", html.escape(request.status))
        + "</dl>"
    )


def _form(request: ApprovalRequest, desk: ApprovalDesk) -> str:
    if request.status != "pending" or request.expired():
        return "<p>This request can no longer be decided.</p>"
    token = html.escape(desk.form_token(request.request_id))
    return (
        "<form method=post autocomplete=off>"
        f"<input type=hidden name=token value={token}>"
        "<label for=key>Approver key</label>"
        "<input id=key name=key type=password required>"
        "<button name=decision value=approve>Approve once</button>"
        "<button name=decision value=deny>Deny</button>"
        "</form>"
    )
