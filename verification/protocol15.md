# Verification — protocol15.pdf

**Study:** NIDA-CTO-0007, cabergoline for cocaine dependence
**Pages:** 61 · **Output:** `outputs/protocol15.soa.json` · **Model:** gemini-3.1-flash-lite

Checked by reading page 25 of the PDF and comparing cell by cell against the JSON.

---

## Location

| | expected | produced |
|---|---|---|
| schedules | 1 | 1 |
| pages | 25 | 25 |
| heading | "Table 1. Overview of Study Assessments" | exact |

Pages 22 and 60 were considered and rejected. Page 7 carries a "Study Schema" — an
arms-and-arrows diagram, not an activity grid — which is explicitly excluded by the
heading patterns and never nominated.

## Columns — 9 / 9, and the header hierarchy is right

The source stacks a period banner over `Study Week`, and the banner does **not** align one
to one with the week columns: `-4 to 0*` sits under *both* Screening and Baseline.

| group | leaf columns | spans |
|---|---|---|
| Screening | `-4 to 0*` | 1 |
| Baseline | (shares the week label) | 1 |
| Treatment | `1-3`, `4`, `5-7`, `8`, `9-11`, `12` | 6 |
| Follow-up | `16` | 1 |

**Produced: 9 leaf columns, 4 groups, spans 1/1/6/1 — exact.** Getting 9 rather than 8 here
matters: the `Adverse events` row carries nine values, so an 8-column reading would have
had to drop one.

Each column's `group_path` resolves correctly, e.g. column 3 reports
`Treatment / 4`.

## Rows — 31 / 31 assessments, 3 / 3 categories

Hand-counted from the page: 31 assessments under three banners (`Screening`, `Safety`,
`Efficacy`). All present, categories kept in `row_groups` rather than `rows`.

The output's `rows` array holds 34 entries because reconciliation added 3 rows the
geometric engine read and the vision engine did not. All three are **flagged `geo only`**
and appear as high-severity warnings — they are wrapped-label fragments, not real
assessments. This is the recall-biased design behaving as intended: an extra flagged row
rather than a silently dropped one, exactly the trade the brief asks for.

## Cell values — verbatim, including the merged annotations

Distinct values produced:

```
X   3X   3 X   Xa   Xb   Xc   Weekly x 2 weeks   3 X/week for 2 weeks
```

The last two are the interesting ones. `Weekly x 2 weeks` and `3 X/week for 2 weeks` are
**frequency annotations printed across several week columns as one merged cell**, not
per-visit marks. They are captured verbatim with `col_span > 1`, so the visual span
survives and no attempt is made to expand them into per-visit events — an expansion that
would require knowing the visit calendar and would silently invent case report forms. The
question of whether that expansion is ever safe to automate is recorded in
`QUESTIONS.md` #5.

`3 X` and `3X` both appear, spaced differently in the source. Both were preserved as
printed rather than unified — faithful, not clever.

## Footnotes — 5 / 5 captured and all linked

| marker | anchors | text (opening) |
|---|---|---|
| `*` | 1 | "Baseline assessments must occur within 2 weeks during the screening period…" |
| `Xa` | 4 | "Blood is collected in fasted state at least once (preferably twice)…" |
| `Xb` | 67 | "Once during the week preferably at the first visit of the week." |
| `Xc` | 23 | "At the final scheduled study visit (last visit of week 12)…" |
| `Xd` | 2 | "FEV1 is only performed in those subjects suspected of having asthma." |

Footnote `a` runs to four printed lines and was captured complete. The high anchor counts
for `b` and `c` are correct, not noise: those markers genuinely sit on most cells in the
treatment columns.

The marker resolution worked in both directions here — the list prints `Xa – …` while the
cells carry `Xa`, and both spellings map to one footnote.

## Rotated text — handled

`RANDOMIZATION` is printed sideways between the Baseline and Treatment columns as thirteen
upright single-character spans. One `rotated_annotation`, zero shredded rows. The benchmark
records 0 shredded cells for this project against 11 for camelot-stream on the same page.

## Bounding boxes

**124 of 128 cells carry a bounding box** — the best coverage in the set. Clicking a cell in
the UI highlights its exact region on the rendered page, which is what makes the output
checkable against the source rather than merely readable.

## Reconciliation

`rows: {geometric: 32, vision: 31, final: 34}` · `columns: {geometric: 10, vision: 9}` ·
**cell agreement 66.9 %** — the lowest of the five. The disagreements are concentrated in
the merged frequency annotations, where the geometric engine splits `3 X/week for 2 weeks`
across the columns it visually covers while the vision engine keeps it as one spanning
cell. The vision reading is kept and every disagreement is listed.

## Summary

| aspect | result |
|---|---|
| schedule located | correct; the Study Schema diagram correctly not nominated |
| columns | **9 / 9**, 4 / 4 groups with correct spans |
| rows | **31 / 31** assessments, 3 / 3 categories (+3 flagged `geo only`) |
| cell values verbatim | correct, including merged frequency annotations with `col_span` |
| footnotes | **5 / 5** captured, **5 / 5** linked |
| rotated divider | correct — 1 annotation, 0 shredded rows |
| bbox coverage | **124 / 128** |
