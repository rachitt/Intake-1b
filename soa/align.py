"""Hold the extracted schedule to the grid the source actually prints.

The two engines read *content* well and *structure* only as well as a reader does, and a
reader silently tidies structure up. Both of the corrections made here were observed on
the reference protocols:

* **A blank printed column disappears.** Protocol 1 prints an unlabelled column between
  Visit 5 / Week 4 and Visit 7 / Week 6, and protocol 12 prints one between the screening
  column and Study Week 1-3. Nothing is drawn inside either, so a reader does not report
  them -- and every visit to their right then sits one column too far left. That is not a
  cosmetic loss: it is a visit reassigned to the wrong week.

* **A merged row label becomes several rows.** Protocol 1 draws one row whose label cell
  reads "Study drug record / Medications dispensed / Medications returned", with a single
  set of X marks against all three. Read as prose those are three activities, and both a
  person and a model will say so -- but the source draws one row, and splitting it invents
  two rows that carry no data and detaches the X marks from two of the three activities.

The rule applied is the same in both directions and it is not a matter of taste: the
printed lattice recovered by :mod:`soa.extract.ruled` decides what a row and a column are.
An engine's reading supplies the *text*; the page supplies the *structure*.

Everything here is conservative by construction. If the lattice cannot be recovered on
every page of the table, or if the engines' columns cannot be matched to it confidently,
alignment declines to act and says so in a warning rather than reshaping the output on a
guess. A wrong alignment would be worse than none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .extract.ruled import (
    RuledGrid,
    SourceBand,
    is_axis_caption,
    read_grid,
    split_stub_columns,
)
from .locate.spans import TableSpan
from .pdfdoc import PdfDoc
from .reconcile import normalise_label
from .schema import (
    BBox,
    Cell,
    Column,
    Engine,
    ExtractionWarning,
    HeaderCell,
    Row,
    Schedule,
)

# Similarity above which two labels are the same row. Matching here is SYMMETRIC --
# `fuzz.token_sort_ratio`, not the `token_set_ratio` reconciliation uses. Reconciliation
# asks "did the other engine see this row at all", where treating a subset as a match is
# right. This asks "is this row printed inside that box", where it is catastrophic: "ECG"
# is a subset of "Ambulatory ECG removed" and scores 100, which would fuse two rows the
# page rules apart.
_ROW_MATCH = 82.0

# Column headers are short and mostly numeric, where a fuzzy ratio is nearly meaningless:
# "12 24" against "1 -2" scores over 50 on nothing but shared digits. Signatures are
# therefore compared exactly first, and the fuzzy fallback is deliberately strict.
_COLUMN_MATCH = 88.0

# Below this share of the engine's columns matching the printed lattice, the lattice is
# assumed to describe a different table than the one that was read, and nothing is changed.
_MIN_COLUMN_MATCH_RATE = 0.6

# A header row gives a column its identity only if it holds a value for most of the
# columns. Below this fill rate it is a spanning banner drawn inside one cell, or a header
# row the table leaves blank, and either would make one column's signature unlike the rest.
_VALUE_BAND_FILL = 0.6

# A header text repeated across at least this share of the columns is a caption printed on
# every column, not something that tells one column from another.
_UBIQUITOUS_TEXT = 0.6


@dataclass
class _AxisColumn:
    """One column of the merged, cross-page source axis."""

    header_texts: list[str] = field(default_factory=list)
    value_texts: list[str] = field(default_factory=list)
    """Only the header rows that give every column a value -- banners excluded."""
    signature: str = ""
    body_text: str = ""
    """Everything printed in the column below the header, joined. Usually empty for a
    column with no header -- but not always: a table that rules a narrow column to hold a
    sideways RANDOMIZATION divider prints that divider's letters here."""
    pages: list[int] = field(default_factory=list)
    boxes: dict[int, tuple[float, float]] = field(default_factory=dict)
    """page -> (x0, x1) of the printed column on that page."""

    @property
    def blank(self) -> bool:
        """True only when the source prints nothing in this column at all.

        Not the same as "has no signature". A signature is built from the header rows that
        give every column a value, and a column can miss those and still be labelled --
        protocol 5's blood-collection table heads its first two columns "Volume Per Sample"
        and "Type" on a row that leaves the twelve study-day columns empty. Those are
        labelled columns, not blank ones.
        """
        return (
            not self.signature
            and not any(t.strip() for t in self.header_texts)
            and not self.body_text.strip()
        )

    @property
    def printed_texts(self) -> list[str]:
        """The header text to show for this column, best row first."""
        return (
            self.value_texts
            if any(t.strip() for t in self.value_texts)
            else self.header_texts
        )


@dataclass
class _AxisRow:
    """One printed row band of the merged, cross-page source axis."""

    label_text: str
    label_lines: list[str]
    pages: list[int] = field(default_factory=list)
    has_data: bool = False
    boxes: dict[int, tuple[float, float]] = field(default_factory=dict)
    """page -> (y0, y1) of the printed row band on that page."""


# Header signatures keep signs, decimal points and slashes: "Week -2" and "Week 2" are
# different visits, and a normaliser that strips punctuation makes them the same string.
_SIGNATURE_NOISE = re.compile(r"[^a-z0-9+\-./±]+")


def _signature(texts: list[str]) -> str:
    joined = " ".join(t.strip() for t in texts if t and t.strip()).lower()
    return re.sub(r"\s+", " ", _SIGNATURE_NOISE.sub(" ", joined)).strip()


def _same_column(a: str, b: str) -> bool:
    """Whether two column header signatures describe the same printed column.

    Whitespace is ignored on the second try because the text layer scatters it through
    short headers -- "9 -11" for a printed "9-11", "12/ Term" for "12/Term" -- and those
    are the same column by any reading. It is not ignored on the first try, and the fuzzy
    fallback is strict, because these strings are short and mostly digits.
    """
    if not a or not b:
        return False
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return True
    return fuzz.token_sort_ratio(a, b) >= _COLUMN_MATCH


