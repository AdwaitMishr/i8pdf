"""Export a representative slice of the knowledge layer as JSON.

Produces the files under ``samples/output/`` so the system can be evaluated --
and the demo video followed -- without running anything. Run after ingesting:

    python -m factlayer --db demo.db ingest data/starter
    python -m factlayer --db demo.db ingest data/probe/probe-note-delhivery.pdf \
        --collection delhivery --subject "Delhivery"
    python -m factlayer --db demo.db ingest data/probe/probe-note-india.pdf \
        --collection india-macroeconomy --subject "India"
    python scripts/export_samples.py --db demo.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from factlayer.api.app import fact_json          # noqa: E402
from factlayer.report import render_cases        # noqa: E402
from factlayer.store.db import Store             # noqa: E402

OUT = ROOT / "samples" / "output"
# Relationship kinds worth shipping as samples, and how many of each.
SLICES = {
    "corroborates": 8,
    "contradicts": 8,
    "reconciled_by_context": 10,
    "part_of": 5,
}


def relation_payload(store: Store, row) -> dict:
    left, right = store.fact(row["left_id"]), store.fact(row["right_id"])
    return {
        "relation": row["relation"],
        "confidence": row["confidence"],
        "found_by": row["basis"],
        "differs_on": json.loads(row["dimensions"]),
        "reasoning": row["explanation"],
        "left": fact_json(left, store) if left else None,
        "right": fact_json(right, store) if right else None,
    }


def write(name: str, payload) -> None:
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {path.relative_to(ROOT)}  ({path.stat().st_size // 1024} KB)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="demo.db")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)

    write("stats.json", {
        "summary": store.stats(),
        "documents": [dict(row) for row in store.documents()],
    })

    for kind, limit in SLICES.items():
        rows = store.relations(kind=kind, cross_document=True, limit=limit)
        rows += store.relations(kind=kind, limit=max(0, limit - len(rows)))
        seen, payload = set(), []
        for row in rows:
            if row["relation_id"] in seen:
                continue
            seen.add(row["relation_id"])
            payload.append(relation_payload(store, row))
        write(f"relations-{kind.replace('_', '-')}.json", payload)

    write("facts-sample.json",
          [fact_json(f, store) for f in store.facts(limit=40)])
    write("concepts.json", [dict(row) for row in store.concepts(40)])

    cases = OUT / "four-cases.md"
    cases.write_text(render_cases(store))
    print(f"wrote {cases.relative_to(ROOT)}  ({cases.stat().st_size // 1024} KB)")
    store.close()


if __name__ == "__main__":
    main()
