"""Thin PyMuPDF wrapper.

Everything downstream reads pages through this module so that rotation handling, word
extraction and ruling-line detection happen once, consistently, in one place.

Two things here are load-bearing, and both were found by looking at the actual documents
rather than assumed:

1. **Page rotation.** Some SoA pages are true ``/Rotate 90`` landscape. Their text is
   stored in unrotated media space with a direction vector of ``(0, -1)``, so a naive
   "is this span rotated?" test flags the entire page as sideways. We therefore map every
   coordinate and every direction vector through the page's own rotation matrix first, so
   downstream code always sees the page the way a reader sees it.

2. **Vertically-set labels.** A sideways ``RANDOMIZATION`` divider between two column
   groups is *not* stored as rotated text in the protocols we examined. It is thirteen
   separate single-character spans at a constant x with a regular vertical pitch, each
   with a perfectly horizontal direction vector. Every generic extractor we benchmarked
   assigns those characters to thirteen different grid rows. Detecting the run by its
   geometry and lifting it out is what stops that.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import fitz  # PyMuPDF

# A span counts as rotated when its *visual* writing direction departs from horizontal by
# more than this. Real horizontal text is exact, so the tolerance only absorbs float noise.
_ROTATION_TOLERANCE_DEG = 5.0

# Vertically-set label detection.
_VLABEL_MIN_CHARS = 4  # shorter runs are too likely to be coincidence
_VLABEL_X_TOLERANCE = 4.0  # how far the left edges may wander, in points
_VLABEL_PITCH_JITTER = 0.35  # allowed relative variation in line-to-line spacing


@dataclass
class Word:
    """One text run with its position on the page, in visual coordinates."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    font: str
    bold: bool
    rotation_deg: float = 0.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class Line:
    """A ruling line (table border), axis-aligned, in visual coordinates."""

    orientation: str  # "h" or "v"
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


def join_words(words: list["Word"]) -> str:
    """Join spans into text, respecting the horizontal gaps actually on the page."""
    if not words:
        return ""
    ordered = sorted(words, key=lambda w: w.x0)
    out = [ordered[0].text]
    for prev, cur in zip(ordered, ordered[1:]):
        gap = cur.x0 - prev.x1
        # A space is already present in the span text often enough that we must not add
        # a second one; and a genuine inter-word gap is a meaningful fraction of the em.
        threshold = max(0.8, min(prev.size, cur.size) * 0.22)
        needs_space = gap > threshold and not out[-1].endswith(" ") and not cur.text.startswith(" ")
        out.append(" " + cur.text if needs_space else cur.text)
    return re.sub(r"\s+", " ", "".join(out)).strip()


@dataclass
class TextLine:
    """Words grouped into a visual line, left to right."""

    words: list[Word]
    y0: float
    y1: float

    @property
    def text(self) -> str:
        """Reassemble the line, inserting a space only where the page has one.

        Several of the reference protocols emit a word as multiple spans -- a font or
        kerning change mid-word -- so naively joining spans with a space produces
        "Appendi x I: Tim e and Event s Schedule". Heading detection, row labels and
        footnote text all degrade from that, so the gap between spans decides: a gap
        narrower than a fraction of the font size means the spans are one word.
        """
        return join_words(self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words) if self.words else 0.0

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words) if self.words else 0.0


@dataclass
class VerticalLabel:
    """A label set one character per line, running down the page.

    Structurally an annotation (a divider, a spanning banner turned on its side), never a
    grid row. Kept so the information is not lost, but excluded from the table body.
    """

    text: str
    x: float
    y0: float
    y1: float
    rotation_deg: float