def _row_score(label: str, band_text: str, band_lines: list[str]) -> float:
    """How well a reported row label matches the text printed inside one band.

    Both readings are scored because the two disagree in opposite directions. A label that
    merely *wraps* comes back from the engines already joined, so it matches the band's
    joined text but none of its individual lines. A label printed in a *merged* cell
    alongside two other activities matches one line exactly and the joined text not at
    all. Either is a match; the difference between them is what `merged_label` records.
    """
    if not label:
        return 0.0
    scores = [fuzz.token_sort_ratio(label, normalise_label(band_text))] if band_text else []
    scores += [
        fuzz.token_sort_ratio(label, normalise_label(line))
        for line in band_lines
        if line.strip()
    ]
    return max(scores, default=0.0)


def _same_row(label: str, band_text: str, band_lines: list[str]) -> bool:
    return _row_score(label, band_text, band_lines) >= _ROW_MATCH


def _place_in_run(scores, start: int, length: int) -> int | None:
    """The best-matching position in the first unbroken run of matches at or after ``start``.

    Taking the *first* band over the threshold is not good enough where a table prints two
    all-but-identical labels next to each other -- protocol 9 has "Modified Clinical Global
    Impressions Scale - Patient" directly above "... - Rater", which score 90 against each
    other. First-match puts the Rater row in the Patient row's box and then fuses the two.
    So the run of consecutive matching bands is taken and the best of them wins, with ties
    going to the earlier band, which keeps the placement monotonic.
    """
    first = None
    for j in range(start, length):
        if scores(j) >= _ROW_MATCH:
            first = j
            break
    if first is None:
        return None

    best, best_score = first, scores(first)
    j = first + 1
    while j < length and scores(j) >= _ROW_MATCH:
        if scores(j) > best_score:
            best, best_score = j, scores(j)
        j += 1
    return best


# A label line that opens like this continues the line above it -- a qualifier, a time
# window, a dose, a CRF form number -- and cannot be an activity in its own right.
_CONTINUATION_START = re.compile(r"^\s*[(\[\-–—,;:/]|^\s*[a-z]|^\s*\d")


def lines_stand_alone(lines: list[str]) -> bool:
    """Whether every line of a label cell reads as an activity of its own.

    Guards `merged_label` against the far commoner case it looks exactly like: a single
    label that wrapped. "Physical Examination (04)" over "(Study Day 1 and Exit Day)" is
    one activity on two lines; "Study drug record" over "Medications dispensed" is two
    activities the page happens to rule as one row. Only the second is a merged label, and
    getting it wrong puts a wrap fragment into the output as though it named a procedure.

    Deliberately one-directional: it can only *demote* a claim of merging, never invent
    one, so a wrong answer here costs a joined label rather than a fabricated activity.
    """
    return len(lines) > 1 and not any(
        _CONTINUATION_START.match(line) for line in lines[1:]
    )


# --------------------------------------------------------------------------------------
# reading one page's lattice as a header / body / stub structure
# --------------------------------------------------------------------------------------


@dataclass
class _PageStructure:
    grid: RuledGrid
    header_bands: list[SourceBand]
    body_bands: list[SourceBand]
    stub_signatures: list[str]
    leaf_indices: list[int]
    """Indices into ``grid.columns`` of the columns that are real leaf columns."""
    value_bands: list[int] = field(default_factory=list)
    """Positions in ``header_bands`` that carry one value per column rather than a banner."""

    def header_texts(self, col_index: int) -> list[str]:
        return [b.data_texts[col_index] for b in self.header_bands]

    def signature_texts(self, col_index: int) -> list[str]:
        """The header texts that identify this column, banners excluded.

        A spanning banner -- "Detoxification: Medication or Placebo Phase" over six study
        days -- is drawn inside one of the cells it covers, so it lands on a single column
        and makes that column's header text unlike every other column's. Including it in
        the signature stops Study Day 2 matching Study Day 2. Banners belong to
        `column_groups`; identity comes from the header rows that give every column a
        value of its own.
        """
        return [self.header_bands[i].data_texts[col_index] for i in self.value_bands]


def _first_body_band(grid: RuledGrid, row_labels: list[str]) -> int | None:
    """The index of the first band that carries a row of the table rather than a header.

    Decided by asking the engines rather than by geometry. Every geometric rule tried for
    this got at least one reference protocol wrong: "the first band with a cell marker"
    swallows a category banner printed above the first data row (protocol 12 prints
    "Screening" there), and "the first band with no header text" swallows the Date and
    Day of Week header rows that protocol 9 leaves empty. The engines already know which
    labels are rows; the lattice only has to say where they sit.

    With one veto. A band captioned with the name of its own axis -- "Study Week", "Study
    Day", "VISIT" -- is a header row whatever the engines call it, and they do sometimes
    call it a row. Accepting that claim shifts the header/body split up by one and every
    column signature is then read from the wrong band, which loses the whole alignment.
    """
    for band in grid.bands:
        if not band.label_text.strip() or is_axis_caption(band.label_text):
            continue
        for label in row_labels:
            if _same_row(label, band.label_text, band.label_lines):
                return band.index
    return None


def _is_banner_band(
    band: SourceBand, leaf_indices: list[int], group_labels: list[str]
) -> bool:
    """True when a header band is a spanning banner rather than a row of column values.

    The engines already know: a banner is what they returned as a `column_groups` entry.
    Asking them is far steadier than guessing from geometry, because a banner drawn across
    six columns is stored in one of them, and "how many columns did this band fill" then
    depends on how many banners the row happens to hold.
    """
    texts = [band.data_texts[i].strip() for i in leaf_indices if band.data_texts[i].strip()]
    if not texts:
        return False
    hits = sum(
        1
        for t in texts
        if any(_same_column(_signature([t]), label) for label in group_labels)
    )
    return hits >= 0.5 * len(texts)


