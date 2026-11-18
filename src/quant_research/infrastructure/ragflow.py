"""PDF parsing through a firm's own RAGFlow server (standard library only).

`RagflowPdfLoader` implements the `SourceLoader` port for ``.pdf`` files. It sends each PDF to
RAGFlow's DeepDoc parser, which reads layout and tables and runs OCR on scanned pages, and turns
the parsed chunks back into page texts. Quant Research then chunks, links, and cites those pages
as it does pypdf's, so citations name the page and a line range within the page text RAGFlow
returned, not the PDF's own lines.

Each file gets a temporary dataset: create it, upload, parse, read the chunks, and delete it.
Only ``qaos ingest`` uses the loader; serving never contacts RAGFlow. Parsing in RAGFlow embeds
the chunks with the RAGFlow tenant's embedding model, which RAGFlow must have configured.

The API key is a `SecretStr` and never appears in errors. Plain HTTP is refused except on the
loopback interface, because the key travels in a header.

API reference (checked 2026-11-18, RAGFlow 1.0):
https://github.com/infiniflow/ragflow/blob/main/docs/references/http_api_reference.md. The
reference leaves the format of a chunk's ``positions`` undocumented; this loader reads each as
``[page, x0, x1, top, bottom]`` with pages counted from 1, as RAGFlow's parser writes them, and
refuses a file whose chunks carry no page, so it never guesses a citation.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from quant_research.domain.ports import SourceFile, SourceReadError
from quant_research.infrastructure.files import uri_for
from quant_research.infrastructure.remote_embedding import (
    Clock,
    HttpResponse,
    Sleep,
    Transport,
    urllib_transport,
)

VERSION = "ragflow-v1"
PARSER = "general"  # RAGFlow's built-in chunker with DeepDoc layout recognition
PAGE_SIZE = 100  # the largest page the chunk listing accepts
_DONE = frozenset({"DONE", "3"})
_FAILED = frozenset({"FAIL", "4", "CANCEL", "2"})
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


class RagflowPdfLoader:
    """A `SourceLoader` that parses PDFs with a RAGFlow server's DeepDoc parser."""

    version = VERSION
    suffixes = frozenset({".pdf"})

    def __init__(
        self,
        url: str,
        api_key: SecretStr,
        *,
        timeout: float = 60.0,
        parse_timeout: float = 600.0,
        poll_interval: float = 2.0,
        transport: Transport | None = None,
        sleep: Sleep | None = None,
        clock: Clock | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise SourceReadError(f"RAGFLOW_URL must be an http(s) URL, not {url!r}.")
        if parsed.scheme == "http" and parsed.hostname not in _LOOPBACK:
            raise SourceReadError(
                "RAGFLOW_URL must use https unless RAGFlow runs on this machine, because the "
                "API key travels with every request."
            )
        if not api_key.get_secret_value().strip():
            raise SourceReadError("The RAGFlow PDF parser needs RAGFLOW_API_KEY to be set.")
        self._base = url.rstrip("/") + "/api/v1"
        self._api_key = api_key
        self._timeout = timeout
        self._parse_timeout = parse_timeout
        self._poll_interval = poll_interval
        self._transport = transport or urllib_transport
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic

    def load(self, path: Path, root: Path) -> SourceFile:
        """Parse ``path`` in a temporary RAGFlow dataset and return its pages."""
        raw = path.read_bytes()
        sha256 = hashlib.sha256(raw).hexdigest()
        dataset = self._call(
            "POST",
            "/datasets",
            {
                "name": f"qaos-ingest-{sha256[:12]}-{uuid.uuid4().hex[:8]}",
                "parse_type": 1,
                "parser_id": PARSER,
            },
        )["id"]
        try:
            chunks = self._parse(str(dataset), path.name, raw)
        except SourceReadError:
            with contextlib.suppress(SourceReadError):  # report the parse failure, not cleanup's
                self._call("DELETE", "/datasets", {"ids": [dataset]})
            raise
        self._call("DELETE", "/datasets", {"ids": [dataset]})
        return SourceFile(
            uri=uri_for(path, root),
            sha256=sha256,
            body="",
            body_first_line=1,
            pages=pages_from_chunks(chunks, path.name),
            loader_version=VERSION,
        )

    def _parse(self, dataset: str, name: str, raw: bytes) -> list[dict[str, Any]]:
        uploaded = self._call("POST", f"/datasets/{dataset}/documents", files={name: raw})
        document = str(uploaded[0]["id"])
        self._call("POST", f"/datasets/{dataset}/chunks", {"document_ids": [document]})
        self._wait(dataset, document, name)
        chunks: list[dict[str, Any]] = []
        page = 1
        while True:
            listed = self._call(
                "GET",
                f"/datasets/{dataset}/documents/{document}/chunks"
                f"?page={page}&page_size={PAGE_SIZE}",
            )
            batch = listed.get("chunks") or []
            chunks += batch
            if len(batch) < PAGE_SIZE or len(chunks) >= int(listed.get("total", 0)):
                return chunks
            page += 1

    def _wait(self, dataset: str, document: str, name: str) -> None:
        deadline = self._clock() + self._parse_timeout
        while True:
            docs = self._call("GET", f"/datasets/{dataset}/documents?id={document}")["docs"]
            state = str(docs[0].get("run", "")) if docs else ""
            if state in _DONE:
                return
            if state in _FAILED:
                message = docs[0].get("progress_msg") or "no reason given"
                if isinstance(message, list):
                    message = message[-1] if message else "no reason given"
                raise SourceReadError(f"RAGFlow could not parse {name}: {message}")
            if self._clock() + self._poll_interval > deadline:
                raise SourceReadError(
                    f"RAGFlow did not finish parsing {name} within {self._parse_timeout:.0f} s."
                )
            self._sleep(self._poll_interval)

    def _call(
        self,
        method: str,
        endpoint: str,
        body: dict[str, Any] | None = None,
        *,
        files: dict[str, bytes] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        data: bytes | None = None
        if files is not None:
            boundary = uuid.uuid4().hex
            data = _multipart(files, boundary)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self._base + endpoint, data=data, headers=headers, method=method
        )
        try:
            response = self._transport(request, self._timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SourceReadError(f"RAGFlow is unreachable at {self._base}: {error}.") from error
        return _unwrap(response, method, endpoint)


def _unwrap(response: HttpResponse, method: str, endpoint: str) -> Any:
    """Return the ``data`` of a successful RAGFlow response, or raise with its message."""
    where = f"{method} {endpoint.split('?')[0]}"
    try:
        envelope = json.loads(response.body)
    except ValueError:
        envelope = None
    if not isinstance(envelope, dict):
        raise SourceReadError(f"RAGFlow returned HTTP {response.status} for {where}.")
    if response.status != 200 or envelope.get("code") != 0:
        message = envelope.get("message") or f"HTTP {response.status}"
        hint = " Check RAGFLOW_API_KEY." if envelope.get("code") in {109, 401} else ""
        raise SourceReadError(f"RAGFlow refused {where}: {message}.{hint}")
    return envelope.get("data")


def _multipart(files: dict[str, bytes], boundary: str) -> bytes:
    parts: list[bytes] = []
    for name, content in files.items():
        safe = name.replace('"', "_")
        parts += [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{safe}"\r\n'.encode(),
            b"Content-Type: application/pdf\r\n\r\n",
            content,
            b"\r\n",
        ]
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


def pages_from_chunks(chunks: Sequence[dict[str, Any]], name: str) -> tuple[str, ...]:
    """Place each chunk's text on the page where it starts, in reading order.

    A chunk's first position gives its page and its top edge; chunks on one page are ordered by
    that edge, then by RAGFlow's order. Raises `SourceReadError` when a chunk has text but no
    page, because its citation would be a guess.
    """
    placed: list[tuple[int, float, int, str]] = []
    for order, chunk in enumerate(chunks):
        text = str(chunk.get("content") or "").strip()
        if not text:
            continue
        positions = chunk.get("positions") or []
        try:
            first = positions[0]
            page, top = int(first[0]), float(first[3]) if len(first) > 3 else 0.0
        except (IndexError, TypeError, ValueError) as error:
            raise SourceReadError(
                f"RAGFlow returned a chunk of {name} without a page position, so it cannot be "
                "cited; parse this file with the default PDF parser."
            ) from error
        if page < 1:
            raise SourceReadError(f"RAGFlow returned page {page} for a chunk of {name}.")
        placed.append((page, top, order, text))
    if not placed:
        return ()
    pages: list[list[str]] = [[] for _ in range(max(item[0] for item in placed))]
    for page, _, _, text in sorted(placed):
        pages[page - 1].append(text)
    return tuple("\n\n".join(texts) for texts in pages)
