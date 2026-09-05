# Novelty, impact, and getting into a better journal

"Which journal" and "is this strong enough" are the same question asked twice. This
file is the rubric for the second half.

## Reading the novelty scan

`jmatch.py novelty --text ms.txt` returns three things.

### 1. `nearest_works` — the scoop check

Read this table first, before any journal discussion. These are the published papers
closest to the manuscript's own terms.

- **If one of them is essentially this paper**, the target journal is not the problem.
  Say so directly and work out what remains differentiating — population, comparator,
  endpoint, setting, duration, design — before recommending any tier.
- **If two or three are close**, they are the papers the discussion must engage with
  and that reviewers will ask about. A manuscript that does not cite its nearest
  neighbours reads as either unaware or evasive; both cost more than the citation.
- **If none are close**, either the work is genuinely novel or the search terms are
  wrong. Check the extracted `query_terms` before believing it.

The nearest-works list is term-based, not semantic. It finds overlap, not
equivalence, and it will miss a paper that describes the same thing in different
vocabulary. It is a prompt to look, not a clearance.

### 2. Topic saturation — how fast novelty decays here

| Reading | What it implies for the ladder |
|---|---|
| **sparse** | A specialist journal, or the framing is too narrow for a general one. Consider broadening the question rather than dropping a tier. |
| **active** | The normal case. The tiers mean what they usually mean. |
| **crowded, flat** | A mature topic. Incremental work goes mid-tier; a higher tier needs a genuinely different question, not more data. |
| **crowded and still growing** | Novelty has a short shelf life. Weight speed over prestige, keep the scoop list under review until acceptance, and be sceptical of any improvement plan costing more than a couple of months. |

### 3. Reference recency

`references_last_5y_pct` and `reference_median_year`. A reference list whose median
year is a decade old reads as a dated manuscript to an editor, regardless of the
journal. This is cheap to fix and expensive to leave.

## Judging impact before submission

Ask the four questions an editor asks, and answer them from the manuscript:

1. **Who changes what they do because of this?** A clinician, a guideline committee,
   a subsequent trial designer — name them. "Adds to the literature" is the answer
   that produces a desk reject.
2. **What is the effect size, and is it clinically rather than statistically
   meaningful?** A significant 2 mmHg difference is a statistics result, not a
   finding.
3. **How large and how representative is the sample, against the topic's norms?**
   The journals in the shortlist publish this topic — what N do their papers carry?
4. **Would the result have been publishable had it come out the other way?** If not,
   the design has a problem that the journal choice cannot fix.

## The optimisation levers

When the user asks how to reach a better journal, draw from what the data showed and
be specific to this manuscript. Generic advice is worthless here.

**Reframe to the question the higher tier asks.** The most common upgrade path, and
it costs no new data. "We measured X in population Y" is a mid-tier paper; "does
knowing X change the decision about Y" can be a tier up with the same numbers.

**Close the analysis gap the tier expects.** Look at what the topic-matching papers
in the reach journals did that this one did not — the sensitivity analysis, the
competing-risk model, the external validation cohort, the calibration plot, the
pre-registered protocol. This is checkable rather than speculative: those papers are
in the `nearest_works` and discovery output.

**Add the obvious missing comparator or subgroup.** Whatever a reviewer will ask for
first is cheaper to add now than after a rejection.

**Report to the relevant guideline** — CONSORT, STROBE, TRIPOD+AI, PRISMA, STARD,
CARE. Higher-tier journals increasingly desk-reject on this alone, and it is the
cheapest possible improvement. For a prediction model, hand off to
`probast-tripod-ai`.

**Match the empirical length and reference profile** of the target from the `match`
output. A 90-reference manuscript aimed at a journal whose median is 30 signals
"wrong article type" before anyone reads a sentence.

**Rewrite the novelty statement using `nearest_works`.** One sentence saying what
this adds that each of those papers does not. If that sentence is hard to write, the
problem is the study and not the framing — which is itself the most useful finding
this skill can produce.

**Consider the invited route.** A guest-edited special issue on the manuscript's
exact theme, in a journal that passed the hard gates, is often a better outcome than
a cold submission a tier up — provided the issue is real (named guest editors with
affiliations, a stated theme, ordinary peer review) and the journal carries no
integrity flags.

## Being honest about the trade

Every lever costs time, and in a crowded, growing topic time costs novelty. State
the trade explicitly: *"adding the validation cohort would likely move this from a
Q2 to a Q1 journal, but it is six months, and this topic published 900 papers last
year — two of them close to yours."*

That sentence is the deliverable. The ranked table is just how you got there.