def _page_structure(
    grid: RuledGrid, row_labels: list[str], group_labels: list[str]
) -> _PageStructure | None:
    """Split one page's lattice into header bands, body bands, stub and leaf columns."""
    first_body = _first_body_band(grid, row_labels)
    if first_body is None:
        return None

    header_bands = grid.bands[:first_body]
    body_bands = grid.bands[first_body:]
    if not body_bands:
        return None

    stub_indices, leaf_indices = split_stub_columns(grid, header_bands, body_bands)
    if not leaf_indices:
        return None

    value_bands = [
        i
        for i, band in enumerate(header_bands)
        if not _is_banner_band(band, leaf_indices, group_labels)
        and sum(1 for c in leaf_indices if band.data_texts[c].strip())
        >= _VALUE_BAND_FILL * len(leaf_indices)
    ]
    if not value_bands:
        # No header row gives every column a value of its own. Nothing better to identify
        # columns by than everything printed above them.
        value_bands = list(range(len(header_bands)))

    return _PageStructure(
        grid=grid,
        header_bands=header_bands,
        body_bands=body_bands,
        stub_signatures=[
            _signature([header_bands[b].data_texts[i] for b in value_bands])
            for i in stub_indices
        ],
        leaf_indices=leaf_indices,
        value_bands=value_bands,
    )


# --------------------------------------------------------------------------------------
# merging the per-page structures into one source axis
# --------------------------------------------------------------------------------------


def _splice_start(items, axis, same) -> int:
    """Where in the axis a page's first row or column belongs.

    Splicing has to begin somewhere, and starting every page at position 0 is wrong in the
    common case: a continuation page carrying an entirely new block of visits shares no
    header with the axis, so nothing matches and every one of its columns is inserted at
    the front -- putting Visit 9 to the left of Visit 1. So the page is first scanned for
    anything it has in common with the axis. What it shares fixes the offset; a page that
    shares nothing follows what is already there.
    """
    for offset, item in enumerate(items):
        for position, entry in enumerate(axis):
            if same(item, entry):
                return max(0, position - offset)
    return len(axis)


def _merge_column_axis(structures: list[_PageStructure]) -> list[_AxisColumn]:
    """Splice each page's leaf columns into one left-to-right axis.

    A continuation page may reprint the same visit columns, carry an entirely new set, or
    do both. Columns are therefore matched by header signature and spliced in at the
    cursor, never appended blindly -- and a blank column has no signature, so it can never
    be matched to anything and always takes a place of its own. That is exactly the
    behaviour wanted: two pages each printing one blank column print two blank columns.
    """
    axis: list[_AxisColumn] = []
    for structure in structures:
        signatures = [
            _signature(structure.signature_texts(i)) for i in structure.leaf_indices
        ]
        cursor = _splice_start(
            signatures, axis, lambda s, entry: _same_column(s, entry.signature)
        )
        for col_index in structure.leaf_indices:
            texts = structure.header_texts(col_index)
            signature = _signature(structure.signature_texts(col_index))

            hit = -1
            if signature:
                for j in range(cursor, len(axis)):
                    if _same_column(signature, axis[j].signature):
                        hit = j
                        break

            source = structure.grid.columns[col_index]
            body = " ".join(
                b.data_texts[col_index].strip()
                for b in structure.body_bands
                if b.data_texts[col_index].strip()
            )
            if hit >= 0:
                axis[hit].pages.append(structure.grid.page)
                axis[hit].boxes[structure.grid.page] = (source.x0, source.x1)
                axis[hit].body_text = (axis[hit].body_text + " " + body).strip()
                # A reprinted header may be more complete than the first reading of it.
                for level, text in enumerate(texts):
                    if level < len(axis[hit].header_texts):
                        if not axis[hit].header_texts[level].strip() and text.strip():
                            axis[hit].header_texts[level] = text
                    else:
                        axis[hit].header_texts.append(text)
                cursor = hit + 1
            else:
                axis.insert(
                    cursor,
                    _AxisColumn(
                        header_texts=list(texts),
                        value_texts=list(structure.signature_texts(col_index)),
                        signature=signature,
                        body_text=body,
                        pages=[structure.grid.page],
                        boxes={structure.grid.page: (source.x0, source.x1)},
                    ),
                )
                cursor += 1
    return axis


def _merge_row_axis(structures: list[_PageStructure]) -> list[_AxisRow]:
    """Splice each page's body bands into one top-to-bottom axis, the same way."""
    axis: list[_AxisRow] = []
    for structure in structures:
        cursor = _splice_start(
            structure.body_bands,
            axis,
            lambda band, entry: _same_row(
                normalise_label(band.label_text), entry.label_text, entry.label_lines
            ),
        )
        for band in structure.body_bands:
            key = normalise_label(band.label_text)

            found = (
                _place_in_run(
                    lambda j: _row_score(key, axis[j].label_text, axis[j].label_lines),
                    cursor,
                    len(axis),
                )
                if key
                else None
            )
            hit = -1 if found is None else found

            if hit >= 0:
                axis[hit].pages.append(structure.grid.page)
                axis[hit].has_data = axis[hit].has_data or band.has_data
                axis[hit].boxes[structure.grid.page] = (band.y0, band.y1)
                cursor = hit + 1
            else:
                axis.insert(
                    cursor,
                    _AxisRow(
                        label_text=band.label_text,
                        label_lines=list(band.label_lines),
                        pages=[structure.grid.page],
                        has_data=band.has_data,
                        boxes={structure.grid.page: (band.y0, band.y1)},
                    ),
                )
                cursor += 1
    return axis


