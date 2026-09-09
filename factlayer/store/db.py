"""SQLite storage for documents, facts, concepts and relations.

Kept deliberately boring -- one file, no server, full-text search from the
standard library -- because the interesting part of this project is how facts
are found and compared, not where the bytes live.

Ingest is incremental: a new document's facts are compared against the facts
already stored for its collection, and only the new concepts and new relations
are written.  Adding a seventh PDF does not rebuild the first six.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from ..knowledge.concepts import Concept, ConceptIndex
from ..models import Fact, Relation, SourceDocument

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    title       TEXT NOT NULL,
    sha256      TEXT NOT NULL UNIQUE,
    n_pages     INTEGER NOT NULL,
    collection  TEXT NOT NULL DEFAULT 'default',
    subject     TEXT,
    subject_key TEXT,
    published_on TEXT,
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    doc_id      TEXT NOT NULL,
    page_no     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    n_columns   INTEGER NOT NULL DEFAULT 1,
    printed_label TEXT,
    PRIMARY KEY (doc_id, page_no)
);
CREATE TABLE IF NOT EXISTS facts (
    fact_id     TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    page_no     INTEGER NOT NULL,
    char_start  INTEGER NOT NULL,
    char_end    INTEGER NOT NULL,
    value_start INTEGER NOT NULL,
    value_end   INTEGER NOT NULL,
    evidence    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    subject     TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    subject_source TEXT NOT NULL DEFAULT 'document',
    metric      TEXT NOT NULL,
    metric_key  TEXT NOT NULL,
    concept_id  TEXT,
    value       REAL,
    unit        TEXT NOT NULL,
    display     TEXT NOT NULL,
    precision   REAL NOT NULL DEFAULT 0,
    period_label TEXT,
    period_kind TEXT,
    period_start TEXT,
    period_end  TEXT,
    context     TEXT NOT NULL DEFAULT '{}',
    label_text  TEXT,
    label_start INTEGER,
    label_end   INTEGER,
    state       TEXT,
    effective_on TEXT,
    confidence  REAL NOT NULL,
    extractor   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_doc ON facts(doc_id);
CREATE INDEX IF NOT EXISTS facts_concept ON facts(concept_id, unit);
CREATE TABLE IF NOT EXISTS relations (
    relation_id TEXT PRIMARY KEY,
    left_id     TEXT NOT NULL,
    right_id    TEXT NOT NULL,
    relation    TEXT NOT NULL,
    confidence  REAL NOT NULL,
    explanation TEXT NOT NULL,
    dimensions  TEXT NOT NULL DEFAULT '[]',
    basis       TEXT NOT NULL DEFAULT 'concept'
);
CREATE INDEX IF NOT EXISTS relations_left ON relations(left_id);
CREATE INDEX IF NOT EXISTS relations_right ON relations(right_id);
CREATE INDEX IF NOT EXISTS relations_kind ON relations(relation, confidence);
CREATE TABLE IF NOT EXISTS concepts (
    concept_id    TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    canonical_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS concept_keys (
    metric_key TEXT PRIMARY KEY,
    concept_id TEXT NOT NULL,
    surface    TEXT NOT NULL,
    uses       INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS token_frequency (
    token TEXT PRIMARY KEY,
    df    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
    left_key  TEXT NOT NULL,
    right_key TEXT NOT NULL,
    votes     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (left_key, right_key)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE VIEW IF NOT EXISTS concept_support AS
    SELECT concept_id, COUNT(*) AS n FROM facts
    WHERE concept_id IS NOT NULL GROUP BY concept_id;
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    metric, evidence, subject, fact_id UNINDEXED, tokenize='porter'
);
"""


# Beyond this many facts a concept is "well attested"; more does not mean better.
SUPPORT_CAP = 12
# Beyond three words a metric phrase is specific enough; more is not better.
WORD_CAP = 3


