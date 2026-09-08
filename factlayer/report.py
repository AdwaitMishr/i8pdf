"""Select and render the four cases the assignment asks to see.

Nothing here knows anything about the starter documents: each case is chosen by
querying the knowledge layer for the shape that case describes, so the same
command produces a report for any corpus that has been ingested.
"""

from __future__ import annotations

import sqlite3
import textwrap

from .store.db import Store

# Context dimensions that make an apparent conflict explainable rather than real.
_EXPLANATORY = ("consolidation", "vintage", "price_basis", "publication",
                "adjustment", "aggregation", "period")
_WRAP = 92


def _fact_block(store: Store, fact_id: str, side: str) -> str:
    fact = store.fact(fact_id)
    if fact is None:
        return f"- **{side}:** (missing fact {fact_id})"
    row = store.conn.execute(
        "SELECT title, filename FROM documents WHERE doc_id = ?", (fact.doc_id,)).fetchone()
    source = (row["title"] or row["filename"]) if row else fact.doc_id
    context = ", ".join(f"{k}={v}" for k, v in sorted(fact.context.items())) or "none stated"
    quote = "\n".join(textwrap.wrap(fact.evidence.strip(), _WRAP,
                                    initial_indent="  > ", subsequent_indent="  > "))
    return (f"- **{side}:** `{fact.display}` — {fact.metric}\n"
            f"  - source: {source}, page {fact.page_no} (characters "
            f"{fact.char_start}–{fact.char_end})\n"
            f"  - period: {fact.period_label or 'not stated'} · context: {context} · "
            f"confidence: {fact.confidence}\n"
            f"  - evidence:\n{quote}\n"
            + (f"  - caption recovered from another cell on the same page: "
               f"\"{fact.label_text}\"\n" if fact.label_text else ""))


def _render(store: Store, row: sqlite3.Row, title: str, note: str) -> str:
    explanation = "\n".join(textwrap.wrap(row["explanation"], _WRAP))
    return (f"### {title}\n\n{note}\n\n"
            f"**System verdict:** `{row['relation']}` "
            f"(confidence {row['confidence']}, found by {row['basis']})\n\n"
            f"**Reasoning**\n\n{explanation}\n\n"
            f"{_fact_block(store, row['left_id'], 'Fact A')}\n"
            f"{_fact_block(store, row['right_id'], 'Fact B')}\n")


def _pick(rows: list[sqlite3.Row], predicate=None) -> sqlite3.Row | None:
    for row in rows:
        if predicate is None or predicate(row):
            return row
    return None


def _same_metric(store: Store, row: sqlite3.Row) -> bool:
    """Both sides name the measure identically -- the clearest kind of comparison."""
    keys = store.conn.execute(
        "SELECT metric_key FROM facts WHERE fact_id IN (?, ?)",
        (row["left_id"], row["right_id"])).fetchall()
    return len(keys) == 2 and keys[0]["metric_key"] == keys[1]["metric_key"]


def _best(store: Store, rows: list[sqlite3.Row], predicate=None) -> sqlite3.Row | None:
    """Prefer a pair whose two sides use the same words, then fall back."""
    matching = [r for r in rows if (predicate is None or predicate(r))]
    return _pick(matching, lambda r: _same_metric(store, r)) or _pick(matching)


