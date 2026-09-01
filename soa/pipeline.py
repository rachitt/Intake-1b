"""End-to-end orchestration: locate, extract with both engines, reconcile, assemble.

The pipeline degrades rather than fails. If the vision engine is unavailable -- no API key,
a refused model, a network error -- the geometric engine still runs and the output records
which engine was missing and that recall is therefore unverified. If a single schedule
fails to extract, the others still complete.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .extract import geometric, vision
from .extract.prompt import VSchedule
from .footnotes import FOOTNOTE_LINE_RE, link_footnotes, parse_footnote_block
from .locate import locate
from .locate.spans import TableSpan
from .pdfdoc import PdfDoc
from .reconcile import reconcile
from .schema import (
    BBox,
    Cell,
    Column,
    ColumnGroup,
    DocumentInfo,
    ExtractionRun,
    ExtractionWarning,
    Footnote,
    HeaderCell,
    LocatorEvidence,
    Row,
    RowGroup,
    Schedule,
    ScheduleKind,
    SoADocument,
    VisitWindow,
)
from . import __version__

_WINDOW_RE = re.compile(
    r"(?P<sign>[±+]/?-?|\+|-)\s*(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>day|d|week|wk|hour|hr|h|month|mo)s?\b",
    re.I,
)
_UNIT_CANON = {
    "d": "days", "day": "days",
    "wk": "weeks", "week": "weeks",
    "h": "hours", "hr": "hours", "hour": "hours",
    "mo": "months", "month": "months",
}


def parse_visit_window(text: str) -> VisitWindow | None:
    """Pull a visit window out of a header cell. Raw text is always preserved."""
    if not text:
        return None
    m = _WINDOW_RE.search(text)
    if not m:
        return None
    return VisitWindow(
        raw=m.group(0).strip(),
        value=float(m.group("value")),
        unit=_UNIT_CANON.get(m.group("unit").lower(), m.group("unit").lower()),
    )


def _role_value(headers: list[HeaderCell], role: str) -> str | None:
    for h in headers:
        if h.role == role and h.text.strip():
            return h.text.strip()
    return None


def _bbox_for_cell(
    grids: list[geometric.PageGrid], row_label: str, col_index: int
) -> BBox | None:
    """Find the geometric bounding box matching a vision-reported cell.

    This is what makes the UI able to highlight the source region for a cell. The vision
    engine cannot supply coordinates, so they are borrowed from the deterministic engine
    wherever the two agree on where a cell is.
    """
    from .reconcile import normalise_label

    target = normalise_label(row_label)
    if not target:
        return None
    for grid in grids:
        for grow in grid.rows:
            if normalise_label(grow.label) != target:
                continue
            for c in grid.cells:
                if c.row == grow.index and c.col - 1 == col_index:
                    return BBox(page=c.page, x0=c.x0, y0=c.y0, x1=c.x1, y1=c.y1)
    return None


def _assemble_schedule(
    span: TableSpan,
    index: int,
    vis: VSchedule | None,
    grids: list[geometric.PageGrid],
    doc: PdfDoc,
    engines_run: list[str],
    engines_failed: dict[str, str],
) -> Schedule:
    """Turn one span's engine output into a Schedule."""
    sid = f"soa-{index + 1}"

    columns: list[Column] = []
    column_groups: list[ColumnGroup] = []
    rows: list[Row] = []
    row_groups: list[RowGroup] = []
    cells: list[Cell] = []
    footnotes: list[Footnote] = []
    rotated: list = []

    if vis is not None:
        # -- columns ---------------------------------------------------------------------
        for vc in sorted(vis.columns, key=lambda c: c.index):
            headers = [
                HeaderCell(role=h.role, text=h.text, footnote_refs=[])
                for h in vc.header_cells
            ]
            window = None
            if vc.visit_window_raw:
                window = parse_visit_window(vc.visit_window_raw) or VisitWindow(
                    raw=vc.visit_window_raw
                )
            if window is None:
                for h in headers:
                    window = parse_visit_window(h.text)
                    if window:
                        break

            columns.append(
                Column(
                    id=f"{sid}-c{vc.index}",
                    index=vc.index,
                    header_cells=headers,
                    visit_label=_role_value(headers, "visit_name"),
                    visit_number=_role_value(headers, "visit_number"),
                    study_day=_role_value(headers, "study_day"),
                    study_week=_role_value(headers, "study_week"),
                    visit_window=window,
                    footnote_refs=list(vc.footnote_markers),
                    pages=list(span.pages),
                    engines=["vision"],
                )
            )

        col_by_index = {c.index: c for c in columns}

        for gi, vg in enumerate(vis.column_groups):
            span_ids = [
                col_by_index[i].id
                for i in range(vg.first_column_index, vg.last_column_index + 1)
                if i in col_by_index
            ]
            group = ColumnGroup(
                id=f"{sid}-cg{gi}",
                label=vg.label,
                level=vg.level,
                span=span_ids,
                footnote_refs=list(vg.footnote_markers),
            )
            column_groups.append(group)
            for cid in span_ids:
                col = next(c for c in columns if c.id == cid)
                col.group_path.append(group.id)

        # -- rows and row groups ---------------------------------------------------------
        group_by_label: dict[str, RowGroup] = {}
        for vr in sorted(vis.rows, key=lambda r: r.index):
            if vr.is_category_header:
                rg = RowGroup(
                    id=f"{sid}-rg{len(row_groups)}",
                    label=vr.label,
                    level=len(vr.category_path),
                    page=span.pages[0],
                )
                row_groups.append(rg)
                group_by_label[vr.label.strip().lower()] = rg
                continue

            path = [
                group_by_label[p.strip().lower()].id
                for p in vr.category_path
                if p.strip().lower() in group_by_label
            ]
            rows.append(
                Row(
                    id=f"{sid}-r{vr.index}",
                    index=vr.index,
                    label=vr.label,
                    group_path=path,
                    is_category_header=False,
                    footnote_refs=list(vr.footnote_markers),
                    engines=["vision"],
                )
            )

        row_by_index = {int(r.id.rsplit("r", 1)[-1]): r for r in rows}

        # -- cells -----------------------------------------------------------------------
        for vcell in vis.cells:
            row = row_by_index.get(vcell.row_index)
            col = col_by_index.get(vcell.column_index)
            if row is None or col is None:
                continue
            cells.append(
                Cell(
                    row_id=row.id,
                    column_id=col.id,
                    raw=vcell.raw,
                    footnote_refs=list(vcell.footnote_markers),
                    col_span=max(1, vcell.column_span),
                    ambiguous=vcell.ambiguous,
                    notes=vcell.note,
                    bbox=_bbox_for_cell(grids, row.label, col.index),
                    engines={"vision": vcell.raw},
                )
            )

        # -- footnotes -------------------------------------------------------------------
        for vf in vis.footnotes:
            pages = list(span.footnote_pages) or list(span.pages)
            footnotes.append(
                Footnote(
                    marker=vf.marker,
                    marker_style=vf.marker_style,
                    text=vf.text,
                    text_complete=vf.text_complete,
                    pages=pages if vf.continues_across_pages else pages[:1],
                    continued_from_page=pages[0] if vf.continues_across_pages else None,
                )
            )

        from .schema import RotatedAnnotation

        for vr_lab in vis.rotated_labels:
            page_hit = span.pages[0]
            for grid in grids:
                for text, x, y0, y1 in grid.vertical_labels:
                    if text.lower().replace(" ", "") == vr_lab.text.lower().replace(" ", ""):
                        page_hit = grid.page
            rotated.append(
                RotatedAnnotation(
                    text=vr_lab.text,
                    page=page_hit,
                    rotation_deg=90.0,
                    interpretation=vr_lab.interpretation,
                )
            )

    else:
        # Vision unavailable: build a reduced schedule from the geometric grids alone.
        seen_cols = max((len(g.columns) - 1 for g in grids), default=0)
        for i in range(seen_cols):
            headers = []
            for grid in grids:
                for hr in grid.header_rows:
                    if i + 1 < len(hr) and hr[i + 1].strip():
                        headers.append(HeaderCell(role="other", text=hr[i + 1]))
            columns.append(
                Column(
                    id=f"{sid}-c{i}",
                    index=i,
                    header_cells=headers,
                    pages=list(span.pages),
                    engines=["geometric"],
                )
            )
        col_by_index = {c.index: c for c in columns}

        for grid in grids:
            for grow in grid.rows:
                if not grow.label.strip():
                    continue
                row = Row(
                    id=f"{sid}-r{grid.page}-{grow.index}",
                    index=len(rows),
                    label=grow.label,
                    is_category_header=grow.is_category,
                    page=grid.page,
                    bbox=BBox(
                        page=grid.page,
                        x0=grow.label_bbox[0],
                        y0=grow.label_bbox[1],
                        x1=grow.label_bbox[2],
                        y1=grow.label_bbox[3],
                    )
                    if grow.label_bbox
                    else None,
                    engines=["geometric"],
                )
                rows.append(row)
                for c in grid.cells:
                    if c.row != grow.index:
                        continue
                    col = col_by_index.get(c.col - 1)
                    if col is None:
                        continue
                    cells.append(
                        Cell(
                            row_id=row.id,
                            column_id=col.id,
                            raw=c.raw,
                            bbox=BBox(page=c.page, x0=c.x0, y0=c.y0, x1=c.x1, y1=c.y1),
                            engines={"geometric": c.raw},
                        )
                    )

    # -- footnote text from the text layer, for pages the model may have skipped ----------
    if not footnotes:
        footnotes = _footnotes_from_text_layer(doc, span)

    # -- linkage --------------------------------------------------------------------------
    link_footnotes(footnotes, rows, columns, cells, row_groups, column_groups)

    # -- reconciliation -------------------------------------------------------------------
    rows, report = reconcile(
        rows, columns, cells, grids, engines_run, engines_failed
    )

    for fn in footnotes:
        if fn.unattached_reason:
            report.warnings.append(
                ExtractionWarning(
                    type="footnote_unlinked",
                    severity="medium",
                    message=f"Footnote '{fn.marker}' could not be linked to any grid element.",
                )
            )
        if not fn.text_complete:
            report.warnings.append(
                ExtractionWarning(
                    type="footnote_incomplete",
                    severity="high",
                    message=(
                        f"Footnote '{fn.marker}' looks truncated. Check whether its text "
                        f"continues onto a following page."
                    ),
                )
            )

    orientation = {
        str(p): doc.page_no(p).orientation for p in span.pages if 1 <= p <= len(doc)
    }

    assumptions = list(vis.assumptions) if vis else []
    if not vis:
        assumptions.append(
            "The vision engine did not run for this schedule; structure comes from the "
            "text layer's geometry alone, and hierarchical headers are not resolved."
        )
    if span.starts_at_y is not None:
        assumptions.append(
            f"This schedule begins partway down page {span.pages[0]}; the upper part of "
            f"that page belongs to the preceding schedule."
        )

    return Schedule(
        id=sid,
        heading=(vis.heading if vis and vis.heading else span.heading),
        heading_page=span.heading_page,
        kind=ScheduleKind(span.kind),
        pages=list(span.pages),
        footnote_pages=sorted({p for fn in footnotes for p in fn.pages}) or list(span.footnote_pages),
        page_orientation=orientation,
        column_groups=column_groups,
        columns=columns,
        row_groups=row_groups,
        rows=rows,
        cells=cells,
        footnotes=footnotes,
        rotated_annotations=rotated,
        locator=LocatorEvidence(
            score=round(span.score, 2),
            signals=span.signals,
            per_page_scores=span.per_page_scores,
            heading_source=span.heading_source,
        ),
        reconciliation=report,
        assumptions=assumptions,
        open_questions=list(vis.open_questions) if vis else [],
    )


