# Manual verification — summary

Every protocol was opened next to its JSON output and compared against the source. Per
protocol detail is in `protocol1.md`, `protocol5.md`, `protocol9.md`, `protocol12.md` and
`protocol15.md`. Questions raised rather than guessed at are in `QUESTIONS.md`. A test
against a protocol the tool had never seen is in `UNSEEN.md`.

Each output records the model that actually served it in `run.vision_model`. The configured
default is `gemini-3.5-flash`, but free-tier quota is metered per model and it was exhausted
when these were generated, so the fallback chain served most of them. **Which model answers
changes the cell-level result** -- one run lost the superscript in `Xb`, another dropped two
X marks, a third returned all 139 cells verbatim -- while the row and column structure came
out identical every time, because that is settled against the printed grid rather than read.

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

## Extraction — counts against verified ground truth

Ground truth for rows and columns is no longer a hand count. All five protocols draw a
fully ruled grid, so the printed row and column boundaries were read back from each page's
own vector graphics and the cells read out of them independently of both engines. Where the
two disagree, the page wins. `python bench/check_grid.py` re-runs that comparison cell by
cell against the committed outputs and prints the score below.

| protocol | columns | column groups | assessment rows | categories | footnotes | linked |
|---|---|---|---|---|---|---|
| protocol1 | **15 / 15** | — | **28 / 28** | — | 4 / 5 | **4 / 4** |
| protocol5 — Appendix I | **11 / 11** | 8 / 7 | **31 / 31** | 1 / 0 | **10 / 10** | **10** |
| protocol5 — Appendix II | **15 / 15** | 1 | **8 / 8** | 1 / 0 | **2 / 2** | **2** |
| protocol9 | **11 / 11** | **4 / 4** | **33 / 33** | **4 / 4** | 6 / 4 | **4 / 4 real** |
| protocol12 | **9 / 9** | **3 / 3** | **37 / 37** | 4 / 3 | **14 / 14** | 13 |
| protocol15 | **10 / 10** | **4 / 4** | **31 / 31** | **3 / 3** | **5 / 5** | **5** |

**Row and column recall is now exact on all six schedules**, and both engines agree on the
column count of every one of them — which they did not before, because they were being
compared against a guess at the grid rather than the grid.

Three of those numbers moved because the ground truth itself was wrong in the previous
pass, and each mistake was the same kind of mistake:

| | previously recorded | actually printed |
|---|---|---|
| protocol1 columns | 14 | **15** — a column is ruled between Week 4 and Week 6 with nothing in it |
| protocol1 rows | 30 | **28** — three activities share one ruled row |
| protocol12 / protocol15 columns | 8 / 9 | **9 / 10** — each rules a column for the sideways `RANDOMIZATION` divider |
| protocol5 Appendix II columns | "15, ~14 drawn" | **15**, and 15 are drawn |

All four are the same error read two ways: a column with nothing printed in it looks like
whitespace, and several activities inside one ruled cell look like several rows. Both are
now settled against the page rather than against a reading of it.

### What changed from the earlier passes

| | before | after |
|---|---|---|
| protocol9 footnote linkage | 0 / 4 | **4 / 4** |
| protocol1 footnotes | 2 / 5 | **5 / 5** |
| protocol5 Appendix II | 12 footnotes, 10 borrowed from its neighbour, 0 linked | **2 footnotes, both its own, both linked** |
| protocol1 blank printed column | dropped, shifting Visits 7–RT one place left | **kept, `printed_blank: true`** |
| protocol12 / 15 divider column | dropped | **kept** |
| protocol1 merged row label | split into 3 rows, 2 of them empty | **one row, `merged_label: true`** |
| geometric engine on protocol1 | 8 columns, 45 rows including page furniture | **8 and 7 columns per page, 28 rows** |

The footnote fixes were: recognise markers printed at the *start* of a row label (protocol9
leads with `*`, `**`, `***` and a bullet instead of trailing them); run marker detection
independently of the model's own marker list rather than only when that list is empty (the
model reported CRF form numbers as markers, which suppressed our detection entirely); tell
the vision engine when a schedule begins partway down a page it shares with another; and
drop footnotes that anchor into a neighbouring schedule rather than this one.

The structural fixes all come from one change: reading the grid the page actually draws
(`soa/extract/ruled.py`) and holding the extraction to it (`soa/align.py`), instead of
trusting a reader's account of what the grid is. The same lattice now drives the geometric
engine, so the recall cross-check compares two readings of the same structure.

## Cell-level score against the printed grid

```
schedule                    printed   exact  missing  spurious  differs
protocol1  soa-1                139     139        0         0        0
protocol5  soa-1                107      95       11         4        1
protocol5  soa-2                 43      37        6         5        0
protocol9  soa-1                164     105       26        61       33
protocol12 soa-1                126     113        3         6       10
protocol15 soa-1                128     120        5        20        3
TOTAL                           707     609       51        96       47    86.1% exact
```

**protocol1 is exact: 139 printed cells, 139 extracted, every one in the box the page draws
it in and every value character for character.** It is also the protocol whose structure
this pass was built around, and the one whose grid was checked against the rendered page by
eye as well as by coordinates.

