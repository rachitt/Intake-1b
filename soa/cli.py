"""Command line interface.

    python -m soa.cli extract data/protocols/protocol9.pdf -o outputs/
    python -m soa.cli extract data/protocols/*.pdf -o outputs/ --no-vision
    python -m soa.cli locate data/protocols/protocol1.pdf
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .env import load_env
from .locate import locate
from .pdfdoc import PdfDoc
from .pipeline import extract_document


def _load_env() -> None:
    """Load a local .env if one exists, so the key need not be exported by hand."""
    load_env()


def _summarise(result, stream=sys.stdout) -> None:
    doc = result.document
    print(f"\n{doc.source_file}  ({doc.page_count} pages)", file=stream)
    if doc.title_guess:
        print(f"  title:   {doc.title_guess[:90]}", file=stream)
    if doc.sponsor_guess:
        print(f"  sponsor: {doc.sponsor_guess}", file=stream)
    for warning in doc.text_layer_warnings:
        print(f"  ! {warning}", file=stream)

    for sched in result.schedules:
        rec = sched.reconciliation
        print(
            f"\n  [{sched.id}] {sched.heading[:74]}"
            f"\n     kind={sched.kind.value}  pages={sched.pages}"
            f"  footnote pages={sched.footnote_pages}",
            file=stream,
        )
        print(
            f"     {len(sched.columns)} columns ({len(sched.column_groups)} groups), "
            f"{len(sched.rows)} rows ({len(sched.row_groups)} categories), "
            f"{len(sched.cells)} cells, {len(sched.footnotes)} footnotes",
            file=stream,
        )
        linked = sum(1 for f in sched.footnotes if f.attached_to)
        print(
            f"     footnotes linked to the grid: {linked}/{len(sched.footnotes)}",
            file=stream,
        )
        if sched.rotated_annotations:
            labels = ", ".join(r.text for r in sched.rotated_annotations)
            print(f"     rotated annotations kept out of the grid: {labels}", file=stream)
        if rec:
            print(
                f"     engines={rec.engines_run} rows={rec.row_counts} "
                f"cols={rec.column_counts} cell agreement={rec.cell_agreement}",
                file=stream,
            )
            high = [w for w in rec.warnings if w.severity == "high"]
            if high:
                print(f"     {len(high)} HIGH severity warning(s):", file=stream)
                for w in high[:6]:
                    print(f"       - {w.type}: {w.message[:110]}", file=stream)
        for q in sched.open_questions:
            print(f"     ? for a clinical SME: {q}", file=stream)


def cmd_extract(args: argparse.Namespace) -> int:
    _load_env()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for pdf in args.pdfs:
        path = Path(pdf)
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            failures += 1
            continue

        def progress(stage: str, detail: str) -> None:
            if not args.quiet:
                print(f"  [{stage}] {detail}", file=sys.stderr)

        print(f"\n=== {path.name} ===", file=sys.stderr)
        try:
            result = extract_document(
                path, use_vision=not args.no_vision, progress=progress
            )
        except Exception as exc:  # noqa: BLE001 - reported per file, run continues
            print(f"error: {path.name} failed: {exc}", file=sys.stderr)
            failures += 1
            continue

        target = out_dir / f"{path.stem}.soa.json"
        target.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _summarise(result, sys.stderr)
        print(f"\n  -> {target}", file=sys.stderr)

    return 1 if failures else 0


def cmd_locate(args: argparse.Namespace) -> int:
    """Run only the locator. Useful for checking recall without spending an API call."""
    for pdf in args.pdfs:
        doc = PdfDoc(pdf)
        spans, scores, near = locate(doc)
        print(f"\n=== {Path(pdf).name} ({len(doc)} pages) ===")
        for span in spans:
            print(
                f"  pages={span.pages} footnote candidates={span.footnote_pages} "
                f"kind={span.kind} score={span.score:.1f} via={span.heading_source}"
            )
            print(f"    {span.heading}")
            print(f"    signals: {', '.join(span.signals)}")
        if near:
            print(f"  considered and rejected: {sorted({n.page for n in near})}")
        if args.top:
            ranked = sorted(scores, key=lambda s: -s.score)[: args.top]
            print("  highest scoring pages:")
            for s in ranked:
                print(f"    p{s.page:<4d} {s.score:5.1f}  {', '.join(s.signals[:5])}")
        doc.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="soa",
        description="Locate and extract Schedule of Activities tables from protocol PDFs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="run the full pipeline and write JSON")
    p_extract.add_argument("pdfs", nargs="+", help="protocol PDF(s)")
    p_extract.add_argument("-o", "--output", default="outputs", help="output directory")
    p_extract.add_argument(
        "--no-vision",
        action="store_true",
        help="run the geometric engine only (no API calls, degraded output)",
    )
    p_extract.add_argument("-q", "--quiet", action="store_true")
    p_extract.set_defaults(func=cmd_extract)

    p_locate = sub.add_parser("locate", help="run only the locator")
    p_locate.add_argument("pdfs", nargs="+")
    p_locate.add_argument("--top", type=int, default=0, help="show N highest scoring pages")
    p_locate.set_defaults(func=cmd_locate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
