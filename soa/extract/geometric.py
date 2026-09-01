"""Deterministic table extraction from the text layer.

This engine reads coordinates, never pixels. It exists for three reasons:

1. It is free and repeatable, so it can run on every page of every document.
2. It produces exact bounding boxes, which is what lets the UI highlight the source
   region for a cell the reviewer clicks. A vision model cannot supply those reliably.
3. It gives the vision engine something to be checked against. The reconciliation step
   compares the two and reports every row or column that only one of them saw, which is
   the pipeline's defence against the failure the brief penalises most heavily.

It is *not* expected to win. On pages with corrupted glyph mappings, or where a merged
annotation spans several columns, the vision engine is the better reader. The point of
running both is knowing where they disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..pdfdoc import Line, Page, PdfDoc, Word, join_words
from ..locate.patterns import CATEGORY_ROW_RE, is_cell_marker
from ..locate.spans import TableSpan

# Column boundaries derived from text are clustered with this tolerance, in points.
_COL_TOLERANCE = 12.0
# Rows closer together than this are treated as one wrapped row.
_ROW_MERGE_GAP = 3.0


@dataclass
class GridCell:
    row: int
    col: int
    raw: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int


@dataclass
class GridRow:
    index: int
    label: str
    y0: float
    y1: float
    page: int
    is_category: bool = False
    label_bbox: tuple[float, float, float, float] | None = None


@dataclass
class GridColumn:
    index: int
    x0: float
    x1: float
    header_texts: list[str] = field(default_factory=list)


@dataclass
class PageGrid:
    """The grid recovered from one page."""

    page: int
    columns: list[GridColumn]
    rows: list[GridRow]
    cells: list[GridCell]
    header_rows: list[list[str]]
    header_bottom_y: float
    table_top_y: float
    table_bottom_y: float
    vertical_labels: list[tuple[str, float, float, float]] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# column detection
# --------------------------------------------------------------------------------------


def _columns_from_rules(rules: list[Line], top: float, bottom: float) -> list[float]:
    """Column boundaries taken from vertical ruling lines that span the table body."""
    height = bottom - top
    if height <= 0:
        return []
    spanning = [
        r.x0
        for r in rules
        if r.orientation == "v"
        and r.length >= height * 0.35
        and r.y0 <= top + height * 0.4
        and r.y1 >= bottom - height * 0.4
    ]
    if len(spanning) < 3:
        return []
    spanning.sort()
    merged = [spanning[0]]
    for x in spanning[1:]:
        if x - merged[-1] > 3.0:
            merged.append(x)
    return merged if len(merged) >= 3 else []


def _columns_from_text(words: list[Word], label_cut: float) -> list[float]:
    """Column boundaries inferred from where short tokens stack up vertically.

    Used when the table has no vertical rules -- one of the reference SoAs is ruled only
    horizontally. Only tokens to the right of the row-label column are considered.
    """
    marks = [w for w in words if w.x0 >= label_cut and len(w.text.strip()) <= 14]
    if len(marks) < 6:
        return []

    centres = sorted(w.cx for w in marks)
    clusters: list[list[float]] = [[centres[0]]]
    for c in centres[1:]:
        if c - clusters[-1][-1] <= _COL_TOLERANCE:
            clusters[-1].append(c)
        else:
            clusters.append([c])

    keep = [sum(c) / len(c) for c in clusters if len(c) >= 2]
    if len(keep) < 2:
        return []

    # Convert centres into boundaries: midpoints between adjacent clusters.
    bounds = [label_cut]
    for a, b in zip(keep, keep[1:]):
        bounds.append((a + b) / 2.0)
    bounds.append(keep[-1] + (keep[-1] - bounds[-1]))
    return bounds


def _build_columns(bounds: list[float], label_left: float, label_cut: float, page_right: float) -> list["GridColumn"]:
    """Assemble leaf columns, with column 0 reserved for the row-label column.

    Making the label column explicit rather than implicit is what stops the first data
    column being silently discarded when the inferred boundaries happen to start exactly
    at the label cut.
    """
    data_bounds = [b for b in bounds if b > label_cut + 1.0]
    if not data_bounds or data_bounds[-1] < page_right:
        data_bounds.append(page_right)

    edges = [label_left, label_cut] + data_bounds
    cols: list[GridColumn] = []
    for i in range(len(edges) - 1):
        if edges[i + 1] - edges[i] < 1.0:
            continue
        cols.append(GridColumn(index=len(cols), x0=edges[i], x1=edges[i + 1]))
    return cols


# Running headers and footers sit in these margins. Excluding them is not cosmetic: a
# repeated title line such as "Clinical Study Protocol" otherwise becomes a row, and the
# reconciliation then promotes it into the output as a row the vision engine "missed".
_HEADER_MARGIN = 0.045
_FOOTER_MARGIN = 0.91


def _is_running_furniture(word: Word, page: Page) -> bool:
    """True for a word in the running header or footer band."""
    return word.y1 < page.height * _HEADER_MARGIN or word.y0 > page.height * _FOOTER_MARGIN


def table_region(page: Page, words: list[Word]) -> tuple[float, float]:
    """The vertical extent of the ruled table body on a page.

    Needed before anything else, because the footnote block below the table also contains
    tokens that look exactly like data cells -- entries beginning "Xa - ", "Xb - ". Left
    in scope they drag the row-label boundary to the left margin and the whole grid
    collapses. Long horizontal rules bound the table reliably; when a page has none, the
    whole page is used and the footnote parser trims later.
    """
    long_rules = [
        r
        for r in page.ruling_lines
        if r.orientation == "h" and r.length >= page.width * 0.35
    ]
    if len(long_rules) >= 2:
        ys = sorted(r.y0 for r in long_rules)
        return ys[0] - 2.0, ys[-1] + 2.0
    if not words:
        return 0.0, page.height
    return min(w.y0 for w in words) - 2.0, max(w.y1 for w in words) + 2.0


def _label_column_cut(words: list[Word], page: Page, body_bottom: float) -> float:
    """Where the row-label column ends and the first data column begins.

    Uses the leftmost *dense* stack of cell markers rather than the leftmost marker
    outright: a handful of footnote markers share a left margin too, and a single stray
    one would otherwise define the boundary.
    """
    marks = [
        w
        for w in words
        if w.y1 <= body_bottom and is_cell_marker(w.text) and len(w.text.strip()) <= 8
    ]
    if len(marks) < 3:
        return page.width * 0.28

    xs = sorted(w.x0 for w in marks)
    clusters: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] <= _COL_TOLERANCE:
            clusters[-1].append(x)
        else:
            clusters.append([x])

    biggest = max(len(c) for c in clusters)
    # A real data column carries a meaningful share of the page's markers.
    dense = sorted(
        (c for c in clusters if len(c) >= max(2, biggest * 0.25)), key=lambda c: c[0]
    )
    if not dense:
        dense = sorted(clusters, key=lambda c: c[0])

    # The row-label column is where the long text lives. If the first dense marker cluster
    # sits so far left that most long words would fall to its right, the cluster is part of
    # the labels (a wrapped label, a form number) rather than the first data column, so
    # step right until the labels are actually enclosed. Without this, a landscape
    # continuation page can end up with every row label parked in a data cell.
    long_words = [w for w in words if w.y1 <= body_bottom and len(w.text.strip()) > 15]
    for cluster in dense:
        cut = max(page.width * 0.08, cluster[0] - _COL_TOLERANCE)
        if not long_words:
            return cut
        left_share = sum(1 for w in long_words if w.x0 < cut) / len(long_words)
        if left_share >= 0.6:
            return cut

    return max(page.width * 0.08, dense[-1][0] - _COL_TOLERANCE)


# --------------------------------------------------------------------------------------
# row detection
# --------------------------------------------------------------------------------------


def _rows_from_lines(
    words: list[Word], header_bottom: float, label_cut: float
) -> list[tuple[float, float, list[Word]]]:
    """Group body words into rows by vertical position.

    A row label frequently wraps across two or three lines while its data cells sit on
    only the first. Wrapped continuation lines are recognised because they carry text in
    the label column but no data cells, and are merged upwards.
    """
    body = [w for w in words if w.y0 >= header_bottom - 1]
    if not body:
        return []

    by_y = sorted(body, key=lambda w: (w.y0, w.x0))
    bands: list[list[Word]] = [[by_y[0]]]
    for w in by_y[1:]:
        ref = bands[-1][0]
        tol = max(2.0, min(ref.height, w.height) * 0.6)
        if abs(w.y0 - ref.y0) <= tol or (w.y0 < ref.y1 - tol and w.y1 > ref.y0 + tol):
            bands[-1].append(w)
        else:
            bands.append([w])

    # Merge a band into the previous row when it is label-only continuation text.
    rows: list[list[Word]] = []
    for band in bands:
        has_data = any(w.x0 >= label_cut for w in band)
        label_only = not has_data
        close_above = (
            rows
            and min(w.y0 for w in band) - max(w.y1 for w in rows[-1]) <= _ROW_MERGE_GAP
        )
        if label_only and rows and close_above:
            rows[-1].extend(band)
        else:
            rows.append(list(band))

    return [
        (min(w.y0 for w in r), max(w.y1 for w in r), r) for r in rows
    ]


# --------------------------------------------------------------------------------------
# header detection
# --------------------------------------------------------------------------------------


def _header_bottom(page: Page, words: list[Word], label_cut: float) -> float:
    """Find where the stacked column headers end and the data body begins.

    The first band that contains a recognisable cell marker is the first data row, so the
    header ends just above it.
    """
    by_y = sorted(words, key=lambda w: w.y0)
    for w in by_y:
        if w.x0 >= label_cut and is_cell_marker(w.text):
            return w.y0 - 2.0
    # No markers at all: fall back to the top fifth of the page.
    return page.height * 0.2


def _header_bands(
    words: list[Word], top: float, bottom: float
) -> list[list[Word]]:
    """Split the header region into its stacked rows."""
    head = [w for w in words if w.y0 >= top - 1 and w.y1 <= bottom + 2]
    if not head:
        return []
    by_y = sorted(head, key=lambda w: (w.y0, w.x0))
    bands: list[list[Word]] = [[by_y[0]]]
    for w in by_y[1:]:
        ref = bands[-1][0]
        tol = max(2.0, min(ref.height, w.height) * 0.6)
        if abs(w.y0 - ref.y0) <= tol:
            bands[-1].append(w)
        else:
            bands.append([w])
    return bands


# --------------------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------------------


def extract_page(page: Page, start_y: float | None = None) -> PageGrid:
    """Recover the grid from one page.

    ``start_y`` restricts extraction to below a given y, for a page shared by two
    schedules.
    """
    words = [
        w
        for w in page.words
        if (start_y is None or w.y0 >= start_y - 2) and not _is_running_furniture(w, page)
    ]
    if not words:
        return PageGrid(page.number, [], [], [], [], 0.0, 0.0, 0.0)

    region_top, region_bottom = table_region(page, words)
    if start_y is not None:
        region_top = max(region_top, start_y - 2)
    # Everything below the last long rule is the footnote block, not table body.
    body_words = [w for w in words if w.y0 >= region_top - 2 and w.y1 <= region_bottom + 2]
    if len(body_words) < 5:
        body_words = words
        region_top = min(w.y0 for w in words)
        region_bottom = max(w.y1 for w in words)

    table_top = min(w.y0 for w in body_words)
    table_bottom = max(w.y1 for w in body_words)
    label_left = min(w.x0 for w in body_words)

    label_cut = _label_column_cut(body_words, page, region_bottom)
    header_bottom = _header_bottom(page, body_words, label_cut)

    # -- columns ------------------------------------------------------------------------
    bounds = _columns_from_rules(page.ruling_lines, header_bottom, table_bottom)
    if not bounds:
        bounds = _columns_from_text(body_words, label_cut)

    right_edge = max(page.width, max(w.x1 for w in body_words) + 4)
    columns = _build_columns(bounds, label_left, label_cut, right_edge)
    if len(columns) < 2:
        columns = _build_columns([label_cut, right_edge], label_left, label_cut, right_edge)

    def column_for(w: Word) -> int | None:
        for c in columns:
            if c.x0 - 1 <= w.cx < c.x1 + 1:
                return c.index
        return None

    # -- header rows --------------------------------------------------------------------
    header_rows: list[list[str]] = []
    for band in _header_bands(body_words, table_top, header_bottom):
        cells = [""] * len(columns)
        buckets: dict[int, list[Word]] = {}
        for w in band:
            ci = column_for(w)
            if ci is None:
                continue
            buckets.setdefault(ci, []).append(w)
        for ci, ws in buckets.items():
            cells[ci] = join_words(ws)
        if any(c.strip() for c in cells):
            header_rows.append(cells)

    # -- body rows and cells -------------------------------------------------------------
    rows: list[GridRow] = []
    cells: list[GridCell] = []

    for idx, (y0, y1, band) in enumerate(
        _rows_from_lines(body_words, header_bottom, label_cut)
    ):
        label_words = [w for w in band if w.x0 < label_cut]
        data_words = [w for w in band if w.x0 >= label_cut]
        label = join_words(label_words)
        if not label and not data_words:
            continue

        is_category = bool(label) and not data_words and bool(
            CATEGORY_ROW_RE.match(label) or label.rstrip().endswith(":")
        )

        row = GridRow(
            index=len(rows),
            label=label,
            y0=y0,
            y1=y1,
            page=page.number,
            is_category=is_category,
            label_bbox=(
                min((w.x0 for w in label_words), default=0.0),
                y0,
                max((w.x1 for w in label_words), default=0.0),
                y1,
            )
            if label_words
            else None,
        )
        rows.append(row)

        buckets: dict[int, list[Word]] = {}
        for w in data_words:
            ci = column_for(w)
            if ci is None or ci == 0:
                continue
            buckets.setdefault(ci, []).append(w)

        for ci, ws in buckets.items():
            raw = join_words(ws)
            if not raw.strip():
                continue
            cells.append(
                GridCell(
                    row=row.index,
                    col=ci,
                    raw=raw,
                    x0=min(w.x0 for w in ws),
                    y0=min(w.y0 for w in ws),
                    x1=max(w.x1 for w in ws),
                    y1=max(w.y1 for w in ws),
                    page=page.number,
                )
            )

    vlabels = [
        (v.text, v.x, v.y0, v.y1)
        for v in page.vertical_labels
        if start_y is None or v.y0 >= start_y - 2
    ]

    return PageGrid(
        page=page.number,
        columns=columns,
        rows=rows,
        cells=cells,
        header_rows=header_rows,
        header_bottom_y=header_bottom,
        table_top_y=table_top,
        table_bottom_y=table_bottom,
        vertical_labels=vlabels,
    )


def extract_span(doc: PdfDoc, span: TableSpan) -> list[PageGrid]:
    """Recover the grid from every page of a span, in order."""
    grids: list[PageGrid] = []
    for page_no in span.pages:
        start_y = span.starts_at_y if page_no == span.pages[0] else None
        grids.append(extract_page(doc.page_no(page_no), start_y=start_y))
    return grids
