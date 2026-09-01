# Generalisation test — a protocol the tool has never seen

The brief requires the UI to work on an unseen protocol, so the tool was pointed at one
that is nothing like the reference set: a modern, much larger, industry-sponsored protocol
downloaded from ClinicalTrials.gov.

**Document:** NCT03036098, `Prot_SAP_000.pdf` — Bristol-Myers Squibb CA209901, **193 pages**
(the reference protocols are 57–97 pages). Not committed to this repository; reproduce with

```bash
curl -o unseen.pdf https://cdn.clinicaltrials.gov/large-docs/98/NCT03036098/Prot_SAP_000.pdf
python -m soa.cli locate unseen.pdf --top 10
```

---

## What worked

**The locator found the real Schedule of Activities.** It nominated a span opening at page
24 with the heading `2 SCHEDULE OF ACTIVITIES` — a top-level numbered section, a structure
that appears nowhere in the reference set, where every schedule lives in an appendix or as a
mid-body table.

Every category of signal fired, including one the reference protocols never exercised:

```
signals: assessment_vocab, cell_marker_density, column_structure, heading_on_page,
         landscape, ruling_grid, visit_header:study_day, visit_header:study_period,
         visit_header:study_week, visit_header:visit_number, visit_header:visit_window
```

`visit_header:visit_window` is the `± N days` pattern. **None of the five reference
protocols prints a visit window at all** — the pattern was written from the brief's
description rather than from an example, and it fired correctly the first time it met one.

The scorer also separated signal from noise across 193 pages: the two genuine table regions
scored 10.5 and 10.3, while twelve other pages were considered and rejected, and they are
listed in the near-miss output so the decision can be audited.

## What broke

**A 20-page span was returned as one schedule.** The locator merged pages 24–43 into a
single table. In an ICH M11 protocol, Section 2 typically contains *several* schedules —
screening, treatment, follow-up, and often a PK sub-schedule — under one section heading.
The span splitter looks for a fresh SoA-style heading to divide on, and sub-schedule titles
inside that section did not match strongly enough to trigger a split.

Consequences, and what the tool does about each:

- **Cost and latency.** Twenty pages in one vision request is slow and risks running past
  the output token limit part-way through the table, which would drop trailing rows
  silently. This is precisely why the page cap was added: at most 8 pages go in one request
  (`SOA_MAX_VISION_PAGES`), the omitted page numbers are named, and a **high-severity
  warning** records that rows on those pages are missing. It fails loudly rather than
  quietly, but it does fail — the extraction of this document would be incomplete.
- **Structure.** Several distinct schedules merged into one would produce a single table
  with an incoherent column axis.

**Two spurious spans were kept.** Pages 47 and 85 scored 5.4 and 5.2 against a document best
of 10.5, clearing the 0.5 dominance ratio. Both are prose pages that merely discuss visits.
On this document the relative threshold is too permissive; on the reference set, where real
schedules score 12–16 and noise scores 3–5, it is well calibrated. A fixed threshold would
have the opposite problem.

**Metadata guessing degraded.** `title_guess` returned the first long line of a cover letter
(`Administrative 11-Jul-2022 The purpose of this letter is to noti…`) rather than the study
title. This is advisory only and nothing depends on it, but it is wrong. `protocol_id_guess`
correctly returned `CA209901`.

## What this says about the tool

The **locator generalises**: it found a schedule under a heading style, in a document
structure, and at a document size none of its heuristics were tuned against, and it fired a
signal it had never seen an example of.

The **span splitter does not**. It was built against protocols where one heading introduces
one table, and modern protocols group several schedules under a single numbered section. On
the evidence here that is the first thing to fix, and it is a locator problem, not an
extraction problem — the pages were found correctly, they were just not divided correctly.

Nothing here was silently wrong. The 20-page span is visible in the locator output, and the
page cap converts what would have been quietly truncated rows into a named, high-severity
warning.