# --------------------------------------------------------------------------------------
# applying the axis to the schedule
# --------------------------------------------------------------------------------------


def _align_columns(
    schedule: Schedule, axis: list[_AxisColumn], stub_signatures: set[str]
) -> tuple[list[ExtractionWarning], bool]:
    """Rebuild the leaf column list from the printed axis and remap every cell onto it.

    Returns the warnings raised and whether the schedule's columns are now the printed
    ones -- ``False`` means the alignment declined to act and the engines' columns stand.
    """
    warnings: list[ExtractionWarning] = []
    old_columns = sorted(schedule.columns, key=lambda c: c.index)
    if not old_columns or not axis:
        return warnings, False

    cells_by_column: dict[str, list[Cell]] = {}
    for cell in schedule.cells:
        cells_by_column.setdefault(cell.column_id, []).append(cell)

    def cells_of(column: Column) -> int:
        return len(cells_by_column.get(column.id, []))

    # -- match the engine's columns onto the printed axis, left to right ------------------
    engine_signature = _engine_signatures(old_columns)
    mapping: dict[str, int] = {}       # old column id -> axis position
    unmatched: list[Column] = []
    cursor = 0
    for col in old_columns:
        signature = engine_signature[col.id]
        hit = -1
        if signature:
            for j in range(cursor, len(axis)):
                if _same_column(signature, axis[j].signature):
                    hit = j
                    break
        if hit >= 0:
            mapping[col.id] = hit
            cursor = hit + 1
        else:
            unmatched.append(col)

    unmatched = _fill_gaps_by_position(old_columns, mapping, unmatched, axis, cells_of)

    matched_rate = len(mapping) / len(old_columns)
    if matched_rate < _MIN_COLUMN_MATCH_RATE:
        warnings.append(
            ExtractionWarning(
                type="source_grid_unavailable",
                severity="medium",
                message=(
                    f"The printed column lattice ({len(axis)} columns) could not be matched "
                    f"to the {len(old_columns)} columns the engines read "
                    f"({len(mapping)} matched). The engines' columns were kept unchanged."
                ),
            )
        )
        return warnings, False

    # -- decide what to do with columns the printed grid does not contain -----------------
    dropped: list[Column] = []
    for col in unmatched:
        signature = engine_signature[col.id]
        has_cells = bool(cells_by_column.get(col.id))
        is_stub = any(_same_column(signature, s) for s in stub_signatures)
        if has_cells and not is_stub:
            # Real content with nowhere to put it. Something is wrong with the lattice or
            # with the reading; either way, reshaping the table now would be a guess.
            warnings.append(
                ExtractionWarning(
                    type="source_grid_unavailable",
                    severity="medium",
                    message=(
                        f"Column {col.index} ({signature or 'unlabelled'!r}) carries cells "
                        f"but matches no column in the printed grid, so the columns were "
                        f"left as the engines read them."
                    ),
                    column_id=col.id,
                )
            )
            return warnings, False
        dropped.append(col)

    # -- build the new column list straight from the printed axis -------------------------
    # Header depth comes from the columns the engines read, not from the lattice: the
    # lattice counts a spanning banner as a header band, while the engines keep banners in
    # `column_groups`. Using the lattice's depth would give a restored column a header row
    # that no other column has.
    depth = max(
        (len(c.header_cells) for c in old_columns if c.id in mapping),
        default=max((len(a.header_texts) for a in axis), default=0),
    )
    role_by_level = _roles_by_level(old_columns, mapping, depth)

    new_columns: list[Column] = []
    old_by_axis = {pos: col for col, pos in ((c, mapping[c.id]) for c in old_columns if c.id in mapping)}
    id_map: dict[str, str] = {}

    for position, entry in enumerate(axis):
        source = old_by_axis.get(position)
        new_id = f"{schedule.id}-c{position}"
        if source is not None:
            column = source.model_copy(deep=True)
            column.id = new_id
            column.index = position
            column.pages = list(entry.pages) or column.pages
            id_map[source.id] = new_id

            # The engines placed this column but read no header on it -- it was matched by
            # position, between two columns they did read. The page prints a header there,
            # so use it: an unlabelled column in the output is a visit nobody can identify.
            printed = [t.strip() for t in entry.printed_texts]
            if not any(h.text.strip() for h in column.header_cells) and any(printed):
                column.header_cells = [
                    HeaderCell(
                        role=role_by_level[level],
                        text=printed[level] if level < len(printed) else "",
                    )
                    for level in range(depth)
                ]
                if Engine.GEOMETRIC not in column.engines:
                    column.engines.append(Engine.GEOMETRIC)
                warnings.append(
                    ExtractionWarning(
                        type="column_missing_in_engine",
                        severity="medium",
                        message=(
                            f"The engines returned this column with no header; the source "
                            f"prints {' / '.join(t for t in printed if t)!r} above it, "
                            f"which was taken from the page."
                        ),
                        column_id=new_id,
                        engine="vision",
                    )
                )
        else:
            # A restored column is described by the header rows that give every column a
            # value, not by the banners above them -- those are `column_groups`.
            printed = entry.printed_texts
            column = Column(
                id=new_id,
                index=position,
                header_cells=[
                    HeaderCell(
                        role=role_by_level[level],
                        text=printed[level].strip() if level < len(printed) else "",
                    )
                    for level in range(depth)
                ],
                pages=list(entry.pages),
                engines=[],
            )
            warnings.append(
                ExtractionWarning(
                    type="column_missing_in_engine",
                    severity="medium" if entry.blank else "high",
                    message=_restored_column_message(entry, schedule),
                    column_id=new_id,
                )
            )
        column.printed_blank = entry.blank and not any(
            h.text.strip() for h in column.header_cells
        )
        new_columns.append(column)

    # -- remap everything that referred to a column by id ---------------------------------
    # Keyed on `mapping`, never on membership of the new id set: the new ids reuse the old
    # naming scheme, so a dropped column's id can coincide with a *different* column's new
    # id, and a membership test would silently reattach its cells to that column.
    schedule.cells = [c for c in schedule.cells if c.column_id in id_map]
    for cell in schedule.cells:
        cell.column_id = id_map[cell.column_id]
    for group in schedule.column_groups:
        group.span = [id_map[s] for s in group.span if s in id_map]
    for footnote in schedule.footnotes:
        footnote.attached_to = [
            a for a in footnote.attached_to if a.column_id is None or a.column_id in id_map
        ]
        for anchor in footnote.attached_to:
            if anchor.column_id:
                anchor.column_id = id_map[anchor.column_id]
    schedule.columns = new_columns

    for col in dropped:
        signature = engine_signature[col.id]
        lost = len(cells_by_column.get(col.id, []))
        warnings.append(
            ExtractionWarning(
                type="column_not_in_source_grid",
                severity="medium" if lost else "low",
                message=(
                    f"Dropped a column the engines reported that the source does not draw: "
                    f"{signature or 'unlabelled'!r}"
                    + (
                        f", along with {lost} cell(s) placed in it."
                        if lost
                        else ". The printed grid rules the row-label area and the visit "
                        "columns; the header stub inside it is not a visit."
                    )
                ),
            )
        )
    return warnings, True


