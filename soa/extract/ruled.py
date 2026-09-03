"""Recovery of the *printed* table lattice from a page's ruling lines.

Everything else in the extractor reads content -- what the table says. This module reads
structure -- what the table *is*: where the printer actually drew the row and column
boundaries. That distinction is the whole point of it.

A vision model reading a page image reads the table the way a person does, and a person
silently corrects the layout while reading. Three lines of text inside one tall ruled box
read as three activities, so they come back as three rows. A narrow column with nothing
printed in it reads as whitespace, so it comes back as nothing at all. Both readings are
sensible and both destroy the correspondence between the output and the page: rows stop
lining up with the printed grid, and every visit column to the right of a dropped blank
shifts one place left. For a Schedule of Activities that is not a cosmetic problem -- it
is a visit assigned to the wrong week.

The lattice is not a matter of judgement. It is drawn on the page, and it can be read
back exactly. So this module recovers it and :mod:`soa.align` uses it to hold the engines'
reading to the printed structure.

Two details make the recovery work on real protocols:

* **Borders are drawn per cell, not per table.** Every one of the five reference protocols
  draws its grid as hundreds of short segments -- one per cell edge -- rather than as long
  ruled lines. A filter looking for a line that spans the table therefore finds nothing.
  Segments are clustered by position and their *union* length is what is measured.

* **Row boundaries are read in the row-label column, not across the page.** Protocols
  routinely rule a row separator across the data columns but not across the label column,
  or the reverse. The label column is the one that decides whether two printed lines are
  one row or two, because that is the cell the label is printed in -- so that is where the
  boundary is looked for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..pdfdoc import Line, Page, Word

# Ruling segments within this many points of each other are the same printed line. Set
# from measurement, not taste: protocol15 draws its heavier borders as two parallel
# hairlines 2.1pt apart, and they must collapse into one edge.
_EDGE_TOLERANCE = 4.0

# A vertical edge must run down this fraction of the table to be a column boundary. Well
# below 1.0 because a header banner spanning several columns legitimately interrupts the
# boundaries beneath it.
_MIN_COLUMN_COVERAGE = 0.5

# A horizontal edge must cross this fraction of the row-label column to be a row boundary.
# High, because the question being asked is precisely "is the label cell divided here?".
_MIN_ROW_COVERAGE = 0.8

# Fewer edges than this and there is no lattice worth trusting.
_MIN_EDGES = 3


@dataclass
class SourceBand:
    """One printed row of the grid: the strip between two horizontal boundaries."""

    index: int
    y0: float
    y1: float
    label_lines: list[str] = field(default_factory=list)
    """Text printed in the row-label cell, one entry per printed line."""
    label_text: str = ""
    """The same text joined, for matching against an engine's row label."""
    data_texts: list[str] = field(default_factory=list)
    """Text printed in each non-label column, left to right. Empty string where blank."""

    @property
    def has_data(self) -> bool:
        return any(t.strip() for t in self.data_texts)

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class SourceColumn:
    """One printed column of the grid, excluding the row-label column."""

    index: int
    x0: float
    x1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class RuledGrid:
    """The lattice recovered from one page.

    Deliberately uninterpreted. It says where the lines are and what is printed between
    them; it does not say which bands are headers, which column is a stub, or which blank
    column is real. Those are judgements, and they are made in :mod:`soa.align` where the
    engines' reading of the page is also available.
    """

    page: int
    x_edges: list[float]
    y_edges: list[float]
    label_x0: float
    label_x1: float
    columns: list[SourceColumn]
    bands: list[SourceBand]

    @property
    def column_count(self) -> int:
        return len(self.columns)


# --------------------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------------------


def _cluster(values: list[float], tol: float = _EDGE_TOLERANCE) -> list[list[float]]:
    """Group nearby coordinates. Returns clusters in ascending order."""
    if not values:
        return []
    ordered = sorted(values)
    out: list[list[float]] = [[ordered[0]]]
    for v in ordered[1:]:
        if v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def _union_length(segments: list[tuple[float, float]], lo: float, hi: float) -> float:
    """Total length covered by ``segments`` inside ``[lo, hi]``, overlaps counted once."""
    clipped = [
        (max(a, lo), min(b, hi)) for a, b in segments if b > lo + 0.5 and a < hi - 0.5
    ]
    if not clipped:
        return 0.0
    clipped.sort()
    total = 0.0
    cur_start, cur_end = clipped[0]
    for start, end in clipped[1:]:
        if start <= cur_end + 1.5:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    return total + (cur_end - cur_start)


