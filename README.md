# SoA Extraction

Finds the Schedule of Activities in a clinical trial protocol PDF, extracts it into a
structured representation that preserves the grid, both grouping hierarchies, visit
windows and footnote linkage, and shows the result beside the source page so it can be
checked.

Built for the Intake AI take-home. The five reference protocols are in `data/protocols/`
and their extracted output is committed in `outputs/`.

---

## Quick start

```bash
# 1. Python environment (Python 3.10+)
uv venv && uv pip install -e .        # or: python -m venv .venv && pip install -e .

# 2. API key for the vision engine (free tier is sufficient)
cp .env.example .env                  # then put your key in GEMINI_API_KEY
# get one at https://aistudio.google.com/apikey

# 3. Extract from the command line
python -m soa.cli extract data/protocols/protocol9.pdf -o outputs/

# 4. Or run the UI: backend and frontend in two terminals
uvicorn api.main:app --port 8000
cd ui && npm install && npm run dev    # then open http://localhost:5173
```

Then drop **any** protocol PDF onto the page. Nothing is precomputed — the locator reads
the document you give it.

Without an API key the tool still runs (`--no-vision`, or untick the box in the UI). It
falls back to the deterministic engine alone, and the output records that recall is
unverified rather than pretending otherwise.

Useful extras:

```bash
python -m soa.cli locate data/protocols/protocol1.pdf --top 8   # locator only, no API calls
python bench/run_bench.py                                        # tool comparison
python bench/check_grid.py                                       # score outputs against the printed grid
```

---

## Architecture

```
PDF ──► locator ──► span(s)             free, runs on every page
     │
     ├──► geometric engine              text coordinates
     ├──► vision engine                 only the located pages
     │
     ▼
     align to the printed grid          ruled.py: the lattice the page draws
     │
     ▼
     footnote linkage                   anchors onto rows and columns by id
     │
     ▼
     reconcile                          the recall diff
     │
     ▼
     schema ──► UI / JSON
```

The middle step is load-bearing and is easy to skip. Both engines read *content* well and
*structure* only as well as a reader does — and a reader silently tidies structure up, so a
column the page rules and leaves empty vanishes and a ruled cell naming three activities
becomes three rows. Alignment happens **before** footnote linkage, because linkage anchors
onto rows and columns by id and the printed grid is what decides which of those exist.

The split is deliberate and is what keeps the tool affordable. The five protocols total
~370 pages, but only about a dozen carry a schedule. Locating is a coarse judgement — "is
this page a grid of visits?" — which heuristics do well and for free. Extraction is a fine
judgement, and it is where coordinate-based reading provably fails on these documents. So
cheap heuristics find the pages, and the vision model only ever sees the ~12 that matter.

A full five-protocol run sends ~14 page images and costs on the order of **$0.10–0.15**,
or nothing inside the free tier.

### 1. The locator (`soa/locate/`)

No page numbers are hardcoded anywhere; `python -m soa.cli locate` proves this by showing
the score for every page. Five stages:

1. **Contents harvest** (`toc.py`) — the PDF outline plus dotted-leader lines on printed
   contents pages. Printed page numbers disagree with PDF indices (front matter is numbered
   separately), so an offset is estimated from page footers. A contents entry only *biases*
   a page's score; it can never select a page on its own.
2. **Per-page scoring** (`score.py`) — every page is scored on additive, inspectable
   signals: cell-marker density, how many distinct vertical alignments of short tokens the
   page holds, ruling-line geometry, visit-header vocabulary (`Day -?\d+`, `Week -?\d+`,
   `± N days`, period names), assessment vocabulary, and a heading in the top third. Every
   signal that fired is recorded in the output.
3. **Span assembly** (`spans.py`) — adjacent nominated pages merge into one table. A page
   whose header echoes the previous one, or that says "continued"/"(concluded)", extends
   the current table; a fresh heading starts a new one.
4. **Footnote extension** — every span also claims the page *after* it as a footnote
   candidate. This is what catches a block that spills past the table with no heading and
   no marker.
