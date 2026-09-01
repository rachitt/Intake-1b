"""Footnote linkage and page-spill handling.

The brief grades three separate things here, and this module is responsible for all of
them: the full text of every footnote, the linkage from each marker to the specific cell,
row, column or header it sits on, and correct handling of footnote text that continues
onto a following page.

Linkage is the hard part. Markers arrive in several shapes across the reference protocols:

* a bare superscript letter attached to a cell's ``X`` -- and the text layer may or may not
  keep it as a separate span;
* the footnote list itself printed as ``Xa - ...``, ``Xb - ...``, so the *marker* as
  printed in the list is ``Xa`` while the marker sitting on the cell is ``a``;
* asterisk tiers ``*``, ``**``, ``***``, ``****``;
* one protocol whose marker set is inconsistent (``Xa`` through ``Xi`` and then ``XJ``).

And there is a trap: one protocol appends CRF form numbers to its row labels -- "Addiction
Research Center Inventory (22)" -- which look exactly like parenthesised numeric markers.
Those are only ever treated as markers when the same token also appears in the footnote
block's own marker set, which it does not.
"""

from __future__ import annotations

import re

from .schema import Cell, Column, ColumnGroup, Footnote, FootnoteAnchor, Row, RowGroup

# A marker-led line in a footnote block.
FOOTNOTE_LINE_RE = re.compile(
    r"^\s*(?P<marker>"
    r"X?[a-zA-Z]{1,2}"  # a, b, Xa, XJ
    r"|\d{1,2}"  # 1, 12
    r"|\*{1,4}"  # * ** *** ****
    r"|[†‡§¶#•·●]{1,3}"  # symbol tiers, incl. bullets
    r"|\([a-zA-Z0-9]{1,2}\)"  # (a), (1)
    r")\s*[-–—.):]\s+(?P<text>\S.*)$"
)

# Trailing marker(s) on a cell or label, e.g. "X a", "Xa", "X*", "X a,b".
_TRAILING_MARKER_RE = re.compile(
    r"(?P<body>.*?)\s*(?P<markers>(?:[a-zA-Z]|\*{1,4}|[†‡§¶#]|\d{1,2})"
    r"(?:\s*[,/]\s*(?:[a-zA-Z]|\*{1,4}|[†‡§¶#]|\d{1,2}))*)\s*$"
)


def marker_aliases(marker: str) -> set[str]:
    """Every form a marker may take between the footnote list and the grid.

    A footnote printed as ``Xa - ...`` is referenced on the grid as the cell text ``Xa``,
    but the marker proper is ``a``. Both spellings must resolve to the same footnote.
    """
    m = (marker or "").strip()
    if not m:
        return set()

    forms = {m, m.lower()}
    stripped = m.strip("()")
    forms.add(stripped)
    forms.add(stripped.lower())
    # "Xa" -> "a"; also covers the inconsistent "XJ" -> "J"/"j".
    if len(stripped) == 2 and stripped[0] in "Xx" and stripped[1].isalpha():
        forms.add(stripped[1])
        forms.add(stripped[1].lower())
    return {f for f in forms if f}


def split_trailing_markers(text: str, known: set[str]) -> tuple[str, list[str]]:
    """Separate a cell's content from any footnote markers printed inside it.

    Only markers that exist in the footnote block are recognised, which is what keeps CRF
    form numbers such as "(22)" from being mistaken for footnote references. ``raw`` is
    never modified by this -- the caller keeps it intact and stores the split result
    separately.
    """
    t = (text or "").strip()
    if not t or not known:
        return t, []

    # Whole-token match first: the cell is nothing but a marker-bearing X.
    lowered = t.lower()
    if lowered in known:
        return t, [t]

    found: list[str] = []
    body = t

    m = _TRAILING_MARKER_RE.match(t)
    if m:
        candidates = re.split(r"[,/\s]+", m.group("markers").strip())
        if candidates and all(c.lower() in known for c in candidates if c):
            found = [c for c in candidates if c]
            body = m.group("body").strip() or t

    if not found:
        # "Xa" style: a leading X followed by a known single-letter marker.
        m2 = re.fullmatch(r"([Xx]|\d+[Xx])\s*([a-zA-Z])", t)
        if m2 and m2.group(2).lower() in known:
            return m2.group(1), [m2.group(2)]

    return body, found


def split_leading_markers(text: str, known: set[str]) -> tuple[str, list[str]]:
    """Separate footnote markers printed at the *start* of a label.

    Most protocols trail their markers (``Xa``, ``X*``). One of the reference protocols
    leads with them instead -- ``* Morphine (0600, 1100, 1630, 2200 h)``,
    ``**Lofexidine or Placebo``, ``•Modified Himmelsbach (MHOWS)`` -- and a linker that
    only looks at the end of a string finds nothing to attach, leaving every footnote in
    that protocol orphaned.

    Symbols may sit flush against the text, because that is how they are printed. Letters
    and digits must be followed by a separator, otherwise an ordinary label beginning with
    a capital ``A`` would be read as a reference to footnote ``a``.
    """
    t = (text or "").strip()
    if not t or not known:
        return t, []

    found: list[str] = []
    rest = t

    while True:
        m = re.match(r"^(\*{1,4}|[†‡§¶#•·●])\s*", rest)
        if m is None:
            m = re.match(r"^([a-zA-Z]{1,2}|\d{1,2})\s*[-–—.):]\s+", rest)
        if m is None:
            break

        marker = m.group(1)
        if marker.lower() not in known:
            break
        # Refuse to strip everything: a marker with no label after it is not a marker.
        remainder = rest[m.end():].strip()
        if len(remainder) < 3:
            break

        found.append(marker)
        rest = remainder

    return (rest if found else t), found