def _candidate_footnote_lines(page, is_table_page: bool):
    """Lines on one page that could belong to a footnote block.

    On a table page the block sits below the grid. The obvious boundary -- the bottom of
    the ruled table box -- does not work, because these protocols frequently draw the
    border *around* the footnotes as well, so the ruled region covers the whole page.
    Anchoring on content does work: nothing above the last row carrying grid cells can be
    a footnote.

    Running headers and footers are excluded by position, so a page number is never read
    as a footnote marker.
    """
    from .locate.patterns import is_cell_marker

    last_grid_y = 0.0
    if is_table_page:
        for line in page.lines:
            markers = sum(1 for w in line.words if is_cell_marker(w.text))
            if markers >= 2:
                last_grid_y = max(last_grid_y, line.y0)

    header_cut = page.height * 0.04
    footer_cut = page.height * 0.92
    return [
        line
        for line in page.lines
        if line.text.strip()
        and line.y0 > last_grid_y
        and header_cut <= line.y0 < footer_cut
    ]


def _footnotes_from_text_layer(doc: PdfDoc, span: TableSpan) -> list[Footnote]:
    """Fallback footnote parse straight from the text layer.

    Used when the vision engine did not run. A block may begin beneath the table, or
    entirely on the page after it, or begin on one and continue onto the other -- all three
    occur in the reference protocols -- so pages are concatenated in reading order and
    collection starts at the first marker-led line wherever it falls.
    :func:`parse_footnote_block` then absorbs unmarked continuation lines, which is what
    carries a block across a page break.
    """
    import re as _re

    # A line that clearly begins a new section of the protocol, ending the block.
    section_re = _re.compile(r"^(?:\d+(?:\.\d+)*\s+[A-Z]|[A-Z][A-Z 	]{8,}$|Table\s+\d)")

    lines: list[tuple[str, int]] = []
    started = False

    for page_no in sorted(set(span.footnote_pages)):
        if not (1 <= page_no <= len(doc)):
            continue
        page = doc.page_no(page_no)
        for line in _candidate_footnote_lines(page, page_no in span.pages):
            text = line.text.strip()
            if not started:
                if not FOOTNOTE_LINE_RE.match(text):
                    continue
                started = True
            elif section_re.match(text):
                started = False
                break
            lines.append((text, page_no))

    out: list[Footnote] = []
    for marker, text, pages, complete in parse_footnote_block(lines):
        style = (
            "asterisk_tier"
            if set(marker) <= {"*"}
            else "superscript_number"
            if marker.isdigit()
            else "symbol"
            if marker and marker[0] in "†‡§¶#"
            else "superscript_letter"
        )
        out.append(
            Footnote(
                marker=marker,
                marker_style=style,
                text=text,
                text_complete=complete,
                pages=pages,
                continued_from_page=pages[0] if len(pages) > 1 else None,
            )
        )
    return out


