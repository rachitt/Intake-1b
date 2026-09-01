"""Cross-check the two engines and report every disagreement.

The brief is explicit that a dropped row or a dropped visit is the most heavily penalised
failure, and that recall matters more than precision. So the pipeline does not pick a
winner between the geometric and vision engines. It takes the vision engine's structure --
which is consistently the better reading of a page -- and then asks one question of the
geometric engine: *did you see a row or a column that vision did not?*

Anything only one engine saw is emitted as a warning and, for rows, carried into the
output as an extra row flagged for review. An extra row is a problem the brief calls
tolerable; a dropped assessment is one it calls much worse.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from .extract.geometric import PageGrid
from .schema import Cell, Column, ExtractionWarning, Reconciliation, Row

# Two row labels are the same row above this similarity.
_ROW_MATCH_THRESHOLD = 82.0
# Two column header signatures are the same column above this similarity.
_COLUMN_MATCH_THRESHOLD = 75.0


def normalise_label(text: str) -> str:
    """Reduce a label to a comparable form.

    Deliberately aggressive: the two engines wrap lines differently, disagree about
    footnote markers, and one of them appends CRF form numbers to labels. None of that
    should register as a different row.
    """
    t = (text or "").lower()
    t = re.sub(r"\(\d{1,3}\)", " ", t)  # CRF form numbers
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _column_signature(col: Column) -> str:
    return normalise_label(" ".join(h.text for h in col.header_cells))


def _best_match(needle: str, haystack: list[str]) -> tuple[int, float]:
    """Index and score of the closest entry, or ``(-1, 0.0)``."""
    if not needle or not haystack:
        return -1, 0.0
    best_i, best_s = -1, 0.0
    for i, candidate in enumerate(haystack):
        if not candidate:
            continue
        score = fuzz.token_set_ratio(needle, candidate)
        if score > best_s:
            best_i, best_s = i, score
    return best_i, best_s


def reconcile(
    rows: list[Row],
    columns: list[Column],
    cells: list[Cell],
    grids: list[PageGrid],
    engines_run: list[str],
    engines_failed: dict[str, str] | None = None,
) -> tuple[list[Row], Reconciliation]:
    """Compare the vision-derived structure against the geometric grids.

    Returns the (possibly extended) row list and the reconciliation report.
    """
    warnings: list[ExtractionWarning] = []
    failed = dict(engines_failed or {})

    geo_rows = [r for g in grids for r in g.rows if r.label.strip() and not r.is_category]
    geo_labels = [normalise_label(r.label) for r in geo_rows]
    vis_labels = [normalise_label(r.label) for r in rows if not r.is_category_header]
    vis_row_objs = [r for r in rows if not r.is_category_header]

    geo_col_count = max((len(g.columns) - 1 for g in grids), default=0)

    report = Reconciliation(
        engines_run=list(engines_run),
        engines_failed=failed,
        row_counts={"geometric": len(geo_rows), "vision": len(vis_row_objs)},
        column_counts={"geometric": geo_col_count, "vision": len(columns)},
        cell_counts={
            "geometric": sum(len(g.cells) for g in grids),
            "vision": len(cells),
        },
    )

    if len(engines_run) < 2:
        only = engines_run[0] if engines_run else "none"
        report.warnings.append(
            ExtractionWarning(
                type="engine_failed",
                severity="high",
                message=(
                    f"Only the {only} engine ran, so no independent cross-check on dropped "
                    f"rows or columns was possible. Treat recall here as unverified."
                ),
                engine=only,
            )
        )
        return rows, report

    # -- rows the geometric engine saw and vision did not ---------------------------------
    added: list[Row] = []
    for geo_row, geo_label in zip(geo_rows, geo_labels):
        if not geo_label or len(geo_label) < 3:
            continue
        _, score = _best_match(geo_label, vis_labels)
        if score >= _ROW_MATCH_THRESHOLD:
            continue

        warnings.append(
            ExtractionWarning(
                type="row_missing_in_engine",
                severity="high",
                message=(
                    f"The geometric engine read a row the vision engine did not return: "
                    f"{geo_row.label!r} (page {geo_row.page}). It is included in the output "
                    f"and flagged, because a dropped assessment is worse than a spurious one."
                ),
                engine="vision",
            )
        )
        new_row = Row(
            id=f"r-geo-{geo_row.page}-{geo_row.index}",
            index=len(rows) + len(added),
            label=geo_row.label,
            is_category_header=False,
            page=geo_row.page,
            engines=["geometric"],
        )
        added.append(new_row)

    # -- rows vision saw and the geometric engine did not ---------------------------------
    for vis_row, vis_label in zip(vis_row_objs, vis_labels):
        if not vis_label or len(vis_label) < 3:
            continue
        _, score = _best_match(vis_label, geo_labels)
        if score >= _ROW_MATCH_THRESHOLD:
            continue
        warnings.append(
            ExtractionWarning(
                type="row_missing_in_engine",
                severity="medium",
                message=(
                    f"Only the vision engine read this row: {vis_row.label!r}. The "
                    f"geometric engine merges wrapped labels aggressively, so this is "
                    f"usually a geometric miss rather than a vision hallucination -- but "
                    f"it is worth a glance at the source."
                ),
                row_id=vis_row.id,
                engine="geometric",
            )
        )

    # -- column count disagreement --------------------------------------------------------
    if geo_col_count and abs(geo_col_count - len(columns)) > 0:
        severity = "high" if geo_col_count > len(columns) else "medium"
        warnings.append(
            ExtractionWarning(
                type="column_missing_in_engine",
                severity=severity,
                message=(
                    f"Column count differs: geometric read {geo_col_count}, vision read "
                    f"{len(columns)}. A dropped column is a patient visit nobody built, so "
                    f"check the header row against the source page."
                ),
                engine="vision" if geo_col_count > len(columns) else "geometric",
            )
        )

    # -- cell-level agreement on the cells both engines placed ----------------------------
    geo_cell_index: dict[tuple[str, int], str] = {}
    for grid in grids:
        row_by_index = {r.index: r for r in grid.rows}
        for c in grid.cells:
            gr = row_by_index.get(c.row)
            if gr is None or not gr.label.strip():
                continue
            geo_cell_index[(normalise_label(gr.label), c.col - 1)] = c.raw

    row_label_by_id = {r.id: normalise_label(r.label) for r in rows}
    col_index_by_id = {c.id: c.index for c in columns}

    compared = agreed = 0
    for cell in cells:
        key = (row_label_by_id.get(cell.row_id, ""), col_index_by_id.get(cell.column_id, -1))
        geo_raw = geo_cell_index.get(key)
        if geo_raw is None:
            continue
        compared += 1
        cell.engines.setdefault("geometric", geo_raw)
        if normalise_label(geo_raw) == normalise_label(cell.raw):
            agreed += 1
        else:
            warnings.append(
                ExtractionWarning(
                    type="cell_value_disagreement",
                    severity="medium",
                    message=(
                        f"Engines read this cell differently: geometric {geo_raw!r} vs "
                        f"vision {cell.raw!r}. The vision reading is kept."
                    ),
                    row_id=cell.row_id,
                    column_id=cell.column_id,
                )
            )

    report.cell_agreement = round(agreed / compared, 3) if compared else None

    if added:
        rows = rows + added
        report.row_counts["final"] = len([r for r in rows if not r.is_category_header])
    else:
        report.row_counts["final"] = len(vis_row_objs)

    report.warnings = warnings
    return rows, report
