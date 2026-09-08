"""PDF -> normalised page text with a reconstructed reading order.

Why this module is more than a `get_text()` call: on a two-column institutional
report, naive extraction interleaves the columns, welding half of one sentence
onto half of another.  Facts mined from those sentences would be "grounded" in
text that never existed on the page.  So the reading order is rebuilt from line
geometry before any text is concatenated:

    lines -> column detection (whitespace gutter) -> band ordering -> paragraphs

Every downstream fact points at a ``(page, char_start, char_end)`` span of the
page text produced here, so normalisation happens once, on the way in.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pymupdf

from .text import normalise

_MIN_LINE_CHARS = 2
_BIN_PT = 2.0
# Gutter search is limited to the middle of the content width; whitespace found
# hard against a margin is a margin, not a column separator.
_GUTTER_SEARCH_BAND = (0.30, 0.70)
_GUTTER_MIN_WIDTH_FRAC = 0.008
_GUTTER_MIN_WIDTH_PT = 4.0
_MIN_LINES_TO_SPLIT = 8
# Share of lines allowed to cross a channel before it stops counting as a gutter.
_GUTTER_CROSSING_TOLERANCE = 0.08
# Vertical slice treated as body text (excludes running headers and footers).
_BODY_BAND = (0.06, 0.93)
# Paragraph-assembly geometry.
_PARA_Y_GAP = 0.6        # max gap to the next line, in line heights
_PARA_OVERLAP = 0.5      # bboxes may overlap this much and still be separate lines
_PARA_OUTDENT = 10.0     # how far left a continuation line may start
_PARA_INDENT = 26.0      # how far right it may start (hanging indents, bullets)
_WRAP_SLACK_FRAC = 0.12  # tolerance when deciding a line reached the margin
_CELL_GAP = 7.0          # horizontal gap that starts a new cell on the same line

LEFT, RIGHT, FULL = 0, 1, 2
# Two levels of splitting cover imposed spreads (2 pages x 2 columns).
_MAX_SPLIT_DEPTH = 2
# A line filling this share of the column counts as wrapped prose, and a column
# needs this share of such lines before a narrow gutter is treated as a break.
_WIDE_LINE_FILL = 0.60
_TEXT_COLUMN_SHARE = 0.25
# A channel this wide separates panels or spread halves, not text columns.
_PANEL_GUTTER_PT = 30.0
_PANEL_GUTTER_FRAC = 0.04


@dataclass
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class Cell:
    """A run of text separated from its neighbours by a visible gap.

    Slides and tables put a value in one cell and its label in another, on a
    different line but the same horizontal position.  Keeping cell geometry --
    with offsets into the page text -- is what lets the extractor reconnect them
    without inventing text that is not on the page.
    """
    text: str
    start: int          # offset into Page.text
    end: int
    x0: float
    x1: float
    y0: float
    y1: float
    line: int           # index of the visual line this cell belongs to
    numeric: bool


@dataclass
class Page:
    page_no: int                        # 1-indexed position within this PDF
    text: str                           # normalised; fact offsets index into this
    n_columns: int
    printed_label: str | None = None    # page number printed by the publisher
    cells: list["Cell"] = field(default_factory=list)


@dataclass
class Document:
    doc_id: str
    filename: str
    title: str
    sha256: str
    n_pages: int
    pages: list[Page] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    published_on: date | None = None


# --------------------------------------------------------------------------
# Column detection
# --------------------------------------------------------------------------

def _find_gutter(lines: list[Line]) -> tuple[float, float] | None:
    """Locate the widest vertical whitespace channel in the middle of the page.

    Coverage is counted rather than flagged, so a single wide line (a caption, a
    stray heading) cannot erase an otherwise obvious gutter.
    """
    if not lines:
        return None
    x0 = min(l.x0 for l in lines)
    x1 = max(l.x1 for l in lines)
    span = x1 - x0
    if span <= 0:
        return None

    n_bins = max(int(span / _BIN_PT), 1)
    coverage = [0] * n_bins
    for l in lines:
        lo = max(0, int((l.x0 - x0) / _BIN_PT))
        hi = min(n_bins, int((l.x1 - x0) / _BIN_PT) + 1)
        for i in range(lo, hi):
            coverage[i] += 1

    tolerance = int(len(lines) * _GUTTER_CROSSING_TOLERANCE)
    lo_bound = n_bins * _GUTTER_SEARCH_BAND[0]
    hi_bound = n_bins * _GUTTER_SEARCH_BAND[1]
    best_len, best_mid, run_start = 0, None, None
    for i in range(n_bins + 1):
        empty = i < n_bins and coverage[i] <= tolerance
        if empty and run_start is None:
            run_start = i
        elif not empty and run_start is not None:
            mid = (run_start + i) / 2
            if lo_bound <= mid <= hi_bound and (i - run_start) > best_len:
                best_len, best_mid = i - run_start, mid
            run_start = None

    min_width = max(_GUTTER_MIN_WIDTH_PT, span * _GUTTER_MIN_WIDTH_FRAC)
    if best_mid is None or best_len * _BIN_PT < min_width:
        return None
    return x0 + best_mid * _BIN_PT, best_len * _BIN_PT


def _is_text_column(members: list[Line]) -> bool:
    """Do these lines look like wrapped prose rather than table cells?

    Measured as the share of lines that run most of the column's width.  A share
    rather than a median, because a real column of prose is routinely mixed with
    a table: the prose still fills the column even when the cells around it do
    not, and the median would be dragged down by the cells alone.
    """
    if len(members) < 4:
        return False
    extent = max(l.x1 for l in members) - min(l.x0 for l in members)
    if extent <= 0:
        return False
    wide = sum(1 for l in members if l.width >= extent * _WIDE_LINE_FILL)
    return wide / len(members) >= _TEXT_COLUMN_SHARE


def _order_group(idx: list[int], lines: list[Line], page_w: float, page_h: float,
                 depth: int, groups: dict[int, int]) -> tuple[list[int], int]:
    """Recursively split a set of lines into columns and return reading order.

    One split is not enough in practice: publisher PDFs are often imposed as
    two-page spreads, and each half then carries its own two columns.  So the
    widest gutter is found, the region is divided, and each side is split again
    until no convincing gutter remains.  ``groups`` is filled with the leaf
    column each line ends up in, which paragraph assembly needs later.
    """
    idx = sorted(idx, key=lambda i: (round(lines[i].y0, 1), lines[i].x0))

    def as_leaf() -> tuple[list[int], int]:
        leaf = len(set(groups.values()))
        for i in idx:
            groups.setdefault(i, leaf)
        return idx, 0

    if depth >= _MAX_SPLIT_DEPTH or len(idx) < _MIN_LINES_TO_SPLIT:
        return as_leaf()

    body = [i for i in idx
            if _BODY_BAND[0] * page_h < lines[i].y0 < _BODY_BAND[1] * page_h]
    if len(body) < _MIN_LINES_TO_SPLIT:
        return as_leaf()
    found = _find_gutter([lines[i] for i in body])
    if found is None:
        return as_leaf()
    split, gutter_width = found
    x_extent = (max(lines[i].x1 for i in body) - min(lines[i].x0 for i in body)) or 1.0

    margin = page_w * 0.005
    side: dict[int, int] = {}
    for i in idx:
        if lines[i].x1 <= split + margin:
            side[i] = LEFT
        elif lines[i].x0 >= split - margin:
            side[i] = RIGHT
        else:
            side[i] = FULL

    body_set = set(body)
    n_left = sum(1 for i in body_set if side[i] == LEFT)
    n_right = sum(1 for i in body_set if side[i] == RIGHT)
    spanning = sum(lines[i].width for i in idx if side[i] == FULL)
    total = sum(lines[i].width for i in idx) or 1.0

    # A genuine column break carries substantial text on both sides and few
    # lines crossing the gutter; otherwise the channel came from a wide table.
    if n_left < 4 or n_right < 4 or spanning / total > 0.4:
        return as_leaf()
    # A wide channel separates panels or the two halves of an imposed spread;
    # those legitimately contain tables, so accept them on geometry alone.  A
    # narrow channel is only a column break if both sides read like wrapped
    # prose -- that is what stops a numeric table being split into "columns".
    panel = gutter_width >= max(_PANEL_GUTTER_PT, x_extent * _PANEL_GUTTER_FRAC)
    if not panel and not all(
        _is_text_column([lines[i] for i in body_set if side[i] == want])
        for want in (LEFT, RIGHT)
    ):
        return as_leaf()

    # Full-width lines (headings, wide tables) divide the region horizontally:
    # the columns restart below them, so order band by band rather than globally.
    out: list[int] = []
    band: list[int] = []
    nested = 0

    def flush() -> None:
        nonlocal nested
        for want in (LEFT, RIGHT):
            members = [i for i in band if side[i] == want]
            if members:
                child, child_depth = _order_group(
                    members, lines, page_w, page_h, depth + 1, groups)
                out.extend(child)
                nested = max(nested, child_depth)
        band.clear()

    for i in idx:
        if side[i] == FULL:
            flush()
            groups.setdefault(i, len(set(groups.values())))
            out.append(i)
        else:
            band.append(i)
    flush()
    return out, nested + 1


def _layout(lines: list[Line], page_width: float, page_height: float
            ) -> tuple[list[Line], dict[int, int], int]:
    """Return (reading-ordered lines, line index -> leaf column, column count).

    The column count is ``2 ** splits`` along the deepest successful split, so a
    plain page reports 1, a two-column page 2, and an imposed spread of
    two-column pages 4.
    """
    groups: dict[int, int] = {}
    order, split_depth = _order_group(
        list(range(len(lines))), lines, page_width, page_height, 0, groups)
    return [lines[i] for i in order], groups, 2 ** split_depth


# --------------------------------------------------------------------------
# Paragraph assembly
# --------------------------------------------------------------------------

_NUMERIC_TOKEN = re.compile(r"^[(\[]?[₹$€£]?-?[\d,]+(?:\.\d+)?\)?%?$")


def _is_tabular(text: str) -> bool:
    """Two or more bare numbers means this line is a table row, not prose."""
    return sum(1 for tok in text.split() if _NUMERIC_TOKEN.match(tok)) >= 2


@dataclass
class Fragment:
    text: str
    x0: float
    x1: float


@dataclass
class VisualLine:
    """One rendered line of text, after side-by-side fragments are rejoined."""
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    column: int
    cells: list[Fragment] = field(default_factory=list)

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 1.0)


def _visual_lines(ordered: list[Line], column_of: dict[int, int]) -> list[VisualLine]:
    """Rejoin fragments that sit on the same baseline within the same column.

    PyMuPDF splits a rendered line wherever styling changes, so a numbered
    paragraph arrives as ``"4."`` plus the sentence, and a table row arrives as
    one fragment per cell.  Both belong on one line.
    """
    out: list[VisualLine] = []
    for line in ordered:
        column = column_of.get(id(line), -1)
        text = line.text.strip()
        if not text:
            continue
        prev = out[-1] if out else None
        overlap = 0.0
        if prev is not None:
            overlap = min(prev.y1, line.y1) - max(prev.y0, line.y0)
        if (prev is not None and prev.column == column
                and overlap >= 0.5 * min(prev.height, line.y1 - line.y0)
                and line.x0 >= prev.x1 - 2.0):
            # A visible gap starts a new cell; a small one continues the current.
            if line.x0 - prev.x1 > _CELL_GAP:
                prev.cells.append(Fragment(text, line.x0, line.x1))
            else:
                prev.cells[-1].text = prev.cells[-1].text.rstrip() + " " + text
                prev.cells[-1].x1 = line.x1
            prev.text = prev.text.rstrip() + " " + text
            prev.x1 = max(prev.x1, line.x1)
            prev.y0 = min(prev.y0, line.y0)
            prev.y1 = max(prev.y1, line.y1)
        else:
            out.append(VisualLine(line.x0, line.y0, line.x1, line.y1, text, column,
                                  [Fragment(text, line.x0, line.x1)]))
    return out


def _column_right_edges(lines: list[VisualLine]) -> dict[int, float]:
    """Right text margin of each column, used to tell wrapped lines from rows."""
    by_column: dict[int, list[float]] = {}
    for line in lines:
        by_column.setdefault(line.column, []).append(line.x1)
    edges: dict[int, float] = {}
    for column, xs in by_column.items():
        xs.sort()
        edges[column] = xs[int(len(xs) * 0.9)] if len(xs) > 4 else xs[-1]
    return edges


def _merge_paragraphs(lines: list[VisualLine]) -> tuple[list[str], list[tuple[int, int]]]:
    """Glue consecutive visual lines back into paragraphs.

    A line only continues the previous one when the previous line reached the
    column's right margin -- i.e. the text wrapped.  That test is what keeps
    table rows, which stop short, from being welded into one blob, while still
    rejoining prose that the PDF stores one line at a time.

    Returns the paragraphs and, for each input line, ``(paragraph index, offset
    within that paragraph)`` so cell offsets survive the reassembly.
    """
    right_edges = _column_right_edges(lines)
    paragraphs: list[str] = []
    placements: list[tuple[int, int]] = []
    current = ""
    prev: VisualLine | None = None
    para_x0 = 0.0

    def flush() -> None:
        nonlocal current
        if current:
            paragraphs.append(current)
            current = ""

    for line in lines:
        if prev is None or not _continues(prev, line, para_x0, right_edges):
            flush()
            para_x0 = line.x0
            offset = 0
            current = line.text
        else:
            para_x0 = min(para_x0, line.x0)
            # A trailing hyphen before a lowercase continuation is a soft break.
            if current.endswith("-") and line.text[:1].islower():
                current = current[:-1]
                offset = len(current)
            else:
                current += " "
                offset = len(current)
            current += line.text
        placements.append((len(paragraphs), offset))
        prev = line
    flush()
    return paragraphs, placements


def _continues(prev: VisualLine, cur: VisualLine, para_x0: float,
               right_edges: dict[int, float]) -> bool:
    """Is ``cur`` the next wrapped line of the paragraph ``prev`` belongs to?"""
    if prev.column != cur.column:
        return False
    height = min(prev.height, cur.height)
    gap = cur.y0 - prev.y1
    if not (-_PARA_OVERLAP * height <= gap <= _PARA_Y_GAP * height):
        return False
    if not (para_x0 - _PARA_OUTDENT <= cur.x0 <= para_x0 + _PARA_INDENT):
        return False
    if _is_tabular(prev.text) and _is_tabular(cur.text):
        return False
    edge = right_edges.get(cur.column)
    if edge is None:
        return False
    # Did the previous line run to the margin (wrapped) or stop short (row end)?
    slack = max(edge - prev.x0, 1.0) * _WRAP_SLACK_FRAC
    return prev.x1 >= edge - slack


# --------------------------------------------------------------------------
# Document assembly
# --------------------------------------------------------------------------

_CELL_NUMERIC = re.compile(r"^[(\[]?[₹$€£]?\s?-?[\d,]+(?:\.\d+)?\)?\s*(?:%|[A-Za-z]{1,8})?\)?$")


def _locate_cells(lines: list[VisualLine], paragraphs: list[str],
                  placements: list[tuple[int, int]]) -> list[Cell]:
    """Map every cell to its exact span in the assembled page text."""
    starts: list[int] = []
    running = 0
    for para in paragraphs:
        starts.append(running)
        running += len(para) + 2          # paragraphs are joined by a blank line
    cells: list[Cell] = []
    for index, (line, (para_index, offset)) in enumerate(zip(lines, placements)):
        if para_index >= len(starts):
            continue
        base = starts[para_index] + offset
        cursor = 0
        for cell in line.cells:
            found = line.text.find(cell.text, cursor)
            if found < 0:
                continue
            cursor = found + len(cell.text)
            cells.append(Cell(
                text=cell.text, start=base + found, end=base + found + len(cell.text),
                x0=cell.x0, x1=cell.x1, y0=line.y0, y1=line.y1, line=index,
                numeric=bool(_CELL_NUMERIC.match(cell.text.strip())),
            ))
    return cells


_PRINTED_LABEL = re.compile(r"^\s*(?:page\s+)?([0-9]{1,4}|[ivxlcIVXLC]{1,7})\s*$")


def _printed_label(lines: list[Line], page_height: float) -> str | None:
    for l in lines:
        if l.y0 < page_height * 0.88:
            continue
        m = _PRINTED_LABEL.match(l.text.strip())
        if m:
            return m.group(1)
    return None


def _parse_meta_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = re.search(r"(\d{4})(\d{2})(\d{2})", raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _page_lines(page: pymupdf.Page) -> list[Line]:
    lines: list[Line] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = normalise("".join(span["text"] for span in line.get("spans", [])))
            if len(text) < _MIN_LINE_CHARS:
                continue
            x0, y0, x1, y1 = line["bbox"]
            lines.append(Line(x0, y0, x1, y1, text))
    return lines


def read_pdf(path: str | Path, doc_id: str | None = None) -> Document:
    """Extract every page of ``path`` as normalised, reading-ordered text."""
    path = Path(path)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()

    with pymupdf.open(stream=raw, filetype="pdf") as pdf:
        meta = dict(pdf.metadata or {})
        pages: list[Page] = []
        for index, page in enumerate(pdf, start=1):
            rect = page.rect
            lines = _page_lines(page)
            ordered, groups, n_cols = _layout(lines, rect.width, rect.height)
            column_of = {id(lines[i]): column for i, column in groups.items()}
            visual = _visual_lines(ordered, column_of)
            paragraphs, placements = _merge_paragraphs(visual)
            text = "\n\n".join(paragraphs)
            pages.append(Page(
                page_no=index,
                text=text,
                n_columns=n_cols,
                printed_label=_printed_label(lines, rect.height),
                cells=_locate_cells(visual, paragraphs, placements),
            ))

    title = (meta.get("title") or "").strip() or path.stem
    return Document(
        doc_id=doc_id or sha[:12],
        filename=path.name,
        title=title,
        sha256=sha,
        n_pages=len(pages),
        pages=pages,
        meta=meta,
        published_on=_parse_meta_date(meta.get("creationDate")),
    )