def _restored_column_message(entry: _AxisColumn, schedule: Schedule) -> str:
    """Say what the source draws in a column that neither engine reported.

    Three cases, and they are genuinely different. A column with a header the engines
    missed is a dropped visit and reads as one. A column with nothing in it at all is a
    printed blank, kept because dropping it moves every visit to its right. And a narrow
    column ruled to hold a sideways RANDOMIZATION divider is neither: it carries ink, but
    the ink is an annotation the schema already keeps in `rotated_annotations`, so the
    column is empty *as a column*.
    """
    if not entry.blank:
        return (
            f"The source draws a column here that neither engine reported: "
            f"{entry.signature!r}. A dropped column is a patient visit nobody built."
        )

    for annotation in schedule.rotated_annotations:
        box = annotation.bbox
        span = entry.boxes.get(box.page) if box else None
        if span and span[0] - 2.0 <= box.x0 <= span[1] + 2.0:
            return (
                f"The source rules a column here to hold the sideways {annotation.text!r} "
                f"divider. It carries no grid data -- the label is kept as a rotated "
                f"annotation, not as rows -- but the column is printed, so it is kept and "
                f"the columns to its right stay in their printed positions."
            )

    letters = re.sub(r"[^a-z0-9]", "", entry.body_text.lower())
    if letters:
        return (
            f"The source draws a column here with no header that neither engine reported. "
            f"What is printed in it is {entry.body_text[:120]!r}; no cells were created "
            f"from it. The column is kept so the columns to its right stay aligned."
        )
    return (
        "The source draws a column here that neither engine reported -- it is printed "
        "blank, with no visit header and no cells. It is kept so that the columns to its "
        "right stay aligned with the printed grid, and nothing is inferred about what it "
        "might have been."
    )


def _fill_gaps_by_position(
    columns: list[Column],
    mapping: dict[str, int],
    unmatched: list[Column],
    axis: list[_AxisColumn],
    cells_of,
) -> list[Column]:
    """Place columns whose header text identifies nothing, using the ones around them.

    A model reading a table with two columns headed "-4 to 0*" will sometimes label only
    the first and leave the second's header empty. An empty header matches nothing, but
    the column is not homeless: it sits between two columns that *did* match, and the gap
    between their positions on the printed axis is exactly where it belongs.

    The gap is only closed when the count works out, and a column carrying cells is never
    placed on a source column that is printed blank -- a blank column holds nothing, so
    anything with content in it is by definition something else. Anything left over stays
    unmatched, and the caller decides whether that is fatal.
    """
    if not unmatched:
        return unmatched

    anchors = [(i, mapping[c.id]) for i, c in enumerate(columns) if c.id in mapping]
    still: list[Column] = []

    bounds = [(-1, -1)] + anchors + [(len(columns), len(axis))]
    taken = set(mapping.values())
    for (left_i, left_j), (right_i, right_j) in zip(bounds, bounds[1:]):
        gap = [columns[i] for i in range(left_i + 1, right_i) if columns[i].id not in mapping]
        if not gap:
            continue
        free = [j for j in range(left_j + 1, right_j) if j not in taken]
        with_data = [j for j in free if not axis[j].blank]

        if len(gap) == len(free):
            slots = free
        elif len(gap) == len(with_data) and all(cells_of(c) for c in gap):
            slots = with_data
        else:
            still.extend(gap)
            continue

        for column, slot in zip(gap, slots):
            mapping[column.id] = slot
            taken.add(slot)

    return still


def _engine_signatures(columns: list[Column]) -> dict[str, str]:
    """Identifying header text for each column the engines read.

    A caption the model repeats on every column -- it will happily copy the "Study Week"
    stub caption into all eight columns' header cells -- says nothing about which column
    is which, and leaving it in swamps the short value that does. Anything printed on
    most of the columns is therefore dropped before the signature is built.
    """
    counts: dict[str, int] = {}
    for col in columns:
        for text in {_signature([h.text]) for h in col.header_cells if h.text.strip()}:
            counts[text] = counts.get(text, 0) + 1

    ubiquitous = {
        text
        for text, n in counts.items()
        if len(columns) > 2 and n >= _UBIQUITOUS_TEXT * len(columns)
    }
    out: dict[str, str] = {}
    for col in columns:
        texts = [h.text for h in col.header_cells if _signature([h.text]) not in ubiquitous]
        out[col.id] = _signature(texts)
    return out


