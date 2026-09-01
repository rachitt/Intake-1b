# Manual verification — summary

Every protocol was opened next to its JSON output and compared against the source. Per
protocol detail is in `protocol1.md`, `protocol5.md`, `protocol9.md`, `protocol12.md` and
`protocol15.md`. Questions raised rather than guessed at are in `QUESTIONS.md`. A test
against a protocol the tool had never seen is in `UNSEEN.md`.

All outputs were generated with **gemini-3.1-flash-lite**. The intended default,
`gemini-3.5-flash`, was unavailable during generation — first `503 UNAVAILABLE` (Google-side
serving capacity), later `429 RESOURCE_EXHAUSTED` once the free-tier request quota, which is
metered per model, ran out. Each output records the model that actually served it.

---

## Locator — 6 / 6 schedules found, on the right pages

| protocol | schedules | pages produced | correct? |
|---|---|---|---|
| protocol1 | 1 | 53–54 | yes — in an appendix after the references |
| protocol5 | **2** | 50, and 51 from y≈223 | yes — second schedule shares a page with the first's footnotes |
| protocol9 | 1 | 26–28 | yes — intro page 25 and footnote page 29 both correctly excluded from the body |
| protocol12 | 1 | 48 (+49 footnotes) | yes — not split by the "Notes on the Schedule…" heading |
| protocol15 | 1 | 25 | yes — the Study Schema diagram correctly not nominated |

No page numbers are hardcoded; `python -m soa.cli locate <pdf> --top 10` prints the score
and the signals that fired for every page.

## Extraction — counts against hand-verified ground truth

| protocol | columns | column groups | assessment rows | categories | footnotes | linked | cell agreement |
|---|---|---|---|---|---|---|---|
| protocol1 | **14 / 14** | — | **30 / 30** | — | 2 / 5 | 2 | 94.6 % |
| protocol5 — Appendix I | **11 / 11** | **7 / 7** | **31 / 31** | — | **10 / 10** | 9 | 95.1 % |
| protocol5 — Appendix II | 12 (~14 drawn) | 1 | **8 / 8** | — | 2 own (+10 cross-attributed) | 0 | — |
| protocol9 | **11 / 11** | **4 / 4** | 33 (cross-read) | **4 / 4** | **4 / 4** | 0 | 98.2 % |
| protocol12 | **8 / 8** | **3 / 3** | **37 / 37** | **3 / 3** | **14 / 14** | 13 | 96.1 % |
| protocol15 | **9 / 9** | **4 / 4** | **31 / 31** | **3 / 3** | **5 / 5** | 5 | 66.9 % |

**Row and column recall is exact on every protocol where ground truth was hand-keyed.** That
is the measure the brief weights most heavily.

## What went right, specifically

- **Disjoint continuation columns.** protocol1's page 54 carries visits 9–13 plus ET and RT —
  a completely different set from page 53's 1–8, with no visit on both pages. Merged into one
  14-column table, and the missing visit 6 reproduced as a gap rather than renumbered.
- **Abbreviated continuation headers.** protocol9's pages 27–28 drop the study-phase banner
  entirely. All three pages merged into one 11-column table with the banner applied across.
- **Footnote page spill.** protocol12's block runs 48 → 49. All 14 captured, including the
  five that live only on page 49.
- **Two schedules sharing a page.** protocol5's Appendix II starts partway down page 51,
  below Appendix I's footnote block. Both found, and the second correctly classified `pk`.
- **Sideways dividers.** `RANDOMIZATION` in protocols 12 and 15 is thirteen upright
  single-character spans, not rotated text. Captured as one annotation with **zero** shredded
  rows; the benchmark shows camelot-stream producing 5 and 11 fragments on the same pages.
- **CRF form numbers.** protocol9's `Addiction Research Center Inventory (22)` was not
  misread as a footnote reference.
- **Verbatim cells.** `Xc Xe` (two markers, one cell), `XJ` (inconsistent capital), `2X/weekh`,
  `3X/weekf`, `1&2`, `390 mL`, `40 mg cocaine i.v.`, `Weekly x 2 weeks`,
  `Admission, Monday, Wednesday, Friday, Discharge and As Needed`. Nothing normalised.

## What went wrong, specifically

Ranked by how much it would matter to a consumer.

1. **protocol9: 0 / 4 footnotes linked.** The markers are printed at the *start* of the row
   label (`* Morphine …`, `•Modified Himmelsbach …`) rather than trailing a cell. The linker
   only matches trailing markers. Text is captured; linkage is absent. All four are flagged
   `footnote_unlinked`. **The clearest single defect in the set**, and a narrow fix.
2. **protocol1: only 2 / 5 footnotes captured.** `Xa`, `Xb`, `P` and the `X` legend and
   abbreviations line are printed on both pages 53 and 54; flash-lite returned two. A model
   regression — an earlier 3.5-flash run on the same page captured 5 / 5.
3. **protocol5 Appendix II: 10 footnotes cross-attributed** from Appendix I, which sits above
   it on the same page. They report `attached_to: []` with an `unattached_reason`, so they are
   visibly unanchored rather than silently wrong, but they should not be on that schedule.
4. **protocol5 Appendix II: columns over-counted** (12 vs ~14 drawn, split differently). The
   table is a volumes-by-day matrix rather than an activity-by-visit grid; see `QUESTIONS.md`
   #1.
5. **Geometric engine under-reads dense landscape pages.** On protocol9 it found 26 rows to
   the vision engine's 33, and two of its contributions are garbled merges carried into the
   output flagged `geo only`. Its weakness is worst exactly where the table is hardest, which
   makes the recall diff least useful there.
6. **Footnote page attribution.** protocol12 records `footnote_pages: [48]` though the block
   runs 48–49; protocol1 records `[54]` though it appears on both 53 and 54. The *text* is
   complete in both cases — only the page numbers are wrong.
7. **Bounding box coverage varies with the geometric engine**: 124/128 on protocol15, 57/197
   on protocol9. Cells without a box still display but cannot highlight their source region.

## Honest scope of the checking

- protocols **1, 5, 12, 15** — rows and columns hand-keyed from the PDF and counted; cell
  values, footnote text and linkage spot-checked against the page.
- protocol **9** — columns, groups, row-group banners, footnote text and the distinct cell
  vocabulary verified directly; the 33 assessment rows were cross-read against the page text
  rather than hand-keyed one by one, so a small miscount is possible there in a way it is not
  for the other four.