5. **Trimming and ranking** — trailing pages with no grid are moved out of the table body,
   and spans scoring below half the document's best are dropped as noise but still reported
   as near misses, so a recall failure can be audited rather than guessed at.

The threshold is set **recall-biased**: a wrongly nominated page costs one cheap API call,
a wrongly skipped page costs an entire block of patient visits.

**Result: the locator finds exactly the right pages for all five protocols**, including a
protocol with two schedules where the second begins partway down a page that also carries
the first one's footnotes.

| protocol | schedules | pages | note |
|---|---|---|---|
| protocol1 | 1 | 53–54 | in an appendix after the references |
| protocol5 | 2 | 50, and 51 from y≈223 | second schedule shares a page with the first's footnotes |
| protocol9 | 1 | 26–28 | landscape, `/Rotate 90` |
| protocol12 | 1 | 48 (+49 footnotes) | footnote block spills to the next page |
| protocol15 | 1 | 25 | |

### 2. The extractors (`soa/extract/`)

**Geometric** (`geometric.py`) reads coordinates, never pixels. It is free, repeatable, and
supplies exact coordinates, which a vision model cannot. Where the table is ruled it reads
the printed lattice (see below) rather than clustering text positions, which is what makes
it a real second opinion on recall instead of a report of its own clustering errors; it
falls back to clustering where there are no rules to read. It is not expected to win.

**Vision** (`vision.py`) renders the located pages at 200 DPI and sends *all pages of a span
in one request*, so the model can reconcile a continuation header against the page before
it and see a spilled footnote block as one document. The raw text layer goes along as an
anchoring side-channel, with an explicit instruction that the **image wins** on conflict —
necessary because one protocol's fonts have no ToUnicode CMap and another's text order
duplicates rows.

The prompt (`prompt.py`) is where the brief's requirements are encoded as rules: copy cells
character for character, never reduce to booleans, never resolve ambiguity (flag it
instead), emit every printed row including empty ones, treat category rows as structure,
and report sideways text as annotation rather than data.

### 3. The printed grid (`soa/extract/ruled.py`, `soa/align.py`)

Both engines read *content* well and *structure* only as well as a reader does — and a
reader silently tidies structure up. Two corrections were needed, and both come from the
same place:

- **A blank printed column disappears.** Protocol1 rules a column between Visit 5 / Week 4
  and Visit 7 / Week 6 and prints nothing in it; protocol12 and protocol15 each rule one
  too. Nothing is drawn inside, so nobody reports it — and every visit to its right then
  sits one column too far left. That is not cosmetic: it reassigns visits to the wrong
  weeks.
- **A merged row label becomes several rows.** Protocol1 draws *one* row whose label cell
  reads `Study drug record / Medications dispensed / Medications returned`, with a single
  set of X marks covering all three. Read as prose those are three activities, and a model
  will say so — but splitting the cell invents two rows that carry no data and detaches the
  marks from two of the three.

Neither is a matter of judgement, because the grid is drawn on the page. `ruled.py`
recovers it exactly and `align.py` holds the extraction to it. Two details make the
recovery work on real protocols: every one of the five draws its borders as *hundreds of
per-cell segments* rather than table-length lines, so segments are clustered by position
and their union length is measured; and row boundaries are read **inside the row-label
column**, because that is the cell the label sits in and therefore the only place that can
say whether two printed lines are one row or two.

The stub that says `VISIT` above the visit numbers is *not* a visit column — it is the
header's own caption box, and reading it as a leaf column produces a narrow empty column
wedged between the row labels and Visit 1. It is identified by two signals together: it
carries no data anywhere in the table, and every header cell in it is an axis caption. A
column that is merely *empty* is the opposite case and is always kept.

It also settles a smaller ambiguity in the same way. A model reading a stacked header will
occasionally return its bottom row — `Study Week`, `Study Day` — as a *row* of the table,
which then sits in the output as an assessment nobody performs. The printed grid puts it
above the first body row, so it is dropped; but only when it also matches no body row and
carries nothing but the header's own values, because deleting a row is the one thing this
pass must not get wrong.

