"""Command line entry point: ``python -m factlayer <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import ingest
from .store.db import Store

DEFAULT_DB = "factlayer.db"


def _collection_for(path: Path, given: str | None) -> str:
    """Default a collection from the containing folder, never the file name."""
    if given:
        return given
    parent = path.resolve().parent.name
    return parent or "default"


def cmd_ingest(args: argparse.Namespace) -> int:
    store = Store(args.db)
    targets: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        targets.extend(sorted(path.rglob("*.pdf")) if path.is_dir() else [path])
    if not targets:
        print("no PDFs found", file=sys.stderr)
        return 1
    for path in targets:
        report = ingest(store, path, _collection_for(path, args.collection),
                        subject=args.subject)
        if report.skipped:
            print(f"skip  {path.name}: {report.reason}")
            continue
        print(f"ok    {path.name}  [{report.collection}] subject={report.subject!r} "
              f"{report.n_pages}p  {report.n_facts} facts  "
              f"{report.n_relations} relations  {report.seconds:.1f}s")
    print(json.dumps(store.stats(), indent=2))
    store.close()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    store = Store(args.db)
    print(json.dumps(store.stats(), indent=2))
    store.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from .api.app import create_app
    uvicorn.run(create_app(args.db), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .report import render_cases
    store = Store(args.db)
    text = render_cases(store, collection=args.collection)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factlayer",
                                     description="A fact knowledge layer for PDFs")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite file (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="ingest PDFs (files or directories)")
    p.add_argument("paths", nargs="+")
    p.add_argument("--collection", help="scope facts are compared within")
    p.add_argument("--subject", help="override the detected document subject")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("stats", help="summarise the knowledge layer")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("serve", help="run the API and UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("report", help="write the four demonstration cases")
    p.add_argument("--collection")
    p.add_argument("--out")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