def _roles_by_level(
    old_columns: list[Column], mapping: dict[str, int], depth: int
) -> list[str]:
    """The header role for each stacked header row, taken from the columns that matched.

    A column the engines never saw still needs its header cells typed, and the rows of a
    header are the same across the table: whatever role the matched columns agree on at
    a given level is the role for that level.
    """
    roles: list[str] = []
    for level in range(depth):
        votes: dict[str, int] = {}
        for col in old_columns:
            if col.id not in mapping or level >= len(col.header_cells):
                continue
            role = col.header_cells[level].role
            votes[role] = votes.get(role, 0) + 1
        roles.append(max(votes, key=votes.get) if votes else "other")
    return roles


def _drop_header_rows(
    schedule: Schedule, axis: list[_AxisRow], header_bands: list[SourceBand]
) -> list[ExtractionWarning]:
    """Remove rows the engines reported that the source prints as part of the header.

    A model reading a stacked header will occasionally return its bottom row as a row of
    the table -- "Study Week" against protocol 12, "Study Day" against protocol 5 -- and
    that row then sits in the output as an assessment nobody performs.

    Three conditions, all required, because deleting a row is the one thing this pass must
    not get wrong: the label has to match a band the source draws *above* the body, it must
    match no body band at all, and the row must carry nothing but the header's own values.
    """
    warnings: list[ExtractionWarning] = []
    if not header_bands:
        return warnings

    cells_by_row: dict[str, list[Cell]] = {}
    for cell in schedule.cells:
        cells_by_row.setdefault(cell.row_id, []).append(cell)

    drop: set[str] = set()
    for row in schedule.rows:
        key = normalise_label(row.label)
        if not key:
            continue
        if any(_same_row(key, e.label_text, e.label_lines) for e in axis):
            continue
        band = next(
            (b for b in header_bands if _same_row(key, b.label_text, b.label_lines)), None
        )
        if band is None:
            continue

        printed = {normalise_label(t) for t in band.data_texts if t.strip()}
        values = {normalise_label(c.raw) for c in cells_by_row.get(row.id, []) if c.raw.strip()}
        if values - printed:
            continue

        drop.add(row.id)
        warnings.append(
            ExtractionWarning(
                type="row_not_in_source_grid",
                severity="low",
                message=(
                    f"Dropped {row.label!r}: the source prints it in the table's header, "
                    f"above the first row of the body, and the engines returned it as an "
                    f"assessment. It carries no data of its own."
                ),
                row_id=row.id,
            )
        )

    if drop:
        schedule.rows = [r for r in schedule.rows if r.id not in drop]
        schedule.cells = [c for c in schedule.cells if c.row_id not in drop]
        for footnote in schedule.footnotes:
            footnote.attached_to = [
                a for a in footnote.attached_to if a.row_id not in drop
            ]
    return warnings


def _align_rows(
    schedule: Schedule, axis: list[_AxisRow]
) -> tuple[list[ExtractionWarning], dict[str, int]]:
    """Collapse rows the engines split out of one printed row back into that row.

    Returns the warnings raised and, for every row that could be placed, the index of the
    printed band it sits in -- which is what lets bounding boxes be taken from the page.
    """
    warnings: list[ExtractionWarning] = []
    rows = sorted(schedule.rows, key=lambda r: r.index)
    if not rows or not axis:
        return warnings, {}

    # -- place each reported row on a printed band ----------------------------------------
    placement: list[int | None] = []
    cursor = 0
    for row in rows:
        key = normalise_label(row.label)
        hit = (
            _place_in_run(
                lambda j: _row_score(key, axis[j].label_text, axis[j].label_lines),
                cursor,
                len(axis),
            )
            if key
            else None
        )
        placement.append(hit)
        if hit is not None:
            # Deliberately not hit + 1: the next row may belong to this same printed row,
            # which is the case this whole pass exists to find.
            cursor = hit

    # -- group consecutive rows that landed on the same band ------------------------------
    groups: list[list[int]] = []
    for i, band in enumerate(placement):
        if band is not None and groups and placement[groups[-1][-1]] == band:
            groups[-1].append(i)
        else:
            groups.append([i])

    merged_any = False
    band_of_row: dict[str, int] = {}
    cells_by_row: dict[str, list[Cell]] = {}
    for cell in schedule.cells:
        cells_by_row.setdefault(cell.row_id, []).append(cell)

    new_rows: list[Row] = []
    for group in groups:
        members = [rows[i] for i in group]
        head = members[0]
        if placement[group[0]] is not None:
            band_of_row[head.id] = placement[group[0]]
        if len(members) == 1:
            # A row the engines already reported as one merged printed row keeps its own
            # line breakdown -- but only if its lines really do read as separate
            # activities, since a wrapped label looks identical from here.
            if not head.label_lines and head.label.strip():
                head.label_lines = [head.label]
            head.merged_label = head.merged_label and lines_stand_alone(head.label_lines)
            if not head.merged_label and len(head.label_lines) > 1:
                head.label_lines = [" ".join(head.label_lines)]
                head.label = head.label_lines[0]
            new_rows.append(head)
            continue

        # The engines split one printed row. Usually that means they read a merged label
        # cell as several activities; sometimes it means they split a label that merely
        # wrapped. Either way the row is one row, and the same test decides how to write
        # its label as decides it in the pipeline.
        lines = [m.label for m in members if m.label.strip()]
        merged_any = True
        head.merged_label = lines_stand_alone(lines)
        head.label_lines = lines if head.merged_label else [" ".join(lines)]
        head.label = "\n".join(head.label_lines)
        for other in members[1:]:
            for marker in other.footnote_refs:
                if marker not in head.footnote_refs:
                    head.footnote_refs.append(marker)
            for engine in other.engines:
                if engine not in head.engines:
                    head.engines.append(engine)

        _merge_cells(schedule, head, members, cells_by_row)
        warnings.append(
            ExtractionWarning(
                type="merged_source_row_restored",
                severity="medium",
                message=(
                    f"The source draws one row here; the engines returned {len(members)}. "
                    + (
                        f"Its label cell names {len(head.label_lines)} activities "
                        f"({'; '.join(head.label_lines)}), and they were merged back into "
                        f"the single printed row so its cells stay attached to all of them."
                        if head.merged_label
                        else f"The label {head.label!r} was split at a line wrap; the "
                        f"lines were rejoined."
                    )
                ),
                row_id=head.id,
            )
        )
        new_rows.append(head)

    for index, row in enumerate(new_rows):
        row.index = index
    schedule.rows = new_rows
    if not merged_any:
        return warnings, band_of_row

    kept = {r.id for r in new_rows}
    schedule.cells = [c for c in schedule.cells if c.row_id in kept]
    for footnote in schedule.footnotes:
        footnote.attached_to = [
            a for a in footnote.attached_to if a.row_id is None or a.row_id in kept
        ]
    return warnings, band_of_row