Alignment is conservative by construction. If the lattice cannot be read on every page, or
the engines' columns cannot be matched to it confidently, it declines to act and says so in
a warning rather than reshaping the table on a guess — a wrong alignment is worse than
none. Everything it does change is reported: `merged_source_row_restored`,
`column_missing_in_engine`, `column_not_in_source_grid`, `row_not_in_source_grid`,
`source_grid_unavailable`.

It also supplies exact coordinates. Once the lattice is known, a cell's bounding box is the
ruled cell itself rather than a guess at where its ink is, so clicking a cell in the UI
boxes the real region on the page — and so does clicking a footnote, which lights up every
cell, row and column its marker sits on.

### 4. Reconciliation (`soa/reconcile.py`)

The pipeline does not pick a winner. It takes the vision engine's structure and asks the
geometric engine one question: *did you see a row or a column that vision did not?*

Rows only the geometric engine saw are **carried into the output**, flagged `geo only` in
the UI and as a high-severity warning in the JSON. Rows only vision saw are reported at
medium severity. Column-count differences and per-cell disagreements are reported too.
This directly implements the brief's weighting: an extra row is tolerable, a dropped
assessment is not.

### 5. Footnotes (`soa/footnotes.py`)

Three graded things, handled separately:

- **Full text** — the block is found by anchoring on content (the first marker-led line
  below the last row carrying grid cells), not on the ruled box, because these protocols
  frequently draw the table border *around* the footnotes as well.
- **Linkage** — markers are resolved between the list and the grid. A footnote printed as
  `Xa - ...` is referenced on a cell as `Xa` while the marker proper is `a`; both resolve to
  the same footnote. Anchors are typed (`cell`, `row`, `column`, `column_group`,
  `row_group`, `table`), so "marker c sits on the Week 4 ECG cell" is expressible. A marker
  that cannot be linked keeps its text and records `unattached_reason` — dropping it would
  be worse than admitting we could not place it.
- **Page spill** — `Footnote.pages` is a list, so a block crossing a page break is a
  representable state rather than a bug. Unmarked continuation lines are absorbed into the
  footnote that was open when the page ended.

---

## Output schema

Defined as Pydantic models in `soa/schema.py`; every field carries a description, so
`SoADocument.model_json_schema()` is the full specification.

```
SoADocument
├── document          source file, sha256, page count, text-layer warnings
├── schedules[]
│   ├── heading, kind (main | pk | sub_study | extension), pages, footnote_pages
│   ├── column_groups[]   hierarchical banners, with the leaf columns each spans
│   ├── columns[]         header_cells[] (one per stacked header row, verbatim),
│   │                     group_path, visit_number/day/week, visit_window,
│   │                     printed_blank (the source rules it and prints nothing)
│   ├── row_groups[]      category rows — structure, not assessments
│   ├── rows[]            label, label_lines[], merged_label, row_span,
│   │                     group_path, page, bbox, engines
│   ├── cells[]           SPARSE list keyed by (row_id, column_id)
│   │                     raw (verbatim), value_text, footnote_refs, col_span,
│   │                     ambiguous, notes, bbox, engines{}
│   ├── footnotes[]       marker, style, text, pages[], attached_to[] typed anchors
│   ├── rotated_annotations[]
│   ├── locator           score, signals that fired, per-page scores
│   ├── reconciliation    per-engine counts, cell agreement, warnings[]
│   └── assumptions[], open_questions[]
└── run               model, duration, pages sent to vision
```

**Why this shape:**

- **A list of schedules, not one.** Protocols carry sub-study, PK and extension schedules;
  one of the five reference protocols has two.
- **Sparse cells, not a dense matrix.** A table that runs across pages is frequently
  ragged — protocol1's continuation page carries an entirely different set of visit
  columns. A dense matrix forces you to invent cells the document does not contain.
- **`raw` is required and never normalised.** Everything derived from it is optional and
  explicitly secondary. There is no code path that can reduce a cell to a boolean.
- **Groups are first-class on both axes.** Flattening "Treatment" into each visit column's
  name loses the hierarchy the brief asks to preserve; keeping category rows out of `rows`
  means a consumer counting activities does not count "Safety Assessments" as one.
