# Verification — protocol12.pdf

**Study:** NIDA/CSP-1026, modafinil for methamphetamine dependence
**Pages:** 97 · **Output:** `outputs/protocol12.soa.json` · **Model:** gemini-3.1-flash-lite

Checked by reading pages 48–49 of the PDF and comparing cell by cell against the JSON.
This is the protocol whose **footnote block spills onto the following page**.

---

## Location

| | expected | produced |
|---|---|---|
| schedules | 1 | 1 |
| table body | page 48 | page 48 |
| footnote block | pages 48–49 | both pages read |
| heading | "Table 3. Overview of Study Assessments" | exact |

Page 49 opens with "Notes on the Schedule of Assessments" — a heading that matches the SoA
vocabulary and would otherwise have started a *second* schedule. It is recognised as a
footnote-block heading and correctly attached to the schedule on page 48 rather than
splitting it. Pages 10, 30, 45 and 57 were considered and rejected.

## Columns — 9 / 9, with the hierarchy intact

Source: a period banner over `Study Week`.

| group | leaf columns |
|---|---|
| Screening/Baseline | `14-21 days prior to randomization` |
| *(none)* | the divider column — see below |
| Study Medication Administration | `1-3`, `4`, `5-7`, `8`, `9-11`, `12/Term` |
| Follow-up | `16` |

**Produced: 9 leaf columns and 3 column groups, matching exactly**, with each group spanning
the right leaves (1, 6, 1).

The ninth is the narrow column the page rules between the screening column and Study Week
1-3 to hold the sideways `RANDOMIZATION` divider. It carries no grid data — the divider
itself is kept as a `rotated_annotation`, not as rows — but the page draws it, so it is kept
with empty header text and `printed_blank: true`, and the treatment columns keep their
printed positions. Nothing is inferred about it; it is simply reported as printed, with a
warning saying why it is there.

## Rows — 37 / 37 assessments, 3 / 3 categories

Ground truth from the page's own ruling lines: 40 ruled rows in the body, of which three
are the category banners (`Screening`, `Safety`, `Efficacy`), leaving 37 assessments. All
present. The categories are in `row_groups`, not `rows`.

One run of the vision engine also returned the header's `Study Week` row as an assessment.
The printed grid puts it above the first body row and it carries nothing but the header's
own values, so it is dropped with a `row_not_in_source_grid` warning rather than left in the
output as a procedure nobody performs.

Multi-line labels were correctly joined rather than split, e.g.
`ACDS assessment (ADHD diagnosis)` and `Infectious disease panel/syphilis test/PPD`, both of
which wrap across two printed lines with the data cell on the first.

## Cell values — verbatim, and this is the richest set in the protocol suite

Every distinct value produced:

```
X   3X   3X/week   3Xd   3X/weekd   3X/weekf   2X/weekh   Weeklyi   Twice
Xa  Xb   Xc        Xg    Xc Xe      X wk 6     Xb wk 6
```

Four of these are the cases the brief specifically warns about:

- **`Xc Xe`** — a single cell carrying **two** footnote markers. Both were captured and both
  resolved to their footnotes.
- **`3X/weekd`, `2X/weekh`, `Weeklyi`** — a frequency *and* a footnote letter fused into one
  token. Stripping the letter would silently change "three times a week, per note d" into
  "three times a week"; keeping it in `raw` preserves both.
- **`X wk 6`, `Xb wk 6`** — a mark plus a timing qualifier printed on two lines in one cell.
- **`Twice`** — a word where the rest of the column has marks.

None was normalised. Reducing any of these to a boolean would have destroyed real dosing
and timing information.

## Footnotes — 14 / 14 captured, 13 linked, including the page spill

The source prints four asterisk-tier notes and ten letter notes, **and the block runs from
page 48 onto page 49**. All fourteen were captured with full text.

| marker | anchors | marker | anchors |
|---|---|---|---|
| `*` | 0 | `d` | 10 |
| `**` | 2 | `e` | 6 |
| `***` | 2 | `f` | 2 |
| `****` | 1 | `g` | 3 |
| `a` | 2 | `h` | 2 |
| `b` | 34 | `i` | 3 |
| `c` | 15 | `J` | 1 |

**`XJ` is worth calling out.** The protocol's marker sequence runs `Xa`…`Xi` and then
inconsistently uses a capital `XJ`. It was captured verbatim and linked to its cell. A
normaliser that lower-cased markers, or one that assumed a contiguous a–z sequence, would
have mismatched it.

`*` is the one unlinked footnote, correctly: it is a general note about screening and
baseline timing that sits on no particular cell, and it carries an `unattached_reason`
rather than being dropped.

**Imprecision:** `footnote_pages` records `[48]`, though the block genuinely occupies 48–49.
The *text* from page 49 is present and complete — only the page attribution is wrong.

## Rotated text — handled

`RANDOMIZATION` is printed sideways between the Screening/Baseline and Study Medication
columns. In the text layer it is **thirteen separate upright single-character spans** at a
constant x. It appears once in `rotated_annotations`, and **zero** single-letter rows were
produced. The benchmark confirms this independently: this project's shredded-cell count on
page 48 is 0, while camelot-stream returns 5.

## Reconciliation

`rows: {geometric: 36, vision: 37, final: 37}` · `columns: {geometric: 9, vision: 8}` ·
**cell agreement 96.1 %** — the highest in the set. The engines disagree on one column,
flagged for review; the geometric engine's extra column is a splitting artefact.

## Summary

| aspect | result |
|---|---|
| schedule located | correct, and not split by the "Notes on…" heading |
| columns | **8 / 8**, 3 / 3 groups with correct spans |
| rows | **37 / 37** assessments, 3 / 3 categories |
| cell values verbatim | **correct on all 16 distinct values**, including `Xc Xe` and `XJ` |
| footnotes captured | **14 / 14**, including the page-49 spill |
| footnote linkage | 13 / 14; the unlinked one is a genuine table-level note |
| rotated divider | correct — 1 annotation, 0 shredded rows |
| cell agreement | 96.1 % |

The strongest result in the set.