def _coverage(
    segments: list[tuple[float, float]], lo: float, hi: float
) -> float:
    span = hi - lo
    if span <= 0:
        return 0.0
    return _union_length(segments, lo, hi) / span


def _cell_lines(words: list[Word], x0: float, y0: float, x1: float, y1: float) -> list[str]:
    """The text printed inside one lattice cell, split back into its printed lines.

    Membership is by the *centre* of each word rather than its box: a descender or an
    accent routinely pokes a point or two past a ruling line, and containment tests then
    drop the word out of its own cell.
    """
    inside = [
        w
        for w in words
        if x0 - 1.0 <= w.cx <= x1 + 1.0 and y0 - 0.5 <= w.cy <= y1 + 0.5
    ]
    if not inside:
        return []
    inside.sort(key=lambda w: (round(w.y0, 1), w.x0))

    lines: list[list[Word]] = [[inside[0]]]
    for w in inside[1:]:
        ref = lines[-1][-1]
        if abs(w.y0 - ref.y0) <= max(2.0, min(ref.height, w.height) * 0.6):
            lines[-1].append(w)
        else:
            lines.append([w])

    from ..pdfdoc import join_words

    out = []
    for group in lines:
        text = join_words(group).strip()
        if text:
            out.append(text)
    return out


# --------------------------------------------------------------------------------------
# lattice recovery
# --------------------------------------------------------------------------------------


def _vertical_edges(
    verticals: list[Line],
) -> tuple[list[float], float, float] | None:
    """Column boundaries, plus the vertical extent of the table they bound.

    The extent is taken from the tallest run of vertical rules rather than from the
    horizontal ones. Protocols frequently draw a ruled box around the footnote block
    beneath the table; its horizontal edges would stretch the table hundreds of points
    past its last row, while the column rules stop where the grid stops.
    """
    if not verticals:
        return None

    clusters = _cluster([r.x0 for r in verticals])
    if len(clusters) < _MIN_EDGES:
        return None

    spans: list[tuple[float, float, float, float, list[tuple[float, float]]]] = []
    for cluster in clusters:
        lo, hi = min(cluster), max(cluster)
        segments = [
            (r.y0, r.y1)
            for r in verticals
            if lo - _EDGE_TOLERANCE <= r.x0 <= hi + _EDGE_TOLERANCE
        ]
        if not segments:
            continue
        top = min(s[0] for s in segments)
        bottom = max(s[1] for s in segments)
        drawn = _union_length(segments, top, bottom)
        if drawn > 0:
            spans.append((sum(cluster) / len(cluster), top, bottom, drawn, segments))

    if not spans:
        return None

    # The tallest column rule defines the table's vertical extent.
    _x, table_top, table_bottom, _drawn, _segs = max(spans, key=lambda s: s[3])
    if table_bottom - table_top < 20:
        return None

    edges = [
        x
        for x, _top, _bottom, _drawn, segments in spans
        if _coverage(segments, table_top, table_bottom) >= _MIN_COLUMN_COVERAGE
    ]
    if len(edges) < _MIN_EDGES:
        return None
    return sorted(edges), table_top, table_bottom


def _horizontal_edges(
    horizontals: list[Line],
    label_x0: float,
    label_x1: float,
    table_top: float,
    table_bottom: float,
) -> list[float]:
    """Row boundaries, read across the row-label column only."""
    edges: list[float] = []
    for cluster in _cluster([r.y0 for r in horizontals]):
        lo, hi = min(cluster), max(cluster)
        centre = sum(cluster) / len(cluster)
        if not (table_top - 3.0 <= centre <= table_bottom + 3.0):
            continue
        segments = [
            (r.x0, r.x1)
            for r in horizontals
            if lo - _EDGE_TOLERANCE <= r.y0 <= hi + _EDGE_TOLERANCE
        ]
        if _coverage(segments, label_x0, label_x1) >= _MIN_ROW_COVERAGE:
            edges.append(centre)
    return sorted(edges)


