"""Harvest table-of-contents entries that point at a Schedule of Activities.

Two sources, because neither is reliable alone:

* the PDF outline (``/Outlines``), which is absent in one reference protocol and carries
  871 mostly-useless entries in another; and
* dotted-leader lines in the printed contents pages.

Both produce *hints*. Printed page numbers routinely disagree with PDF page indices --
front matter is numbered separately, and one reference protocol's contents is off by one
against its own body -- so a hint biases the page score and never selects a page by
itself. The locator still has to see grid structure on the page.
"""

from __future__ import annotations

import re

from ..pdfdoc import PdfDoc
from .patterns import looks_like_soa_heading

# "3.1  Schedule of Activities ......... 42" and friends.
DOTTED_LEADER_RE = re.compile(
    r"^\s*(?P<label>.+?)\s*[.…]{3,}\s*(?P<page>\d{1,4})\s*$"
)
# Some contents pages use whitespace rather than dot leaders.
SPACED_LEADER_RE = re.compile(r"^\s*(?P<label>.{6,150}?)\s{3,}(?P<page>\d{1,4})\s*$")

CONTENTS_HEADER_RE = re.compile(
    r"^\s*(?:table\s+of\s+contents|contents|list\s+of\s+tables|list\s+of\s+appendices)\s*$",
    re.I,
)


def _printed_to_pdf_offset(doc: PdfDoc, sample_limit: int = 40) -> int:
    """Estimate how far the printed page numbers lag the PDF page indices.

    Reads the footer of a spread of pages, finds the ones that print a bare number, and
    takes the modal difference. Returns 0 when nothing conclusive is found.
    """
    diffs: dict[int, int] = {}
    step = max(1, len(doc) // sample_limit)

    for i in range(0, len(doc), step):
        page = doc.page(i)
        footer_cut = page.height * 0.90
        for line in reversed(page.lines):
            if line.y0 < footer_cut:
                break
            m = re.search(r"(?:^|\s)(\d{1,4})(?:\s|$)", line.text)
            if m:
                printed = int(m.group(1))
                if 0 < printed <= len(doc) + 40:
                    diff = page.number - printed
                    diffs[diff] = diffs.get(diff, 0) + 1
                break

    if not diffs:
        return 0
    best = max(diffs.items(), key=lambda kv: kv[1])
    # Only trust an offset that shows up repeatedly.
    return best[0] if best[1] >= 3 else 0


def harvest(doc: PdfDoc, scan_pages: int = 20) -> dict[int, str]:
    """Return ``{pdf_page_number: heading}`` for every contents entry naming an SoA."""
    hints: dict[int, str] = {}
    offset = _printed_to_pdf_offset(doc)

    # -- source 1: the PDF outline -------------------------------------------------------
    for _level, title, page_no in doc.toc:
        if not looks_like_soa_heading(title):
            continue
        if 1 <= page_no <= len(doc):
            hints.setdefault(page_no, title.strip())

    # -- source 2: printed contents pages ------------------------------------------------
    # Also scan the tail: appendix listings sometimes appear only at the back, and two of
    # the reference protocols keep their schedules in appendices.
    candidate_pages = list(range(min(scan_pages, len(doc))))
    candidate_pages += list(range(max(0, len(doc) - 5), len(doc)))

    for i in sorted(set(candidate_pages)):
        page = doc.page(i)
        text_lines = [line.text for line in page.lines]
        looks_like_contents = any(CONTENTS_HEADER_RE.match(t) for t in text_lines) or sum(
            1 for t in text_lines if DOTTED_LEADER_RE.match(t)
        ) >= 4
        if not looks_like_contents:
            continue

        for raw in text_lines:
            m = DOTTED_LEADER_RE.match(raw) or SPACED_LEADER_RE.match(raw)
            if not m:
                continue
            label = m.group("label").strip()
            if not looks_like_soa_heading(label):
                continue
            printed = int(m.group("page"))
            target = printed + offset
            if 1 <= target <= len(doc):
                hints.setdefault(target, label)

    return hints