- **The printed grid is the grid.** `Column.printed_blank`, `Row.label_lines` and
  `Row.merged_label` make a column the page rules but leaves empty, and a row whose one
  label cell names three activities, representable exactly as printed. Compacting either
  away reads better and is wrong.
- **`footnotes[].pages` is a list.** Page spill is a state, not an exception.
- **Uncertainty is recorded, not resolved.** `ambiguous`, `unattached_reason`,
  `text_complete`, `assumptions`, `open_questions`, and the reconciliation warnings all
  exist so the tool can say "I don't know" instead of guessing.

---

## Tools evaluated

Run `python bench/run_bench.py` to reproduce; results in `bench/RESULTS.md`.

| tool | verdict | why |
|---|---|---|
| **PyMuPDF** (text + geometry) | **chosen** as the foundation | Only library tested that exposes span-level bounding boxes, per-line writing direction, page rotation matrices *and* vector drawings together. Everything else needed a second library for one of those. |
| **Gemini Flash vision** | **chosen** as the primary extractor | Reads the page as printed, so rotation, corrupt glyph maps and cramped columns stop mattering. Handles merged header banners and continuation-page reconciliation that no coordinate method got right. Cheap because it only sees located pages. |
| **pdfplumber** | rejected as primary | Good API, but `extract_tables` returns wildly inflated column counts on these tables (32 columns where the table has 9), because it cannot find real boundaries without vertical rules. |
| **Camelot (lattice)** | rejected | Depends on ruling lines. Works on the ruled protocols, returns nothing useful on tables ruled only horizontally. Also the slowest option by an order of magnitude. |
| **Camelot (stream)** | rejected | Whitespace clustering does better on unruled tables than lattice, but has no notion of stacked header rows, so the hierarchy the brief asks for is lost by construction. |
| **PyMuPDF `find_tables`** | rejected as primary | Same inflated-column failure as pdfplumber on these documents. |
| **Hosted document-AI services** (Azure Document Intelligence, LlamaParse, Reducto) | not evaluated | The protocols must not be uploaded anywhere they would be retained. Ruling them out on that basis was a deliberate choice, not an oversight. |
| **OCR (Tesseract)** | not needed | All five protocols have a text layer. Kept in mind as the fallback for genuinely scanned documents, which this tool currently does not handle. |

### Model choice, and what the free tier actually does

`gemini-3.5-flash` is the configured default. Free-tier quota is metered per model and it
was exhausted when the committed outputs were generated, so most were served by the fallback
chain; every output records the model that actually served it in `run.vision_model`.

**Which model answers changes the cell-level result, and it is worth being blunt about how
much.** Across four regenerations of protocol1: one run returned all 139 cells and every
distinct value verbatim; one lost two X marks; one returned Myanmar glyphs where the page
prints a superscript `a`; one read every `Xb` as `X`. The **row and column structure was
identical in all four**, because it is settled against the printed grid rather than read off
the image — which is the entire argument for `soa/align.py`. Reconciliation caught the
cell-level differences in every case and reported them per cell.

Two distinct failures showed up, and telling them apart matters:

- **`503 UNAVAILABLE`** — Google-side serving capacity ("this model is currently experiencing
  high demand"). It hit 3.7-flash first, then 3.5-flash. Not quota: it failed 0/6 over 60s on
  a *two-token* prompt, and failed identically on two different API keys while other models
  answered normally on the same key in the same second.
- **`429 RESOURCE_EXHAUSTED`** — free-tier request quota, which is metered **per model**. An
  exhausted model is not an exhausted key: 3.5-flash and 2.5-flash returned 429 while
  3.7-flash and 3-flash-preview answered fine on the same key.

So the fallback chain is load-bearing rather than decorative, and 429 is treated as
non-retryable on the same model (per-model quota does not recover in seconds) while 503 gets
one retry before moving on. Requests also carry an explicit timeout — without one a stalled
request hung a five-protocol batch for over ten minutes with no error and no output.

Thinking is **off** by default. Measured on the same page and prompt, no thinking budget
returned 34 rows, 126 cells and all 5 footnotes in 52 seconds; a 4096-token budget pushed the
same request past four minutes without improving the result.

On quality between models, the honest answer is that the differences are small and my
comparison is not clean — `gemini-3.5-flash` matched ground truth exactly on the two
protocols where I have data for it, and `gemini-3.1-flash-lite` matched on rows and columns
everywhere but lost three footnotes on protocol1. What *is* clearly demonstrable is speed:
2.5-flash and 3.1-flash-lite complete these requests in 15–50s where the 3.x Flash models
took several minutes or timed out.

### Two failures worth naming specifically

Both were found by reading the documents, not by assuming:

**Sideways labels are not rotated text.** The `RANDOMIZATION` divider in protocols 12 and 15
is *thirteen separate upright single-character spans* at a constant x with a 13.8pt pitch,
each with a perfectly horizontal direction vector. Every generic extractor assigns those to
thirteen different rows. Detecting the run geometrically and lifting it out is what stops
that — and it takes three tests to avoid eating real data: right-adjacency (rejects
row-label first characters, which some producers emit as separate spans), character
diversity (rejects a column of `X` marks, which has the same geometry), and a
leftmost-on-line test (rejects footnote list markers `a`, `b`, `c` down a margin).

**True `/Rotate 90` pages are not sideways text.** Protocol9's SoA pages are genuinely
landscape; their text is stored in unrotated media space with direction `(0, -1)`. A naive
"is this span rotated?" test flags the entire page. Mapping every coordinate and direction
vector through the page's own rotation matrix first is what makes both cases work at once.

---

## Verification

Every protocol was opened next to its JSON and compared against the source. Full detail in
`verification/`; `verification/SUMMARY.md` has the complete table.

Rows and columns are no longer checked by hand. All five protocols draw a ruled grid, so
`python bench/check_grid.py` reads the printed rows, columns and cells back out of each
page's vector graphics and scores the committed output against them, cell by cell:

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

Read it as a signal for review, not a verdict. It is not independent of the extraction --
`soa/align.py` reads the same lattice to place the cells -- and a good share of the
disagreements are the *text layer* being wrong rather than the extraction: protocol15's
page renders a superscript marker ahead of its mark, so the page reads `a X` where the
image plainly shows `Xa`, and two protocols have damaged font encodings that turn `Prior`
into `rior`. What it is good for is exactly what a hand count could not do: it re-scores in
two seconds after any change, and it flags every cell worth looking at by position.

**Locator: 6 / 6 schedules found, on the right pages, with no hardcoded page numbers.**

Ground truth for rows and columns is read back from each page's own ruling lines rather
than counted by eye — all five protocols draw a fully ruled grid — so these are not
estimates.

| protocol | columns | column groups | assessment rows | categories | footnotes | linked |
|---|---|---|---|---|---|---|
| protocol1 | **15 / 15** | — | **28 / 28** | — | 4 / 5 | **4 / 4** |
| protocol5 — Appendix I | **11 / 11** | 8 / 7 | **31 / 31** | 1 / 0 | **10 / 10** | **10** |
| protocol5 — Appendix II | **15 / 15** | 1 | **8 / 8** | 1 / 0 | **2 / 2** | **2** |
| protocol9 | **11 / 11** | **4 / 4** | **33 / 33** | **4 / 4** | 6 / 4 | **4 / 4 real** |
| protocol12 | **9 / 9** | **3 / 3** | **37 / 37** | 4 / 3 | **14 / 14** | 13 |
| protocol15 | **10 / 10** | **4 / 4** | **31 / 31** | **3 / 3** | **5 / 5** | **5** |

Row and column recall is exact on all six schedules — the measure the brief weights most
heavily.

Three of these numbers moved when the ground truth was re-read from the pages' ruling
lines, and every correction was the same mistake made twice: **a column with nothing
printed in it looks like whitespace, and several activities inside one ruled cell look like
several rows.** protocol1 has 15 columns, not 14 — one is ruled and empty. It has 28 rows,
not 30 — three activities share one ruled row. protocol12 and protocol15 each rule one more
column than was counted, for the sideways `RANDOMIZATION` divider.

**The hard cases the brief names all work:** protocol1's continuation page carrying a
*different* visit range (9–13, ET, RT vs 1–8) merged into one 15-column table, with the
column the page rules but leaves blank kept in place and no Visit 6 invented to fill it;
protocol9's continuation pages dropping the study-phase banner entirely and still merging
into one 11-column table; protocol12's footnote block spilling 48 → 49 with all 14 captured;
protocol5's second schedule starting partway down a page already carrying the first one's
footnotes; and protocol1's single ruled row naming three activities kept as one row rather
than split into three, two of which would have carried no data.

**What is still wrong,** in order: protocol9 returns 6 footnotes where 4 exist (the extras
are abbreviation and legend lines in the same block, all anchoring to nothing and flagged);
several of protocol9's wrapped row labels are reported as `merged_label` when they are one
label over two lines rather than two activities — the row count is right, the claim about
how to read it is not; the vision engine occasionally mis-transcribes a superscript marker
(one run returned Myanmar glyphs for the `a` in `Xa`), which reconciliation catches but
nothing repairs. Per-schedule detail, including the smaller imprecisions, is in
`verification/`.

Questions raised for a clinical SME rather than guessed at are in
`verification/QUESTIONS.md`. A test against a protocol the tool had never seen — a modern
193-page ICH M11 protocol from ClinicalTrials.gov — is in `verification/UNSEEN.md`: the
locator found its `2 SCHEDULE OF ACTIVITIES` section and fired the visit-window signal for
the first time (no reference protocol prints one), but the span splitter merged twenty pages
into one schedule, which is the first thing to fix.

---

## Where it breaks, and what it does when it breaks

**Fails loudly (recorded in the output, visible in the UI):**

- *Vision engine unavailable* — no key, refused model, network error. The geometric engine
  runs alone; `reconciliation.engines_failed` records why and a high-severity warning states
  that recall is unverified.
- *Engines disagree on a row or column* — reported as a warning; geometric-only rows are
  carried into the output flagged `geo only`.
- *A footnote cannot be linked* — kept, with `unattached_reason`.
- *Damaged font encodings* — detected up front from missing ToUnicode CMaps and surfaced in
  `document.text_layer_warnings`; the vision engine is then told the text layer is
  untrustworthy.

**Degrades quietly — these are real limitations:**

- **Scanned protocols are not handled.** All five references have a text layer. A scanned
  document would locate poorly (the scorer reads text) and the geometric engine would return
  nothing. `document.has_text_layer` reports it, but there is no OCR path.
- **Everything structural rests on the source drawing its grid.** All five reference
  protocols do, and where a lattice is found the row and column counts are facts rather than
  readings. A table ruled only horizontally, or not at all, gets none of it: alignment
  declines, emits `source_grid_unavailable`, and the engines' own reading of the structure
  stands — blank columns and merged row labels included.
- **Footnotes can be attributed to the wrong schedule when two share a page.** In protocol5,
  Appendix I's footnote block sits above Appendix II on page 51, and some of those footnotes
  are attached to both schedules. The ones that do not belong show up unlinked rather than
  silently wrong.
- **Legends are modelled as footnotes.** `X = Performed at this visit.` links to every `X`
  cell — correct but noisy. See question 6 in `verification/QUESTIONS.md`.
- **The span dominance filter is relative.** A genuine third schedule scoring below half the
  document's best would be dropped. It would appear in the near-miss list, but it would not
  be extracted.
- **One schedule per span.** Two tables stacked on one page are handled; three are not
  tested.
- **The text-layer-only footnote fallback is partial.** With `--no-vision` it recovers
  footnotes from three of five protocols; the two it misses use formats
  (`* Morphine: ...`, `X = Performed...`) that its marker regex does not anchor on.
- **`--no-vision` does not merge continuation pages.** Each page's grid is now read exactly
  — protocol1 comes back as 28 rows and 8 columns on page 53, 28 and 7 on page 54, which is
  what the pages draw — but the fallback path concatenates them instead of splicing them
  into one 15-column table, so the same 28 rows appear twice. Merging is the vision path's
  job today; the axis-splicing in `soa/align.py` would do it, and wiring the geometric-only
  path through it is the obvious next step.

---

## What I would build next, given two more weeks

1. **Ground truth for tables that are not ruled.** `bench/check_grid.py` now gets ground
   truth for free wherever the source draws its grid, which covers all five reference
   protocols and gives a real per-cell number instead of a proxy metric. It gets nothing on
   an unruled table, and it cannot referee a disagreement where the text layer itself is
   damaged. Hand-keying one protocol with corrupted fonts would settle both.
2. **A lattice-free path for unruled tables.** `ruled.py` gives an exact answer wherever
   the source draws its grid, which covers all five reference protocols. A table ruled only
   horizontally, or not at all, still falls back to the engines' own reading, and the blank
   column and merged row protections do not apply there. Recovering an implied lattice from
   whitespace, and being explicit about how much less it is trusted, is the next step.
3. **Self-consistency on the vision engine.** Two runs at temperature 0 with the page order
   and the prompt's emphasis varied, then diff them. Cells that differ between runs are
   exactly the cells worth showing a human, and this needs no ground truth to be useful.
4. **OCR path for scanned protocols.** Render, OCR, and feed the result through the same
   locator. The vision engine already reads pixels, so mostly this is about making the
   *locator* work without a text layer.
5. **A visit-calendar derivation, kept strictly separate from the extraction.** Expanding
   `3X/week for 2 weeks` into visit-level events is genuinely useful and genuinely
   dangerous; it belongs in a distinct layer that cites the raw cell it derived from, so the
   faithful extraction stays faithful.
6. **Review workflow in the UI.** Accept/reject per flagged row, edit a cell, and export the
   corrections as a labelled example. That turns the recall diff from a warning list into a
   correction loop.

---

## AI tools used

**Claude Code (Claude Opus)** wrote effectively all of this repository — the locator
heuristics, both engines, the schema, the UI and this README — working from the assignment
PDF and the five protocols.

**What it was good for.** The largest single gain was using it to *read the documents
before designing anything*. It went through all five protocols and reported where each
schedule was, how the headers stacked, what the cell vocabulary looked like and where the
awkward cases were. Two load-bearing decisions came straight out of that — the
vertical-label detector and the page-rotation handling — rather than out of a guess that
later needed unpicking. It was also fast at mechanical breadth: a five-engine benchmark
harness, a Pydantic schema with a description on every field, and a React UI were all cheap.

**Where it needed watching.** The failures had one shape: code that reads correctly and is
wrong about the document.

- *A rule that sounds right and destroys data.* The first vertical-label detector was
  written to a sensible-sounding rule and silently removed every `X` column on the page.
  Nothing in the code looked wrong. Running it over the real PDFs and printing what it had
  removed is what exposed it — as it did for the row-label boundary, which was quietly being
  dragged to the left margin by footnote markers sitting below the table.
- *An assumption standing in for a measurement.* The whole subject of this pass is one of
  these: the extraction dropped a column the page rules and leaves empty, and split a ruled
  cell holding three activities into three rows. Both readings are what a careful person
  would produce from the image; both are wrong about the page. The fix was not a better
  prompt but reading the grid out of the page's own vector graphics and holding the
  extraction to it.
- *A confident report of something not checked.* An early subagent described protocol5's
  second schedule as a standalone table on page 51. It is on page 51 *below the first
  schedule's footnote block* — taken at face value it would have produced a wrong span.
- *Escaping bugs in generated patch scripts.* A `\b` in a non-raw Python string became a
  literal backspace inside a regex: invisible in a diff, and it silently broke
  footnote-heading detection. Found by scanning the sources for control characters.

The pattern throughout is that it is quick and reliable at structure and breadth, and
unreliable about anything that depends on what is actually inside the documents. Every
heuristic here was tuned against printed output from the real PDFs, and several were wrong
on the first attempt in ways not visible from the code. That is also why
`bench/check_grid.py` exists: the ground truth these tables need was sitting in the
documents all along, and scoring against it is worth more than any amount of reasoning
about whether the code looks right.
