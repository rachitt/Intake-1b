# Verification — protocol1.pdf

**Study:** Eli Lilly, Xanomeline TTS in mild to moderate Alzheimer's disease, H2Q-MC-LZZT(c)
**Pages:** 97
**Output:** `outputs/protocol1.soa.json`

Checked by reading pages 53–54 of the PDF and comparing cell by cell against the JSON.

Ground truth for this protocol is not a matter of reading: pages 53 and 54 draw a fully
ruled lattice, so the printed row and column boundaries were recovered from the page's own
vector graphics and every cell read out of them independently of both engines. The tables
below are that lattice.

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

## Columns — 15 / 15, including a blank column and excluding a stub

The lattice on page 53 has **ten** columns and on page 54 **nine**. Two of those are not
visit columns:

```
page 53:  [ ACTIVITY ][ VISIT ][ 1 ][ 2 ][ 3 ][ 4 ][ 5 ][   ][ 7 ][ 8 ]
                      [ WEEK  ][-2 ][-.3][ 0 ][ 2 ][ 4 ][   ][ 6 ][ 8 ]
page 54:  [ ACTIVITY ][ VISIT ][ 9 ][10 ][11 ][12 ][13 ][ET ][RT ]
                      [ WEEK  ][12 ][16 ][20 ][24 ][26 ][   ][   ]
```

Which gives **15 leaf columns**, and the extraction produces exactly those. Three things
here are worth calling out, because each is a different failure if handled naively:

- **The `VISIT` / `WEEK` box is not a visit column.** It is the header's own caption box,
  drawn inside the row-header area. Reading it as a leaf column puts a narrow, permanently
  empty column between the assessment labels and Visit 1 — which is what an earlier version
  of this tool did. It is excluded because it carries no data anywhere in the table *and*
  every header cell in it is an axis caption.
- **The unlabelled column between Week 4 and Week 6 is real.** The page rules it and prints
  nothing in it: no visit number, no week, no marks. It is kept, with empty header text and
  `printed_blank: true`. Dropping it — which both engines do, because there is nothing to
  see — shifts Visits 7 through RT one place left, and the extraction then says Visit 7 is
  the sixth column of the study when the page says it is the seventh. Nothing is inferred
  about it: **no Visit 6 is invented**, and the sequence still reads 1, 2, 3, 4, 5, —, 7, 8.
- **The continuation page carries a completely different visit range.** No visit appears on
  both pages. The two ranges merge into one 15-column table rather than becoming two
  disconnected fragments.

## Rows — 28 / 28

The lattice has **28 body rows** per page (the same 28 reprinted on the continuation page),
and the extraction produces 28.

The row that makes this non-trivial is the one the page draws as:

```
┌──────────────────────────┬───┬───┬───┬───┬───┬───┬───┬───┐
│ Study drug record        │   │   │ X │ X │ X │   │ X │ X │
│ Medications dispensed    │   │   │   │   │   │   │   │   │
│ Medications returned     │   │   │   │   │   │   │   │   │
└──────────────────────────┴───┴───┴───┴───┴───┴───┴───┴───┘
```

— **one** ruled row, 39.7pt tall where a single-line row is 13.8pt, with one set of marks
covering all three activities. Read as prose those are three separate activities and a
model will return three rows; that reading invents two rows carrying no data and detaches
the X marks from two of the three. It is kept as the one row the page draws:

```json
{ "label": "Study drug record\nMedications dispensed\nMedications returned",
  "label_lines": ["Study drug record", "Medications dispensed", "Medications returned"],
  "merged_label": true }
```

The distinction is between a merged cell and a wrapped one, and both appear on this page.
`CT Scan (if not within last year and patient passes all other screens)` is also printed
over three lines in a 39.8pt row — but it is one sentence wrapped, so it stays one label
with one `label_lines` entry and `merged_label: false`. The rule that separates them is
not stylistic: the source draws no rule between the lines in either case, so both are one
row; whether the lines are one activity or three is what `label_lines` records.

## Cell values — correct, verbatim

Every cell was compared against the lattice read directly from the page. Distinct values in
the source: `X`, `Xa`, `Xb`, `P`. All four appear verbatim in the output. Spot-checked:

| row | column | source | output |
|---|---|---|---|
| Hemoglobin A1C | visit 1 | `Xa` | `Xa` |
| NPI-X | visit 8 | `Xb` | `Xb` |
| ADAS-Cog | visit 1 | `P` | `P` |
| NPI-X | visits 9, 10, 11 | `Xb Xb Xb` | `Xb` in each of three columns |
| Study drug record … returned | visits 3, 4, 5, 7, 8, 9–13, ET | `X` ×11 | `X` ×11, all on the one merged row |
| every row | the blank column | nothing printed | no cell emitted |

The `P` values matter: they mean "practice only" per the legend, and reducing them to a
boolean would have destroyed that. They survived.

This was not spot-checked but scored in full. `python bench/check_grid.py --protocol
protocol1` compares every cell of the output against the lattice read from the pages:

```
schedule                    printed   exact  missing  spurious  differs
protocol1 soa-1                 139     139        0         0        0
```

**139 printed cells, 139 extracted, every one in the box the page draws it in and every
value character for character.** Nothing missing, nothing invented, nothing altered.

## Footnotes — 4 of 5, all four linked

The source prints a footnote block on **both** pages 53 and 54, with page 54's
`Abbreviations` line extended to define `ET` and `RT`. Five distinct entries exist across
the two, and the committed run returns four of them:

| marker | linked | note |
|---|---|---|
| `X` | 131 anchors | this is a *legend*, not a footnote — see QUESTIONS.md #6 |
| `Xa` | Hemoglobin A1C × visit 1 | correct |
| `Xb` | 4 anchors | correct |
| `P` | ADAS-Cog, CIBIC+, DAD, NPI-X at visit 1 | correct |
| `Abbreviations:` | **not returned** | the abbreviations legend, which anchors to nothing |

The four that carry cell-level meaning are all captured with full text and all linked. The
missing one is the `Abbreviations: CT = computed tomography; ECG = electrocardiogram; ET =
Early Termination; RT = Retrieval` line — a legend rather than a footnote, and one that
earlier runs did return, unlinked. Which of the two behaviours is right is question 6 in
`QUESTIONS.md`; either way nothing anchored to the grid was lost.

## Reconciliation behaviour

Both engines now read the same printed lattice, so the recall diff on this protocol is a
real comparison rather than a comparison with a text-clustering artefact: the geometric
engine reads 28 rows and 8 columns on page 53 and 7 on page 54, matching the vision
engine's reading of the same pages.

Two earlier bugs are recorded here because they illustrate the risk of the recall-biased
design, where a second engine that *over*-reads pollutes the output rather than protecting
it. The geometric engine used to read running headers and footers (`Clinical Study
Protocol`) as table rows, four of which were promoted into the output as rows vision had
"missed"; and it used to count the two pages' reprinted rows separately, reporting a 28-row
table as 56 rows. Both are fixed.

## Summary

| aspect | result |
|---|---|
| schedule located | correct |
| columns | **15 / 15** — the blank printed column kept, the `VISIT`/`WEEK` stub excluded, the disjoint continuation range merged |
| rows | **28 / 28** — the three-activity merged cell kept as one printed row |
| cell values verbatim | correct, all four distinct values preserved, all aligned to the printed grid |
| footnotes captured | 4 / 5 — the four cell-level ones, with full text |
| footnote linkage | **4 / 4**; the missing fifth is the `Abbreviations:` legend line |
