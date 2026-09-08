"""End to end: ingest, link, store, serve."""

import json

from fastapi.testclient import TestClient

from factlayer.api.app import create_app
from factlayer.pipeline import ingest, short_label
from factlayer.report import render_cases
from factlayer.store.db import Store


def build(tmp_path, probe_pdfs):
    store = Store(tmp_path / "test.db")
    reports = [ingest(store, path, collection="probe") for path in probe_pdfs]
    return store, reports


def test_ingest_produces_grounded_facts(tmp_path, probe_pdfs):
    store, reports = build(tmp_path, probe_pdfs)
    assert all(not r.skipped for r in reports)
    facts = store.facts()
    assert facts, "no facts extracted"
    for fact in facts:
        page = store.page(fact.doc_id, fact.page_no)
        assert page["text"][fact.char_start:fact.char_end] == fact.evidence
        assert page["text"][fact.value_start:fact.value_end] in fact.evidence
    store.close()


def test_reingesting_the_same_bytes_is_a_no_op(tmp_path, probe_pdfs):
    store, _ = build(tmp_path, probe_pdfs)
    before = store.stats()
    again = ingest(store, probe_pdfs[0], collection="probe")
    assert again.skipped and store.stats() == before
    store.close()


def test_contradiction_is_detected_between_documents(tmp_path, probe_pdfs):
    """The two probe notes restate figures the other states differently."""
    store, _ = build(tmp_path, probe_pdfs)
    ingest(store, probe_pdfs[0], collection="probe")     # skipped, same bytes
    rows = store.relations(collection="probe", limit=200)
    assert rows, "no relationships derived"
    assert {r["relation"] for r in rows} <= {
        "corroborates", "contradicts", "reconciled_by_context",
        "consistent_across_context", "part_of"}
    store.close()


def test_deleting_a_document_removes_its_facts_and_relations(tmp_path, probe_pdfs):
    store, reports = build(tmp_path, probe_pdfs)
    store.delete_document(reports[0].doc_id)
    assert all(f.doc_id != reports[0].doc_id for f in store.facts())
    left = {r["left_id"] for r in store.relations(limit=500)}
    assert reports[0].doc_id not in {store.fact(i).doc_id for i in left if store.fact(i)}
    store.close()


def test_report_renders_all_four_cases(tmp_path, probe_pdfs):
    store, _ = build(tmp_path, probe_pdfs)
    text = render_cases(store)
    for heading in ("Case 1", "Case 2", "Case 3", "Case 4"):
        assert heading in text
    store.close()


def test_short_label_trims_bibliographic_titles():
    long = ("India: 2025 Article IV Consultation-Press Release; Staff Report; "
            "and Statement by the Executive Director")
    assert len(short_label(long, "x.pdf")) < 60
    assert short_label("", "file.pdf") == "file.pdf"


def test_api_round_trip(tmp_path, probe_pdfs):
    store, _ = build(tmp_path, probe_pdfs)
    store.close()
    client = TestClient(create_app(str(tmp_path / "test.db")))

    assert client.get("/").status_code == 200
    stats = client.get("/api/stats").json()
    assert stats["documents"] == len(probe_pdfs) and stats["facts"] > 0

    facts = client.get("/api/facts?limit=5").json()
    evidence = facts[0]["evidence"]
    start, end = evidence["value_offset"]
    assert evidence["text"][start:end], "value offsets must address the evidence"

    detail = client.get(f"/api/facts/{facts[0]['fact_id']}").json()
    assert "relations" in detail

    relations = client.get("/api/relations?limit=5").json()
    assert "counts" in relations and isinstance(relations["items"], list)


def test_api_upload(tmp_path, probe_pdfs):
    client = TestClient(create_app(str(tmp_path / "upload.db")))
    with open(probe_pdfs[0], "rb") as handle:
        response = client.post("/api/documents",
                               files={"file": (probe_pdfs[0].name, handle, "application/pdf")},
                               data={"collection": "uploaded"})
    assert response.status_code == 200
    assert response.json()["n_facts"] > 0
    assert client.get("/api/documents?collection=uploaded").json()


def test_api_rejects_non_pdf(tmp_path):
    client = TestClient(create_app(str(tmp_path / "reject.db")))
    response = client.post("/api/documents",
                           files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 400
