"""Split page text into the units a fact can be attached to.

A unit is a sentence or a table row, carrying the character offsets it occupies
in the page text.  Those offsets are the anchor for every piece of evidence the
system shows, so segmentation never rewrites the text -- it only slices it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Abbreviations whose full stop does not end a sentence.  Domain-neutral plus
# the handful of report-writing conventions that appear in filings everywhere.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "shri", "smt", "hon", "st", "jr", "sr",
    "ltd", "pvt", "inc", "corp", "co", "plc", "llp", "cos",
    "no", "nos", "vol", "art", "sec", "cl", "para", "fig", "figs", "tbl", "ch",
    "rs", "cr", "approx", "est", "avg", "yr", "qtr", "mn", "bn", "tn",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
    "e.g", "i.e", "viz", "etc", "cf", "vs", "al",
    "u.s", "u.k", "p.a", "w.e.f",
}
_MAX_UNIT_CHARS = 700

_BOUNDARY = re.compile(r"[.!?]['\")\]]?(?=\s)")
_WORD_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*)\.?['\")\]]?$")
_NUMERIC_TOKEN = re.compile(r"^[(\[]?[₹$€£]?-?[\d,]+(?:\.\d+)?\)?%?$")
_SOFT_BREAK = re.compile(r"(?<=;)\s|(?<=:)\s|\n")


@dataclass(frozen=True)
class Unit:
    text: str
    start: int          # inclusive offset into the page text
    end: int            # exclusive
    kind: str           # "sentence" | "row"

    @property
    def length(self) -> int:
        return self.end - self.start


def _is_row(text: str) -> bool:
    """A line of mostly bare numbers is a table row, not a sentence."""
    tokens = text.split()
    if len(tokens) < 2:
        return False
    numeric = sum(1 for t in tokens if _NUMERIC_TOKEN.match(t))
    return numeric >= 2 and not re.search(r"[.!?]\s", text)


def _ends_sentence(text: str, dot: int) -> bool:
    """Is the full stop at ``dot`` a sentence boundary rather than an abbreviation?"""
    head = text[:dot + 1]
    m = _WORD_BEFORE.search(head)
    if not m:
        return True
    word = m.group(1).rstrip(".").lower()
    if word in _ABBREVIATIONS:
        return False
    # A single letter before the stop is an initial ("Mr. S. Barua").
    return not (len(word) == 1 and word.isalpha())


def _split_sentences(text: str, offset: int) -> list[tuple[str, int]]:
    spans: list[tuple[str, int]] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        if not _ends_sentence(text, m.start()):
            continue
        end = m.end()
        chunk = text[start:end]
        if chunk.strip():
            spans.append((chunk, offset + start))
        start = end
    tail = text[start:]
    if tail.strip():
        spans.append((tail, offset + start))
    return spans


def _cap_length(text: str, start: int) -> list[tuple[str, int]]:
    """Break over-long units on soft punctuation so evidence stays quotable."""
    if len(text) <= _MAX_UNIT_CHARS:
        return [(text, start)]
    out, cursor = [], 0
    for m in _SOFT_BREAK.finditer(text):
        if m.end() - cursor >= _MAX_UNIT_CHARS // 2:
            out.append((text[cursor:m.end()], start + cursor))
            cursor = m.end()
    out.append((text[cursor:], start + cursor))
    return [(t, s) for t, s in out if t.strip()]


def iter_units(page_text: str) -> list[Unit]:
    """Slice a page into sentences and table rows with exact offsets."""
    units: list[Unit] = []
    for block in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", page_text):
        text, offset = block.group(), block.start()
        pieces = ([(text, offset)] if _is_row(text)
                  else _split_sentences(text, offset))
        kind = "row" if _is_row(text) else "sentence"
        for piece, piece_start in pieces:
            for capped, capped_start in _cap_length(piece, piece_start):
                lead = len(capped) - len(capped.lstrip())
                body = capped.strip()
                if not body:
                    continue
                start = capped_start + lead
                units.append(Unit(body, start, start + len(body), kind))
    return units


def verify(page_text: str, units: list[Unit]) -> None:
    """Fail loudly if any unit's offsets do not reproduce its text."""
    for u in units:
        if page_text[u.start:u.end] != u.text:
            raise AssertionError(f"offset drift at {u.start}:{u.end}: {u.text[:60]!r}")
