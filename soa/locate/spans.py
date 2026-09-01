"""Turn nominated pages into table spans.

Five problems this module exists to solve, all observed in the reference protocols:

* **Tables run across pages.** One reference SoA occupies three consecutive landscape
  pages. Naive per-page extraction yields three disconnected fragments.
* **Continuation headers vary.** One protocol reprints the full header, one reprints an
  abbreviated "Table 4, Continued" and drops the study-phase banner entirely, and one
  reprints nothing but a fresh set of visit numbers covering a *different* range.
* **Footnote blocks spill past the table.** One protocol's footnotes begin on the table
  page and continue onto the next with no header, no marker, and nothing tying them to
  the table behind them. Every span therefore claims the page after it as a footnote
  candidate, and the footnote parser decides what is really there.
* **Two schedules can share one page.** In one protocol a page carries the footnotes of
  the preceding schedule in its upper half and the whole of the *next* schedule below
  them. That page belongs to two different spans, in two different roles.
* **A body cross-reference is not a heading.** "(see Schedule of Events, Attachment
  LZZT.1)" appears in running prose on three separate pages of one protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..pdfdoc import Page, PdfDoc
from .patterns import (
    CONTINUATION_RE,
    classify_kind,
    is_footnote_block_heading,
    is_title_like,
    looks_like_soa_heading,
    repair_letter_spacing,
)
from .score import PageScore

# How much of a page's header token set must reappear for the page to read as a
# continuation of the previous one.
_HEADER_JACCARD_MIN = 0.25

# A span is kept only if it scores at least this fraction of the document's best span.
# The reference protocols separate cleanly -- true schedules score 12 to 16, incidental
# pages that merely mention visits score 3 to 5 -- so this discards noise without
# endangering a genuine secondary schedule (the weakest real one scores 0.75 of its
# document's best). Anything dropped is still reported as a near miss.
_DOMINANCE_RATIO = 0.5


@dataclass
class HeadingHit:
    """An SoA heading found on a page, with where on the page it sits."""

    text: str
    page: int
    y0: float
    is_continuation: bool


@dataclass
class TableSpan:
    """A contiguous run of pages holding one schedule."""

    pages: list[int]
    heading: str
    heading_page: int | None
    kind: str
    score: float
    signals: list[str] = field(default_factory=list)
    per_page_scores: dict[str, float] = field(default_factory=dict)
    footnote_pages: list[int] = field(default_factory=list)
    heading_source: str = "inferred"
    # When a schedule starts partway down a shared page, everything above this y on that
    # page belongs to the previous schedule.
    starts_at_y: float | None = None

    @property
    def all_pages(self) -> list[int]:
        return sorted(set(self.pages) | set(self.footnote_pages))


def find_headings(page: Page) -> list[HeadingHit]:
    """Every SoA heading printed anywhere on a page.

    Scanning the whole page rather than just the top is what lets a second schedule that
    begins halfway down be found at all.
    """
    hits: list[HeadingHit] = []
    for line in page.lines:
        text = line.text.strip()
        if not text or is_footnote_block_heading(text):
            continue
        if not (looks_like_soa_heading(text) and is_title_like(text)):
            continue
        hits.append(
            HeadingHit(
                text=text,
                page=page.number,
                y0=line.y0,
                is_continuation=bool(CONTINUATION_RE.search(text)),
            )
        )
    return hits


def _header_tokens(page: Page, fraction: float = 0.35) -> set[str]:
    """Tokens from the upper part of a page, used to compare header signatures."""
    cut = page.height * fraction
    tokens: set[str] = set()
    for line in page.lines:
        if line.y0 > cut:
            break
        for word in line.text.split():
            w = word.strip(".,:;()[]").lower()
            if len(w) >= 2:
                tokens.add(w)
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _has_grid(page: Page) -> bool:
    """True when a page carries actual table structure, not just text.

    Used to trim trailing pages off a span. A footnote block that follows the table scores
    highly enough to be nominated -- it shares the table's vocabulary -- but it is prose,
    and folding it into the table body would invent rows that do not exist.
    """
    from .score import _column_clusters

    # Counting cell markers is not enough: a footnote block whose entries begin "Xa - ",
    # "Xb - " looks marker-dense while being pure prose. Nor are vertical rules enough:
    # one reference SoA is ruled only horizontally. What separates them cleanly is how
    # many distinct vertical alignments of short tokens the page holds -- table pages in
    # the reference set show 9 to 16, footnote pages show 1.
    return len(_column_clusters(page)) >= 4


def _trim_to_grid(doc: PdfDoc, pages: list[int]) -> list[int]:
    """Drop trailing pages that hold no grid. Never returns an empty list."""
    trimmed = list(pages)
    while len(trimmed) > 1 and not _has_grid(doc.page_no(trimmed[-1])):
        trimmed.pop()
    return trimmed


def build_spans(doc: PdfDoc, scores: list[PageScore]) -> tuple[list[TableSpan], list[PageScore]]:
    """Group nominated pages into spans. Returns ``(spans, near_misses)``."""
    by_page = {s.page: s for s in scores}
    nominated = sorted(s.page for s in scores if s.nominated)
    if not nominated:
        return [], []

    # -- group into contiguous runs, tolerating a single low-scoring page inside one ------
    runs: list[list[int]] = [[nominated[0]]]
    for page_no in nominated[1:]:
        if page_no - runs[-1][-1] <= 2:
            for missing in range(runs[-1][-1] + 1, page_no):
                runs[-1].append(missing)
            runs[-1].append(page_no)
        else:
            runs.append([page_no])

    # -- split runs wherever a new (non-continuation) schedule begins ---------------------
    @dataclass
    class _Segment:
        pages: list[int]
        heading: HeadingHit | None
        starts_at_y: float | None = None

    segments: list[_Segment] = []
    for run in runs:
        current = _Segment(pages=[run[0]], heading=None)
        first_hits = [h for h in find_headings(doc.page_no(run[0])) if not h.is_continuation]
        if first_hits:
            current.heading = first_hits[0]

        for prev_no, page_no in zip(run, run[1:]):
            page = doc.page_no(page_no)
            hits = [h for h in find_headings(page) if not h.is_continuation]
            echoes_previous = (
                _jaccard(_header_tokens(doc.page_no(prev_no)), _header_tokens(page))
                >= _HEADER_JACCARD_MIN
            )
            page_says_continued = bool(CONTINUATION_RE.search(page.text[:400]))

            new_heading = None
            for h in hits:
                # A heading echoing the one this span already carries is a repeated title
                # on a continuation page, not a new schedule.
                if current.heading and _jaccard(
                    set(h.text.lower().split()), set(current.heading.text.lower().split())
                ) >= 0.5:
                    continue
                new_heading = h
                break

            if new_heading is not None and not page_says_continued and not echoes_previous:
                segments.append(current)
                current = _Segment(pages=[page_no], heading=new_heading)
                # A heading below the top of the page means the page is shared with the
                # schedule above it.
                if new_heading.y0 > page.height * 0.25:
                    current.starts_at_y = new_heading.y0
            else:
                current.pages.append(page_no)

        segments.append(current)

    # -- a schedule may also start partway down the *first* page of a run ----------------
    expanded: list[_Segment] = []
    for seg in segments:
        expanded.append(seg)
        # Look for a second heading further down the last page of the segment.
        last = doc.page_no(seg.pages[-1])
        hits = [h for h in find_headings(last) if not h.is_continuation]
        for h in hits:
            if h.y0 <= last.height * 0.25:
                continue
            if seg.heading and h.text.strip() == seg.heading.text.strip():
                continue
            already = any(
                s.pages and s.pages[0] == h.page and s.starts_at_y for s in expanded
            )
            if already:
                continue
            expanded.append(_Segment(pages=[h.page], heading=h, starts_at_y=h.y0))
            break

    # -- turn segments into spans --------------------------------------------------------
    spans: list[TableSpan] = []
    for seg in expanded:
        body_pages = _trim_to_grid(doc, sorted(set(seg.pages)))
        heading_text = seg.heading.text if seg.heading else None
        heading_page = seg.heading.page if seg.heading else body_pages[0]
        heading_source = "page_heading" if seg.heading else "inferred"

        if heading_text is None:
            ps = by_page.get(body_pages[0])
            if ps and ps.heading:
                heading_text, heading_source = ps.heading, "toc"
            else:
                heading_text = f"Untitled schedule (page {body_pages[0]})"

        # Footnote candidates: the last body page always, plus the page after it. Both are
        # only candidates; the footnote parser decides what is genuinely there.
        footnote_pages = [body_pages[-1]]
        if body_pages[-1] + 1 <= len(doc):
            footnote_pages.append(body_pages[-1] + 1)

        span_scores = {
            str(p): round(by_page[p].score, 2) for p in body_pages if p in by_page
        }
        signals = sorted({s for p in body_pages if p in by_page for s in by_page[p].signals})

        spans.append(
            TableSpan(
                pages=body_pages,
                heading=repair_letter_spacing(re.sub(r"\s+", " ", heading_text).strip()),
                heading_page=heading_page,
                kind=classify_kind(heading_text),
                score=max((by_page[p].score for p in body_pages if p in by_page), default=0.0),
                signals=signals,
                per_page_scores=span_scores,
                footnote_pages=footnote_pages,
                heading_source=heading_source,
                starts_at_y=seg.starts_at_y,
            )
        )

    # -- drop noise, but report what was dropped -----------------------------------------
    if not spans:
        return [], []
    best = max(s.score for s in spans)
    cutoff = best * _DOMINANCE_RATIO
    kept = [s for s in spans if s.score >= cutoff]
    dropped = [s for s in spans if s.score < cutoff]

    near_misses = [
        by_page[p]
        for s in dropped
        for p in s.pages
        if p in by_page
    ]
    kept.sort(key=lambda s: (s.pages[0], s.starts_at_y or 0.0))
    return kept, near_misses


def locate(doc: PdfDoc) -> tuple[list[TableSpan], list[PageScore], list[PageScore]]:
    """Full locator: contents hints, per-page scoring, span assembly.

    Returns ``(spans, all_page_scores, near_misses)``. The scores and near misses are kept
    so the output can show what the locator considered and rejected, which is the only way
    a reviewer can audit a recall failure.
    """
    from .score import score_document
    from .toc import harvest

    hints = harvest(doc)
    pages = [doc.page(i) for i in range(len(doc))]
    scores = score_document(pages, toc_hints=hints)
    spans, near_misses = build_spans(doc, scores)
    return spans, scores, near_misses
