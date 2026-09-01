# Verification — protocol9.pdf

**Study:** NIDA, lofexidine for opiate withdrawal
**Pages:** 57 · **Output:** `outputs/protocol9.soa.json` · **Model:** gemini-3.1-flash-lite

Checked against pages 26–28 of the PDF. This is the **three-page landscape** table and the
hardest extraction in the set. Structure, headers, footnotes and cell values were verified
directly; the full row list was cross-read rather than hand-keyed line by line, and that
limit is flagged below.

---

## Location

| | expected | produced |
|---|---|---|
| schedules | 1 | 1 |
| table body | 26–28 | **26, 27, 28** |
| footnote block | 29 | read as a footnote page |
| heading | "Table 4. Schedule of Measures and Data Collection for Lofexidine Phase 3" | recovered |

Two things the locator had to get right and did:

- **Page 25 is the intro page** ("Table 4 is a flow chart for the entire study…"), 41 words,
  2 ruling lines. It is *not* table body and was correctly excluded.
- **Page 29 is the footnote block** ("Footnotes to Flow Chart"), 33 words. It scored 7.2 —
  high enough to be nominated — but was trimmed out of the table body by the grid test and
  kept as a footnote page. Without that trim it would have contributed phantom rows.

Pages 26–28 are true `/Rotate 90` landscape: text stored in unrotated media space with
direction `(0, -1)`. Handled by mapping coordinates and direction vectors through the page
rotation matrix, so the pages read as ordinary horizontal text rather than being flagged
sideways wholesale.

## Columns — 11 / 11, and the phase banner is right

Source header stacks a study-phase banner over `Study Day 1…11`.

| group | leaf columns | spans |
|---|---|---|
| Opiate Agonist Phase (all Morphine) | days 1–3 | 3 |
| Detoxification: Medication or Placebo Phase | days 4–8 | 5 |
| Post Med/Detox Phase (Placebo, QID) | days 9–10 | 2 |
| Medical Discharge | day 11 | 1 |

**Produced: 11 leaf columns, 4 groups, spans 3/5/2/1 — matching the source.**

This is the case where continuation pages matter most. Page 26 prints the full multi-row
header; pages 27 and 28 print only an abbreviated "Table 4, Continued" plus a re-stated
`Study Day` row and **drop the phase banner entirely**. The three pages were merged into one
11-column table rather than three disconnected fragments, and the banner from page 26 is
applied across all of them.

## Row hierarchy — 4 / 4 category banners

`Primary Outcome Measure:`, `Secondary Outcome Measures:`, `Abuse Potential Assessment:`,
`Safety Measures:` — all four recovered as `row_groups`, matching the source exactly. These
carry no data cells and are correctly excluded from `rows`.

33 assessment rows were extracted across the three pages.

## Cell values — verbatim, and this protocol has the longest ones

```
1X   1 X   5X   6X   8X   Prior to Day 4
Admission, Monday, Wednesday, Friday, Discharge and As Needed
```

The long free-text cells are the point here. `Admission, Monday, Wednesday, Friday,
Discharge and As Needed` is a scheduling rule written into a cell; any normalisation to a
mark destroys it entirely. It came through intact.

**CRF form numbers were correctly *not* treated as footnote markers.** Row labels in this
protocol carry trailing parenthesised numbers — `Addiction Research Center Inventory (22)`,
`Plasma Lofexidine Pk (LCMS) (29)` — which look exactly like parenthesised numeric footnote
references. They are case report form identifiers. The linker only accepts a marker that
also appears in the footnote block's own marker set, and `(22)` does not, so none was
misread. This was a deliberate guard, and it held.

## Footnotes — 4 / 4 captured, 0 linked

The block on page 29 uses `*`, `**`, `***` and a bullet glyph. All four were captured with
full text, including the longest:

> `*` "Morphine: study day 1 morphine administration times may vary depending upon…"

**None is linked to the grid, and this is the weakest footnote result in the set.** The
cause is visible in the extracted rows: the markers are printed at the *start of the row
label* (`* Morphine (0600, 1100, 1630, 2200 h)`, `**Lofexidine or Placebo …`,
`•Modified Himmelsbach (MHOWS) …`) rather than as a trailing superscript on a cell. The
linker looks for trailing markers on cells and labels, so leading markers on labels are
missed.

All four record an `unattached_reason` and are surfaced as `footnote_unlinked` warnings, so
they are visibly unanchored rather than silently wrong — but the linkage the brief grades is
absent here. **This is the clearest single defect across all five protocols**, and the fix is
narrow: extend marker matching to the leading position of a row label.

## Reconciliation

`rows: {geometric: 26, vision: 33, final: 35}` · `columns: {geometric: 12, vision: 11}` ·
**cell agreement 98.2 %** — the highest in the set on the cells both engines placed.

The geometric engine read only 26 rows to the vision engine's 33. On these dense landscape
pages its y-clustering merges wrapped labels aggressively, so its half of the recall diff is
weakest exactly where the table is hardest. Two rows it contributed are visibly garbled
merges (`WPbreakfast) rimary Outcome Measureeight (on admission & 0630-0800h (13)`); they
are carried into the output flagged `geo only` rather than dropped, which is the recall-bias
trade working as designed but at a visible precision cost.

**Bounding box coverage is 57 / 197** — the lowest in the set, for the same reason: boxes are
borrowed from the geometric engine, and it matched fewer rows here. Cells without a box
still display; they just cannot highlight their source region in the UI.

## Limits of this check

Columns, groups, row-group banners, footnote text, and the distinct cell-value vocabulary
were verified directly against the source pages. The 33 assessment rows were cross-read
against the page text rather than hand-keyed one by one, so a small miscount is possible
here in a way it is not for protocols 1, 5, 12 and 15.

## Summary

| aspect | result |
|---|---|
| schedule located | correct — 26–28, intro page and footnote page both excluded from the body |
| landscape / rotation | correct |
| continuation headers | correct — merged despite the phase banner being dropped on pages 27–28 |
| columns | **11 / 11**, 4 / 4 groups with correct spans |
| row categories | **4 / 4** |
| rows | 33 assessments (cross-read, not hand-keyed) + 2 flagged `geo only` |
| cell values verbatim | correct, including long free-text scheduling rules |
| CRF numbers vs markers | correct — not misread as footnotes |
| footnotes captured | **4 / 4** with full text |
| footnote linkage | **0 / 4** — markers lead the row label; not matched |
| cell agreement | 98.2 % |
| bbox coverage | 57 / 197 |