def render_cases(store: Store, collection: str | None = None) -> str:
    """Build the four-case report from whatever is currently in the store."""
    stats = store.stats()
    out = ["# Four cases, selected from the knowledge layer",
           "",
           f"Generated from {stats['documents']} documents, {stats['pages']} pages, "
           f"{stats['facts']} facts and {stats['relations']} relationships"
           + (f" (collection `{collection}`)." if collection else "."),
           "",
           "Every case below was chosen by querying for the *shape* of that case, "
           "not by naming a document or a figure.",
           ""]

    # 1 -- corroboration across documents, in different words.
    bridges = store.relations(kind="corroborates", cross_document=True,
                              collection=collection, limit=40)
    case1 = _pick(bridges, lambda r: r["basis"] == "value_bridge") or _pick(bridges)
    out.append("## Case 1 — Corroborated across documents, expressed differently\n")
    out.append(_render(store, case1,
        "Same measure, different wording and different unit scale",
        "Two documents state the same measure without using the same words. The link "
        "was not made from the wording: it was made because both figures are stated "
        "for the same subject and period, in the same unit, and agree to within the "
        "precision each document reports.") if case1 else
        "_No cross-document corroboration found in the current knowledge layer._\n")

    # 2 -- a real disagreement.
    conflicts = store.relations(kind="contradicts", cross_document=True,
                                collection=collection, limit=60) or \
        store.relations(kind="contradicts", collection=collection, limit=60)
    case2 = _best(store, conflicts)
    out.append("\n## Case 2 — A genuine or likely contradiction\n")
    out.append(_render(store, case2,
        "Same measure, same period, no differentiating context — different numbers",
        "The system only calls a pair contradictory after ruling out every context "
        "difference it models, and after checking that the gap is larger than the "
        "precision the two documents claim.") if case2 else
        "_No contradiction survived the context checks._\n")

    # 3 -- an apparent conflict that context explains.
    # Some of the most instructive reconciliations are inside one document -- a
    # filing states the same figure on a standalone and a consolidated basis in
    # consecutive sentences -- so both scopes are considered, cross-document first.
    reconciled = (store.relations(kind="reconciled_by_context", cross_document=True,
                                  collection=collection, limit=150)
                  + store.relations(kind="reconciled_by_context",
                                    collection=collection, limit=150))
    out.append("\n## Case 3 — An apparent contradiction explained by context\n")
    shown = 0
    for dimension in _EXPLANATORY:
        row = _best(store, reconciled,
                    lambda r, d=dimension: f'"{d}"' in r["dimensions"])
        if row is None:
            continue
        out.append(_render(store, row, f"Reconciled by *{dimension}*",
                           "The two figures differ, and the difference is accounted "
                           "for by the context each statement carries."))
        shown += 1
        if shown == 3:
            break
    if not shown:
        out.append("_No reconciled pair found._\n")

    # 4 -- the system auditing itself.
    out.append("\n## Case 4 — Extraction and reasoning failures\n")
    out.append(_diagnostics(store, collection))
    return "\n".join(out)


def _diagnostics(store: Store, collection: str | None) -> str:
    """Measurable weak spots, so the failure case is evidence rather than opinion."""
    where, args = "", []
    if collection:
        where = " JOIN documents d ON d.doc_id = f.doc_id WHERE d.collection = ?"
        args = [collection]

    def count(extra: str) -> int:
        clause = (where + (" AND " if where else " WHERE ") + extra) if extra else where
        return store.conn.execute(
            f"SELECT COUNT(*) AS n FROM facts f{clause}", args).fetchone()["n"]

    total = count("")
    no_period = count("f.period_label IS NULL")
    thin = count("LENGTH(f.metric_key) - LENGTH(REPLACE(f.metric_key,' ','')) = 0")
    uncomparable = count("f.unit IN ('count','unknown')")
    reconstructed = store.conn.execute(
        "SELECT COUNT(*) AS n FROM pages WHERE n_columns > 1").fetchone()["n"]

    lines = [
        "The system's own audit of where it is weakest. These counts are computed "
        "from the store, not asserted.\n",
        f"| Signal | Count | Share |",
        f"| --- | ---: | ---: |",
        f"| Facts extracted | {total} | 100% |",
        f"| Facts with no period attached | {no_period} | {no_period / max(total,1):.0%} |",
        f"| Facts whose metric is a single word | {thin} | {thin / max(total,1):.0%} |",
        f"| Facts in units that cannot be compared across documents | {uncomparable} | "
        f"{uncomparable / max(total,1):.0%} |",
        f"| Pages whose reading order had to be reconstructed | {reconstructed} | |",
        "",
        "A single-word metric is the main source of false conflict: two different "
        "series on one chart can both reduce to \"margin\". The weakest contradictions "
        "the system is currently asserting, ranked by how thin their metric phrase is:\n",
    ]
    rows = store.conn.execute(
        "SELECT r.explanation, lf.metric_key FROM relations r "
        "JOIN facts lf ON lf.fact_id = r.left_id "
        "WHERE r.relation = 'contradicts' "
        "ORDER BY LENGTH(lf.metric_key) ASC, r.confidence DESC LIMIT 3").fetchall()
    for row in rows:
        wrapped = "\n".join(textwrap.wrap(row["explanation"], _WRAP,
                                          initial_indent="- ", subsequent_indent="  "))
        lines.append(wrapped)
    return "\n".join(lines) + "\n"