def parse_footnote_block(
    lines: list[tuple[str, int]],
) -> list[tuple[str, str, list[int], bool]]:
    """Parse marker-led lines into footnotes, absorbing continuations.

    ``lines`` is ``[(text, page_number)]`` in reading order across the footnote pages.
    Returns ``[(marker, text, pages, text_complete)]``.

    A line that carries no marker continues the footnote above it. That is the whole
    mechanism behind page-spill support: the continuation of a block has no heading, no
    marker and nothing that ties it to the table, so the only correct reading is "this
    belongs to whatever was open when the page ended".
    """
    out: list[list] = []

    for text, page in lines:
        stripped = text.strip()
        if not stripped:
            continue

        m = FOOTNOTE_LINE_RE.match(stripped)
        if m:
            out.append([m.group("marker").strip(), m.group("text").strip(), [page]])
            continue

        if out:
            # Continuation of the footnote currently open, possibly on a new page.
            out[-1][1] = f"{out[-1][1]} {stripped}".strip()
            if page not in out[-1][2]:
                out[-1][2].append(page)

    results: list[tuple[str, str, list[int], bool]] = []
    for marker, text, pages in out:
        # A footnote that stops without terminal punctuation and without a following
        # sibling is suspicious; flag rather than silently accept.
        complete = bool(re.search(r"[.!?)\]]\s*$", text)) or len(text) < 40
        results.append((marker, text, sorted(set(pages)), complete))
    return results


def link_footnotes(
    footnotes: list[Footnote],
    rows: list[Row],
    columns: list[Column],
    cells: list[Cell],
    row_groups: list[RowGroup] | None = None,
    column_groups: list[ColumnGroup] | None = None,
) -> list[Footnote]:
    """Attach every footnote to the grid elements that reference it.

    Mutates and returns ``footnotes``. Anything that cannot be linked keeps its text and
    records ``unattached_reason`` -- dropping a footnote is worse than admitting we could
    not place it.
    """
    by_alias: dict[str, Footnote] = {}
    for fn in footnotes:
        for alias in marker_aliases(fn.marker):
            by_alias.setdefault(alias.lower(), fn)

    known = set(by_alias)
    if not known:
        return footnotes

    def anchors_for(refs: list[str]) -> list[tuple[Footnote, str]]:
        hits: list[tuple[Footnote, str]] = []
        for ref in refs:
            fn = by_alias.get((ref or "").strip().lower())
            if fn is not None:
                hits.append((fn, ref))
        return hits

    # -- cells ---------------------------------------------------------------------------
    for cell in cells:
        refs = list(cell.footnote_refs)
        if not refs:
            _, found = split_trailing_markers(cell.raw, known)
            refs = found
        for fn, ref in anchors_for(refs):
            if ref not in cell.footnote_refs:
                cell.footnote_refs.append(ref)
            fn.attached_to.append(
                FootnoteAnchor(kind="cell", row_id=cell.row_id, column_id=cell.column_id)
            )

    # -- rows ----------------------------------------------------------------------------
    for row in rows:
        refs = list(row.footnote_refs)
        if not refs:
            _, trailing = split_trailing_markers(row.label, known)
            _, leading = split_leading_markers(row.label, known)
            refs = trailing + [m for m in leading if m not in trailing]
        for fn, ref in anchors_for(refs):
            if ref not in row.footnote_refs:
                row.footnote_refs.append(ref)
            fn.attached_to.append(FootnoteAnchor(kind="row", row_id=row.id))

    # -- columns and their stacked headers -----------------------------------------------
    for col in columns:
        refs = list(col.footnote_refs)
        for hc in col.header_cells:
            refs.extend(hc.footnote_refs)
            _, found = split_trailing_markers(hc.text, known)
            refs.extend(found)
        for fn, ref in anchors_for(refs):
            if ref not in col.footnote_refs:
                col.footnote_refs.append(ref)
            fn.attached_to.append(FootnoteAnchor(kind="column", column_id=col.id))

    # -- group banners -------------------------------------------------------------------
    for group in column_groups or []:
        refs = list(group.footnote_refs)
        _, found = split_trailing_markers(group.label, known)
        refs.extend(found)
        for fn, ref in anchors_for(refs):
            fn.attached_to.append(
                FootnoteAnchor(kind="column_group", group_id=group.id)
            )

    for group in row_groups or []:
        refs = list(group.footnote_refs)
        _, trailing = split_trailing_markers(group.label, known)
        _, leading = split_leading_markers(group.label, known)
        refs.extend(trailing)
        refs.extend(m for m in leading if m not in trailing)
        for fn, ref in anchors_for(refs):
            fn.attached_to.append(FootnoteAnchor(kind="row_group", group_id=group.id))

    # -- deduplicate and report failures --------------------------------------------------
    for fn in footnotes:
        seen: set[tuple] = set()
        unique: list[FootnoteAnchor] = []
        for a in fn.attached_to:
            key = (a.kind, a.row_id, a.column_id, a.group_id)
            if key not in seen:
                seen.add(key)
                unique.append(a)
        fn.attached_to = unique

        if not fn.attached_to:
            fn.unattached_reason = (
                f"No cell, row, column or header carries marker '{fn.marker}'. The "
                f"footnote may apply to the table as a whole, or its marker may be "
                f"rendered in a form the grid extraction did not preserve."
            )

    return footnotes
