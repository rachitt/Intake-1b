"""Per-page scoring: how much does this page look like a Schedule of Activities?

The brief forbids hardcoded page numbers, and the five reference protocols put their SoA
in five different places -- an appendix after the references, two appendices at the very
back, and the middle of the body in two others. So the locator reads every page and
scores it.

The scoring is deliberately **recall-biased**. A page wrongly nominated costs one cheap
vision call; a page wrongly skipped costs an entire block of patient visits, which the
brief calls the most heavily penalised failure. The threshold is set accordingly, and
every signal that fired is recorded so a reviewer can audit why a page was or was not
chosen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..pdfdoc import Page
from .patterns import (
    ASSESSMENT_VOCAB_RE,
    VISIT_HEADER_RES,
    is_cell_marker,
    looks_like_soa_heading,
)

# Weights. Tuned by hand against the five reference protocols and deliberately kept
# additive and inspectable rather than learned -- a reviewer must be able to read why a
# page scored what it did.
WEIGHTS = {
    "heading_on_page": 3.0,
    "toc_hint": 2.0,
    "cell_marker_density": 3.5,
    "column_structure": 2.5,
    "ruling_grid": 2.0,
    "visit_header": 2.5,
    "assessment_vocab": 1.5,
    "wide_or_landscape": 0.5,
    "continuation": 1.5,
}

# A page must beat this to be nominated. Low on purpose; see the module docstring.
SCORE_THRESHOLD = 4.0


@dataclass
class PageScore:
    """The locator's verdict on one page, with its reasoning intact."""

    page: int
    score: float = 0.0
    signals: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    heading: str | None = None
    is_continuation: bool = False

    @property
    def nominated(self) -> bool:
        return self.score >= SCORE_THRESHOLD


def _column_clusters(page: Page, tolerance: float = 12.0) -> list[float]:
    """Cluster the x-centres of short tokens into candidate column positions.

    A grid shows up as several tight vertical stacks of short tokens. Prose does not:
    its tokens spread evenly across the measure. Only short tokens are considered so
    that long row labels do not smear the clusters.
    """
    xs = sorted(w.cx for w in page.words if len(w.text.strip()) <= 12)
    if not xs:
        return []

    clusters: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] <= tolerance:
            clusters[-1].append(x)
        else:
            clusters.append([x])

    # A real column has several tokens stacked in it.
    return [sum(c) / len(c) for c in clusters if len(c) >= 3]


def score_page(
    page: Page,
    toc_hint: bool = False,
    heading_hint: str | None = None,
) -> PageScore:
    """Score one page. Pure function of the page plus any table-of-contents hint."""
    result = PageScore(page=page.number)
    words = page.words
    lines = page.lines

    if not words:
        return result

    # -- heading printed on the page itself ---------------------------------------------
    # Only the top third counts: a body paragraph mentioning "the schedule of assessments"
    # is a cross-reference, not the table.
    top_cut = page.height / 3.0
    for line in lines[:20]:
        if line.y0 > top_cut:
            break
        if looks_like_soa_heading(line.text):
            result.heading = line.text.strip()
            result.score += WEIGHTS["heading_on_page"]
            result.signals.append("heading_on_page")
            break

    if toc_hint:
        result.score += WEIGHTS["toc_hint"]
        result.signals.append("toc_hint")
        if not result.heading and heading_hint:
            result.heading = heading_hint

    # -- density of cell markers --------------------------------------------------------
    markers = sum(1 for w in words if is_cell_marker(w.text))
    density = markers / max(len(words), 1)
    result.features["cell_markers"] = float(markers)
    result.features["cell_marker_density"] = density
    if markers >= 8:
        # Saturates: 25 markers is as convincing as 200.
        result.score += WEIGHTS["cell_marker_density"] * min(1.0, markers / 25.0)
        result.signals.append("cell_marker_density")

    # -- column structure ---------------------------------------------------------------
    clusters = _column_clusters(page)
    result.features["column_clusters"] = float(len(clusters))
    if len(clusters) >= 4:
        result.score += WEIGHTS["column_structure"] * min(1.0, len(clusters) / 8.0)
        result.signals.append("column_structure")

    # -- ruling lines -------------------------------------------------------------------
    rules = page.ruling_lines
    h_rules = sum(1 for r in rules if r.orientation == "h" and r.length > 60)
    v_rules = sum(1 for r in rules if r.orientation == "v" and r.length > 30)
    result.features["h_rules"] = float(h_rules)
    result.features["v_rules"] = float(v_rules)
    if h_rules >= 3 and v_rules >= 3:
        result.score += WEIGHTS["ruling_grid"] * min(1.0, (h_rules + v_rules) / 30.0)
        result.signals.append("ruling_grid")

    # -- visit header vocabulary --------------------------------------------------------
    page_text = page.text
    header_hits = [name for name, rx in VISIT_HEADER_RES if rx.search(page_text)]
    result.features["visit_header_hits"] = float(len(header_hits))
    if header_hits:
        result.score += WEIGHTS["visit_header"] * min(1.0, len(header_hits) / 3.0)
        result.signals.extend(f"visit_header:{h}" for h in header_hits)

    # -- assessment vocabulary ----------------------------------------------------------
    vocab_hits = len(set(m.group(0).lower() for m in ASSESSMENT_VOCAB_RE.finditer(page_text)))
    result.features["assessment_vocab_hits"] = float(vocab_hits)
    if vocab_hits >= 3:
        result.score += WEIGHTS["assessment_vocab"] * min(1.0, vocab_hits / 8.0)
        result.signals.append("assessment_vocab")

    # -- page shape ---------------------------------------------------------------------
    if page.orientation == "landscape":
        result.score += WEIGHTS["wide_or_landscape"]
        result.signals.append("landscape")

    result.features["score"] = result.score
    return result


def score_document(
    pages: list[Page],
    toc_hints: dict[int, str] | None = None,
) -> list[PageScore]:
    """Score every page of the document.

    ``toc_hints`` maps a 1-indexed page number to the heading that pointed at it. Table of
    contents page numbers are frequently off by one or more against the body (one of the
    reference protocols is), so a hint biases the score and never selects a page outright.
    """
    hints = toc_hints or {}
    scores: list[PageScore] = []

    for page in pages:
        # Allow a hint to reach one page either side of where the TOC claimed.
        hint_text = None
        for offset in (0, 1, -1, 2):
            if page.number - offset in hints:
                hint_text = hints[page.number - offset]
                break
        scores.append(
            score_page(page, toc_hint=hint_text is not None, heading_hint=hint_text)
        )

    return scores
