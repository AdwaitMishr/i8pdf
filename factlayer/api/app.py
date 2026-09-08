"""HTTP API and UI for the fact knowledge layer.

Endpoints are shaped around the three questions the system exists to answer:
what facts are in these documents, where exactly does each one come from, and
how do they relate to each other.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..llm import build_refiner
from ..models import Fact
from ..pipeline import ingest
from ..store.db import Store

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def fact_json(fact: Fact, store: Store | None = None) -> dict:
    """A fact plus the offsets needed to highlight it inside its evidence."""
    payload = {
        "fact_id": fact.fact_id,
        "doc_id": fact.doc_id,
        "page_no": fact.page_no,
        "subject": fact.subject,
        "subject_source": fact.subject_source,
        "metric": fact.metric,
        "metric_key": fact.metric_key,
        "concept_id": fact.concept_id,
        "value": fact.value,
        "unit": fact.unit,
        "display": fact.display,
        "period": fact.period_label,
        "period_kind": fact.period_kind,
        "context": fact.context,
        "confidence": fact.confidence,
        "extractor": fact.extractor,
        "evidence": {
            "text": fact.evidence,
            "char_start": fact.char_start,
            "char_end": fact.char_end,
            # Offsets relative to the evidence string, for highlighting.
            "value_offset": [fact.value_start - fact.char_start,
                             fact.value_end - fact.char_start],
        },
    }
    if fact.label_text is not None:
        payload["evidence"]["label"] = {
            "text": fact.label_text,
            "char_start": fact.label_start,
            "char_end": fact.label_end,
        }
    if store is not None:
        row = store.conn.execute(
            "SELECT title, filename, collection FROM documents WHERE doc_id = ?",
            (fact.doc_id,)).fetchone()
        if row:
            payload["document"] = {"title": row["title"], "filename": row["filename"],
                                   "collection": row["collection"]}
    return payload


def create_app(db_path: str = "factlayer.db") -> FastAPI:
    app = FastAPI(title="Fact Knowledge Layer", version="0.1.0")
    store = Store(db_path)

    @app.get("/api/stats")
    def stats() -> dict:
        return store.stats()

    @app.get("/api/documents")
    def documents(collection: str | None = None) -> list[dict]:
        return [dict(row) for row in store.documents(collection)]

    @app.post("/api/documents")
    async def upload(file: UploadFile = File(...),
                     collection: str = Form("default"),
                     subject: str | None = Form(None)) -> dict:
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "only PDF uploads are supported")
        tmp = Path(tempfile.mkdtemp()) / Path(file.filename).name
        try:
            size = 0
            with tmp.open("wb") as out:
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "file too large")
                    out.write(chunk)
            report = ingest(store, tmp, collection=collection,
                            subject=subject or None, refiner=build_refiner())
            return report.as_dict()
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    @app.delete("/api/documents/{doc_id}")
    def remove(doc_id: str) -> dict:
        store.delete_document(doc_id)
        return {"deleted": doc_id}

    @app.get("/api/facts")
    def facts(q: str | None = None, doc_id: str | None = None,
              collection: str | None = None,
              limit: int = Query(50, ge=1, le=500)) -> list[dict]:
        found = (store.search_facts(q, limit) if q
                 else store.facts(collection=collection, doc_id=doc_id, limit=limit))
        return [fact_json(f, store) for f in found]

    @app.get("/api/facts/{fact_id}")
    def fact_detail(fact_id: str) -> dict:
        fact = store.fact(fact_id)
        if fact is None:
            raise HTTPException(404, "no such fact")
        related = []
        for row in store.relations(fact_id=fact_id, limit=50):
            other_id = row["right_id"] if row["left_id"] == fact_id else row["left_id"]
            other = store.fact(other_id)
            related.append({
                "relation": row["relation"], "confidence": row["confidence"],
                "explanation": row["explanation"], "basis": row["basis"],
                "dimensions": row["dimensions"],
                "other": fact_json(other, store) if other else None,
            })
        return {**fact_json(fact, store), "relations": related}

    @app.get("/api/relations")
    def relations(type: str | None = None, collection: str | None = None,
                  cross_document: bool = False,
                  limit: int = Query(30, ge=1, le=200),
                  offset: int = Query(0, ge=0)) -> dict:
        rows = store.relations(kind=type, collection=collection,
                               cross_document=cross_document,
                               limit=limit, offset=offset)
        items = []
        for row in rows:
            left, right = store.fact(row["left_id"]), store.fact(row["right_id"])
            if not left or not right:
                continue
            items.append({
                "relation_id": row["relation_id"], "relation": row["relation"],
                "confidence": row["confidence"], "explanation": row["explanation"],
                "basis": row["basis"], "dimensions": row["dimensions"],
                "left": fact_json(left, store), "right": fact_json(right, store),
            })
        return {"counts": store.relation_counts(collection), "items": items}

    @app.get("/api/concepts")
    def concepts(limit: int = Query(60, ge=1, le=500)) -> list[dict]:
        return [dict(row) for row in store.concepts(limit)]

    @app.get("/api/aliases")
    def aliases() -> list[dict]:
        return [dict(row) for row in store.aliases()]

    @app.get("/api/pages/{doc_id}/{page_no}")
    def page(doc_id: str, page_no: int) -> dict:
        row = store.page(doc_id, page_no)
        if row is None:
            raise HTTPException(404, "no such page")
        return dict(row)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
