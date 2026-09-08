"""Reconnect a value to its label when the layout separated them.

Slides and financial tables routinely put the number in one cell and the thing
it measures in another -- a different line, the same column.  Reading order
alone cannot join them, so this module matches them by geometry.

The label is *not* spliced into the evidence text.  It is returned with its own
character span, so a fact assembled this way can cite both places on the page
and remain honest about where each half came from.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ingest.pdf import Cell

# How far from the value a label may sit, in multiples of the value's line height.
_Y_WINDOW = 3.5
# A label is mostly letters; "YoY: 11.5%" is another value, not a caption.
_MIN_ALPHA_RATIO = 0.6
_MIN_OVERLAP = 0.15


@dataclass(frozen=True)
class Label:
    text: str
    start: int
    end: int
    score: float


def _alpha_ratio(text: str) -> float:
    dense = [c for c in text if not c.isspace()]
    if not dense:
        return 0.0
    return sum(c.isalpha() for c in dense) / len(dense)


def is_label(cell: Cell) -> bool:
    if cell.numeric or _alpha_ratio(cell.text) < _MIN_ALPHA_RATIO:
        return False
    return any(len(w) >= 3 and w.isalpha() for w in cell.text.split())


def _x_overlap(a: Cell, b: Cell) -> float:
    """Jaccard overlap of the two x-ranges.

    Jaccard rather than plain overlap so a page-wide heading, which contains the
    value's column but is ten times wider, does not outscore the caption sitting
    directly under it.
    """
    inner = min(a.x1, b.x1) - max(a.x0, b.x0)
    if inner <= 0:
        return 0.0
    union = max(a.x1, b.x1) - min(a.x0, b.x0)
    return inner / union if union else 0.0


def cell_at(cells: list[Cell], start: int, end: int) -> Cell | None:
    """The cell whose span covers the value at ``start:end``."""
    for cell in cells:
        if cell.start <= start and end <= cell.end:
            return cell
    for cell in cells:
        if start < cell.end and cell.start < end:
            return cell
    return None


def label_for(cells: list[Cell], value: Cell) -> Label | None:
    """Best label for ``value``: same column, nearby line, mostly letters."""
    height = max(value.y1 - value.y0, 1.0)
    best: Label | None = None
    for cell in cells:
        if cell.line == value.line or not is_label(cell):
            continue
        gap = abs((cell.y0 + cell.y1) / 2 - (value.y0 + value.y1) / 2)
        if gap > height * _Y_WINDOW:
            continue
        overlap = _x_overlap(cell, value)
        if overlap < _MIN_OVERLAP:
            continue
        score = overlap - 0.15 * (gap / (height * _Y_WINDOW))
        if best is None or score > best.score:
            best = Label(cell.text, cell.start, cell.end, score)
    return best
