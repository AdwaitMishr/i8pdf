"""Detect the context that makes two different numbers both correct.

"Standalone" versus "consolidated", an advance estimate versus an actual, real
versus nominal: these are the dimensions along which honest figures diverge.
Pulling them out of the sentence -- and out of the metric phrase -- is what lets
the reasoning layer say *why* two facts differ instead of just that they do.

The vocabulary lives in ``lexicon/qualifiers.json`` and can be extended, or
replaced wholesale via the ``FACTLAYER_LEXICON`` environment variable, without
touching this module.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DEFAULT_LEXICON = Path(__file__).resolve().parent.parent / "lexicon" / "qualifiers.json"

# A qualifier is usually wrapped in filler ("on a standalone basis", "at constant
# prices").  Absorbing the filler keeps it out of the extracted metric phrase.
_LEAD = r"(?:\b(?:on|at|in|under|per|as)\s+)?(?:\b(?:a|an|the)\s+)?"
_TRAIL = r"(?:\s+(?:basis|terms|prices|price))?(?:\s+of\s+\w+)?"


@dataclass(frozen=True)
class Qualifier:
    dimension: str
    value: str
    raw: str
    span: tuple[int, int]


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, dict[str, list[str]]]:
    data = json.loads(Path(path).read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=4)
def _compiled(path: str) -> list[tuple[str, str, re.Pattern]]:
    out: list[tuple[str, str, re.Pattern]] = []
    for dimension, values in _load(path).items():
        for value, phrases in values.items():
            for phrase in phrases:
                body = re.escape(phrase).replace(r"\ ", r"\s+")
                out.append((dimension, value,
                            re.compile(rf"{_LEAD}\b{body}s?\b{_TRAIL}", re.IGNORECASE)))
    # Longest surface form first so "first advance estimate" beats "estimate".
    out.sort(key=lambda t: -len(t[2].pattern))
    return out


def lexicon_path() -> str:
    return os.environ.get("FACTLAYER_LEXICON") or str(_DEFAULT_LEXICON)


def dimensions() -> list[str]:
    return list(_load(lexicon_path()))


def find_qualifiers(text: str) -> list[Qualifier]:
    """All context qualifiers in ``text``, longest match first, non-overlapping."""
    found: list[Qualifier] = []
    taken: list[tuple[int, int]] = []
    for dimension, value, rx in _compiled(lexicon_path()):
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            found.append(Qualifier(dimension, value, m.group().strip(), (s, e)))
            taken.append((s, e))
    return sorted(found, key=lambda q: q.span)


def as_context(qualifiers: list[Qualifier]) -> dict[str, str]:
    """Collapse to one value per dimension (first mention wins)."""
    context: dict[str, str] = {}
    for q in qualifiers:
        context.setdefault(q.dimension, q.value)
    return context