def _merge_cells(
    schedule: Schedule,
    head: Row,
    members: list[Row],
    cells_by_row: dict[str, list[Cell]],
) -> None:
    """Move every member row's cells onto the single printed row.

    The engines normally put the whole printed row's marks on whichever of the split rows
    they placed first, so this is usually a no-op for all but one member. Where two
    members really do disagree about the same column the cell is kept and marked
    ambiguous, because the source drew one cell there and we cannot know which reading of
    it was meant.
    """
    by_column: dict[str, Cell] = {}
    for member in members:
        for cell in cells_by_row.get(member.id, []):
            existing = by_column.get(cell.column_id)
            if existing is None:
                cell.row_id = head.id
                by_column[cell.column_id] = cell
            elif normalise_label(existing.raw) != normalise_label(cell.raw):
                existing.ambiguous = True
                existing.notes = (
                    (existing.notes + " ") if existing.notes else ""
                ) + (
                    f"The engines split this printed row and read this cell as "
                    f"{existing.raw!r} against {member.label!r} and {cell.raw!r} against "
                    f"another line of the same cell."
                )

    keep_ids = {id(c) for c in by_column.values()}
    member_ids = {m.id for m in members}
    schedule.cells = [
        c for c in schedule.cells if c.row_id not in member_ids or id(c) in keep_ids
    ]


# --------------------------------------------------------------------------------------
# source coordinates
# --------------------------------------------------------------------------------------


def _attach_boxes(
    schedule: Schedule,
    structures: list[_PageStructure],
    row_axis: list[_AxisRow],
    column_axis: list[_AxisColumn],
    band_of_row: dict[str, int],
    columns_aligned: bool,
) -> None:
    """Give every row, column and cell the rectangle the source draws it in.

    Until now a cell's box was borrowed from wherever the geometric engine happened to
    put a matching word, which is an approximation of the *ink*. Once the lattice is
    known the actual ruled cell is available, so clicking a cell in the UI highlights the
    box on the page rather than a guess at it -- and a cell the source leaves blank still
    has somewhere to point at.
    """
    label_x = {s.grid.page: (s.grid.label_x0, s.grid.label_x1) for s in structures}
    header_y = {
        s.grid.page: (s.header_bands[0].y0, s.header_bands[-1].y1)
        for s in structures
        if s.header_bands
    }

    for row in schedule.rows:
        band = band_of_row.get(row.id)
        if band is None or band >= len(row_axis):
            continue
        entry = row_axis[band]
        page = entry.pages[0]
        if page not in label_x or page not in entry.boxes:
            continue
        x0, x1 = label_x[page]
        y0, y1 = entry.boxes[page]
        row.page = page
        row.bbox = BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1)

    if not columns_aligned:
        return

    for column in schedule.columns:
        entry = column_axis[column.index] if column.index < len(column_axis) else None
        if entry is None or not entry.pages:
            continue
        page = entry.pages[0]
        if page not in entry.boxes or page not in header_y:
            continue
        x0, x1 = entry.boxes[page]
        y0, y1 = header_y[page]
        column.bbox = BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1)

    column_by_id = {c.id: c.index for c in schedule.columns}
    for cell in schedule.cells:
        band = band_of_row.get(cell.row_id)
        col_index = column_by_id.get(cell.column_id)
        if band is None or col_index is None or band >= len(row_axis):
            continue
        if col_index >= len(column_axis):
            continue
        row_entry, col_entry = row_axis[band], column_axis[col_index]
        shared = [p for p in row_entry.boxes if p in col_entry.boxes]
        if not shared:
            continue
        page = shared[0]
        y0, y1 = row_entry.boxes[page]
        x0, x1 = col_entry.boxes[page]
        cell.bbox = BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1)


# --------------------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------------------


