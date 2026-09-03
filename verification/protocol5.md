# Verification — protocol5.pdf

**Study:** NIDA-CPU-Atomoxetine-0001, intravenous cocaine × atomoxetine interaction
**Pages:** 61
**Output:** `outputs/protocol5.soa.json`

Checked by reading pages 50–51 of the PDF and comparing cell by cell against the JSON.
This is the protocol with **two** schedules, and the hardest locator case in the set.

---

## Location — the hardest case, handled correctly

Page 50 carries Appendix I. Page 51 carries **two things**: Appendix I's entire footnote
block in its upper half, and the whole of Appendix II below it, starting at y ≈ 223.

| | expected | produced |
|---|---|---|
| schedules | 2 | 2 |
| schedule 1 | page 50, footnotes on 51 | page 50, footnote pages 50–51 |
| schedule 2 | page 51, lower portion | page 51 with `starts_at_y: 223` |
| schedule 2 kind | a blood-sampling schedule | `pk` |

Both headings are set with letter-spacing, so the text layer yields
`Appendi x I: Tim e and Event s Schedul e`. They were matched by testing a whitespace-
stripped copy of the line, and repaired for display to `Appendix I: Time and Events
Schedule` and `APPENDIX II: Schedule of Blood Collections`.

The document also has a broken-font warning: several embedded fonts have no ToUnicode CMap,
so text-layer characters may be silently wrong. This is surfaced in
`document.text_layer_warnings`, and the vision engine was told the text layer is
untrustworthy for this document.

---

## Schedule 1 — Appendix I, Time and Events Schedule

### Columns — 11 / 11, and 7 / 7 period groups

Source header: `Study day: Up to -35 | -15* to -9 | -6 | -2 | -1 | 7 | 8 | 12 | 13 | 17 | 31`
→ **11 columns produced, exact match.**

Source period banner: `Pre-intake Screening | Intake Screening | Screening Infusions |
Baseline Infusions | Treatment Infusions | Discharge | Follow-up`
→ **7 column groups produced, exact match**, each spanning the right leaf columns.

### Rows — 31 / 31

Every assessment row recovered, including the ones most likely to be dropped:

- dosing rows interleaved with assessments (`Saline/20 mg cocaine/40 mg cocaine i.v.`,
  `20 mg cocaine i.v.`, `40 mg cocaine i.v.`)
- `Cognitive assessments****`, which has **no data cells at all** — an empty row is still a
  row, and it survived
- `Cocaine Infusion Session #`, whose cells are session numbers rather than marks

### Cell values — verbatim, including the awkward ones

Distinct values in the source: `X`, `Xa`, `Xb`, `Xc`, `Xd`, `Xe`, `Xf`, `1&2`, `3`, `4`,
`5`, `6`, `7`, `8`. **All fourteen appear verbatim in the output.**

`1&2` is the one to check: it means infusion sessions 1 and 2 both occur at that timepoint.
Any normalisation to a boolean or an integer destroys it. It came through intact.

### Footnotes — 10 / 10, all linked

Two marker systems coexist and both were handled:

| markers | style | count | linked |
|---|---|---|---|
| `*`, `**`, `***`, `****` | asterisk tier | 4 | 4 / 4 |
| `Xa` … `Xf` | letter suffix baked into the cell value | 6 | 6 / 6 |

The letter markers are printed in the list as `Xa - POMS, BSCS will be performed…` while
sitting on cells as `Xa`. Both spellings resolved to the same footnote.

### Summary — schedule 1

| aspect | result |
|---|---|
| columns | **11 / 11** |
| column groups | **7 / 7** |
| rows | **31 / 31** |
| cell values verbatim | correct, all 14 distinct values |
| footnotes | **10 / 10**, all correctly linked |

This is the cleanest extraction in the set.

---

## Schedule 2 — Appendix II, Schedule of Blood Collections

### Rows — 8 / 8

`Chemistries plus liver function tests`, `Hematology`, `Infectious disease serology`,
`PK Samples for cocaine`, `PK Samples for Atomoxetine`, `Pregnancy Test`, `Alcohol Test`,
`Total`. All present.

### Columns — 15 / 15

The ruled lattice on page 51 draws fifteen columns beside the row labels: `Volume Per
Sample`, `Type`, then the twelve study days (`Screening`, `D-8`, `D-1`, `D1`, `D2`, `D3`,
`D6`, `D8`, `D11`, `D13`, `D17`, `D31`), then `Total Volume`. **All fifteen produced**, and
the geometric and vision engines now agree on all of them.

An earlier pass recorded this as over-counted at 15 against "roughly 14 drawn". That was a
miscount on my part, not the tool's: reading the boundaries out of the page's own vector
graphics settles it at 15. What remains true is the point behind that note — the first two
columns are row *attributes* (how much blood, serum or plasma) rather than visits, so the
activity-by-visit schema fits this table awkwardly even when it reproduces it exactly. See
`QUESTIONS.md` #1.

### Cell values — verbatim

`10 mL`, `5 mL`, `S`, `P`, `1`, `2`, `15`, and the volume totals `20 mL`, `30 mL`, `40 mL`,
`60 mL`, `225 mL`, `390 mL`. All present verbatim. The running total row (`390 mL`) survived,
which matters because it is the study's total blood draw.

### Footnotes — cross-attributed

Appendix II's own footnotes are `aS = serum, P = plasma` and `bD = day` — two entries.

The output carries exactly those **2**, both linked. An earlier pass carried 12 here,
because Appendix I's block sits on the same page above it and was attributed to both
schedules; footnotes that anchor into a neighbouring schedule and nothing in this one are
now dropped from the borrower, and the vision engine is told when a schedule begins partway
down a shared page.

### Summary — schedule 2

| aspect | result |
|---|---|
| located | correct, including starting partway down a shared page |
| classified | correct (`pk`) |
| rows | **8 / 8** |
| columns | **15 / 15** |
| cell values verbatim | correct, volumes and totals preserved |
| footnotes | **2 / 2**, both linked, none borrowed from Appendix I |