def read_grid(page: Page, start_y: float | None = None) -> RuledGrid | None:
    """Recover the printed lattice of the table on ``page``.

    Returns ``None`` when the page does not draw a grid this can be sure of -- which is a
    normal answer, not a failure. Callers fall back to reading content and leave the
    structure as the engines reported it.

    ``start_y`` restricts recovery to below a given y, for a page carrying two schedules.
    """
    rules = page.ruling_lines
    if start_y is not None:
        rules = [r for r in rules if r.y1 >= start_y - 2.0]

    verticals = [r for r in rules if r.orientation == "v"]
    horizontals = [r for r in rules if r.orientation == "h"]
    if not verticals or not horizontals:
        return None

    found = _vertical_edges(verticals)
    if found is None:
        return None
    x_edges, table_top, table_bottom = found
    if start_y is not None:
        table_top = max(table_top, start_y - 2.0)

    label_x0, label_x1 = x_edges[0], x_edges[1]
    y_edges = _horizontal_edges(horizontals, label_x0, label_x1, table_top, table_bottom)
    if len(y_edges) < _MIN_EDGES:
        return None

    columns = [
        SourceColumn(index=i, x0=x_edges[i + 1], x1=x_edges[i + 2])
        for i in range(len(x_edges) - 2)
    ]

    words = [w for w in page.words if table_top - 2.0 <= w.cy <= table_bottom + 2.0]

    bands: list[SourceBand] = []
    for i in range(len(y_edges) - 1):
        y0, y1 = y_edges[i], y_edges[i + 1]
        label_lines = _cell_lines(words, label_x0, y0, label_x1, y1)
        bands.append(
            SourceBand(
                index=i,
                y0=y0,
                y1=y1,
                label_lines=label_lines,
                label_text=" ".join(label_lines),
                data_texts=[
                    " ".join(_cell_lines(words, c.x0, y0, c.x1, y1)) for c in columns
                ],
            )
        )

    return RuledGrid(
        page=page.number,
        x_edges=x_edges,
        y_edges=y_edges,
        label_x0=label_x0,
        label_x1=label_x1,
        columns=columns,
        bands=bands,
    )


# --------------------------------------------------------------------------------------
# the header stub
# --------------------------------------------------------------------------------------

# Captions that name a header row rather than label a visit. A column carrying only these,
# sitting against the row-label column and holding no data, is the table's header stub --
# the little box that says VISIT above the visit numbers, and WEEK above the weeks -- and
# not a visit of its own. Reading it as a leaf column produces a narrow empty column
# wedged between the row labels and the first real visit.
_STUB_CAPTIONS = {
    "visit", "visits", "visit number", "visit no", "visit name", "visit window",
    "week", "weeks", "study week", "day", "days", "study day", "day of week",
    "date", "month", "months", "cycle", "period", "study period", "phase",
    "time", "timepoint", "time point", "window", "epoch",
    "activity", "activities", "assessment", "assessments", "procedure", "procedures",
    "event", "events",
}

_NUMERIC_ONLY = re.compile(r"^[\s+\-\u00b1.,/()0-9]+$")


def _caption_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def split_stub_columns(
    grid: RuledGrid, header_bands: list[SourceBand], body_bands: list[SourceBand]
) -> tuple[list[int], list[int]]:
    """Separate the table's header stub from its real leaf columns.

    Returns ``(stub_indices, leaf_indices)`` as indices into ``grid.columns``. Only the
    leading run of columns can be stub: the stub is the box the header captions are
    printed in, and it is always drawn against the row labels.

    Two signals are required, so that an unlabelled *visit* column is never mistaken for
    one. The column must carry no data anywhere in the table -- a stub never does, because
    it names header rows rather than a visit. And every header cell it carries must read
    as the name of its header row: either a known axis caption, or a word sitting in a
    header row that holds nothing but numbers in every other column, which is what "VISIT"
    above 1, 2, 3 looks like and what a visit label such as "ET" never does.
    """
    headers = [[b.data_texts[c.index] for b in header_bands] for c in grid.columns]

    stub: list[int] = []
    for column in grid.columns:
        texts = headers[column.index]
        if not any(t.strip() for t in texts):
            break
        if any(b.data_texts[column.index].strip() for b in body_bands):
            break

        caption_like = True
        for level, text in enumerate(texts):
            text = (text or "").strip()
            if not text:
                continue
            if _caption_key(text) in _STUB_CAPTIONS:
                continue
            others = [
                (h[level] or "").strip()
                for i, h in enumerate(headers)
                if i != column.index and level < len(h) and (h[level] or "").strip()
            ]
            if len(others) >= 2 and all(_NUMERIC_ONLY.match(o) for o in others):
                continue
            caption_like = False
            break

        if not caption_like:
            break
        stub.append(column.index)

    leaf = [c.index for c in grid.columns if c.index not in set(stub)]
    if not leaf:  # every column looked like a stub; trust nothing and keep them all
        return [], [c.index for c in grid.columns]
    return stub, leaf


def is_axis_caption(text: str) -> bool:
    """True for a word that names an axis of the table rather than a value on it.

    ``ACTIVITY``, ``VISIT``, ``WEEK``, ``Date``. Used to tell a header band from the first
    row of the body when a continuation page prints no cell markers for several rows.
    """
    return _caption_key(text) in _STUB_CAPTIONS
