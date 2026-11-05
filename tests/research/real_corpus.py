"""Opt-in evaluation on real research papers (plan: docs/plans/real-corpus-eval.md).

The mock corpora show that the pipeline handles phrasings written for it. This evaluation
asks whether it handles real papers: 21 pinned arXiv PDFs on factors, momentum, anomalies,
earnings drift, and order flow.

    uv run python tests/research/real_corpus.py fetch   # once, needs network
    uv run python tests/research/real_corpus.py eval    # offline
    uv run python tests/research/real_corpus.py eval --update
    uv run python tests/research/real_corpus.py eval --local   # with the local-embeddings extra
    uv run python tests/research/real_corpus.py ablation   # what each channel contributes
    uv run python tests/research/real_corpus.py relations  # for labelling

PDFs are cached in ``$QUANT_RESEARCH_REAL_CORPUS`` (default
``~/.cache/quant-agent-os/real-corpus``) and are never committed. ``eval`` writes nothing but
``--update``'s baseline. It is not part of the default test suite, which runs offline; the
consistency of these files is.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import tomllib
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hashing_embedder import mock_embedder
from research_eval import location_matches

from quant_research import ResearchApp, ResearchQuery, open_research
from quant_research.application.retrieval import Channel, ResearchService
from quant_research.config import make_embedder
from quant_research.domain.extraction import is_prose, split_sentences
from quant_research.domain.ports import Embedder
from quant_research.domain.vocabulary import EntityMatcher

HERE = Path(__file__).parent
PAPERS = HERE / "real_corpus" / "papers.toml"
GOLD = HERE / "real_corpus" / "gold.toml"
LABELS = HERE / "real_corpus" / "labels.toml"
BASELINE = HERE / "evaluation" / "real_baseline.json"
K = 5
ARXIV_PDF = "https://arxiv.org/pdf/{id}"
USER_AGENT = "quant-agent-os-evaluation (https://github.com/yuxuzi/quant-agent-os)"
PAUSE_SECONDS = 3.0  # arXiv asks automated clients to keep to about one request every 3 s
# The channels a default install fuses; the vector channel needs an embedder.
DEFAULT_CHANNELS: frozenset[Channel] = frozenset({"keyword", "mention", "graph"})


@dataclass(frozen=True)
class Paper:
    """One pinned arXiv paper."""

    id: str
    title: str
    sha256: str

    @property
    def filename(self) -> str:
        """The cached file's name, which is also its source id in the store."""
        return f"{self.id}.pdf"


def papers() -> list[Paper]:
    """Return the pinned papers."""
    return [Paper(**row) for row in tomllib.loads(PAPERS.read_text("utf-8"))["papers"]]


def questions() -> list[dict[str, Any]]:
    """Return the gold questions."""
    return list(tomllib.loads(GOLD.read_text("utf-8"))["questions"])


def labels() -> dict[tuple[str, str, str, str], bool]:
    """Return the hand judgements of typed facts, keyed by (paper, subject, predicate, object)."""
    rows = tomllib.loads(LABELS.read_text("utf-8")).get("relations", [])
    return {(r["paper"], r["subject"], r["predicate"], r["object"]): r["correct"] for r in rows}


def cache_dir() -> Path:
    """Return the local PDF cache directory."""
    default = Path.home() / ".cache" / "quant-agent-os" / "real-corpus"
    return Path(os.environ.get("QUANT_RESEARCH_REAL_CORPUS", default))


def missing(cache: Path) -> list[Paper]:
    """Return the papers whose cached PDF is absent or does not match its pinned hash."""
    return [
        paper
        for paper in papers()
        if not (cache / paper.filename).is_file()
        or hashlib.sha256((cache / paper.filename).read_bytes()).hexdigest() != paper.sha256
    ]


