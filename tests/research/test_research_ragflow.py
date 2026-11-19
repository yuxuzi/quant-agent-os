"""The RAGFlow PDF loader against a fake RAGFlow server (offline)."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from quant_research.application.retrieval import ResearchQuery
from quant_research.bootstrap import open_research
from quant_research.config import RagflowSettings, make_ragflow_loader
from quant_research.domain.chunking import MAX_TABLE_CHARS, split_pages
from quant_research.domain.ports import SourceReadError
from quant_research.infrastructure.ragflow import RagflowPdfLoader, pages_from_chunks, table_text
from quant_research.infrastructure.remote_embedding import HttpResponse

KEY = SecretStr("ragflow-secret-key")
CHUNKS = [
    {
        "content": "Results: low volatility stocks earn higher risk-adjusted returns.",
        "positions": [[2, 50, 500, 100, 140]],
    },
    {"content": "Low volatility anomaly", "positions": [[1, 50, 500, 40, 60]]},
    {"content": "We study betting against beta.", "positions": [[1, 50, 500, 200, 260]]},
    {"content": "", "positions": []},
]


class FakeRagflow:
    """Answers the RAGFlow 1.0 endpoints the loader calls, and records them."""

    def __init__(self, chunks: list[dict[str, Any]], runs: list[str] | None = None) -> None:
        self.chunks = chunks
        self.runs = runs or ["RUNNING", "DONE"]
        self.calls: list[tuple[str, str]] = []
        self.uploaded = b""
        self.headers: dict[str, str] = {}

    def __call__(self, request: urllib.request.Request, timeout: float) -> HttpResponse:
        method, path = request.get_method(), request.full_url.split("/api/v1", 1)[1]
        self.calls.append((method, path))
        self.headers = dict(request.header_items())
        if method == "POST" and path == "/datasets":
            return _ok({"id": "ds1"})
        if method == "POST" and path == "/datasets/ds1/documents":
            self.uploaded = request.data if isinstance(request.data, bytes) else b""
            return _ok([{"id": "doc1", "run": "UNSTART"}])
        if method == "POST" and path == "/datasets/ds1/chunks":
            return _ok(None)
        if method == "GET" and path.startswith("/datasets/ds1/documents?"):
            run = self.runs.pop(0) if len(self.runs) > 1 else self.runs[0]
            return _ok({"docs": [{"id": "doc1", "run": run, "progress_msg": ["OCR failed"]}]})
        if method == "GET" and path.startswith("/datasets/ds1/documents/doc1/chunks"):
            return _ok({"chunks": self.chunks, "total": len(self.chunks)})
        if method == "DELETE" and path == "/datasets":
            return _ok(None)
        return HttpResponse(404, json.dumps({"code": 404, "message": "no route"}).encode())


def _ok(data: Any) -> HttpResponse:
    return HttpResponse(200, json.dumps({"code": 0, "data": data}).encode())


def _loader(server: FakeRagflow, **options: Any) -> RagflowPdfLoader:
    return RagflowPdfLoader(
        "https://ragflow.internal", KEY, transport=server, sleep=lambda _: None, **options
    )


def test_pages_are_rebuilt_in_reading_order(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    server = FakeRagflow(CHUNKS)

    source = _loader(server).load(pdf, tmp_path)

    assert source.uri == "paper.pdf"
    assert source.loader_version == "ragflow-v2"
    assert source.pages == (
        "Low volatility anomaly\n\nWe study betting against beta.",
        "Results: low volatility stocks earn higher risk-adjusted returns.",
    )
    assert b"%PDF-1.4 fake" in server.uploaded
    assert source.tables == ()
    assert server.calls[-1] == ("DELETE", "/datasets")
    assert server.headers["Authorization"] == "Bearer ragflow-secret-key"


def test_ingestion_cites_ragflow_pages(tmp_path: Path) -> None:
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4 fake")

    with open_research(":memory:", pdf=_loader(FakeRagflow(CHUNKS))) as research:
        result = research.ingest.ingest(tmp_path)
        packet = research.research.find_related(ResearchQuery(query="risk-adjusted returns"))

    assert result.added == ["paper.pdf"]
    assert packet.passages[0].source.location is not None
    assert packet.passages[0].source.location.startswith("page 2, lines 1")


def test_a_failed_parse_is_reported_and_the_dataset_removed(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF")
    server = FakeRagflow(CHUNKS, runs=["FAIL"])

    with pytest.raises(SourceReadError, match=r"could not parse scan\.pdf: OCR failed"):
        _loader(server).load(pdf, tmp_path)
    assert server.calls[-1] == ("DELETE", "/datasets")


def test_a_slow_parse_times_out(tmp_path: Path) -> None:
    pdf = tmp_path / "slow.pdf"
    pdf.write_bytes(b"%PDF")
    ticks = iter(range(0, 10_000, 5))

    loader = _loader(
        FakeRagflow(CHUNKS, runs=["RUNNING"]), parse_timeout=12, clock=lambda: next(ticks)
    )
    with pytest.raises(SourceReadError, match=r"did not finish parsing slow\.pdf"):
        loader.load(pdf, tmp_path)


def test_a_chunk_without_a_page_is_refused() -> None:
    with pytest.raises(SourceReadError, match="without a page position"):
        pages_from_chunks([{"content": "text", "positions": []}], "x.pdf")


TABLE = (
    "<table><caption>Table 2: Betting against beta</caption>"
    "<tr><th>Decile</th><th>Beta</th><th>Alpha</th></tr>"
    "<tr><td>1</td><td>0.61</td><td>0.52 &amp; significant</td></tr>"
    "<tr><td>10</td><td>1.72</td><td>-0.03"
    "</table>\nSource: CRSP, 1926-2012."
)


def test_a_table_becomes_rows_with_its_caption() -> None:
    assert table_text(TABLE).splitlines() == [
        "Table 2: Betting against beta",
        "Decile | Beta | Alpha",
        "1 | 0.61 | 0.52 & significant",
        "10 | 1.72 | -0.03",
        "Source: CRSP, 1926-2012.",
    ]


@pytest.mark.parametrize("kind", ["table", ["table"]])
def test_a_table_chunk_is_placed_with_its_line_range(kind: object) -> None:
    pages, tables = pages_from_chunks(
        [
            {"content": "We study betting against beta.", "positions": [[1, 0, 0, 10, 20]]},
            {"content": TABLE, "doc_type_kwd": kind, "positions": [[1, 0, 0, 30, 90]]},
            {"content": "Alpha falls with beta.", "positions": [[1, 0, 0, 95, 99]]},
        ],
        "bab.pdf",
    )

    lines = pages[0].splitlines()
    assert tables == ((1, 3, 7),)
    assert lines[2] == "Table 2: Betting against beta" and lines[6] == "Source: CRSP, 1926-2012."
    assert lines[8] == "Alpha falls with beta."


def test_a_table_stays_in_one_passage() -> None:
    rows = [f"{decile} | 0.{decile}1 | factor return of decile {decile}" for decile in range(40)]
    page = "\n".join(["A long introduction that ends here.", "References", *rows, "After."])

    located = split_pages([page], tables=[(1, 2, 2 + len(rows))])

    assert [(passage.line_start, passage.line_end) for _, passage in located] == [
        (1, 1),
        (2, 42),
        (43, 43),
    ]
    assert all(passage.section is None for _, passage in located)  # "References" is a cell


def test_a_very_long_table_splits_between_rows() -> None:
    rows = [f"{index} | " + "x" * 90 for index in range(100)]

    located = split_pages(["\n".join(rows)], tables=[(1, 1, len(rows))])

    assert len(located) > 1
    assert all(len(passage.text) <= MAX_TABLE_CHARS for _, passage in located)
    assert located[0][1].line_start == 1
    assert located[-1][1].line_end == len(rows)


def test_ingestion_keeps_a_ragflow_table_whole(tmp_path: Path) -> None:
    (tmp_path / "bab.pdf").write_bytes(b"%PDF-1.4 fake")
    chunks = [{"content": TABLE, "doc_type_kwd": "table", "positions": [[3, 0, 0, 10, 90]]}]

    with open_research(":memory:", pdf=_loader(FakeRagflow(chunks))) as research:
        research.ingest.ingest(tmp_path)
        packet = research.research.find_related(ResearchQuery(query="betting against beta decile"))

    passage = packet.passages[0]
    assert passage.source.location is not None
    assert passage.source.location.startswith("page 3, lines 1-5")
    assert "Decile | Beta | Alpha" in passage.text and "10 | 1.72" in passage.text


def test_refusals_carry_ragflow_message_but_never_the_key(tmp_path: Path) -> None:
    def refuse(request: urllib.request.Request, timeout: float) -> HttpResponse:
        return HttpResponse(200, json.dumps({"code": 109, "message": "Invalid API key"}).encode())

    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF")
    loader = RagflowPdfLoader("https://ragflow.internal", KEY, transport=refuse)

    with pytest.raises(SourceReadError) as error:
        loader.load(pdf, tmp_path)
    assert "Invalid API key" in str(error.value) and "RAGFLOW_API_KEY" in str(error.value)
    assert KEY.get_secret_value() not in str(error.value)


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://ragflow.internal", True),
        ("http://localhost:9380", True),
        ("http://127.0.0.1", True),
        ("http://ragflow.internal", False),
        ("ftp://ragflow.internal", False),
    ],
)
def test_plain_http_is_refused_off_this_machine(url: str, allowed: bool) -> None:
    if allowed:
        RagflowPdfLoader(url, KEY)
    else:
        with pytest.raises(SourceReadError, match="RAGFLOW_URL"):
            RagflowPdfLoader(url, KEY)


def test_an_unconfigured_parser_says_what_to_set() -> None:
    with pytest.raises(SourceReadError, match="RAGFLOW_URL and RAGFLOW_API_KEY"):
        make_ragflow_loader(RagflowSettings(_env_file=None))
