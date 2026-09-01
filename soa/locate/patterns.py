"""Shared vocabulary for recognising a Schedule of Activities.

Kept in one module because the locator, the footnote parser and the benchmark harness all
need the same notion of "what an SoA looks like", and drift between three private copies
of these regexes would be a silent source of recall loss.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------------------
# headings
# --------------------------------------------------------------------------------------

# The section heading is not always "Schedule of Activities". Across the five reference
# protocols alone it is "Schedule of Events", "Time and Events Schedule", "Schedule of
# Blood Collections", "Schedule of Measures and Data Collection" and "Overview of Study
# Assessments". The pattern is therefore a product of two loose vocabularies rather than a
# list of known titles.
_HEAD_NOUN = r"(?:schedule|table|chart|flow\s*-?\s*chart|flowchart|overview|calendar|matrix)"
_HEAD_SUBJ = (
    r"(?:activit|assessment|event|measure|procedure|visit|evaluation|observation|"
    r"blood|sampling|collection|study\s+conduct|trial\s+activit)"
)

SOA_HEADING_RE = re.compile(
    rf"{_HEAD_NOUN}\s+(?:of\s+|for\s+)?(?:\w+\s+){{0,3}}?{_HEAD_SUBJ}",
    re.I,
)

# Fixed phrases that do not fit the noun/subject product above.
SOA_HEADING_LITERALS_RE = re.compile(
    r"(?:time\s+and\s+events?|study\s+flow\s*-?\s*chart|schedule\s+of\s+study|"
    r"visit\s+schedule|study\s+calendar|assessment\s+schedule|"
    r"schedule\s+of\s+events?|trial\s+schedule)",
    re.I,
)

# Headings that look like an SoA but are not one. A study schema is an arms-and-arrows
# diagram, not an activity-by-visit grid, and two of the reference protocols have one.
SOA_HEADING_EXCLUDE_RE = re.compile(
    r"(?:study\s+schema|schema\s+diagram|table\s+of\s+contents|list\s+of\s+tables|"
    r"list\s+of\s+abbreviations|list\s+of\s+appendices)",
    re.I,
)

# A footnote block carries its own heading in some protocols ("Notes on the Schedule of
# Assessments", "Footnotes to Flow Chart"). Those read as SoA headings but introduce prose,
# not a grid, and must not be mistaken for the start of another schedule.
FOOTNOTE_BLOCK_HEADING_RE = re.compile(
    r"^\s*(?:notes?\s+(?:on|to|for)\b|footnotes?\b|abbreviations?\s*[:.]|"
    r"key\s*[:.]|legend\s*[:.])",
    re.I,
)

# Some producers set headings with letter-spacing, so the text layer yields
# "Appendi x I: Tim e and Event s Schedul e". Matching a whitespace-stripped copy of the
# line against whitespace-free patterns recovers those, without loosening the main patterns
# (which would then start matching across unrelated adjacent words).
_SOA_HEADING_DESPACED_RE = re.compile(
    SOA_HEADING_RE.pattern.replace(r"\s+", "").replace(r"\s*", ""), re.I
)
_SOA_LITERALS_DESPACED_RE = re.compile(
    SOA_HEADING_LITERALS_RE.pattern.replace(r"\s+", "").replace(r"\s*", ""), re.I
)

# Cross-reference phrasing: a sentence that merely points at the schedule.
_CROSS_REF_RE = re.compile(r"\b(?:see|refer\s+to|described\s+in|listed\s+in|provided\s+in)\b", re.I)


def _despace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _looks_letter_spaced(text: str) -> bool:
    """True when a line shows the letter-spacing artefact.

    Stripping whitespace before matching recovers headings that the text layer breaks up
    ("Appendi x I: Tim e and Event s Schedul e"), but on ordinary prose the same trick is
    dangerous: with the spaces gone, "...the final scheduled study visit..." reads as
    "schedule" followed by "visit" and matches the heading pattern. So the de-spaced test
    is only allowed to run on lines that actually carry the artefact, which shows up as an
    unusual number of stranded single letters.
    """
    tokens = text.split()
    if len(tokens) < 4:
        return False
    stranded = sum(1 for t in tokens if len(t) == 1 and t.isalpha() and t not in "aAI")
    # One stranded letter is enough when the line is short: "APPENDIX II: Schedul e of
    # Blood Collections" strands only the trailing "e". Ordinary prose sentences strand
    # none at all, so the ratio does the real work.
    return stranded >= 1 and stranded / len(tokens) >= 0.10


def repair_letter_spacing(text: str) -> str:
    """Rejoin letters stranded by a letter-spaced heading.

    "Appendi x I: Tim e and Event s Schedul e" becomes "Appendix I: Time and Events
    Schedule". Only applied to lines that show the artefact, and only single letters that
    follow an alphabetic token are absorbed, so genuine one-letter words survive.
    """
    if not _looks_letter_spaced(text):
        return text
    tokens = text.split()
    out: list[str] = []
    for tok in tokens:
        if (
            out
            and len(tok) == 1
            and tok.isalpha()
            and tok not in "aAI"
            and out[-1][-1:].isalpha()
        ):
            out[-1] += tok
        else:
            out.append(tok)
    return " ".join(out)


def looks_like_soa_heading(text: str) -> bool:
    """True when a line reads like the title of a Schedule of Activities."""
    if not text or len(text) > 220:
        return False
    if SOA_HEADING_EXCLUDE_RE.search(text):
        return False
    if FOOTNOTE_BLOCK_HEADING_RE.match(text):
        return False
    if SOA_HEADING_RE.search(text) or SOA_HEADING_LITERALS_RE.search(text):
        return True
    if not _looks_letter_spaced(text):
        return False
    squashed = _despace(text)
    return bool(
        _SOA_HEADING_DESPACED_RE.search(squashed)
        or _SOA_LITERALS_DESPACED_RE.search(squashed)
    )


def is_footnote_block_heading(text: str) -> bool:
    """True for headings that introduce a footnote block rather than a table."""
    return bool(FOOTNOTE_BLOCK_HEADING_RE.match((text or "").strip()))


def is_title_like(text: str) -> bool:
    """Reject prose that merely *mentions* the schedule.

    A body sentence such as "...at early termination (see Schedule of Events, Attachment
    LZZT.1)." satisfies the heading vocabulary but is a cross-reference, not a heading.
    A real heading is short, starts at the beginning of its line, and does not open
    lower-case.
    """
    t = (text or "").strip()
    if not t or len(t) > 140:
        return False
    if t[0].islower():
        return False

    m = SOA_HEADING_RE.search(t) or SOA_HEADING_LITERALS_RE.search(t)
    if m is None:
        if not _looks_letter_spaced(t):
            return False
        squashed = _despace(t)
        m2 = _SOA_HEADING_DESPACED_RE.search(squashed) or _SOA_LITERALS_DESPACED_RE.search(
            squashed
        )
        return bool(m2 and m2.start() <= 30)

    prefix = t[: m.start()]
    # A cross-reference anywhere before the vocabulary disqualifies the line.
    if _CROSS_REF_RE.search(prefix):
        return False
    # Only a table/appendix/section number may precede the heading vocabulary.
    if len(prefix.strip()) > 40:
        return False
    if prefix.strip() and not re.match(
        r"^\s*(?:table|appendix|attachment|figure|exhibit|section|part|\d|[ivxlc]+[.):])",
        prefix.strip(),
        re.I,
    ):
        return False
    return True


# --------------------------------------------------------------------------------------
# schedule classification
# --------------------------------------------------------------------------------------

KIND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "pk",
        re.compile(
            r"\b(?:pk|pharmacokinetics?|blood\s+collections?|blood\s+sampl\w*|"
            r"plasma\s+sampl\w*|pk\s+sampl\w*)\b",
            re.I,
        ),
    ),
    (
        "sub_study",
        re.compile(r"\b(?:sub-?study|substudy|cohort\s+[a-z0-9])\b", re.I),
    ),
    (
        "extension",
        re.compile(r"\b(?:extension|long-?term\s+follow|open-?label\s+extension)\b", re.I),
    ),
]


def classify_kind(heading: str) -> str:
    """Map a heading to a ScheduleKind value. Defaults to ``main``, never guesses wildly."""
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(heading or ""):
            return kind
    return "main"


# --------------------------------------------------------------------------------------
# continuation markers
# --------------------------------------------------------------------------------------

CONTINUATION_RE = re.compile(
    r"\((?:cont(?:inued|\.|d)?|concluded)\)|\b(?:cont(?:inued|\.)|concluded)\b", re.I
)


# --------------------------------------------------------------------------------------
# grid signals
# --------------------------------------------------------------------------------------

# A cell marker: a bare X, an X carrying a footnote letter, a parenthesised X, a frequency
# such as 3X or 2X/day, or a common dosing code.
CELL_MARKER_RE = re.compile(
    r"^\(?\s*(?:\d+\s*)?[Xx]\s*\)?\s*[a-zA-Z0-9*†‡§¶]{0,3}\s*$"
    r"|^\s*Q\d+[WDHM]\s*$"
    r"|^\s*[•·●▪◆♦]\s*$",
    re.I,
)

# Column-header signals: visit numbering, study day/week, and windows.
VISIT_HEADER_RES: list[tuple[str, re.Pattern[str]]] = [
    ("visit_number", re.compile(r"\bvisit\s*#?\s*-?\d+", re.I)),
    ("study_day", re.compile(r"\b(?:study\s+)?day\s*-?\s*\d+|\bD-?\d+\b", re.I)),
    ("study_week", re.compile(r"\b(?:study\s+)?w(?:ee)?k\s*-?\s*\d+", re.I)),
    ("visit_window", re.compile(r"[±+]\s*/?\s*-?\s*\d+\s*(?:day|d|week|wk|hour|h)\b", re.I)),
    (
        "study_period",
        re.compile(
            r"\b(?:screening|baseline|randomi[sz]ation|treatment|titration|"
            r"end\s+of\s+treatment|eot|follow-?up|washout|run-?in|"
            r"early\s+termination|discharge|detoxification|maintenance)\b",
            re.I,
        ),
    ),
]

# Row-label signals: the vocabulary of clinical assessments.
ASSESSMENT_VOCAB_RE = re.compile(
    r"\b(?:informed\s+consent|inclusion|exclusion|eligibilit|demograph|medical\s+history|"
    r"physical\s+exam|vital\s+signs?|weight|height|ecg|electrocardiogram|"
    r"laborator|h[ae]matolog|chemistr|urinalysis|urine\s+drug|pregnancy\s+test|"
    r"adverse\s+event|concomitant\s+medication|prior\s+medication|"
    r"pharmacokinetic|pk\s+sampl|blood\s+sampl|plasma|serum|"
    r"randomi[sz]|dispens|study\s+drug|study\s+medication|dosing|administration|"
    r"tumor\s+(?:imaging|assessment)|imaging|ct\s+scan|mri|biopsy|"
    r"questionnaire|rating\s+scale|assessment|evaluation|"
    r"drug\s+accountability|compliance|discharge|telephone\s+contact)\b",
    re.I,
)

# Category header rows: structure, not assessments.
CATEGORY_ROW_RE = re.compile(
    r"^\s*(?:safety|efficacy|screening|baseline|laboratory|clinical\s+laborator|"
    r"pharmacokinetic|primary\s+outcome|secondary\s+outcome|exploratory|"
    r"study\s+procedures?|general|administrative|abuse\s+potential|other)\b[^.]{0,60}:?\s*$",
    re.I,
)


def is_cell_marker(text: str) -> bool:
    """True for the kind of short token that occupies an SoA data cell."""
    t = (text or "").strip()
    return bool(t) and len(t) <= 8 and bool(CELL_MARKER_RE.match(t))