def extract_document(
    path: str | Path,
    use_vision: bool = True,
    progress=None,
) -> SoADocument:
    """Run the full pipeline over one protocol PDF."""
    started = datetime.now(timezone.utc)
    doc = PdfDoc(path)

    def report(stage: str, detail: str = "") -> None:
        if progress:
            progress(stage, detail)

    report("locating", f"{len(doc)} pages")
    spans, _scores, near_misses = locate(doc)
    report("located", f"{len(spans)} schedule(s)")

    meta = doc.guess_metadata()
    warnings: list[ExtractionWarning] = []
    if near_misses:
        pages = sorted({n.page for n in near_misses})
        warnings.append(
            ExtractionWarning(
                type="low_confidence",
                severity="low",
                message=(
                    f"Pages considered and rejected as schedules: {pages}. Listed so a "
                    f"recall failure can be audited rather than guessed at."
                ),
            )
        )

    text_trustworthy = not doc.text_layer_warnings
    schedules = []
    model_used = None
    pages_to_vision = 0

    for i, span in enumerate(spans):
        report("extracting", f"schedule {i + 1}/{len(spans)}, pages {span.pages}")
        grids = geometric.extract_span(doc, span)

        vis = None
        engines_run = ["geometric"]
        engines_failed: dict[str, str] = {}

        if use_vision:
            try:
                vis, vmeta = vision.extract_span(
                    doc, span, text_layer_trustworthy=text_trustworthy
                )
                engines_run.append("vision")
                model_used = vmeta.get("model")
                pages_to_vision += vmeta.get("pages_sent", 0)
                report("extracted", f"vision: {len(vis.rows)} rows, {len(vis.cells)} cells")
            except Exception as exc:  # noqa: BLE001 - recorded, not raised
                engines_failed["vision"] = str(exc)[:400]
                report("warning", f"vision engine failed: {exc}")
        else:
            engines_failed["vision"] = "disabled by caller"

        schedules.append(
            _assemble_schedule(span, i, vis, grids, doc, engines_run, engines_failed)
        )

    finished = datetime.now(timezone.utc)
    result = SoADocument(
        document=DocumentInfo(
            source_file=Path(path).name,
            sha256=doc.sha256,
            page_count=len(doc),
            title_guess=meta["title_guess"],
            sponsor_guess=meta["sponsor_guess"],
            protocol_id_guess=meta["protocol_id_guess"],
            has_text_layer=doc.has_text_layer,
            text_layer_warnings=doc.text_layer_warnings,
        ),
        schedules=schedules,
        run=ExtractionRun(
            tool_version=__version__,
            vision_model=model_used,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_seconds=round((finished - started).total_seconds(), 2),
            pages_sent_to_vision=pages_to_vision,
            warnings=warnings,
        ),
    )
    doc.close()
    return result