def align_to_source_grid(
    schedule: Schedule, doc: PdfDoc, span: TableSpan
) -> list[ExtractionWarning]:
    """Reshape ``schedule`` so its rows and columns are the ones the source prints.

    Mutates the schedule in place and returns the warnings raised. Does nothing at all
    when the lattice cannot be read on every page of the table.
    """
    pages = [p for p in schedule.pages if 1 <= p <= len(doc)]
    if not pages:
        return []

    grids: list[RuledGrid] = []
    for page_no in pages:
        start_y = span.starts_at_y if page_no == span.pages[0] else None
        grid = read_grid(doc.page_no(page_no), start_y=start_y)
        if grid is None:
            return [
                ExtractionWarning(
                    type="source_grid_unavailable",
                    severity="low",
                    message=(
                        f"Page {page_no} does not draw a table grid this could read back, "
                        f"so the row and column structure is left exactly as the engines "
                        f"reported it. Blank printed columns and merged row labels are "
                        f"not verified for this schedule."
                    ),
                )
            ]
        grids.append(grid)

    row_labels = [normalise_label(r.label) for r in schedule.rows if r.label.strip()]
    row_labels += [normalise_label(g.label) for g in schedule.row_groups if g.label.strip()]
    row_labels = [label for label in row_labels if label]

    group_labels = [
        _signature([g.label]) for g in schedule.column_groups if g.label.strip()
    ]

    structures: list[_PageStructure] = []
    for grid in grids:
        structure = _page_structure(grid, row_labels, group_labels)
        if structure is None:
            return [
                ExtractionWarning(
                    type="source_grid_unavailable",
                    severity="low",
                    message=(
                        f"None of the rows read from page {grid.page} could be located in "
                        f"that page's printed grid, so the structure is left as the "
                        f"engines reported it."
                    ),
                )
            ]
        structures.append(structure)

    stub_signatures = {s for st in structures for s in st.stub_signatures if s}
    row_axis = _merge_row_axis(structures)
    column_axis = _merge_column_axis(structures)

    warnings = _drop_header_rows(
        schedule, row_axis, [b for st in structures for b in st.header_bands]
    )
    row_warnings, band_of_row = _align_rows(schedule, row_axis)
    warnings += row_warnings
    column_warnings, columns_aligned = _align_columns(
        schedule, column_axis, stub_signatures
    )
    warnings += column_warnings

    for row in schedule.rows:
        if not row.label_lines and row.label.strip():
            row.label_lines = [row.label]

    _attach_boxes(
        schedule, structures, row_axis, column_axis, band_of_row, columns_aligned
    )

    if any(w.type in {"merged_source_row_restored", "column_missing_in_engine"} for w in warnings):
        schedule.assumptions.append(
            "Rows and columns were checked against the grid the source actually draws: "
            "a column the page rules but leaves blank is kept so the visits to its right "
            "stay in their printed positions, and several activities printed inside one "
            "row-label cell are kept as the single row the page draws."
        )
    return warnings

# --------------------------------------------------------------------------------------
# reading the printed grid back out, for scoring
# --------------------------------------------------------------------------------------


def printed_cells(
    schedule: Schedule, doc: PdfDoc, span: TableSpan
) -> dict[tuple[int, int], str] | None:
    """Every cell the source prints, keyed by its (row, column) position in the grid.

    The point of this is measurement. Ground truth for these tables used to be a hand
    count, and a hand count was wrong on three of the six reference schedules -- an empty
    ruled column is easy to miss, and so is a ruled row holding three activities. The page
    draws the grid, so the grid can be read back and an extraction scored against it cell
    by cell, without anybody keying anything.

    Keyed by *position*, not by label. Two of these protocols have damaged font encodings,
    so the text layer renders "Informed Consent" as "I nformed Consent" and any comparison
    keyed on text scores every cell wrong for a reason that has nothing to do with the
    extraction. Positions are exactly what alignment establishes, so they are what to
    compare -- and they are what actually matters, since a cell in the wrong column is a
    visit on the wrong week whatever its label says.

    Returns ``None`` when the lattice cannot be read on every page, which is the same
    condition under which alignment declines to act. Not independent of
    :func:`align_to_source_grid` -- both read the same lattice -- so it measures whether
    the content landed in the right boxes, not whether the boxes themselves are right.
    """
    pages = [p for p in schedule.pages if 1 <= p <= len(doc)]
    grids = []
    for page_no in pages:
        start_y = span.starts_at_y if page_no == span.pages[0] else None
        grid = read_grid(doc.page_no(page_no), start_y=start_y)
        if grid is None:
            return None
        grids.append(grid)

    row_labels = [normalise_label(r.label) for r in schedule.rows if r.label.strip()]
    row_labels += [normalise_label(g.label) for g in schedule.row_groups if g.label.strip()]
    group_labels = [
        _signature([g.label]) for g in schedule.column_groups if g.label.strip()
    ]

    structures = []
    for grid in grids:
        structure = _page_structure(grid, [x for x in row_labels if x], group_labels)
        if structure is None:
            return None
        structures.append(structure)

    column_axis = _merge_column_axis(structures)

    # Category banners are rows on the page but `row_groups` in the output, so they are
    # dropped from the printed axis before it is numbered. Without this every position
    # below the first banner is off by one and nothing lines up.
    banners = [normalise_label(g.label) for g in schedule.row_groups if g.label.strip()]
    row_axis = [
        entry
        for entry in _merge_row_axis(structures)
        if not any(
            _same_row(b, entry.label_text, entry.label_lines) for b in banners if b
        )
    ]

    row_at = {}
    for position, entry in enumerate(row_axis):
        for page in entry.boxes:
            row_at[(page, entry.boxes[page])] = position
    col_at = {}
    for position, entry in enumerate(column_axis):
        for page in entry.boxes:
            col_at[(page, entry.boxes[page])] = position

    out: dict[tuple[int, int], str] = {}
    for structure in structures:
        page = structure.grid.page
        for band in structure.body_bands:
            r = row_at.get((page, (band.y0, band.y1)))
            if r is None:
                continue
            for i in structure.leaf_indices:
                source = structure.grid.columns[i]
                c = col_at.get((page, (source.x0, source.x1)))
                value = band.data_texts[i].strip()
                if c is not None and value:
                    out[(r, c)] = value
    return out
