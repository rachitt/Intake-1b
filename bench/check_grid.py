"""Score a committed extraction against the grid its source actually prints.

The README's first item under "what I would build next" used to be *a ground-truth corpus
and a real accuracy number*, on the grounds that everything else was guesswork without one.
It turned out the ground truth was already in the documents. All five reference protocols
draw their tables with ruling lines, so the printed rows, columns and cells can be read
back out of each page's vector graphics and an extraction compared against them cell by
cell -- with nobody keying anything, and no opinion involved.

That matters more than it sounds. The hand counts this replaces were wrong on three of the
six reference schedules, always in the same direction: a column the page rules and leaves
empty reads as whitespace, and three activities inside one ruled cell read as three rows.

    python bench/check_grid.py                     # every protocol in outputs/
    python bench/check_grid.py --protocol protocol1

What it measures is whether the extracted content landed in the right boxes. It is not an
independent check on the *boxes* -- `soa.align` reads the same lattice to place them -- so
a lattice misread would agree with itself. The boxes are checked against the rendered page
by eye, in `verification/`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from soa.align import printed_cells  # noqa: E402
from soa.locate import locate  # noqa: E402
from soa.pdfdoc import PdfDoc  # noqa: E402
from soa.reconcile import normalise_label  # noqa: E402
from soa.schema import Schedule, SoADocument  # noqa: E402

PROTOCOL_DIR = ROOT / "data" / "protocols"
OUTPUT_DIR = ROOT / "outputs"


def _extracted_cells(schedule: Schedule) -> dict[tuple[int, int], str]:
    """The extraction's cells keyed the same way: by position in the grid.

    Positions line up with the printed grid's because that is what alignment made them.
    Where alignment declined -- a schedule with a `source_grid_unavailable` warning -- they
    do not, and the score for that schedule is meaningless rather than merely bad.
    """
    columns = {c.id: c.index for c in schedule.columns}
    rows = {r.id: r.index for r in schedule.rows}
    return {
        (rows[c.row_id], columns[c.column_id]): c.raw
        for c in schedule.cells
        if c.row_id in rows and c.column_id in columns
    }


def check(name: str) -> list[dict]:
    pdf = PROTOCOL_DIR / f"{name}.pdf"
    out = OUTPUT_DIR / f"{name}.soa.json"
    if not pdf.exists() or not out.exists():
        return []

    doc = PdfDoc(pdf)
    spans, _scores, _misses = locate(doc)
    result = SoADocument.model_validate_json(out.read_text(encoding="utf-8"))

    rows = []
    for i, schedule in enumerate(result.schedules):
        if i >= len(spans):
            break
        printed = printed_cells(schedule, doc, spans[i])
        if printed is None:
            rows.append({"schedule": schedule.id, "protocol": name, "lattice": False})
            continue

        got = _extracted_cells(schedule)
        missing = sorted(set(printed) - set(got))
        spurious = sorted(set(got) - set(printed))
        differs = [
            k
            for k in set(printed) & set(got)
            if printed[k].replace(" ", "") != got[k].replace(" ", "")
        ]
        rows.append(
            {
                "protocol": name,
                "schedule": schedule.id,
                "lattice": True,
                "printed": len(printed),
                "extracted": len(got),
                "exact": len(set(printed) & set(got)) - len(differs),
                "missing": len(missing),
                "spurious": len(spurious),
                "differs": len(differs),
                "aligned": not any(
                    w.type == "source_grid_unavailable"
                    for w in (schedule.reconciliation.warnings if schedule.reconciliation else [])
                ),
                "examples": {
                    "missing": [
                        f"row {r} x col {c} = {printed[(r, c)]!r}" for r, c in missing[:5]
                    ],
                    "spurious": [
                        f"row {r} x col {c} = {got[(r, c)]!r}" for r, c in spurious[:5]
                    ],
                    "differs": [
                        f"row {r} x col {c}: printed {printed[(r, c)]!r}, "
                        f"extracted {got[(r, c)]!r}"
                        for r, c in differs[:5]
                    ],
                },
            }
        )
    doc.close()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--protocol", help="check one protocol instead of all")
    ap.add_argument("--verbose", action="store_true", help="list the cells that disagree")
    args = ap.parse_args()

    names = (
        [args.protocol]
        if args.protocol
        else sorted(p.stem for p in PROTOCOL_DIR.glob("*.pdf"))
    )

    results = []
    for name in names:
        results.extend(check(name))

    print(f"\n{'schedule':<26} {'printed':>8} {'exact':>7} {'missing':>8} {'spurious':>9} {'differs':>8}")
    print("-" * 70)
    total = {"printed": 0, "exact": 0, "missing": 0, "spurious": 0, "differs": 0}
    for r in results:
        label = f"{r['protocol']} {r['schedule']}"
        if not r["lattice"]:
            print(f"{label:<26} {'no ruled grid — not scored':>44}")
            continue
        flag = "" if r.get("aligned", True) else "   (alignment declined — not comparable)"
        print(
            f"{label:<26} {r['printed']:>8} {r['exact']:>7} {r['missing']:>8} "
            f"{r['spurious']:>9} {r['differs']:>8}{flag}"
        )
        for k in total:
            total[k] += r[k]

    if total["printed"]:
        pct = 100.0 * total["exact"] / total["printed"]
        print("-" * 70)
        print(
            f"{'TOTAL':<26} {total['printed']:>8} {total['exact']:>7} "
            f"{total['missing']:>8} {total['spurious']:>9} {total['differs']:>8}"
            f"   ({pct:.1f}% of printed cells exact)"
        )

    if args.verbose:
        for r in results:
            if not r.get("lattice") or not any(r["examples"].values()):
                continue
            print(f"\n{r['protocol']} {r['schedule']}")
            for kind, items in r["examples"].items():
                for item in items:
                    print(f"  {kind:<9} {item}")

    (ROOT / "bench" / "GRID_CHECK.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(f"\nwrote {ROOT / 'bench' / 'GRID_CHECK.json'}")


if __name__ == "__main__":
    main()
