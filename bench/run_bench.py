"""Benchmark table-extraction approaches on the SoA pages of the reference protocols.

The brief asks which tools were evaluated, what was chosen, and precisely where each one
broke. This harness produces that evidence rather than an opinion.

Five approaches are compared on the same pages:

* ``pdfplumber``   -- ``extract_tables`` with its default lattice/stream strategies
* ``camelot-lattice`` -- ruling-line based table detection
* ``camelot-stream``  -- whitespace based table detection
* ``pymupdf``      -- PyMuPDF's built-in ``find_tables``
* ``geometric``    -- this project's deterministic engine

The vision engine is scored separately from committed outputs rather than re-run here, so
the benchmark stays free and repeatable.

Metrics deliberately favour recall, matching how the brief says failures are weighted:
rows recovered and columns recovered are reported before anything else, and a "shredded"
count records how many single-character rows an approach invented -- the specific failure
caused by sideways text in two of these protocols.

    python bench/run_bench.py                 # all protocols
    python bench/run_bench.py --protocol protocol9
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from soa.extract.geometric import extract_span  # noqa: E402
from soa.locate import locate  # noqa: E402
from soa.pdfdoc import PdfDoc  # noqa: E402

PROTOCOL_DIR = ROOT / "data" / "protocols"
OUTPUT_DIR = ROOT / "outputs"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _shredded(cells: list[str]) -> int:
    """Isolated single letters anywhere in the table.

    This is the fingerprint of a sideways label being torn into one cell per letter. It is
    counted across every cell rather than only the first column, because different tools
    strand the fragments in different places.
    """
    return sum(1 for t in cells if len(_norm(t)) == 1 and _norm(t).isalpha())


def _score(
    name: str,
    labels: list[str],
    cells: list[str],
    columns: int,
    seconds: float,
    note: str = "",
) -> dict:
    real = [t for t in labels if len(_norm(t)) > 1]
    return {
        "engine": name,
        "rows": len(real),
        "columns": columns,
        "cells": sum(1 for c in cells if _norm(c)),
        "shredded_cells": _shredded(cells),
        "seconds": round(seconds, 2),
        "note": note,
        "sample_labels": real[:5],
    }


# --------------------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------------------


def run_pdfplumber(pdf: Path, pages: list[int]) -> dict:
    t0 = time.time()
    try:
        import pdfplumber
    except ImportError:
        return _score("pdfplumber", [], [], 0, 0, "not installed")

    labels: list[str] = []
    cells: list[str] = []
    max_cols = 0
    try:
        with pdfplumber.open(str(pdf)) as doc:
            for page_no in pages:
                page = doc.pages[page_no - 1]
                for table in page.extract_tables() or []:
                    for row in table:
                        if row and row[0]:
                            labels.append(str(row[0]))
                        cells.extend(str(v) for v in row if v)
                        max_cols = max(max_cols, len(row))
    except Exception as exc:  # noqa: BLE001
        return _score(
            "pdfplumber", labels, cells, max_cols, time.time() - t0, f"error: {exc}"[:120]
        )
    return _score("pdfplumber", labels, cells, max(0, max_cols - 1), time.time() - t0)


def run_camelot(pdf: Path, pages: list[int], flavor: str) -> dict:
    t0 = time.time()
    try:
        import camelot
    except ImportError:
        return _score(f"camelot-{flavor}", [], [], 0, 0, "not installed")

    labels: list[str] = []
    cells: list[str] = []
    max_cols = 0
    try:
        tables = camelot.read_pdf(
            str(pdf), pages=",".join(str(p) for p in pages), flavor=flavor
        )
        for table in tables:
            df = table.df
            max_cols = max(max_cols, df.shape[1])
            labels.extend(str(v) for v in df.iloc[:, 0].tolist())
            cells.extend(str(v) for v in df.values.ravel().tolist())
    except Exception as exc:  # noqa: BLE001
        return _score(
            f"camelot-{flavor}", labels, cells, max_cols, time.time() - t0,
            f"error: {exc}"[:160],
        )
    return _score(
        f"camelot-{flavor}", labels, cells, max(0, max_cols - 1), time.time() - t0
    )


def run_pymupdf(pdf: Path, pages: list[int]) -> dict:
    t0 = time.time()
    import fitz

    labels: list[str] = []
    cells: list[str] = []
    max_cols = 0
    try:
        doc = fitz.open(str(pdf))
        for page_no in pages:
            page = doc[page_no - 1]
            for table in page.find_tables().tables:
                data = table.extract()
                for row in data:
                    if row and row[0]:
                        labels.append(str(row[0]))
                    cells.extend(str(v) for v in row if v)
                    max_cols = max(max_cols, len(row))
        doc.close()
    except Exception as exc:  # noqa: BLE001
        return _score(
            "pymupdf", labels, cells, max_cols, time.time() - t0, f"error: {exc}"[:120]
        )
    return _score("pymupdf", labels, cells, max(0, max_cols - 1), time.time() - t0)


def run_geometric(pdf: Path, spans, doc: PdfDoc) -> dict:
    t0 = time.time()
    labels: list[str] = []
    cells: list[str] = []
    max_cols = 0
    for span in spans:
        for grid in extract_span(doc, span):
            labels.extend(r.label for r in grid.rows if r.label.strip())
            cells.extend(c.raw for c in grid.cells)
            max_cols = max(max_cols, len(grid.columns))
    return _score(
        "geometric (this project)", labels, cells, max(0, max_cols - 1), time.time() - t0
    )


def score_vision_from_output(stem: str) -> dict | None:
    """Score the committed vision output rather than re-running it.

    Keeps the benchmark free and repeatable while still placing the vision engine on the
    same axes as the rest.
    """
    path = OUTPUT_DIR / f"{stem}.soa.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    labels: list[str] = []
    cells: list[str] = []
    max_cols = 0
    seconds = data.get("run", {}).get("duration_seconds") or 0
    for sched in data.get("schedules", []):
        labels.extend(r["label"] for r in sched.get("rows", []))
        cells.extend(c["raw"] for c in sched.get("cells", []))
        max_cols = max(max_cols, len(sched.get("columns", [])))
    model = data.get("run", {}).get("vision_model") or "vision"
    return _score(
        f"vision ({model})", labels, cells, max_cols, seconds, "from committed output"
    )


# --------------------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------------------


def bench_protocol(pdf: Path) -> dict:
    doc = PdfDoc(pdf)
    spans, _scores, _near = locate(doc)
    pages = sorted({p for s in spans for p in s.pages})

    results = [
        run_pdfplumber(pdf, pages),
        run_camelot(pdf, pages, "lattice"),
        run_camelot(pdf, pages, "stream"),
        run_pymupdf(pdf, pages),
        run_geometric(pdf, spans, doc),
    ]
    vision = score_vision_from_output(pdf.stem)
    if vision:
        results.append(vision)

    doc.close()
    return {
        "protocol": pdf.name,
        "soa_pages": pages,
        "schedules_located": len(spans),
        "results": results,
    }


def render_markdown(report: list[dict]) -> str:
    lines = [
        "# Benchmark: table extraction on the located SoA pages",
        "",
        "Generated by `python bench/run_bench.py`. Every approach is given the *same* pages,",
        "the ones this project's locator selected, so the comparison is of extraction quality",
        "alone and not of page-finding.",
        "",
        "`rows` counts recovered row labels longer than one character. `shredded` counts",
        "isolated single letters anywhere in the table -- the fingerprint of a sideways label",
        "being torn into one cell per letter. `columns` is the widest table the approach",
        "returned, excluding the row-label column; a wildly inflated number means the tool",
        "failed to find real column boundaries, not that it found more visits.",
        "",
    ]
    for entry in report:
        lines.append(f"## {entry['protocol']}")
        lines.append("")
        lines.append(
            f"Located {entry['schedules_located']} schedule(s) on page(s) {entry['soa_pages']}."
        )
        lines.append("")
        lines.append("| approach | rows | columns | cells | shredded | seconds | note |")
        lines.append("|---|---:|---:|---:|---:|---:|---|")
        for r in entry["results"]:
            lines.append(
                f"| {r['engine']} | {r['rows']} | {r['columns']} | {r['cells']} | "
                f"{r['shredded_cells']} | {r['seconds']} | {r['note']} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", help="stem of a single protocol, e.g. protocol9")
    parser.add_argument("-o", "--output", default=str(Path(__file__).parent / "RESULTS.md"))
    args = parser.parse_args()

    pdfs = sorted(PROTOCOL_DIR.glob("*.pdf"))
    if args.protocol:
        pdfs = [p for p in pdfs if p.stem == args.protocol]
    if not pdfs:
        print("no protocols found", file=sys.stderr)
        return 1

    report = []
    for pdf in pdfs:
        print(f"benchmarking {pdf.name} ...", file=sys.stderr)
        report.append(bench_protocol(pdf))

    Path(args.output).write_text(render_markdown(report), encoding="utf-8")
    (Path(args.output).with_suffix(".json")).write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