def fetch(cache: Path) -> None:
    """Download every missing or mismatched paper from arXiv and verify its hash."""
    cache.mkdir(parents=True, exist_ok=True)
    for paper in missing(cache):
        request = urllib.request.Request(
            ARXIV_PDF.format(id=paper.id), headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
        digest = hashlib.sha256(body).hexdigest()
        if digest != paper.sha256:
            raise SystemExit(
                f"{paper.id}: arXiv served a different file (sha256 {digest}). arXiv sometimes "
                "rebuilds PDFs; check the paper, then update its sha256 in papers.toml."
            )
        (cache / paper.filename).write_bytes(body)
        print(f"Fetched {paper.id}.")
        time.sleep(PAUSE_SECONDS)
    print(f"All {len(papers())} papers are in {cache}.")


def _paper_id(source_id: str) -> str:
    return source_id.removesuffix(".pdf")


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 1.0


def corpus_metrics(research: ResearchApp) -> dict[str, Any]:
    """Return size and graph-shape figures for the ingested corpus."""
    relations = research.store.all_relations()
    predicates = Counter(r.predicate for r in relations)
    return {
        "relations": len(relations),
        "typed_relations": sum(n for p, n in predicates.items() if p != "related_to"),
        "related_to_share": round(predicates["related_to"] / len(relations), 4)
        if relations
        else 0.0,
    }


def retrieval_metrics(research: ResearchApp, cache: Path) -> dict[str, Any]:
    """Score the gold questions: hit@k, MRR, abstention, citations, and locations."""
    matcher = EntityMatcher(research.vocabulary)
    hits: dict[str, list[float]] = {"topic": [], "paraphrase": []}
    ranks: list[float] = []
    abstained: list[float] = []
    citations: list[float] = []
    locations: list[float] = []
    detail: dict[str, Any] = {}
    for question in questions():
        packet = research.research.find_related(ResearchQuery(query=question["query"], k=K))
        found = [_paper_id(p.source.source_id) for p in packet.passages]
        if question["kind"] == "unanswerable":
            abstained.append(float(not packet.passages))
            detail[question["query"]] = {"returned": found}
            continue
        relevant = set(question["documents"])
        first = next((rank for rank, doc in enumerate(found, 1) if doc in relevant), None)
        hits[question["kind"]].append(float(first is not None))
        if question["kind"] == "topic":
            ranks.append(1 / first if first else 0.0)
        detail[question["query"]] = {"first_relevant_rank": first, "returned": found}
        for entity in packet.related:
            for link in entity.links:
                chunk = research.store.get_chunk(link.chunk_id)
                mentioned = {m.entity_id for m in matcher.find(chunk.text)} if chunk else set()
                citations.append(float({link.subject, link.object} <= mentioned))
        locations += [float(location_matches(research, p.chunk_id, cache)) for p in packet.passages]
    return {
        "topic_hit_rate": _mean(hits["topic"]),
        "topic_mrr": _mean(ranks),
        "paraphrase_hit_rate": _mean(hits["paraphrase"]),
        "abstention_accuracy": _mean(abstained),
        "citation_validity": _mean(citations),
        "location_validity": _mean(locations),
        "_questions": detail,
    }


def typed_relations(research: ResearchApp) -> dict[tuple[str, str, str, str], str]:
    """Return each typed fact once per paper, with one sentence that states it."""
    matcher = EntityMatcher(research.vocabulary)
    facts: dict[tuple[str, str, str, str], str] = {}
    for relation in research.store.all_relations():
        if relation.predicate == "related_to":
            continue
        chunk = research.store.get_chunk(relation.chunk_id)
        document = research.store.get_document(chunk.document_id) if chunk else None
        if chunk is None or document is None:
            continue
        fact = (
            _paper_id(document.uri),
            relation.subject_id,
            relation.predicate,
            relation.object_id,
        )
        if fact in facts:
            continue
        pair = {relation.subject_id, relation.object_id}
        facts[fact] = next(
            (
                " ".join(sentence.split())
                for sentence in split_sentences(chunk.text)
                if is_prose(sentence) and pair <= {m.entity_id for m in matcher.find(sentence)}
            ),
            "",
        )
    return facts


def extraction_metrics(research: ResearchApp) -> dict[str, Any]:
    """Score typed relations against the hand labels; unlabelled ones are counted, not scored."""
    judged = labels()
    facts = typed_relations(research)
    verdicts = [judged[fact] for fact in facts if fact in judged]
    return {
        "typed_facts": len(facts),
        "labelled_facts": len(verdicts),
        "labelled_precision": _mean([float(v) for v in verdicts]),
        "labelled_true_facts_found": sum(verdicts),
        "_unlabelled": sorted("|".join(f) for f in facts if f not in judged),
    }


def run(cache: Path | None = None, embedder: Embedder | None = None) -> dict[str, Any]:
    """Ingest the cached corpus into a fresh store and return every metric."""
    cache = cache or cache_dir()
    if absent := missing(cache):
        raise SystemExit(
            f"{len(absent)} papers are missing from {cache}; run `real_corpus.py fetch` first."
        )
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # font notices from re-extraction
    with open_research(":memory:", embedder=embedder) as research:
        started = time.perf_counter()
        result = research.ingest.ingest(cache)
        ingest_seconds = round(time.perf_counter() - started, 1)
        return {
            "documents": len(result.added),
            "chunks": result.chunks,
            "ingest_seconds": ingest_seconds,
            **corpus_metrics(research),
            **retrieval_metrics(research, cache),
            **extraction_metrics(research),
        }


# Recorded and compared; the rest describe the corpus or vary by machine.
SCORED = (
    "topic_hit_rate",
    "topic_mrr",
    "paraphrase_hit_rate",
    "abstention_accuracy",
    "citation_validity",
    "location_validity",
    "labelled_precision",
)


def channel_scores(service: ResearchService, min_confidence: float = 0.5) -> dict[str, Any]:
    """Score the gold questions through ``service``, keeping each one's first relevant rank."""
    ranks: dict[str, list[float]] = {"topic": [], "paraphrase": []}
    abstained: list[float] = []
    first_ranks: dict[str, int | None] = {}
    for question in questions():
        packet = service.find_related(
            ResearchQuery(query=question["query"], k=K, min_confidence=min_confidence)
        )
        if question["kind"] == "unanswerable":
            abstained.append(float(not packet.passages))
            continue
        found = [_paper_id(p.source.source_id) for p in packet.passages]
        first = next((n for n, doc in enumerate(found, 1) if doc in question["documents"]), None)
        ranks[question["kind"]].append(1 / first if first else 0.0)
        first_ranks[question["query"]] = first
    return {
        "topic_hit_rate": _mean([float(r > 0) for r in ranks["topic"]]),
        "topic_mrr": _mean(ranks["topic"]),
        "paraphrase_hit_rate": _mean([float(r > 0) for r in ranks["paraphrase"]]),
        "paraphrase_mrr": _mean(ranks["paraphrase"]),
        "abstention_accuracy": _mean(abstained),
        "_first_ranks": first_ranks,
    }


def surfaced_links(service: ResearchService) -> dict[str, int]:
    """Count the distinct graph edges the gold questions show a client, and the typed ones."""
    links = {
        (link.subject, link.predicate, link.object, link.chunk_id)
        for question in questions()
        for entity in service.find_related(ResearchQuery(query=question["query"], k=K)).related
        for link in [*entity.path, *entity.links]
    }
    return {
        "surfaced_links": len(links),
        "surfaced_typed_links": sum(link[1] != "related_to" for link in links),
    }


def ablation(research: ResearchApp) -> dict[str, Any]:
    """Score the default channels, each removed, each alone, and two changes to the default.

    ``channels`` limits only the fusion of passages: seeds still come from the keyword index and
    the vocabulary, and related entities still come from the graph, whichever rankings are off.
    The default ``min_confidence`` of 0.5 skips ``related_to`` edges (confidence 0.4), so one
    setting lowers it to show what those edges do; the other adds the mock vector channel.
    """
    store, vocabulary = research.store, research.vocabulary
    settings: dict[str, frozenset[Channel]] = {"default": DEFAULT_CHANNELS}
    for channel in sorted(DEFAULT_CHANNELS):
        settings[f"without {channel}"] = DEFAULT_CHANNELS - {channel}
    for channel in sorted(DEFAULT_CHANNELS):
        settings[f"{channel} only"] = frozenset({channel})
    scores = {
        name: channel_scores(ResearchService(store, vocabulary, channels=channels))
        for name, channels in settings.items()
    }
    scores["default + related_to edges"] = channel_scores(
        ResearchService(store, vocabulary), min_confidence=0.0
    )
    scores["default + hashing vector"] = channel_scores(
        ResearchService(store, vocabulary, embedder=research.embedder, vectors=store)
    )
    return {
        "settings": scores,
        **surfaced_links(ResearchService(store, vocabulary)),
        "typed_facts_by_predicate": dict(
            sorted(Counter(fact[2] for fact in typed_relations(research)).items())
        ),
        **corpus_metrics(research),
    }


def _print_ablation(cache: Path) -> None:
    """Print the ablation as a table, then every question whose rank a setting changed."""
    if absent := missing(cache):
        raise SystemExit(f"{len(absent)} papers are missing; run `real_corpus.py fetch` first.")
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    with open_research(":memory:", embedder=mock_embedder()) as research:
        research.ingest.ingest(cache)
        result = ablation(research)
    settings = result.pop("settings")
    print("| Setting | Topic hit | Topic MRR | Paraphrase hit | Paraphrase MRR | Abstention |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, s in settings.items():
        print(
            f"| {name} | {s['topic_hit_rate']} | {s['topic_mrr']} | {s['paraphrase_hit_rate']} "
            f"| {s['paraphrase_mrr']} | {s['abstention_accuracy']} |"
        )
    default = settings["default"]["_first_ranks"]
    for name, s in settings.items():
        for query, rank in s["_first_ranks"].items():
            if rank != default[query]:
                print(f"{name}: rank {default[query]} -> {rank}: {query}")
    print(json.dumps(result, indent=2))


def _print_relations(cache: Path) -> None:
    """Print every typed fact with its sentence, for labelling in labels.toml."""
    if absent := missing(cache):
        raise SystemExit(f"{len(absent)} papers are missing; run `real_corpus.py fetch` first.")
    judged = labels()
    with open_research(":memory:") as research:
        research.ingest.ingest(cache)
        for fact, sentence in sorted(typed_relations(research).items()):
            mark = "???" if fact not in judged else "ok " if judged[fact] else "BAD"
            print(f"{mark} {' | '.join(fact)}\n      {sentence[:300]}\n")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "eval"
    if command == "fetch":
        fetch(cache_dir())
    elif command == "relations":
        _print_relations(cache_dir())
    elif command == "ablation":
        _print_ablation(cache_dir())
    elif command == "eval":
        local = "--local" in sys.argv
        if local and "--update" in sys.argv:
            raise SystemExit("The baseline records the default, so --local cannot update it.")
        metrics = run(embedder=make_embedder("local") if local else None)
        print(json.dumps(metrics, indent=2))
        if "--update" in sys.argv:
            scored = {name: metrics[name] for name in SCORED}
            BASELINE.write_text(json.dumps(scored, indent=2, sort_keys=True) + "\n", "utf-8")
            print(f"Baseline written to {BASELINE}.")
    else:
        raise SystemExit(f"Unknown command {command!r}; use fetch, eval, ablation, or relations.")
