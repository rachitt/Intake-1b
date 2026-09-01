"""The SoA output schema.

Design notes (these are the choices defended in the README):

* A document yields a *list* of schedules, not one. Protocols routinely carry a main
  schedule plus a PK/blood sub-schedule, a sub-study schedule, or a long-term extension
  schedule, and at least one of the five reference protocols has two.

* Cells are a *sparse list* keyed by ``(row_id, column_id)`` rather than a dense matrix.
  An SoA that runs across pages is frequently ragged -- a continuation page may carry a
  different set of visit columns than the first page (see the "disjoint visit ranges"
  case). A dense matrix forces us to invent cells that the document does not contain;
  a sparse list represents exactly what was observed and nothing more.

* **The printed grid is the grid.** ``Column.printed_blank``, ``Row.label_lines`` and
  ``Row.merged_label`` exist so that a column the page rules but leaves empty, and a row
  whose one label cell names three activities, are both representable exactly as printed.
  Compacting either away reads better and is wrong: dropping a blank column moves every
  visit to its right one place left, and splitting a merged label invents rows that carry
  no data. See :mod:`soa.align`.

* ``Cell.raw`` is required and is never normalised. Everything derived from it
  (``value_text``, ``footnote_refs``) is optional and explicitly secondary. There is no
  code path that can silently reduce a cell to a boolean.

* Footnotes carry ``pages`` as a *list*, so a block that spills across a page break is a
  representable state rather than a bug, and ``attached_to`` is a list of typed anchors
  so that "marker c sits on the Week 4 ECG cell" is expressible.

* Anything the extractor could not confidently interpret is recorded (``ambiguous``,
  ``unattached_reason``, ``ExtractionWarning``) rather than resolved. Be faithful, not
  clever.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------------------


class BBox(BaseModel):
    """A rectangle on a page, in PDF points, origin top-left."""

    page: int = Field(description="1-indexed page number in the source PDF")
    x0: float
    y0: float
    x1: float
    y1: float


class Engine(str, Enum):
    """Which extraction engine produced or corroborated a piece of output."""

    GEOMETRIC = "geometric"
    VISION = "vision"


class ScheduleKind(str, Enum):
    """What sort of schedule this is. Classified from the heading, never guessed silently."""

    MAIN = "main"
    PK = "pk"
    SUB_STUDY = "sub_study"
    EXTENSION = "extension"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------------------
# column axis
# --------------------------------------------------------------------------------------


class ColumnGroup(BaseModel):
    """A spanning header cell above the leaf columns -- e.g. a study period banner.

    Column headers are hierarchical: a "Treatment" banner spans several visit columns,
    which each carry a visit number, a study day and a window. Flattening that into one
    string loses the grouping, so groups are first-class and leaf columns point at them.
    """

    id: str
    label: str = Field(description="Verbatim group label, e.g. 'Detoxification Phase'")
    level: int = Field(0, description="0 is the outermost banner; deeper levels nest")
    parent_id: str | None = None
    span: list[str] = Field(
        default_factory=list, description="ids of the leaf columns this group covers"
    )
    footnote_refs: list[str] = Field(default_factory=list)
    bbox: BBox | None = None


class HeaderCell(BaseModel):
    """One cell from one of the stacked header rows above a leaf column."""

    role: str = Field(
        description=(
            "What this header row is: visit_name, visit_number, study_day, study_week, "
            "date, day_of_week, visit_window, or 'other' when it does not map cleanly"
        )
    )
    text: str = Field(description="Verbatim header text for this column, as printed")
    footnote_refs: list[str] = Field(default_factory=list)


class VisitWindow(BaseModel):
    """An allowable visit window, e.g. 'Day 15 +/- 3 days'."""

    raw: str = Field(description="Verbatim window text as printed")
    value: float | None = None
    unit: str | None = Field(None, description="days | weeks | hours | months")
    asymmetric: dict[str, float] | None = Field(
        None, description="e.g. minus 2 / plus 5, when the window is not symmetric"
    )


class Column(BaseModel):
    """One leaf column of the grid -- normally a single visit or timepoint."""

    id: str
    index: int = Field(description="Left-to-right position among leaf columns, 0-based")
    header_cells: list[HeaderCell] = Field(
        default_factory=list,
        description="One entry per stacked header row, top to bottom, verbatim",
    )
    group_path: list[str] = Field(
        default_factory=list, description="ColumnGroup ids, outermost first"
    )

    # Parsed conveniences. Derived from header_cells; may be null. header_cells is
    # authoritative, these are not.
    visit_label: str | None = None
    visit_number: str | None = None
    study_day: str | None = None
    study_week: str | None = None
    visit_window: VisitWindow | None = None

    printed_blank: bool = Field(
        False,
        description=(
            "True when the source rules this column but prints nothing in it -- no visit "
            "header and no cells. Such a column is kept rather than compacted away, "
            "because dropping it shifts every visit to its right one place left. Its "
            "emptiness is a fact about the document, not a gap in the extraction; nothing "
            "is inferred about what it might have been."
        ),
    )

    footnote_refs: list[str] = Field(default_factory=list)
    pages: list[int] = Field(
        default_factory=list, description="Pages on which this column appears"
    )
    bbox: BBox | None = None
    engines: list[Engine] = Field(
        default_factory=list, description="Which engines saw this column"
    )


# --------------------------------------------------------------------------------------
# row axis
# --------------------------------------------------------------------------------------


class RowGroup(BaseModel):
    """A category header row -- 'Safety Assessments', 'Efficacy'.

    These are structure, not assessments. Keeping them out of ``rows`` is the whole point:
    a consumer counting scheduled activities must not count 'Safety Assessments' as one.
    """

    id: str
    label: str
    level: int = 0
    parent_id: str | None = None
    page: int | None = None
    footnote_refs: list[str] = Field(default_factory=list)
    bbox: BBox | None = None


class Row(BaseModel):
    """One assessment / procedure / activity row."""

    id: str
    index: int = Field(description="Top-to-bottom position, 0-based")
    label: str = Field(
        description=(
            "Verbatim row label. A label that merely wraps across several printed lines "
            "is joined with a space. A row whose single label cell holds several distinct "
            "activities keeps the printed line breaks as newlines and sets `merged_label`; "
            "`label_lines` holds the same text already split."
        )
    )
    label_lines: list[str] = Field(
        default_factory=list,
        description=(
            "The distinct label entries printed inside this row's one label cell, in "
            "order. Normally a single entry. More than one means the source drew one row "
            "against several named activities."
        ),
    )
    merged_label: bool = Field(
        False,
        description=(
            "True when the source draws ONE row whose label cell names several activities "
            "-- e.g. 'Study drug record / Medications dispensed / Medications returned' "
            "with one set of X marks. Splitting such a cell into one row per activity "
            "reads better but is not what the page prints: it invents rows that carry no "
            "data and detaches the marks from all but the first activity."
        ),
    )
    row_span: int = Field(
        1,
        description=(
            "Number of printed row bands this row occupies. 1 unless a label cell is "
            "drawn spanning several ruled rows."
        ),
    )
    group_path: list[str] = Field(
        default_factory=list, description="RowGroup ids, outermost first"
    )
    is_category_header: bool = Field(
        False,
        description=(
            "True when this row is itself a structural banner that was also emitted as a "
            "RowGroup. Kept in rows only when the source prints data on the same line."
        ),
    )
    footnote_refs: list[str] = Field(default_factory=list)
    page: int | None = None
    bbox: BBox | None = None
    engines: list[Engine] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# cells
# --------------------------------------------------------------------------------------


class Cell(BaseModel):
    """One (row, column) intersection that carries content.

    Empty cells are omitted entirely; absence means "nothing printed here".
    """

    row_id: str
    column_id: str

    raw: str = Field(
        description=(
            "VERBATIM cell content, character for character, including any footnote "
            "marker printed inside it. Never normalised, never reduced to a boolean. "
            "Examples seen in real protocols: X / Xa / 3X/week / (X) / 10 mL S / "
            "40 mg cocaine i.v. / Q2W / an em-dash"
        )
    )
    value_text: str | None = Field(
        None,
        description=(
            "Best-effort content with footnote markers stripped. Derived and secondary; "
            "consumers that care about fidelity read `raw`."
        ),
    )
    footnote_refs: list[str] = Field(default_factory=list)

    row_span: int = 1
    col_span: int = Field(
        1,
        description=(
            "Number of leaf columns this cell covers. A merged annotation such as "
            "3 X/week for 2 weeks printed across several week columns has col_span > 1."
        ),
    )

    ambiguous: bool = Field(
        False, description="Set when the source is genuinely unclear; see `notes`"
    )
    notes: str | None = Field(
        None, description="Why it is ambiguous, or alternative readings, verbatim"
    )

    bbox: BBox | None = Field(
        None, description="Powers click-a-cell-to-highlight-the-source in the UI"
    )
    engines: dict[str, str] = Field(
        default_factory=dict,
        description="Per-engine reading of this cell, keyed by engine name",
    )
    confidence: float | None = None


# --------------------------------------------------------------------------------------
# footnotes
# --------------------------------------------------------------------------------------


class FootnoteAnchor(BaseModel):
    """What a footnote marker actually sits on.

    A footnote list detached from the grid is not an extraction. This is the linkage.
    """

    kind: Literal["cell", "row", "column", "column_group", "row_group", "table"]
    row_id: str | None = None
    column_id: str | None = None
    group_id: str | None = None
    detail: str | None = Field(
        None, description="Free text for anchors that do not reduce to ids"
    )


class Footnote(BaseModel):
    marker: str = Field(description="Verbatim marker, e.g. a / 3 / * / ** / a dagger")
    marker_style: str = Field(
        description=(
            "superscript_letter | superscript_number | asterisk_tier | symbol | "
            "parenthesized | inline_suffix | unmarked"
        )
    )
    text: str = Field(description="Full footnote text, verbatim, including any continuation")
    text_complete: bool = Field(
        True,
        description=(
            "False when the text appears truncated and the continuation could not be "
            "located. Surfaced as a warning rather than silently accepted."
        ),
    )
    pages: list[int] = Field(
        default_factory=list,
        description="Every page the footnote text occupies. Length > 1 means it spilled.",
    )
    continued_from_page: int | None = Field(
        None, description="Set on a footnote whose text continues from an earlier page"
    )
    attached_to: list[FootnoteAnchor] = Field(default_factory=list)
    unattached_reason: str | None = Field(
        None,
        description=(
            "Set when no anchor could be found. The footnote is still emitted -- dropping "
            "it would be worse than admitting we could not link it."
        ),
    )
    bbox: BBox | None = None


# --------------------------------------------------------------------------------------
# provenance, warnings, reconciliation
# --------------------------------------------------------------------------------------


class LocatorEvidence(BaseModel):
    """Why the locator believes this is an SoA. Makes the locator auditable."""

    score: float
    signals: list[str] = Field(
        default_factory=list,
        description="Named features that fired, e.g. toc_match, x_token_density",
    )
    per_page_scores: dict[str, float] = Field(default_factory=dict)
    heading_source: str | None = Field(None, description="toc | page_heading | inferred")


class ExtractionWarning(BaseModel):
    """Something a reviewer should look at. Recall problems rank highest."""

    type: str = Field(
        description=(
            "row_missing_in_engine | column_missing_in_engine | cell_value_disagreement | "
            "footnote_unlinked | footnote_incomplete | row_count_mismatch | "
            "rotated_text_discarded | engine_failed | low_confidence | "
            "merged_source_row_restored | column_not_in_source_grid | "
            "row_not_in_source_grid | "
            "source_grid_unavailable"
        )
    )
    severity: Literal["high", "medium", "low"] = "medium"
    message: str
    row_id: str | None = None
    column_id: str | None = None
    engine: str | None = None


class Reconciliation(BaseModel):
    """The recall diff between the two engines.

    The brief is explicit that a dropped row or column is the most heavily penalised
    failure, so the pipeline runs two independent engines and reports every disagreement
    instead of quietly picking a winner.
    """

    engines_run: list[str] = Field(default_factory=list)
    engines_failed: dict[str, str] = Field(
        default_factory=dict, description="engine name -> error message"
    )
    row_counts: dict[str, int] = Field(default_factory=dict)
    column_counts: dict[str, int] = Field(default_factory=dict)
    cell_counts: dict[str, int] = Field(default_factory=dict)
    cell_agreement: float | None = Field(
        None, description="Fraction of shared cells where both engines read the same raw"
    )
    warnings: list[ExtractionWarning] = Field(default_factory=list)


class RotatedAnnotation(BaseModel):
    """Rotated text found inside the table area.

    Vertical dividers such as a sideways RANDOMIZATION label printed between two columns
    are annotations, not data. Naive text extraction shreds them into stray single letters
    that then masquerade as rows; capturing them here keeps them out of the grid without
    throwing the information away.
    """

    text: str
    page: int
    rotation_deg: float
    bbox: BBox | None = None
    interpretation: str | None = Field(
        None, description="e.g. column divider between Baseline and Treatment"
    )


# --------------------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------------------


class Schedule(BaseModel):
    """One Schedule of Activities table."""

    id: str
    heading: str = Field(description="Verbatim heading as printed")
    heading_page: int | None = None
    kind: ScheduleKind = ScheduleKind.UNKNOWN
    pages: list[int] = Field(description="Every page the table body occupies, 1-indexed")
    footnote_pages: list[int] = Field(
        default_factory=list, description="Pages carrying this table's footnote block"
    )
    page_orientation: dict[str, str] = Field(
        default_factory=dict, description="page number -> portrait | landscape"
    )

    column_groups: list[ColumnGroup] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
    row_groups: list[RowGroup] = Field(default_factory=list)
    rows: list[Row] = Field(default_factory=list)
    cells: list[Cell] = Field(default_factory=list)
    footnotes: list[Footnote] = Field(default_factory=list)
    rotated_annotations: list[RotatedAnnotation] = Field(default_factory=list)

    locator: LocatorEvidence | None = None
    reconciliation: Reconciliation | None = None
    assumptions: list[str] = Field(
        default_factory=list,
        description="Judgement calls made during extraction, stated rather than hidden",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Things we would ask a clinical SME rather than guess at",
    )


class DocumentInfo(BaseModel):
    source_file: str
    sha256: str
    page_count: int
    title_guess: str | None = None
    sponsor_guess: str | None = None
    protocol_id_guess: str | None = None
    has_text_layer: bool = True
    text_layer_warnings: list[str] = Field(
        default_factory=list,
        description="e.g. broken ToUnicode CMaps, which risk silent character corruption",
    )


class ExtractionRun(BaseModel):
    tool_version: str
    vision_model: str | None = None
    started_at: str
    finished_at: str | None = None
    duration_seconds: float | None = None
    pages_sent_to_vision: int = 0
    warnings: list[ExtractionWarning] = Field(default_factory=list)


class SoADocument(BaseModel):
    """Top-level output. One JSON file per input PDF."""

    schema_version: str = "1.0"
    document: DocumentInfo
    schedules: list[Schedule] = Field(default_factory=list)
    run: ExtractionRun | None = None
