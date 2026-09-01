"""The vision extraction contract: response schema and prompt.

The response schema deliberately mirrors, but does not reuse, ``soa.schema``. The stored
schema carries provenance, reconciliation and bounding boxes that the model has no way to
know about, and structured-output support degrades badly on deeply nested or
union-typed models. So the model is asked for a flat, minimal shape that is easy for it
to fill correctly, and :mod:`soa.extract.vision` maps that onto the real schema.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class VHeaderCell(BaseModel):
    role: str = Field(
        description=(
            "One of: visit_name, visit_number, study_day, study_week, date, day_of_week, "
            "visit_window, other"
        )
    )
    text: str = Field(description="Verbatim text of this header cell, exactly as printed")


class VColumnGroup(BaseModel):
    label: str = Field(description="Verbatim text of the spanning header cell")
    level: int = Field(description="0 for the topmost banner row, 1 for the next, etc.")
    first_column_index: int = Field(description="Index of the leftmost leaf column covered")
    last_column_index: int = Field(description="Index of the rightmost leaf column covered")
    footnote_markers: list[str] = Field(default_factory=list)


class VColumn(BaseModel):
    index: int = Field(description="0-based, left to right, excluding the row-label column")
    header_cells: list[VHeaderCell] = Field(
        description="One per stacked header row, top to bottom, verbatim"
    )
    visit_window_raw: str | None = Field(
        None, description="Verbatim visit window if printed, e.g. '+/- 3 days'"
    )
    footnote_markers: list[str] = Field(default_factory=list)


class VRow(BaseModel):
    index: int = Field(description="0-based, top to bottom, counting every printed row")
    label: str = Field(description="Verbatim row label; join wrapped lines with a space")
    is_category_header: bool = Field(
        description=(
            "True if this row is a section banner such as 'Safety Assessments' or "
            "'Efficacy' rather than an actual assessment. These usually have no data cells."
        )
    )
    category_path: list[str] = Field(
        default_factory=list,
        description="Labels of the category rows this row sits under, outermost first",
    )
    footnote_markers: list[str] = Field(default_factory=list)


class VCell(BaseModel):
    row_index: int
    column_index: int
    raw: str = Field(
        description=(
            "VERBATIM cell content, character for character, including any footnote "
            "marker printed in the cell. Never convert to true/false. Never normalise. "
            "Copy exactly what is printed: X, Xa, 3X, 3X/week, (X), 2X/day, Q2W, an "
            "arrow, a dash, a number, a dose, a volume."
        )
    )
    footnote_markers: list[str] = Field(
        default_factory=list,
        description="Markers printed in this cell, e.g. ['a'] for a superscript a",
    )
    column_span: int = Field(
        1,
        description=(
            "Number of leaf columns this cell visually spans. Use >1 for an annotation "
            "printed across several columns, e.g. '3X/week for 2 weeks' drawn across "
            "three week columns."
        ),
    )
    ambiguous: bool = Field(
        False, description="True if the printed content is genuinely unclear"
    )
    note: str | None = Field(
        None, description="If ambiguous, describe the alternative readings"
    )


class VFootnote(BaseModel):
    marker: str = Field(description="Verbatim marker, e.g. 'a', '1', '*', '**'")
    marker_style: str = Field(
        description=(
            "superscript_letter, superscript_number, asterisk_tier, symbol, "
            "parenthesized, inline_suffix, or unmarked"
        )
    )
    text: str = Field(
        description=(
            "COMPLETE footnote text, verbatim. If the footnote continues onto a later "
            "page image, include the continuation here as one text. Do not truncate."
        )
    )
    continues_across_pages: bool = Field(
        False, description="True if this footnote's text spans a page break"
    )
    text_complete: bool = Field(
        True, description="False if the text appears cut off and no continuation was found"
    )


class VRotatedLabel(BaseModel):
    text: str = Field(description="The label, read in its natural direction")
    interpretation: str | None = Field(
        None, description="What it marks, e.g. 'divider between Baseline and Treatment'"
    )


class VSchedule(BaseModel):
    """One schedule as read from the page images."""

    heading: str = Field(description="Verbatim table heading as printed")
    column_groups: list[VColumnGroup] = Field(default_factory=list)
    columns: list[VColumn] = Field(default_factory=list)
    rows: list[VRow] = Field(default_factory=list)
    cells: list[VCell] = Field(default_factory=list)
    footnotes: list[VFootnote] = Field(default_factory=list)
    rotated_labels: list[VRotatedLabel] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list,
        description="Judgement calls you had to make while reading the table",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Things you would ask a clinical subject matter expert rather than guess",
    )


SYSTEM_PROMPT = """\
You transcribe Schedule of Activities tables from clinical trial protocols into structured
data. You are a transcriber, not an interpreter. Your output is used to build clinical
trial databases, where a dropped row means a missing case report form and a dropped column
means a patient visit nobody built.

