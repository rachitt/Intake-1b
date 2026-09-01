# Verification — protocol1.pdf

**Study:** Eli Lilly, Xanomeline TTS in mild to moderate Alzheimer's disease, H2Q-MC-LZZT(c)
**Pages:** 97
**Output:** `outputs/protocol1.soa.json`

Checked by reading pages 53–54 of the PDF and comparing cell by cell against the JSON.

---

## Location

| | expected | produced | |
|---|---|---|---|
| schedules | 1 | 1 | correct |
| pages | 53–54 | 53–54 | correct |
| heading | "Schedule of Events for Protocol H2Q-MC-LZZT(c)" | same, prefixed with "Protocol Attachment LZZT.1" | correct |

The schedule sits in an appendix *after* the references section, and the heading says
"Schedule of Events" rather than "Schedule of Activities". Both were handled. Three body
pages (8, 30, 35) contain the phrase "(see Schedule of Events, Attachment LZZT.1)" in
running prose; all three were considered and correctly rejected, and appear in the
near-miss list.

## Columns — correct, including the hard part

Ground truth, read from the pages:

- page 53: `VISIT 1 2 3 4 5 7 8` over `WEEK -2 -.3 0 2 4 6 8`
- page 54: `VISIT 9 10 11 12 13 ET RT` over `WEEK 12 16 20 24 26` (ET and RT have no week)

**14 columns produced, matching exactly**, including two things worth calling out:

- **Visit 6 does not exist.** The sequence runs 1, 2, 3, 4, 5, 7, 8. The extraction
  reproduces the gap rather than renumbering.
- **The continuation page carries a completely different visit range.** No visit appears on
  both pages. The two ranges were merged into one 14-column table rather than becoming two
  disconnected fragments — this is the "continuation page with a different set of columns"
  case, and it is the single thing the geometric engine could not do (it saw 8 columns, one
  page's worth). The reconciliation flagged the difference.

## Rows — 28 of 30 recovered

Ground truth is **30 rows**. The extraction returned **28**.

**The two missing rows, and how:** the source prints

```
Study drug record        X X X X X
Medications dispensed
Medications returned
```

as three separate label lines, where only the first carries data cells. The vision engine
merged all three into one row labelled `Study drug record Medications dispensed
Medications returned`. The information is not lost — the label text is all there — but the
row count is wrong, and a consumer building case report forms would create one form where
the protocol implies three.

This is genuinely ambiguous in the source: it may be one activity with two clarifying
sub-lines, or a category header with two sub-rows. It is recorded as question 2-adjacent in
`QUESTIONS.md` context and is the clearest single defect in this protocol's output.

All other 28 rows are present and correctly labelled, including the three-line wrapped label
`CT Scan (if not within last year and patient passes all other screens)`, which was joined
into one row rather than split into three.

## Cell values — correct, verbatim

Distinct values in the source: `X`, `Xa`, `Xb`, `P`. All four appear verbatim in the output.
Spot-checked:

| row | column | source | output |
|---|---|---|---|
| Hemoglobin A1C | visit 8 | `Xa` | `Xa` |
| NPI-X | visit 8 | `Xb` | `Xb` |
| ADAS-Cog | visit 1 | `P` | `P` |
| NPI-X | visits 9, 10, 11 | `Xb Xb Xb` | `Xb` in each of three columns |

The `P` values matter: they mean "practice only" per the legend, and reducing them to a
boolean would have destroyed that. They survived.

## Footnotes — 5 of 5, with one attribution imprecision

The source prints a footnote block on **both** pages 53 and 54, with page 54's `Abbreviations`
line extended to define `ET` and `RT`. Five distinct entries exist across the two:

| marker | linked | note |
|---|---|---|
| `X` | 131 anchors | this is a *legend*, not a footnote — see QUESTIONS.md #6 |
| `Xa` | 1 anchor (Hemoglobin A1C × visit 8) | correct |
| `Xb` | 4 anchors | correct |
| `P` | 4 anchors (ADAS-Cog, CIBIC+, DAD, NPI-X at visit 1) | correct |
| `Abbreviations:` | 0 anchors, empty marker | correctly reported as unlinked |

All five captured with full text. **Imprecision:** every footnote is recorded with
`pages: [54]`, but the block appears on both 53 and 54. The text captured is complete; the
page attribution is not.

## Reconciliation behaviour

`row_counts: {geometric: 45, vision: 28, final: 32}` in the committed run. The geometric
engine's 45 included running headers and footers (`Clinical Study Protocol`, `Xanomeline
(LY246708) H2Q-MC-LZZT(c)`) read as table rows, four of which were then promoted into the
output as rows vision had "missed".

**This was a real bug and has been fixed** — the geometric engine now excludes the running
header and footer bands. It is left described here because it illustrates the risk of the
recall-biased design: a second engine that over-reads pollutes the output rather than
protecting it, so its precision matters more than it first appears.

## Summary

| aspect | result |
|---|---|
| schedule located | correct |
| columns | **14 / 14**, including the visit-6 gap and the disjoint continuation range |
| rows | **28 / 30** — three label lines merged into one row |
| cell values verbatim | correct, all four distinct values preserved |
| footnotes captured | **5 / 5** with full text |
| footnote linkage | correct; page attribution imprecise |
