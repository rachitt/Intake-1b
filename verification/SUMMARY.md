# Manual verification — summary

Every protocol was opened next to its JSON output and compared against the source. Per
protocol detail is in `protocol1.md`, `protocol5.md`, `protocol9.md`, `protocol12.md` and
`protocol15.md`. Questions raised rather than guessed at are in `QUESTIONS.md`. A test
against a protocol the tool had never seen is in `UNSEEN.md`.

All outputs were generated with **gemini-3.5-flash**. Each output records the model that
actually served it in `run.vision_model`.

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

| protocol | columns | column groups | assessment rows | categories | footnotes | linked |
|---|---|---|---|---|---|---|
| protocol1 | **14 / 14** | — | **30 / 30** | — | **5 / 5** | 4 |
| protocol5 — Appendix I | **11 / 11** | **7 / 7** | **31 / 31** | — | **10 / 10** | **10** |
| protocol5 — Appendix II | 15 (~14 drawn) | 1 | 9 / 8 | — | **2 / 2** | **2** |
| protocol9 | **11 / 11** | **4 / 4** | 35 / 33 | **4 / 4** | 7 / 4 | **4 / 4 real** |
| protocol12 | **8 / 8** | **3 / 3** | **37 / 37** | **3 / 3** | **14 / 14** | 13 |
| protocol15 | **9 / 9** | **4 / 4** | **31 / 31** | **3 / 3** | **5 / 5** | **5** |

**Row and column recall is exact on every protocol where ground truth was hand-keyed** —
the measure the brief weights most heavily. Four of the six schedules are exact on every
axis measured.

### What changed from the first pass

Three defects found during verification were fixed and the outputs regenerated:

| | before | after |
|---|---|---|
| protocol9 footnote linkage | 0 / 4 | **4 / 4** |
| protocol1 rows | 28 / 30 | **30 / 30** |
| protocol1 footnotes | 2 / 5 | **5 / 5** |
| protocol5 Appendix II | 12 footnotes, 10 borrowed from its neighbour, 0 linked | **2 footnotes, both its own, both linked** |

The fixes were: recognise footnote markers printed at the *start* of a row label (protocol9
leads with `*`, `**`, `***` and a bullet instead of trailing them); run marker detection
independently of the model's own marker list rather than only when that list is empty (the
model reported CRF form numbers as markers, which suppressed our detection entirely); tell
the vision engine when a schedule begins partway down a page it shares with another; and
drop footnotes that anchor into a neighbouring schedule rather than this one.

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

## What is still wrong, specifically

Ranked by how much it would matter to a consumer. The four defects listed in the previous
pass were fixed; these are what remains.

1. **protocol9 over-extracts footnotes: 7 returned where 4 exist.** The three extras are
   abbreviation and legend lines sitting in the same block -- `Detox = Detoxification`,
   `SCID: The Structured Clinical Interview for DSM IV (Axis I)`, `time range for collection
   = 0 to ±30 min for all items`. All three anchor to nothing and carry an
   `unattached_reason`, so they are visibly not cell-level notes, but they are not really
   footnotes either. All **4 real footnotes are captured and linked**.
2. **protocol9 over-extracts rows: 35 against 33.** Two are geometric-engine contributions
   carried in by the recall diff and flagged `geo only`; both are visibly garbled merges.
   The trade is deliberate -- an extra flagged row rather than a dropped assessment -- but it
   costs precision on this protocol.
3. **protocol5 Appendix II: columns over-counted** (15 against roughly 14 drawn) and 9 rows
   against 8. This table is a volumes-by-sample-type matrix rather than an activity-by-visit
   grid, and the schema fits it poorly; see `QUESTIONS.md` #1.
4. **Geometric engine under-reads dense landscape pages.** On protocol9 it finds far fewer
   rows than the vision engine, so its half of the recall diff is weakest exactly where the
   table is hardest, and bounding-box coverage suffers with it.
5. **Footnote page attribution.** protocol12 records `footnote_pages: [48]` though the block
   runs 48–49. The *text* is complete, including everything on page 49 -- only the page
   numbers are wrong.
6. **protocol1: 4 of 5 footnotes linked.** The unlinked one is the `Abbreviations:` line,
   which is a legend rather than a cell-level note and correctly anchors to nothing.

## Honest scope of the checking

- protocols **1, 5, 12, 15** — rows and columns hand-keyed from the PDF and counted; cell
  values, footnote text and linkage spot-checked against the page.
- protocol **9** — columns, groups, row-group banners, footnote text and the distinct cell
  vocabulary verified directly; its assessment rows were cross-read against the page text
  rather than hand-keyed one by one, so a small miscount is possible there in a way it is not
  for the other four.