Follow these rules exactly.

FIDELITY
- Copy every cell's content character for character into `raw`. Never normalise it, never
  reduce it to true/false, never expand an abbreviation, never fix a typo.
- Cells are not booleans. Real cells include: X, Xa, 3X, 3X/week, 3X/2 weeks, 2X/day, Q2W,
  (X), X (if applicable), arrows spanning a range, dashes, dots, bare numbers, doses like
  "40 mg cocaine i.v.", and volumes like "10 mL S". All of these carry clinical meaning and
  must survive verbatim.
- If a footnote marker is printed inside a cell, keep it in `raw` AND list it separately in
  `footnote_markers`. Do not strip it from `raw`.
- Do not infer, repair or resolve anything the document leaves unclear. If a cell is
  genuinely ambiguous, set `ambiguous` true and describe the readings in `note`. Never
  quietly pick one.

RECALL
- Missing rows and columns are the worst possible error. Emit every printed row and every
  printed column, including ones that are entirely empty.
- Work down the table row by row. Do not skip, summarise, deduplicate or abbreviate. If the
  table has forty rows, return forty rows.
- An empty cell is simply omitted from `cells`; that is different from omitting the row.

STRUCTURE
- Column headers are hierarchical. A study-period banner such as "Screening" or "Treatment"
  spanning several visit columns is a `column_groups` entry, not part of a column's name.
  Each leaf column then carries its own stacked header cells: visit number, study day,
  study week, visit window.
- Row headers are hierarchical too. A row such as "Safety Assessments" or "Efficacy" with no
  data cells is structure, not an assessment: set `is_category_header` true, and put its
  label into the `category_path` of the rows beneath it.
- Column indices are 0-based left to right and EXCLUDE the row-label column.
- Row indices are 0-based top to bottom and INCLUDE category header rows.

MULTI-PAGE TABLES
- The images you receive are consecutive pages of ONE table. Return ONE schedule covering
  all of them.
- A continuation page may repeat the header fully, repeat it abbreviated, or not repeat it
  at all. It may also carry a different set of visit columns from the first page. Merge
  them into one consistent set of columns, and do not duplicate a column just because its
  header was reprinted.
- A page may be rotated or landscape. Read it in its natural orientation.

FOOTNOTES
- Capture every footnote attached to the table, with its full text, verbatim.
- Footnote blocks routinely continue onto the following page, and the continuation often
  has no heading, no marker, and nothing indicating what it belongs to. If a later image
  begins with text that continues a footnote, append it to that footnote's `text` and set
  `continues_across_pages` true. Truncating a footnote at a page boundary is a failure.
- Markers vary: superscript letters, superscript numbers, daggers, asterisks, parenthesised
  letters. A single cell may carry more than one.

ROTATED TEXT
- A word printed sideways between two columns (commonly "RANDOMIZATION") is a divider
  annotation, not a row and not a column. Put it in `rotated_labels`. Never emit its
  letters as rows.
"""


def build_user_prompt(
    heading_hint: str,
    page_numbers: list[str],
    text_layer: str | None,
    text_layer_trustworthy: bool,
) -> str:
    """Assemble the per-request instruction."""
    parts = [
        f"Transcribe the Schedule of Activities shown in these {len(page_numbers)} page "
        f"image(s), which are consecutive pages of one table.",
        f"Source pages: {', '.join(page_numbers)}.",
    ]
    if heading_hint:
        parts.append(
            f'A text-layer scan suggests the heading is near: "{heading_hint}". '
            f"Correct it from the image if it is wrong or incomplete."
        )

    if text_layer:
        if text_layer_trustworthy:
            parts.append(
                "Below is the raw text layer extracted from the same pages. Use it to get "
                "exact spellings and to catch anything faint in the image. Where the image "
                "and the text layer disagree, TRUST THE IMAGE -- the text layer's reading "
                "order is unreliable and it can duplicate or interleave rows."
            )
        else:
            parts.append(
                "Below is the raw text layer from the same pages, but this document has "
                "damaged font encodings, so individual characters in it may be silently "
                "WRONG. Use it only as a weak hint for layout. TRUST THE IMAGE for every "
                "character you transcribe."
            )
        parts.append("--- BEGIN TEXT LAYER ---")
        parts.append(text_layer[:60000])
        parts.append("--- END TEXT LAYER ---")

    parts.append(
        "Return the complete schedule. Count the rows in the image before you finish and "
        "make sure you emitted every one of them."
    )
    return "\n\n".join(parts)