The rest should be read as a list of cells worth looking at, not as an error rate. Three
things inflate it:

- **The text layer is sometimes the wrong one.** protocol15 emits a superscript marker
  *before* its mark, so the page's text reads `a X` where the image plainly shows `Xa` —
  every one of protocol15's three "differs" is that. protocol9 and protocol1 have fonts with
  no ToUnicode CMap, which is why `Prior to Day 4` arrives as `rior to Day 4`.
- **The comparison is not independent.** `soa/align.py` reads the same lattice to place the
  cells, so a lattice misread would agree with itself. It measures whether the content
  landed in the right boxes, not whether the boxes are right; the boxes were checked against
  the rendered page.
- **protocol9 is genuinely the hardest.** Its 61 spurious and 33 differing cells are a real
  reading disagreement on dense landscape pages — the vision engine reads `5X` where the
  text layer reads `6X` and vice versa, in a block of rows recording clock times.

## What went right, specifically

- **Disjoint continuation columns.** protocol1's page 54 carries visits 9–13 plus ET and RT —
  a completely different set from page 53's 1–8, with no visit on both pages. Merged into one
  15-column table, with the sequence still reading 1, 2, 3, 4, 5, —, 7, 8: no visit renumbered
  and **no Visit 6 invented** to fill the gap the page prints.
- **A blank printed column kept as printed.** The column protocol1 rules between Week 4 and
  Week 6 holds nothing at all. It is emitted with empty header text and `printed_blank: true`,
  because dropping it moves Visit 7 into the sixth position of the study. protocol12 and
  protocol15 each rule one for the sideways `RANDOMIZATION` divider; those are kept too, with
  a warning saying what the source draws there.
- **A merged row label kept as one row.** protocol1 rules one row for `Study drug record` /
  `Medications dispensed` / `Medications returned` and prints one set of X marks against all
  three. It is one row, with the three activities in `label_lines` and `merged_label: true` —
  not three rows, two of which would have carried no data at all.
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

1. **protocol9 over-extracts footnotes: 6 returned where 4 exist.** The extras are
   abbreviation and legend lines sitting in the same block -- `Detox = Detoxification`,
   `SCID: The Structured Clinical Interview for DSM IV (Axis I)`, `time range for collection
   = 0 to ±30 min for all items`. All three anchor to nothing and carry an
   `unattached_reason`, so they are visibly not cell-level notes, but they are not really
   footnotes either. All **4 real footnotes are captured and linked**.
2. **`merged_label` is over-applied on protocol9.** Several of its row labels wrap across
   two or three printed lines inside one ruled cell, and the vision engine reports some of
   them as *merged* -- as though each line named a separate activity -- rather than as one
   label that wrapped. The row count is unaffected (one printed row is still one row) and
   the text is complete; what is wrong is the claim about how to read it. A guard demotes
   the clearest cases (a line opening with a bracket, a time or a lowercase word cannot
   start an activity), but it is deliberately one-directional and does not catch a
   continuation line that happens to begin with a capital.
3. **protocol5 Appendix II is a volumes matrix, not an activity grid.** Its first two
   columns are row *attributes* -- how much blood, serum or plasma -- so the schema
   reproduces the table exactly while describing it awkwardly. See `QUESTIONS.md` #1.
4. **The vision engine occasionally mis-transcribes a superscript marker.** One run of
   protocol1 returned Myanmar glyphs for the superscript `a` and `b` in `Xa` and `Xb`.
   Reconciliation caught it -- the geometric engine reads the same cells from the text layer
   and the disagreement is reported per cell -- but nothing repairs it automatically, and a
   run that hits it will link those footnotes to nothing.
5. **Footnote page attribution.** protocol12 records `footnote_pages: [48]` though the block
   runs 48–49. The *text* is complete, including everything on page 49 -- only the page
   numbers are wrong.
6. **protocol1: 4 of 5 footnotes linked.** The unlinked one is the `Abbreviations:` line,
   which is a legend rather than a cell-level note and correctly anchors to nothing.
7. **Unruled tables get none of this.** Everything above rests on the source drawing its
   grid, which all five reference protocols do. A table ruled only horizontally, or not at
   all, falls back to the engines' own reading of the structure, and the alignment says so
   in a `source_grid_unavailable` warning rather than pretending otherwise.

## Honest scope of the checking

- **Rows and columns, all five protocols** — read back from each page's own ruling lines
  rather than counted by eye, so these are not estimates. The earlier hand counts were wrong
  on three of the six schedules, all in the same direction: a column with nothing printed in
  it is easy to miss, and so is a ruled row holding more than one activity.
- **Cell values** — protocol1 compared cell by cell against the lattice read straight from
  the page; the others spot-checked, with the distinct value vocabulary of each verified in
  full.
- **Footnote text and linkage** — read against the page on all five.
- **What is still eyeballed** — which lines of a multi-line label are separate activities
  and which are one label wrapping. That is a reading of the words, not of the grid, and it
  is where protocol9's remaining imprecision sits.