class Page:
    """One page of the document, presented in visual (reader's) coordinates."""

    def __init__(self, doc: "PdfDoc", index: int):
        self._doc = doc
        self.index = index  # 0-based
        self.number = index + 1  # 1-based, what humans and the schema use
        self._page = doc._fitz[index]

    # -- geometry ----------------------------------------------------------------------

    @property
    def width(self) -> float:
        return self._page.rect.width

    @property
    def height(self) -> float:
        return self._page.rect.height

    @property
    def rotation(self) -> int:
        return self._page.rotation

    @property
    def orientation(self) -> str:
        """Landscape when the page as *rendered* is wider than tall.

        ``page.rect`` already accounts for ``/Rotate``, so this matches what a reader sees
        rather than the raw MediaBox.
        """
        return "landscape" if self.width > self.height else "portrait"

    @property
    def _rot_matrix(self) -> fitz.Matrix:
        """Maps stored (unrotated) coordinates into visual coordinates."""
        return self._page.rotation_matrix

    def _to_visual(self, bbox) -> tuple[float, float, float, float]:
        r = fitz.Rect(bbox) * self._rot_matrix
        r.normalize()
        return (r.x0, r.y0, r.x1, r.y1)

    def _visual_angle(self, direction) -> float:
        """Direction vector angle after the page rotation is applied, in degrees.

        Only the linear part of the matrix acts on a direction, so the translation
        components are deliberately ignored.
        """
        dx, dy = direction
        m = self._rot_matrix
        vdx = m.a * dx + m.c * dy
        vdy = m.b * dx + m.d * dy
        return math.degrees(math.atan2(vdy, vdx))

    # -- text --------------------------------------------------------------------------

    @cached_property
    def _raw_dict(self) -> dict:
        return self._page.get_text("dict", sort=False)

    @cached_property
    def _partitioned(self) -> tuple[list[Word], list[Word], list[VerticalLabel]]:
        """Split spans into (horizontal body text, rotated text, vertical labels)."""
        horizontal: list[Word] = []
        rotated: list[Word] = []

        for block in self._raw_dict.get("blocks", []):
            if block.get("type") != 0:  # not a text block
                continue
            for line in block.get("lines", []):
                angle = self._visual_angle(line.get("dir", (1.0, 0.0)))
                is_rotated = abs(angle) > _ROTATION_TOLERANCE_DEG

                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text.strip():
                        continue
                    x0, y0, x1, y1 = self._to_visual(span["bbox"])
                    font = span.get("font", "")
                    flags = span.get("flags", 0)
                    word = Word(
                        text=text,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        size=span.get("size", 0.0),
                        font=font,
                        bold=bool(flags & 2**4) or "bold" in font.lower(),
                        rotation_deg=angle if is_rotated else 0.0,
                    )
                    (rotated if is_rotated else horizontal).append(word)

        horizontal, vertical_labels = self._lift_vertical_labels(horizontal)

        horizontal.sort(key=lambda w: (round(w.y0, 1), w.x0))
        rotated.sort(key=lambda w: (round(w.x0, 1), w.y0))
        return horizontal, rotated, vertical_labels

    @staticmethod
    def _lift_vertical_labels(
        words: list[Word],
    ) -> tuple[list[Word], list[VerticalLabel]]:
        """Pull out labels set one character per line at a constant x.

        The naive signature -- a column of single characters at a shared left edge -- also
        matches two things that are emphatically *not* annotations, and both occur in the
        reference protocols:

        * a column of ``X`` marks running down a visit column, and
        * the first character of every row label, when the producing application emits it
          as its own span (``A`` + ``ssessment``).

        Two further tests separate them, and both were checked against the documents:

        * **Right-adjacency.** A word fragment has a span resuming a few points to its
          right on the same line. A vertically-set letter has empty space beside it.
        * **Character diversity.** A vertically-set word spells something; a data column
          repeats one glyph. ``RANDOMIZATION`` scores 9 distinct of 13, a column of X
          scores 1 of 19.
        """
        singles = [
            w for w in words if len(w.text.strip()) == 1 and w.text.strip().isalpha()
        ]
        if len(singles) < _VLABEL_MIN_CHARS:
            return words, []

        by_x = sorted(singles, key=lambda w: w.x0)
        clusters: list[list[Word]] = [[by_x[0]]]
        for w in by_x[1:]:
            if w.x0 - clusters[-1][-1].x0 <= _VLABEL_X_TOLERANCE:
                clusters[-1].append(w)
            else:
                clusters.append([w])

        labels: list[VerticalLabel] = []
        consumed: set[int] = set()

        for cluster in clusters:
            if len(cluster) < _VLABEL_MIN_CHARS:
                continue
            column = sorted(cluster, key=lambda w: w.y0)

            for run in Page._regular_pitch_runs(column):
                if len(run) < _VLABEL_MIN_CHARS:
                    continue
                if Page._mostly_word_fragments(run, words):
                    continue

                chars = [w.text.strip() for w in run]
                distinct = set(chars)
                if len(distinct) < 3 or len(distinct) / len(chars) < 0.6:
                    continue
                if Page._mostly_line_leading(run, words):
                    continue

                labels.append(
                    VerticalLabel(
                        text="".join(chars),
                        x=sum(w.x0 for w in run) / len(run),
                        y0=min(w.y0 for w in run),
                        y1=max(w.y1 for w in run),
                        # Reported as 90 degrees because that is how it reads on the page,
                        # even though each glyph is stored upright.
                        rotation_deg=90.0,
                    )
                )
                consumed.update(id(w) for w in run)

        if not consumed:
            return words, []
        return [w for w in words if id(w) not in consumed], labels

    @staticmethod
    def _regular_pitch_runs(column: list[Word]) -> list[list[Word]]:
        """Split a vertically sorted column into maximal runs of even spacing."""
        if len(column) < 2:
            return []
        gaps = [b.y0 - a.y0 for a, b in zip(column, column[1:])]
        median_gap = sorted(gaps)[len(gaps) // 2]
        if median_gap <= 0:
            return []

        runs: list[list[Word]] = []
        run = [column[0]]
        for prev, cur in zip(column, column[1:]):
            gap = cur.y0 - prev.y0
            if abs(gap - median_gap) <= median_gap * _VLABEL_PITCH_JITTER:
                run.append(cur)
            else:
                runs.append(run)
                run = [cur]
        runs.append(run)
        return runs

    @staticmethod
    def _mostly_word_fragments(run: list[Word], all_words: list[Word]) -> bool:
        """True when these characters are the leading glyph of a longer word.

        Checks for a span resuming just to the right on the same visual line. If most of
        the run has one, this is a column of row labels, not a sideways annotation.
        """
        with_neighbour = 0
        for w in run:
            for other in all_words:
                if other is w:
                    continue
                if not (w.x1 - 1.0 <= other.x0 <= w.x1 + 6.0):
                    continue
                if other.y1 < w.y0 + 1 or other.y0 > w.y1 - 1:
                    continue
                with_neighbour += 1
                break
        return with_neighbour > len(run) * 0.5

    @staticmethod
    def _mostly_line_leading(run: list[Word], all_words: list[Word]) -> bool:
        """True when these characters each open their own line.

        A footnote block lists markers ``a``, ``b``, ``c`` down the left margin, one per
        line: distinct characters, regular pitch, nothing immediately to the right. That
        is indistinguishable from a vertically-set label on those tests alone. What
        separates them is position -- a list marker is the leftmost thing on its line,
        whereas a divider set between two column groups always has row content to its
        left. A vertical label in the leftmost column would be missed by this; none of the
        reference protocols has one, and the alternative error (shredding footnote
        markers) is worse.
        """
        leading = 0
        for w in run:
            has_left = any(
                other is not w
                and other.x1 <= w.x0 - 1.0
                and not (other.y1 < w.y0 + 1 or other.y0 > w.y1 - 1)
                for other in all_words
            )
            if not has_left:
                leading += 1
        return leading > len(run) * 0.5

    @property
    def words(self) -> list[Word]:
        """Horizontal body text only. This is what the grid is built from."""
        return self._partitioned[0]

    @property
    def rotated_words(self) -> list[Word]:
        """Spans whose visual writing direction is not horizontal."""
        return self._partitioned[1]

    @property
    def vertical_labels(self) -> list[VerticalLabel]:
        """Labels set one character per line. Annotations, never grid rows."""
        return self._partitioned[2]

    @cached_property
    def rotated_runs(self) -> list[tuple[str, float, tuple[float, float, float, float]]]:
        """All non-horizontal labels on the page as ``(text, angle, bbox)``.

        Combines genuinely rotated spans with the vertically-set single-character runs, so
        callers have one place to look for "text that is not part of the grid".
        """
        runs: list[tuple[str, float, tuple[float, float, float, float]]] = []

        for lbl in self.vertical_labels:
            runs.append(
                (lbl.text, lbl.rotation_deg, (lbl.x, lbl.y0, lbl.x + 12.0, lbl.y1))
            )

        remaining = list(self.rotated_words)
        while remaining:
            seed = remaining.pop(0)
            band = [seed]
            keep: list[Word] = []
            for w in remaining:
                same_angle = (
                    abs(w.rotation_deg - seed.rotation_deg) < _ROTATION_TOLERANCE_DEG
                )
                overlaps_x = not (w.x1 < seed.x0 - 4 or w.x0 > seed.x1 + 4)
                if same_angle and overlaps_x:
                    band.append(w)
                else:
                    keep.append(w)
            remaining = keep

            band.sort(key=lambda w: w.y0, reverse=seed.rotation_deg < 0)
            text = "".join(w.text for w in band).strip()
            if not text:
                continue
            bbox = (
                min(w.x0 for w in band),
                min(w.y0 for w in band),
                max(w.x1 for w in band),
                max(w.y1 for w in band),
            )
            runs.append((text, seed.rotation_deg, bbox))

        return runs

    @cached_property
    def lines(self) -> list[TextLine]:
        """Horizontal words grouped into visual lines by vertical overlap."""
        if not self.words:
            return []

        by_y = sorted(self.words, key=lambda w: (w.y0, w.x0))
        groups: list[list[Word]] = [[by_y[0]]]

        for w in by_y[1:]:
            current = groups[-1]
            ref = current[0]
            tol = max(2.0, min(ref.height, w.height) * 0.5)
            if abs(w.y0 - ref.y0) <= tol or (w.y0 < ref.y1 - tol and w.y1 > ref.y0 + tol):
                current.append(w)
            else:
                groups.append([w])

        out: list[TextLine] = []
        for g in groups:
            g.sort(key=lambda w: w.x0)
            out.append(TextLine(words=g, y0=min(w.y0 for w in g), y1=max(w.y1 for w in g)))
        return out

    @cached_property
    def text(self) -> str:
        """Plain text in visual reading order. Used for scoring and heading detection."""
        return "\n".join(line.text for line in self.lines)

    # -- vector graphics ---------------------------------------------------------------

    @cached_property
    def ruling_lines(self) -> list[Line]:
        """Table borders, in visual coordinates.

        Protocols draw borders three different ways -- stroked lines, stroked rectangles,
        and very thin filled rectangles -- so all three are normalised here.
        """
        out: list[Line] = []
        try:
            drawings = self._page.get_drawings()
        except Exception:
            return out

        for d in drawings:
            for item in d.get("items", []):
                kind = item[0]
                if kind == "l":
                    p1, p2 = item[1], item[2]
                    a = fitz.Point(p1.x, p1.y) * self._rot_matrix
                    b = fitz.Point(p2.x, p2.y) * self._rot_matrix
                    self._add_line(out, a.x, a.y, b.x, b.y)
                elif kind == "re":
                    r = fitz.Rect(item[1]) * self._rot_matrix
                    r.normalize()
                    if r.width <= 2.0 and r.height > 2.0:  # thin vertical bar
                        cx = (r.x0 + r.x1) / 2
                        self._add_line(out, cx, r.y0, cx, r.y1)
                    elif r.height <= 2.0 and r.width > 2.0:  # thin horizontal bar
                        cy = (r.y0 + r.y1) / 2
                        self._add_line(out, r.x0, cy, r.x1, cy)
                    else:  # a real box contributes all four edges
                        self._add_line(out, r.x0, r.y0, r.x1, r.y0)
                        self._add_line(out, r.x0, r.y1, r.x1, r.y1)
                        self._add_line(out, r.x0, r.y0, r.x0, r.y1)
                        self._add_line(out, r.x1, r.y0, r.x1, r.y1)
        return out

    @staticmethod
    def _add_line(out: list[Line], x0: float, y0: float, x1: float, y1: float) -> None:
        if abs(y1 - y0) <= 1.5 and abs(x1 - x0) > 3:
            y = (y0 + y1) / 2
            out.append(Line("h", min(x0, x1), y, max(x0, x1), y))
        elif abs(x1 - x0) <= 1.5 and abs(y1 - y0) > 3:
            x = (x0 + x1) / 2
            out.append(Line("v", x, min(y0, y1), x, max(y0, y1)))

    # -- rendering ---------------------------------------------------------------------

    def render_png(self, dpi: int = 200) -> bytes:
        """Rasterise the page as a reader would see it. This is the vision engine's input."""
        pix = self._page.get_pixmap(dpi=dpi)
        return pix.tobytes("png")


class PdfDoc:
    """An opened protocol PDF."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._bytes = self.path.read_bytes()
        self._fitz = fitz.open(stream=self._bytes, filetype="pdf")
        self._pages: dict[int, Page] = {}

    def __len__(self) -> int:
        return self._fitz.page_count

    def __iter__(self):
        for i in range(len(self)):
            yield self.page(i)

    def page(self, index: int) -> Page:
        """0-indexed page access."""
        if index not in self._pages:
            self._pages[index] = Page(self, index)
        return self._pages[index]

    def page_no(self, number: int) -> Page:
        """1-indexed page access, matching the schema."""
        return self.page(number - 1)

    def close(self) -> None:
        self._fitz.close()

    # -- document level metadata -------------------------------------------------------

    @cached_property
    def sha256(self) -> str:
        return hashlib.sha256(self._bytes).hexdigest()

    @cached_property
    def toc(self) -> list[tuple[int, str, int]]:
        """PDF outline as (level, title, page_number_1_indexed). Often empty or noisy."""
        try:
            return [(lvl, title, pg) for lvl, title, pg in self._fitz.get_toc()]
        except Exception:
            return []

    @cached_property
    def has_text_layer(self) -> bool:
        """True when a meaningful fraction of sampled pages carry extractable text.

        A protocol that fails this is scanned; the geometric engine cannot help, the run
        degrades to vision-only, and the output says so rather than silently thinning out.
        """
        step = max(1, len(self) // 12)
        sample = range(0, len(self), step)
        with_text = sum(1 for i in sample if len(self.page(i).words) > 20)
        return with_text >= 2

    @cached_property
    def text_layer_warnings(self) -> list[str]:
        """Detect font problems that cause silent character corruption.

        A font with a missing or malformed ToUnicode CMap extracts to plausible-looking
        but wrong characters. That is precisely the failure the brief warns is "not
        obvious until you look closely", so it is surfaced up front instead of discovered
        later in a diff.
        """
        warnings: list[str] = []
        suspect_fonts: set[str] = set()

        for i in range(len(self)):
            try:
                fonts = self._fitz.get_page_fonts(i, full=False)
            except Exception:
                continue
            for font in fonts:
                xref, basefont = font[0], font[3]
                if xref <= 0:
                    continue
                try:
                    key_type, _ = self._fitz.xref_get_key(xref, "ToUnicode")
                except Exception:
                    continue
                if key_type == "null":
                    suspect_fonts.add(basefont)

        if suspect_fonts:
            names = ", ".join(sorted(suspect_fonts)[:5])
            warnings.append(
                f"{len(suspect_fonts)} embedded font(s) have no ToUnicode CMap ({names}). "
                f"Characters extracted from these fonts may be silently wrong; the vision "
                f"engine is authoritative on affected pages."
            )

        bad = total = 0
        step = max(1, len(self) // 15)
        for i in range(0, len(self), step):
            t = self.page(i).text
            total += len(t)
            bad += sum(1 for ch in t if ch == "�" or 0xE000 <= ord(ch) <= 0xF8FF)
        if total and bad / total > 0.002:
            warnings.append(
                f"Text layer contains {bad} replacement or private-use characters "
                f"({bad / total:.2%} of sampled text); glyph mapping is unreliable."
            )

        return warnings

    @cached_property
    def front_matter(self) -> str:
        """First few pages of text, used for title and sponsor guessing."""
        return "\n".join(self.page(i).text for i in range(min(4, len(self))))

    def guess_metadata(self) -> dict[str, str | None]:
        """Best-effort document identification. Advisory only; nothing depends on it."""
        meta = self._fitz.metadata or {}
        text = self.front_matter
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

        title = (meta.get("title") or "").strip() or None
        # Word-produced PDFs carry the source filename as the title. Useless; ignore it.
        if title and (
            title.lower().startswith("microsoft word")
            or re.search(r"\.(doc|docx|pdf|rtf)\b", title, re.I)
        ):
            title = None
        if not title:
            candidates = [
                ln
                for ln in lines[:80]
                if 25 <= len(ln) <= 200
                and not re.match(r"^(page|version|date|confidential|table\b)", ln, re.I)
            ]
            title = max(candidates, key=len) if candidates else None

        sponsor = None
        m = re.search(r"(?:^|\n)\s*(?:sponsor|sponsored by)\s*[:\-]?\s*(.{3,80})", text, re.I)
        if m:
            sponsor = m.group(1).strip()
        else:
            for known in ("National Institute on Drug Abuse", "Eli Lilly and Company"):
                if known.lower() in text.lower():
                    sponsor = known
                    break

        protocol_id = None
        m = re.search(
            r"(?:protocol|study)\s*(?:no\.?|number|#|id)\s*[:\-]?\s*"
            r"([A-Z0-9][A-Z0-9\-/().]{4,30})",
            text,
            re.I,
        )
        if m:
            protocol_id = m.group(1).strip().rstrip(".,")

        return {
            "title_guess": title,
            "sponsor_guess": sponsor,
            "protocol_id_guess": protocol_id,
        }
