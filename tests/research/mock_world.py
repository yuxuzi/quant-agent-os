"""A synthetic quant research world with a known answer key, for testing Graph RAG (plan G1).

Every sentence is declared next to what it states, so the extracted graph can be scored
against the truth. The phrasings test the extractor:
- active and passive voice;
- aliases;
- negation;
- "explains";
- distractor co-mentions;
- filler sentences without entities.

The world is synthetic (the claims are not findings) and renders deterministically as
Markdown, text, and PDF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pdf_fixtures import write_pdf

SYMMETRIC = frozenset({"correlates_with", "related_to"})


@dataclass(frozen=True)
class Fact:
    """A sentence that states ``subject predicate object`` (the answer key)."""

    subject: str
    predicate: str
    object: str
    sentence: str


@dataclass(frozen=True)
class Negation:
    """A sentence that denies a relation between two entities; nothing may be extracted."""

    first: str
    second: str
    sentence: str


@dataclass(frozen=True)
class Distractor:
    """A sentence that co-mentions two entities without stating a typed relation."""

    first: str
    second: str
    sentence: str


Line = Fact | Negation | Distractor | str  # a plain str is filler without entities


@dataclass(frozen=True)
class MockDocument:
    """One corpus file: a path, a title, and sections (Markdown) or pages (PDF) of lines."""

    uri: str
    title: str
    parts: list[tuple[str, list[Line]]]


@dataclass(frozen=True)
class Question:
    """A gold question with the entities and documents a correct answer surfaces."""

    query: str
    expected: frozenset[str]
    documents: frozenset[str]
    entity_types: tuple[str, ...] = ("signal", "factor", "strategy")
    hops: int = 1
    paths: dict[str, tuple[str, ...]] = field(default_factory=dict)


DOCUMENTS = [
    MockDocument(
        "microstructure.md",
        "Order flow and short-horizon reversals",
        [
            (
                "Evidence",
                [
                    Fact(
                        "signal:order-flow-imbalance",
                        "predicts",
                        "signal:short-term-reversal",
                        "Order-flow imbalance predicts short-term reversal at daily horizons.",
                    ),
                    Fact(
                        "signal:short-term-reversal",
                        "explained_by",
                        "concept:bid-ask-bounce",
                        "The reversal effect is explained by bid-ask bounce in small stocks.",
                    ),
                    Fact(
                        "signal:short-term-reversal",
                        "correlates_with",
                        "signal:liquidity-provision",
                        "Short-term reversal correlates with liquidity provision returns.",
                    ),
                    Fact(
                        "signal:liquidity-provision",
                        "explained_by",
                        "concept:market-microstructure",
                        "Liquidity provision is captured by market microstructure frictions.",
                    ),
                    "The sample runs from 1993 to 2019 at daily frequency.",
                ],
            ),
            (
                "Robustness",
                [
                    Fact(
                        "signal:order-flow-imbalance",
                        "predicts",
                        "signal:short-term-reversal",
                        "Short-term reversal is predicted by order-flow imbalance.",
                    ),
                    Negation(
                        "signal:order-flow-imbalance",
                        "signal:time-series-momentum",
                        "Order-flow imbalance does not predict time-series momentum.",
                    ),
                    Distractor(
                        "concept:bid-ask-bounce",
                        "concept:crowding",
                        "Bid-ask bounce and crowding are both discussed in the appendix.",
                    ),
                    "Results are similar across sub-periods.",
                ],
            ),
        ],
    ),
    MockDocument(
        "momentum-survey.md",
        "A survey of momentum strategies",
        [
            (
                "Cross-sectional and time-series momentum",
                [
                    Fact(
                        "factor:momentum",
                        "contradicts",
                        "signal:short-term-reversal",
                        "Momentum contradicts the reversal effect at the one-month horizon.",
                    ),
                    Fact(
                        "signal:time-series-momentum",
                        "extends",
                        "factor:momentum",
                        "Time-series momentum builds on cross-sectional momentum.",
                    ),
                    Fact(
                        "strategy:trend-following",
                        "uses",
                        "signal:time-series-momentum",
                        "Trend following relies on time-series momentum.",
                    ),
                ],
            ),
            (
                "Data and methods",
                [
                    Fact(
                        "factor:momentum",
                        "tested_on",
                        "asset_class:us-equities",
                        "Momentum is tested on US equities.",
                    ),
                    Fact(
                        "factor:momentum",
                        "uses",
                        "method:portfolio-sorts",
                        "Momentum is estimated with decile sorts.",
                    ),
                    Fact(
                        "factor:momentum",
                        "uses",
                        "dataset:crsp",
                        "Cross-sectional momentum is measured using CRSP.",
                    ),
                    Negation(
                        "factor:momentum",
                        "signal:carry",
                        "Momentum does not predict carry.",
                    ),
                    "Portfolios are rebalanced monthly.",
                ],
            ),
        ],
    ),
    MockDocument(
        "papers/futures.pdf",
        "Trend and carry in futures markets",
        [
            (
                "page",
                [
                    "Section 1",
                    Fact(
                        "strategy:trend-following",
                        "tested_on",
                        "asset_class:futures",
                        "Trend following is tested on futures markets.",
                    ),
                    Fact(
                        "signal:time-series-momentum",
                        "tested_on",
                        "asset_class:fx",
                        "Time-series momentum works across currencies.",
                    ),
                    Fact(
                        "strategy:risk-parity",
                        "tested_on",
                        "asset_class:futures",
                        "Risk parity is tested on futures.",
                    ),
                    "Returns are measured in excess of the collateral yield.",
                ],
            ),
            (
                "page",
                [
                    "Section 2",
                    Fact(
                        "signal:carry",
                        "tested_on",
                        "asset_class:fx",
                        "Carry is tested on FX.",
                    ),
                    Fact(
                        "concept:crowding",
                        "predicts",
                        "signal:carry",
                        "Carry is predicted by crowding.",
                    ),
                    "Positions are sized to equal volatility.",
                ],
            ),
        ],
    ),
    MockDocument(
        "value-quality.md",
        "Value and quality",
        [
            (
                "Value",
                [
                    Fact(
                        "factor:value",
                        "explained_by",
                        "factor:profitability",
                        "HML is explained by gross profitability.",
                    ),
                    Fact(
                        "factor:value",
                        "explained_by",
                        "factor:profitability",
                        "Profitability explains the value premium.",
                    ),
                    Fact(
                        "factor:value",
                        "uses",
                        "method:fama-macbeth",
                        "The value factor is estimated with Fama-MacBeth regressions.",
                    ),
                    Fact(
                        "factor:size",
                        "explained_by",
                        "concept:market-microstructure",
                        "Market microstructure explains the size premium.",
                    ),
                ],
            ),
            (
                "Quality and low risk",
                [
                    Fact(
                        "factor:quality",
                        "correlates_with",
                        "factor:low-volatility",
                        "QMJ correlates with low beta.",
                    ),
                    Fact(
                        "factor:investment",
                        "correlates_with",
                        "factor:profitability",
                        "Asset growth correlates with operating profitability.",
                    ),
                    Fact(
                        "factor:low-volatility",
                        "contradicts",
                        "factor:size",
                        "Betting against beta contradicts the size premium.",
                    ),
                    Negation(
                        "factor:value",
                        "factor:quality",
                        "HML does not correlate with QMJ.",
                    ),
                ],
            ),
        ],
    ),
    MockDocument(
        "earnings.txt",
        "Earnings signals",
        [
            (
                "",
                [
                    Fact(
                        "signal:earnings-drift",
                        "correlates_with",
                        "signal:analyst-revisions",
                        "PEAD correlates with analyst revisions.",
                    ),
                    Fact(
                        "signal:accruals",
                        "predicts",
                        "signal:earnings-drift",
                        "Accruals forecast PEAD.",
                    ),
                    Fact(
                        "signal:accruals",
                        "uses",
                        "dataset:compustat",
                        "Accruals are measured using Compustat.",
                    ),
                    Fact(
                        "signal:earnings-drift",
                        "tested_on",
                        "asset_class:us-equities",
                        "Post-earnings announcement drift is tested on US equities.",
                    ),
                    Negation(
                        "signal:analyst-revisions",
                        "signal:accruals",
                        "Analyst revisions do not predict accruals.",
                    ),
                    "The effect has weakened since 2005.",
                ],
            ),
        ],
    ),
    MockDocument(
        "papers/stat-arb.pdf",
        "Statistical arbitrage and timing",
        [
            (
                "page",
                [
                    "Section 1",
                    Fact(
                        "strategy:pairs-trading",
                        "uses",
                        "signal:short-term-reversal",
                        "Pairs trading relies on short-term reversal.",
                    ),
                    Fact(
                        "strategy:pairs-trading",
                        "tested_on",
                        "asset_class:us-equities",
                        "Pairs trading is tested on US equities.",
                    ),
                ],
            ),
            (
                "page",
                [
                    Fact(
                        "strategy:factor-timing",
                        "uses",
                        "factor:momentum",
                        "Factor timing uses momentum.",
                    ),
                    Fact(
                        "strategy:factor-timing",
                        "uses",
                        "factor:momentum",
                        "Momentum is used by factor timing.",
                    ),
                    "Signals are standardised cross-sectionally each month.",
                ],
            ),
        ],
    ),
]

QUESTIONS = [
    Question(
        "What predicts short-term reversal?",
        frozenset(
            {
                "signal:order-flow-imbalance",
                "signal:liquidity-provision",
                "factor:momentum",
                "concept:bid-ask-bounce",
            }
        ),
        frozenset({"microstructure.md", "momentum-survey.md"}),
        entity_types=("signal", "factor", "concept"),
    ),
    Question(
        "trend following",
        frozenset({"signal:time-series-momentum", "factor:momentum"}),
        frozenset({"momentum-survey.md"}),
        entity_types=("signal", "factor"),
        hops=2,
        paths={
            "factor:momentum": (
                "strategy:trend-following",
                "signal:time-series-momentum",
                "factor:momentum",
            )
        },
    ),
    Question(
        "order-flow imbalance",
        frozenset({"concept:bid-ask-bounce"}),
        frozenset({"microstructure.md"}),
        entity_types=("concept",),
        hops=2,
        paths={
            "concept:bid-ask-bounce": (
                "signal:order-flow-imbalance",
                "signal:short-term-reversal",
                "concept:bid-ask-bounce",
            )
        },
    ),
    Question(
        "What explains HML?",
        frozenset({"factor:profitability"}),
        frozenset({"value-quality.md"}),
        entity_types=("factor",),
    ),
    Question(
        "Where is carry tested?",
        frozenset({"asset_class:fx"}),
        frozenset({"papers/futures.pdf"}),
        entity_types=("asset_class",),
    ),
    Question(
        "PEAD",
        frozenset({"signal:analyst-revisions", "signal:accruals"}),
        frozenset({"earnings.txt"}),
        entity_types=("signal",),
    ),
    Question(
        "pairs trading",
        frozenset({"signal:short-term-reversal"}),
        frozenset({"papers/stat-arb.pdf"}),
        entity_types=("signal", "factor"),
    ),
    Question(
        "accruals",
        frozenset({"signal:earnings-drift", "signal:analyst-revisions"}),
        frozenset({"earnings.txt"}),
        entity_types=("signal",),
        hops=2,
        paths={
            "signal:analyst-revisions": (
                "signal:accruals",
                "signal:earnings-drift",
                "signal:analyst-revisions",
            )
        },
    ),
    Question("crypto funding rates", frozenset(), frozenset()),
]


def facts() -> list[Fact]:
    """Return every fact in document order (duplicates in other phrasings included)."""
    return [
        line
        for doc in DOCUMENTS
        for _, lines in doc.parts
        for line in lines
        if isinstance(line, Fact)
    ]


def negations() -> list[Negation]:
    """Return every negated pair."""
    return [
        line
        for doc in DOCUMENTS
        for _, lines in doc.parts
        for line in lines
        if isinstance(line, Negation)
    ]


def key(subject: str, predicate: str, obj: str) -> tuple[str, str, str]:
    """Return a comparable triple; symmetric predicates are order-independent."""
    if predicate in SYMMETRIC and obj < subject:
        subject, obj = obj, subject
    return subject, predicate, obj


def true_triples() -> set[tuple[str, str, str]]:
    """Return the distinct true triples."""
    return {key(fact.subject, fact.predicate, fact.object) for fact in facts()}


def _text(line: Line) -> str:
    return line if isinstance(line, str) else line.sentence


def write_world(destination: Path, *, copies: int = 1) -> Path:
    """Render the world under ``destination``; ``copies > 1`` repeats it for scale tests.

    Each copy gets an entity-free marker line, so copies differ in content and are not skipped
    as duplicates.
    """
    for copy in range(copies):
        prefix = Path(f"copy-{copy:03d}") if copies > 1 else Path()
        marker = [f"Synthetic copy {copy}."] if copies > 1 else []
        for document in DOCUMENTS:
            path = destination / prefix / document.uri
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".pdf":
                pages = [[_text(line) for line in lines] for _, lines in document.parts]
                pages[-1] += marker
                write_pdf(
                    path,
                    pages,
                    title=document.title,
                    author="Synthetic Author",
                )
            elif path.suffix == ".md":
                body = [
                    "---",
                    f"title: {document.title}",
                    "authors: Synthetic Author",
                    "license: CC0 synthetic fixture",
                    "---",
                    "",
                    f"# {document.title}",
                ]
                for section, lines in document.parts:
                    body += ["", f"## {section}", ""]
                    body += [_text(line) for line in lines]
                body += ["", *marker] if marker else []
                path.write_text("\n".join(body) + "\n", "utf-8")
            else:
                lines = [_text(line) for _, part in document.parts for line in part] + marker
                path.write_text("\n\n".join(lines) + "\n", "utf-8")
    return destination