def _as_date(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


class Store:
    """Everything the knowledge layer persists, behind a small API."""

    def __init__(self, path: str | Path = "factlayer.db") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- documents -------------------------------------------------------
    def document_by_hash(self, sha256: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM documents WHERE sha256 = ?", (sha256,)).fetchone()

    def add_document(self, doc: SourceDocument, pages: list[tuple[int, str, int, str | None]]) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO documents
               (doc_id, filename, title, sha256, n_pages, collection, subject,
                subject_key, published_on, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (doc.doc_id, doc.filename, doc.title, doc.sha256, doc.n_pages,
             doc.collection, doc.subject, doc.subject_key,
             doc.published_on.isoformat() if doc.published_on else None,
             datetime.now().isoformat(timespec="seconds")))
        self.conn.executemany(
            "INSERT OR REPLACE INTO pages (doc_id, page_no, text, n_columns, printed_label)"
            " VALUES (?,?,?,?,?)",
            [(doc.doc_id, no, text, cols, label) for no, text, cols, label in pages])
        self.conn.commit()

    def documents(self, collection: str | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT d.*, (SELECT COUNT(*) FROM facts f WHERE f.doc_id = d.doc_id)"
               " AS n_facts FROM documents d")
        args: tuple = ()
        if collection:
            sql += " WHERE d.collection = ?"
            args = (collection,)
        return self.conn.execute(sql + " ORDER BY d.ingested_at DESC", args).fetchall()

    def page(self, doc_id: str, page_no: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM pages WHERE doc_id = ? AND page_no = ?",
            (doc_id, page_no)).fetchone()

    def delete_document(self, doc_id: str) -> None:
        fact_ids = [r["fact_id"] for r in self.conn.execute(
            "SELECT fact_id FROM facts WHERE doc_id = ?", (doc_id,))]
        self.conn.executemany("DELETE FROM relations WHERE left_id = ? OR right_id = ?",
                              [(f, f) for f in fact_ids])
        self.conn.executemany("DELETE FROM facts_fts WHERE fact_id = ?",
                              [(f,) for f in fact_ids])
        self.conn.execute("DELETE FROM facts WHERE doc_id = ?", (doc_id,))
        self.conn.execute("DELETE FROM pages WHERE doc_id = ?", (doc_id,))
        self.conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        self.conn.commit()

    # -- facts -----------------------------------------------------------
    def save_facts(self, facts: list[Fact]) -> None:
        rows = [(
            f.fact_id, f.doc_id, f.page_no, f.char_start, f.char_end,
            f.value_start, f.value_end, f.evidence, f.kind, f.subject, f.subject_key,
            f.subject_source, f.metric, f.metric_key, f.concept_id, f.value, f.unit, f.display,
            f.precision, f.period_label, f.period_kind,
            f.period_start.isoformat() if f.period_start else None,
            f.period_end.isoformat() if f.period_end else None,
            json.dumps(f.context), f.label_text, f.label_start, f.label_end,
            f.state, f.effective_on.isoformat() if f.effective_on else None,
            f.confidence, f.extractor,
        ) for f in facts]
        self.conn.executemany(
            "INSERT OR REPLACE INTO facts VALUES (" + ",".join("?" * 31) + ")", rows)
        self.conn.executemany(
            "INSERT INTO facts_fts (metric, evidence, subject, fact_id) VALUES (?,?,?,?)",
            [(f.metric, f.evidence, f.subject, f.fact_id) for f in facts])
        self.conn.commit()

    def facts(self, collection: str | None = None, doc_id: str | None = None,
              limit: int | None = None) -> list[Fact]:
        sql = "SELECT f.* FROM facts f JOIN documents d ON d.doc_id = f.doc_id"
        clauses, args = [], []
        if collection:
            clauses.append("d.collection = ?")
            args.append(collection)
        if doc_id:
            clauses.append("f.doc_id = ?")
            args.append(doc_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY f.confidence DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._as_fact(r) for r in self.conn.execute(sql, args)]

    def fact(self, fact_id: str) -> Fact | None:
        row = self.conn.execute("SELECT * FROM facts WHERE fact_id = ?",
                                (fact_id,)).fetchone()
        return self._as_fact(row) if row else None

    def search_facts(self, query: str, limit: int = 50) -> list[Fact]:
        rows = self.conn.execute(
            "SELECT fact_id FROM facts_fts WHERE facts_fts MATCH ? LIMIT ?",
            (query, limit)).fetchall()
        return [f for f in (self.fact(r["fact_id"]) for r in rows) if f]

    @staticmethod
    def _as_fact(row: sqlite3.Row) -> Fact:
        return Fact(
            doc_id=row["doc_id"], page_no=row["page_no"],
            char_start=row["char_start"], char_end=row["char_end"],
            evidence=row["evidence"], value_start=row["value_start"],
            value_end=row["value_end"], kind=row["kind"], subject=row["subject"],
            subject_key=row["subject_key"], subject_source=row["subject_source"],
            metric=row["metric"],
            metric_key=row["metric_key"], value=row["value"], unit=row["unit"],
            display=row["display"], precision=row["precision"],
            period_label=row["period_label"], period_kind=row["period_kind"],
            period_start=_as_date(row["period_start"]),
            period_end=_as_date(row["period_end"]),
            label_text=row["label_text"], label_start=row["label_start"],
            label_end=row["label_end"], context=json.loads(row["context"]),
            state=row["state"], effective_on=_as_date(row["effective_on"]),
            confidence=row["confidence"], extractor=row["extractor"],
            concept_id=row["concept_id"], fact_id=row["fact_id"])

    # -- relations -------------------------------------------------------
    def save_relations(self, relations: list[Relation]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO relations VALUES (?,?,?,?,?,?,?,?)",
            [(r.relation_id, r.left_id, r.right_id, r.relation, r.confidence,
              r.explanation, json.dumps(r.dimensions), r.basis) for r in relations])
        self.conn.commit()

    def relations(self, kind: str | None = None, fact_id: str | None = None,
                  cross_document: bool = False, collection: str | None = None,
                  dimension: str | None = None,
                  limit: int = 100, offset: int = 0) -> list[sqlite3.Row]:
        # Ranked by confidence weighted by how well attested each side's concept
        # is.  A phrase the corpus keeps using is a real measure; a phrase that
        # appears once is usually a mis-parse, and ordering on confidence alone
        # floats those to the top because a clean number parse scores 0.95.
        sql = ("SELECT r.*, "
               "  lf.doc_id AS left_doc, rf.doc_id AS right_doc, "
               "  ld.title AS left_title, rd.title AS right_title, "
               "  ld.collection AS collection, "
               "  COALESCE(lc.n, 1) AS left_support, COALESCE(rc.n, 1) AS right_support "
               "FROM relations r "
               "JOIN facts lf ON lf.fact_id = r.left_id "
               "JOIN facts rf ON rf.fact_id = r.right_id "
               "JOIN documents ld ON ld.doc_id = lf.doc_id "
               "JOIN documents rd ON rd.doc_id = rf.doc_id "
               "LEFT JOIN concept_support lc ON lc.concept_id = lf.concept_id "
               "LEFT JOIN concept_support rc ON rc.concept_id = rf.concept_id")
        clauses, args = [], []
        if kind:
            clauses.append("r.relation = ?")
            args.append(kind)
        if fact_id:
            clauses.append("(r.left_id = ? OR r.right_id = ?)")
            args += [fact_id, fact_id]
        if cross_document:
            clauses.append("lf.doc_id != rf.doc_id")
        if collection:
            clauses.append("ld.collection = ?")
            args.append(collection)
        if dimension:
            # dimensions is a JSON array of names; matching the quoted name
            # avoids "period" also matching a hypothetical "period_basis".
            clauses.append("r.dimensions LIKE ?")
            args.append(f'%"{dimension}"%')
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # Support says the phrase is a real measure; word count says it is a
        # specific one.  A bare "growth" is used constantly and names nothing.
        words = ("(LENGTH({t}.metric_key) - LENGTH(REPLACE({t}.metric_key,' ','')) + 1)")
        sql += (f" ORDER BY r.confidence"
                f" * MIN(COALESCE(lc.n,1), ?) * MIN(COALESCE(rc.n,1), ?)"
                f" * MIN({words.format(t='lf')}, ?) * MIN({words.format(t='rf')}, ?)"
                f" DESC, r.confidence DESC LIMIT ? OFFSET ?")
        return self.conn.execute(
            sql, (*args, SUPPORT_CAP, SUPPORT_CAP, WORD_CAP, WORD_CAP,
                  limit, offset)).fetchall()

    def relation_counts(self, collection: str | None = None) -> dict[str, int]:
        sql = ("SELECT r.relation, COUNT(*) AS n FROM relations r "
               "JOIN facts lf ON lf.fact_id = r.left_id "
               "JOIN documents ld ON ld.doc_id = lf.doc_id")
        args: tuple = ()
        if collection:
            sql += " WHERE ld.collection = ?"
            args = (collection,)
        sql += " GROUP BY r.relation"
        return {r["relation"]: r["n"] for r in self.conn.execute(sql, args)}

    # -- concepts --------------------------------------------------------
    def load_concept_index(self) -> ConceptIndex:
        index = ConceptIndex()
        for row in self.conn.execute("SELECT * FROM concepts"):
            index.concepts[row["concept_id"]] = Concept(
                row["concept_id"], row["label"], row["canonical_key"])
        for row in self.conn.execute("SELECT * FROM concept_keys"):
            concept = index.concepts.get(row["concept_id"])
            if concept is None:
                continue
            concept.keys.add(row["metric_key"])
            concept.surfaces[row["metric_key"]] = Counter({row["surface"]: row["uses"]})
            index.of_key[row["metric_key"]] = row["concept_id"]
            index._seen.add(row["metric_key"])
        for row in self.conn.execute("SELECT * FROM token_frequency"):
            index.document_frequency[row["token"]] = row["df"]
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'n_keys'").fetchone()
        index.n_keys = int(row["value"]) if row else len(index.of_key)
        for concept in index.concepts.values():
            for token in set(concept.canonical_key.split()):
                index._by_token[token].add(concept.concept_id)
        return index

    def save_concept_index(self, index: ConceptIndex) -> None:
        self.conn.execute("DELETE FROM concepts")
        self.conn.execute("DELETE FROM concept_keys")
        self.conn.execute("DELETE FROM token_frequency")
        self.conn.executemany(
            "INSERT INTO concepts VALUES (?,?,?)",
            [(c.concept_id, c.label, c.canonical_key) for c in index.concepts.values()])
        rows = []
        for concept in index.concepts.values():
            for key, surfaces in concept.surfaces.items():
                surface = surfaces.most_common(1)[0][0] if surfaces else concept.label
                rows.append((key, concept.concept_id, surface, concept.uses(key)))
        self.conn.executemany("INSERT OR REPLACE INTO concept_keys VALUES (?,?,?,?)", rows)
        self.conn.executemany(
            "INSERT OR REPLACE INTO token_frequency VALUES (?,?)",
            list(index.document_frequency.items()))
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('n_keys', ?)",
                          (str(index.n_keys),))
        self.conn.commit()

    def concepts(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT c.concept_id, c.label, COUNT(f.fact_id) AS n_facts "
            "FROM concepts c LEFT JOIN facts f ON f.concept_id = c.concept_id "
            "GROUP BY c.concept_id ORDER BY n_facts DESC LIMIT ?", (limit,)).fetchall()

    # -- aliases ---------------------------------------------------------
    def record_aliases(self, pairs: list[tuple[str, str]]) -> None:
        for left, right in pairs:
            self.conn.execute(
                "INSERT INTO aliases (left_key, right_key, votes) VALUES (?,?,1) "
                "ON CONFLICT(left_key, right_key) DO UPDATE SET votes = votes + 1",
                (left, right))
        self.conn.commit()

    def aliases(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM aliases ORDER BY votes DESC").fetchall()

    # -- summary ---------------------------------------------------------
    def stats(self) -> dict:
        counts = Counter()
        for table in ("documents", "pages", "facts", "relations", "concepts"):
            counts[table] = self.conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        return {
            "documents": counts["documents"], "pages": counts["pages"],
            "facts": counts["facts"], "relations": counts["relations"],
            "concepts": counts["concepts"],
            "by_relation": self.relation_counts(),
            "collections": [r["collection"] for r in self.conn.execute(
                "SELECT DISTINCT collection FROM documents ORDER BY collection")],
        }
