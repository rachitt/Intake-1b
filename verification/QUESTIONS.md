# Questions for a clinical subject matter expert

The brief asks for the questions rather than silent guesses. Each entry states what the
document actually shows, what the tool currently does, and why the answer would change the
output. Where the tool had to choose, the choice is recorded in the affected schedule's
`assumptions` array so it travels with the data.

---

## 1. Is a "Schedule of Blood Collections" a second SoA, or a sub-table of the first?

**Where:** protocol5, Appendix I ("Time and Events Schedule", page 50) and Appendix II
("Schedule of Blood Collections", page 51).

**What the document shows:** Appendix II is a separate numbered appendix with its own
heading and its own column axis (study day), but its rows are sample types and its cells
are volumes and counts rather than "performed / not performed" marks. It shares no rows
with Appendix I.

**What the tool does:** emits it as a second schedule with `kind: "pk"`.

**Why it matters:** if a downstream consumer treats every schedule as an activity-by-visit
grid, a volumes table will be interpreted as visit assessments. Should a sample-volume
table be modelled as an SoA at all, or as a distinct artefact that references the SoA?

---

## 2. Are "practice only" administrations scheduled activities?

**Where:** protocol1, page 54. The legend reads *"P = Practice only - It is recommended
that a sampling of the CIBIC+, ADAS-Cog, DAD ..."*, and `P` appears in cells alongside `X`.

**What the tool does:** captures `P` verbatim as a cell value and keeps the legend as a
footnote. It does not decide whether a `P` cell counts as a scheduled activity.

**Why it matters:** site payment and workload models count activities per visit. If `P`
occurrences are billable or count toward burden, they must be distinguished from an absent
cell; if they are training only, counting them inflates the study's cost model.

---

## 3. Is "Medical Discharge" a study period, a visit, or both?

**Where:** protocol9, page 26. It appears in the topmost header banner alongside
"Opiate Agonist Phase", "Detoxification", and "Post Med/Detox Phase" -- but unlike those, it
spans a single column.

**What the tool does:** emits it as a column group covering one leaf column, because that is
how it is printed.

**Why it matters:** a period that contains exactly one visit and a visit that happens to sit
outside any period are different things to a scheduling system, and the printed table does
not disambiguate them.

---

## 4. Do "ET" and "RT" columns belong to the visit sequence?

**Where:** protocol1, page 54. The continuation page carries visits 9–13 and then two
further columns labelled `ET` (Early Termination) and `RT` (Retrieval), with no study week.

**What the tool does:** emits them as ordinary leaf columns with an empty study-week header
cell, preserving their position.

**Why it matters:** these are conditional, unscheduled visits. Treating them as sequential
visits would place a retrieval visit at week 28 in a derived calendar. Should the schema
mark a column as unscheduled or conditional, and is that distinction reliably derivable
from the label alone?

---

## 5. What does a frequency annotation spanning several columns actually schedule?

**Where:** protocol15, page 25 -- values such as `3 X/week for 2 weeks` and `Weekly x 2
weeks` printed across a group of week columns rather than in a single cell. protocol9 has
richer cases still: `Day 4: 1X, 0800h; Day 6: 1X, 0800h` and `Total of 12 samples (10 ml of
plasma each)`.

**What the tool does:** captures the text verbatim in `raw` and records `col_span` so the
visual span survives. It does not expand the annotation into per-visit occurrences.

**Why it matters:** expanding "3X/week for 2 weeks" into six visit-level events requires
knowing the visit calendar, and getting it wrong silently changes the number of case report
forms. Is per-visit expansion ever safe to automate, or must it always be a human decision?

---

## 6. Should a footnote that is really a legend be modelled differently?

**Where:** protocol1, page 54: `X = Performed at this visit.` sits in the footnote block
with the same visual treatment as genuine footnotes such as `Xa = Performed at this visit
if patient is an insulin-dependent diabetic.`

**What the tool does:** emits both as footnotes. The legend then links to every cell
containing `X` -- 131 anchors in that protocol -- which is technically correct and
practically noisy.

**Why it matters:** a legend defines the notation; a footnote qualifies specific cells.
Conflating them means a consumer cannot tell "this cell has a condition attached" from
"this cell uses the standard mark". Should the schema carry a `legend` flag, and is the
distinction ever ambiguous in practice?

---

## 7. Are CRF form numbers part of the assessment name?

**Where:** protocol9. Row labels carry trailing parenthesised numbers -- "Addiction Research
Center Inventory (22)", "Plasma Lofexidine Pk (LCMS) (29)" -- which are case report form
identifiers, not footnote markers.

**What the tool does:** keeps them in the row label verbatim, and explicitly refuses to read
them as footnote markers (a parenthesised number is only treated as a marker if it also
appears in the footnote block).

**Why it matters:** if these are stable CRF identifiers they are valuable structured data
and deserve their own field rather than living inside a label string. Is the convention
consistent enough across sponsors to parse?

---

## 8. When a continuation page carries a different visit range, is it the same table?

**Where:** protocol1, pages 53–54. Page 53 covers visits 1–8; page 54 covers visits 9–13
plus ET and RT, and reprints the row labels. No row appears on both pages with data.

**What the tool does:** treats it as one schedule with 14 columns, merging the two visit
ranges, because the heading is the same and page 54 is marked "(concluded)".

**Why it matters:** the alternative reading -- two tables sharing a title -- would produce
two schedules with eight and six columns. Merging is almost certainly right here, but the
tool is applying a heuristic where a domain convention may exist.
