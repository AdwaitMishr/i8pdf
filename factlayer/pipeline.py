"""End-to-end ingest: PDF in, grounded facts and relationships out.

    read -> segment -> extract -> assign concepts -> store -> link

The link step only compares the new document's facts against what is already
stored for its collection, so cost grows with the size of the new document
rather than the size of the knowledge layer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .extract.rules import ExtractionContext, extract_page
from .extract.subjects import detect_subject, normalise_subject
from .ingest.pdf import read_pdf
from .knowledge.linking import ALIAS_EVIDENCE, link
from .knowledge.relations import DocumentInfo
from .models import SourceDocument
from .store.db import Store


@dataclass
class IngestReport:
    doc_id: str
    filename: str
    title: str
    collection: str
    subject: str
    n_pages: int
    n_facts: int
    n_relations: int
    seconds: float
    skipped: bool = False
    reason: str = ""
    aliases_learned: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {**self.__dict__, "seconds": round(self.seconds, 2)}


# Citations read badly when a PDF's title metadata is a full bibliographic line.
_LABEL_CHARS = 52


def short_label(title: str, filename: str) -> str:
    label = (title or "").strip() or filename
    if len(label) <= _LABEL_CHARS:
        return label
    cut = label[:_LABEL_CHARS].rsplit(" ", 1)[0]
    return (cut or label[:_LABEL_CHARS]).rstrip(",;:") + "…"


def _document_map(store: Store) -> dict[str, DocumentInfo]:
    out: dict[str, DocumentInfo] = {}
    for row in store.documents():
        published = row["published_on"]
        out[row["doc_id"]] = DocumentInfo(
            doc_id=row["doc_id"],
            label=short_label(row["title"], row["filename"]),
            collection=row["collection"],
            published_on=__import__("datetime").date.fromisoformat(published)
            if published else None)
    return out


def ingest(store: Store, path: str | Path, collection: str = "default",
           subject: str | None = None, refiner=None) -> IngestReport:
    """Ingest one PDF and link it into the knowledge layer.

    ``refiner`` is an optional callable given the extracted facts and the page
    texts, returning refined facts -- the hook the LLM layer plugs into.
    """
    started = time.perf_counter()
    path = Path(path)
    document = read_pdf(path)

    existing = store.document_by_hash(document.sha256)
    if existing:
        return IngestReport(
            existing["doc_id"], path.name, existing["title"], existing["collection"],
            existing["subject"] or "", existing["n_pages"], 0, 0,
            time.perf_counter() - started, skipped=True,
            reason="identical content already ingested")

    subject = subject or detect_subject(
        document.title, [p.text for p in document.pages]) or document.title
    context = ExtractionContext(document.doc_id, subject, normalise_subject(subject))

    facts = []
    for page in document.pages:
        facts.extend(extract_page(page.text, page.page_no, context, page.cells))
    for fact in facts:
        fact.extractor = "rules"
    if refiner is not None:
        facts = refiner(facts, {p.page_no: p.text for p in document.pages})

    index = store.load_concept_index()
    for fact in facts:
        index.observe(fact.metric_key)
    for fact in facts:
        fact.concept_id = index.assign(fact.metric_key, fact.metric)

    record = SourceDocument(
        doc_id=document.doc_id, filename=document.filename, title=document.title,
        sha256=document.sha256, n_pages=document.n_pages, collection=collection,
        subject=subject, subject_key=normalise_subject(subject),
        published_on=document.published_on)
    store.add_document(record, [(p.page_no, p.text, p.n_columns, p.printed_label)
                                for p in document.pages])
    store.save_facts(facts)
    store.save_concept_index(index)

    # Compare only against what this collection already holds.
    pool = store.facts(collection=collection)
    result = link(pool, _document_map(store), index,
                  only_new={f.fact_id for f in facts})
    store.save_relations(result.relations)
    store.record_aliases(result.aliases)

    # A wording pair backed by enough independent bridges becomes one concept.
    promoted = [(row["left_key"], row["right_key"]) for row in store.aliases()
                if row["votes"] >= ALIAS_EVIDENCE]
    if promoted:
        for left, right in promoted:
            index.merge(left, right)
        store.save_concept_index(index)

    return IngestReport(
        doc_id=document.doc_id, filename=document.filename, title=document.title,
        collection=collection, subject=subject, n_pages=document.n_pages,
        n_facts=len(facts), n_relations=len(result.relations),
        seconds=time.perf_counter() - started, aliases_learned=result.aliases)
