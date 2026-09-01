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
```

---

## Architecture

```
PDF ──► locator ──► span(s) ──┬──► geometric engine ──┐
     (free, all pages)        │   (text coordinates)  ├──► reconcile ──► schema ──► UI / JSON
                              └──► vision engine ─────┘   (recall diff)
                                  (located pages only)
```

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
supplies the exact bounding boxes that let the UI highlight a cell's source region — which
a vision model cannot provide. It is not expected to win; its job is to be a second opinion
and a coordinate oracle.

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

### 3. Reconciliation (`soa/reconcile.py`)

The pipeline does not pick a winner. It takes the vision engine's structure and asks the
geometric engine one question: *did you see a row or a column that vision did not?*

Rows only the geometric engine saw are **carried into the output**, flagged `geo only` in
the UI and as a high-severity warning in the JSON. Rows only vision saw are reported at
medium severity. Column-count differences and per-cell disagreements are reported too.
This directly implements the brief's weighting: an extra row is tolerable, a dropped
assessment is not.

### 4. Footnotes (`soa/footnotes.py`)

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
│   │                     group_path, visit_number/day/week, visit_window
│   ├── row_groups[]      category rows — structure, not assessments
│   ├── rows[]            label, group_path, page, engines
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

### Model choice

`gemini-3.5-flash` is the default. `gemini-3.7-flash` was tried first and returned HTTP 503
"high demand" often enough to stall runs; it remains selectable via `SOA_GEMINI_MODEL`, and
the client falls back through `gemini-3-flash-preview` and `gemini-2.5-flash`. A modest
thinking budget (4096) is set deliberately — transcription is a careful-reading task, and an
unbounded budget roughly doubled wall-clock time without changing the output.

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

## Manual verification

Per-protocol, cell-by-cell notes are in `verification/`. Summary:

See `verification/SUMMARY.md` for the table and `verification/protocolN.md` for the
detail of what was right, what was wrong, and how it was wrong.

Questions raised for a clinical SME rather than guessed at are in
`verification/QUESTIONS.md`.

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
- **The geometric engine over-merges wrapped rows**, particularly on protocol9's dense
  landscape pages, where it recovers roughly a third of the rows vision does. This makes its
  half of the recall diff weaker exactly where the table is hardest. It is a cross-check,
  not a second extractor.
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

---

## What I would build next, given two more weeks

1. **A ground-truth corpus and a real accuracy number.** Hand-key two protocols cell by
   cell, then report precision and recall per protocol rather than the proxy metrics the
   benchmark currently uses. Everything below is guesswork without this.
2. **Make the geometric engine a real second reader.** Its row merging is the weakest part
   of the recall diff. Using ruling-line row boundaries where they exist, instead of purely
   y-clustering the text, should close most of the gap on the dense landscape pages.
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

**Claude Code (Claude Opus)** wrote effectively all of this repository, including the
locator heuristics, both engines, the schema, the UI and this README, working from the
assignment PDF and the five protocols.

**Where it helped.** Fastest gains came from using it to *investigate the documents before
designing anything*: it read all five protocols in parallel and reported where each SoA
was, how the headers stacked, and what the cell vocabulary looked like. Two of the load-bearing
design decisions — the vertical-label detector and the page-rotation handling — came
directly out of that reconnaissance rather than from a guess that later needed fixing.
It was also good at the mechanical breadth: five engines in the benchmark harness, a full
Pydantic schema with per-field descriptions, and a React UI were cheap to produce.

**Where it got in the way.** Three specific things:

- *Plausible heuristics that fail on real data.* The first vertical-label detector was
  written to a sensible-sounding rule and silently ate every `X` column on the page. It
  looked correct in the code. Only running it against the actual PDFs and printing what it
  removed exposed it. The same happened with the label-column boundary, which was
  quietly being dragged to the left margin by footnote markers below the table.
- *Escaping bugs in generated patch scripts.* A `\b` in a non-raw Python string became a
  literal backspace character inside a regex — invisible in a diff, and it silently broke
  footnote-heading detection. Caught only by scanning the source files for control
  characters.
- *Confident over-generalisation.* An early subagent report described protocol5's second
  schedule as being on page 51 as a standalone table; it is actually on page 51 *below the
  first schedule's footnote block*. Taking that at face value would have produced a wrong
  span. Reading the page directly settled it.

The pattern throughout: it is fast and good at generating structure and breadth, and
unreliable at anything that depends on what is actually inside the documents. Every
heuristic in this repository was tuned against printed output from the real PDFs, and
several were wrong on the first attempt in ways that were not visible from the code.
