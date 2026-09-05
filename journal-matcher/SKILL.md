---
name: journal-matcher
description: >
  Find and rank the journals a manuscript should actually be submitted to, then
  screen them before submission. Matches scope by what each journal demonstrably
  published on the topic (OpenAlex), and reports Scopus indexation, SJR quartile
  and D1 (top decile), impact factor or its proxy, APC and EISZ read-and-publish
  coverage, MEDLINE indexing, the empirical article types and article length the
  journal publishes, measured submission-to-acceptance time, acceptance and
  desk-reject rate, and author country distribution — with hard filters for
  Scopus-only, Q1/Q2, D1-only or EISZ-only shortlists. Also scores the manuscript
  for novelty and topic saturation, checks for scoops, sweeps the shortlist for
  open calls for papers and special issues, and advises what to strengthen to
  reach a better journal. Use whenever someone asks where to submit, hova küldjem
  be, melyik folyóiratba, which journal fits this paper, is this journal
  Scopus-indexed / Q1 / D1 / predatory, ragadozó folyóirat, what is the IF or APC
  or acceptance rate, is there EISZ támogatás, is there an open special issue or
  call for papers, build a submission ladder, or where to send it after a
  rejection. Also trigger when a manuscript or abstract appears together with any
  question about journals, even without the word "match".
license: Apache-2.0
---

# Journal matcher

Choosing a target journal is usually done from memory — "this feels like a
*European Heart Journal* paper" — and being wrong costs months. This skill replaces
the feel with things that can be checked, in five moves:

1. **Scope fit as evidence.** A journal makes the shortlist because it *published
   N papers on this exact topic in the last five years*, not because its title
   sounds relevant.
2. **Hard gates that reflect how the work will actually be assessed** — Scopus
   indexation, quartile, D1, MEDLINE, EISZ coverage, APC ceiling.
3. **A pre-submission screen of the journal itself** — including the median
   submission-to-acceptance time computed from PubMed's own deposited dates, not
   from the journal's marketing page.
4. **A novelty and saturation read on the manuscript**, because "which journal" and
   "is this strong enough" are the same question asked twice.
5. **A sweep for open calls and special issues**, which are often the highest-yield
   route into a journal one tier up.

Never skip move 3 for a journal "everybody knows". Portfolios churn: journals get
de-listed from Scopus and MEDLINE, get sold, and start running special-issue farms.
The check costs a minute; a wasted submission costs a quarter.

## Setup — once

```bash
export JMATCH_EMAIL="you@example.org"   # OpenAlex/NCBI polite pool: far better rate limits
scripts/jmatch.py sync                  # Scimago (Scopus) file: quartiles, D1, SJR, categories
```

`sync` also prints the reference-data status. Two optional inputs make the output
materially better, and the skill degrades honestly without them:

| File | Gives | Without it |
|---|---|---|
| `data/scimago-<year>.csv` (via `sync`) | Scopus indexation, Q1–Q4, D1, SJR, subject categories, publisher country | Q/D1/Scopus columns blank; those filters cannot be applied |
| `data/jcr.csv` or `$JMATCH_JCR_CSV` — a JCR export from the user's institution | the **real** Clarivate JIF | the OpenAlex 2-year mean citedness is shown with a `~` and called a proxy |
| `data/eisz.csv` | EISZ publisher roster (ships as an **unverified seed**) | no EISZ column |
| `data/acceptance_rates.csv` | acceptance / desk-reject rates (ships empty) | those columns stay `—` |

The Journal Impact Factor and CiteScore are licensed products. This skill never
invents one — if the user needs the JIF specifically and supplied no JCR export,
say so and point them at their library's JCR access.

## The commands

```bash
# whole pipeline: manuscript -> filtered, ranked shortlist
scripts/jmatch.py match --text ms.txt --require-scopus --min-quartile Q2 \
    --apc-budget 2500 --include-cited --top 12

# harder gates
scripts/jmatch.py match --text ms.txt --d1-only --require-medline --eisz-only

# is this strong enough, and has someone already published it?
scripts/jmatch.py novelty --text ms.txt

# dossier on named journals (name, ISSN or OpenAlex S-id)
scripts/jmatch.py profile "Cardiovascular Diabetology" 1475-2840

# open calls / special issues to check, per journal
scripts/jmatch.py match --text ms.txt -f json -o short.json
scripts/jmatch.py calls --shortlist short.json
```

Input is **plain text**. For PDF/DOCX use the `doc-tools` skill first:
`pdftotext ms.pdf | scripts/jmatch.py match --text -`. Everything is cached for a
week under `~/.cache/journal-matcher`.

## Workflow

### Step 1 — Manuscript in, constraints out

Read the manuscript (title + abstract minimum; full text gives better search terms
and lets the reference-overlap signal work). Then ask for whatever is unstated —
these change the answer more than the abstract does:

- **Scopus / MEDLINE required?** For most Hungarian and clinical assessment
  contexts Scopus indexation is non-negotiable — pass `--require-scopus`.
- **Quartile or D1 target**, and whether this is for a habilitation, PhD, or
  promotion file with a formal minimum.
- **APC budget**, and whether **EISZ** covers the institution (`--eisz-only` if the
  APC must be zero-cost).
- **Article type and length** — original, review, case report, brief, letter. The
  most common desk-reject cause is submitting a type the journal does not take.
- **Invited or independent?** If this came from a special-issue invitation or a
  guest editor, say so — the route, the deadline and often the acceptance odds are
  different, and the shortlist should be built around that invitation rather than
  competing with it.
- **Speed vs. prestige** — a thesis deadline or a competitor changes the ladder.
- **Already tried** (and any cascade offer received), and journals to avoid.

### Step 2 — Novelty and impact first, journals second

```bash
scripts/jmatch.py novelty --text ms.txt
```

Read the `nearest_works` table before anything else. **If one of those papers is
your paper, the target journal is not the problem** — say that plainly, and work
out what is still differentiating (population, comparator, endpoint, duration,
design) before recommending a tier.

Then use the saturation read to set the tier honestly:

- *sparse* — a niche or specialist journal, or the framing is too narrow to interest
  a general journal; consider broadening the question rather than lowering the tier.
- *active* — the normal case; the shortlist tiers are meaningful.
- *crowded and still growing* — novelty decays fast here. Weight speed over prestige,
  and check the scoop list monthly until acceptance.

Also read `references_last_5y_pct`: a reference list with a median year from a decade
ago reads as a dated manuscript to an editor regardless of which journal gets it.

### Step 3 — Run `match` with the user's real gates

Apply the constraints from Step 1 as flags, not as post-hoc filtering of a printed
table. The excluded journals are reported under **Excluded by the hard filters** —
report that section too, because "the best-fitting journal for your topic failed the
Scopus gate" is a finding the user needs.

### Step 4 — Read the ranking critically; never just paste it

The score is a transparent weighted sum (`references/scoring.md`) and every component
is printed. Before presenting anything:

- **Check the top rows against the manuscript.** High topic hits with the wrong
  readership (a basic-science venue for a clinical audit) is still a mismatch.
- **Look at what the score is made of.** A journal carried by `impact` alone with
  three topic hits is a reach, not a match — say so.
- **Compare the manuscript to the empirical profile.** The "What each journal
  actually publishes" block gives the real article-type mix and median reference
  count. A 90-reference manuscript aimed at a journal whose median is 30 is a
  length problem the user can fix before submitting, not after.
- **Check the author country distribution** against the manuscript's origin.
- **Turnaround with a small sample (`turnaround_n` under ~10) is noise** — quote it
  as indicative or not at all.
- **Never call the proxy an impact factor.** A `~` in that column means OpenAlex
  2-year mean citedness.

### Step 5 — Sweep for open calls and special issues

```bash
scripts/jmatch.py calls --shortlist short.json
```

This prints the searches and URLs to run — no API lists calls for papers, so run
each `WebSearch` and fetch each URL, then report what is actually open. For every
call found, record: **topic, deadline, guest editors and their affiliations, APC
inside the issue, and whether it is genuinely peer-reviewed.**

Treat a special issue as a real opportunity *and* a real risk. A guest-edited issue
on the manuscript's exact theme, in a journal that passed the gates, is usually the
best route available. A mass-mailed invitation from a journal with an integrity flag
is the mechanism by which special-issue farms grow — and papers published in one
carry that journal's reputation, and its de-listing risk, permanently.

### Step 6 — Deliver a ladder plus an optimisation plan

**The ladder** — this is how submission actually works:

- **Reach (1–2)** — ambitious but arguable; name exactly what would have to go right.
- **Target (2–3)** — the honest best fit; this is the recommendation.
- **Safe (1–2)** — indexed, realistic, no flags. Everyone needs this row, and nobody
  picks it well under the pressure of a second rejection.

Per journal, one sentence on *why this one*, plus APC (and EISZ status), expected
turnaround, and the article type and length it actually takes.

**The optimisation plan** — the part that answers "how do I get into a better
journal", not just "which journal will take this". Draw it from what the data showed,
and be concrete and specific to this manuscript. The recurring levers:

- **Reframe to the question the higher-tier journal asks.** Same data, different
  primary question — a "we measured X in Y" paper becomes publishable a tier up when
  it becomes "does X change the decision about Y".
- **Close the analysis gap the tier expects** — the sensitivity analysis, the
  competing-risk model, the external validation cohort, the calibration plot. Look
  at what the topic-matching papers in the reach journals did that this one did not.
- **Fix the length and reference profile** to the target's empirical norms.
- **Add the comparator or the subgroup** whose absence is the obvious reviewer question.
- **Report to the relevant guideline** (CONSORT, STROBE, TRIPOD+AI, PRISMA, STARD) —
  higher-tier journals increasingly desk-reject on this alone. For a prediction model,
  hand off to the `probast-tripod-ai` skill.
- **Strengthen the novelty statement itself**, using the `nearest_works` list: state
  in one sentence what this adds that each of those papers does not.

Say which levers are worth the delay and which are not, given the saturation read
from Step 2. In a fast-moving topic, a three-month improvement can cost more than
the tier gained.

### Step 7 — Integrity screen before the user commits

`match` runs `references/red-flags.md` automatically and prints an **Integrity flags**
section. Report every flag on any journal in the ladder, in plain language, and be
explicit that a flag means *look closer*, not *this journal is fraudulent* — the
screen is positive-signal based (does this show the marks of a real journal?), not a
blacklist. For a borderline journal, send the user to Think.Check.Submit and, where
their institution has access, to the Scopus source list and JCR directly.

## Working with other tools

- **`composer`** (szk-plugins) — its PubMed harvest is the best source for the
  reference-overlap signal. Export the library and pass it in:
  `jmatch.py match --text ms.txt --refs composer_export.json`. Any format works —
  DOIs are extracted from JSON, BibTeX or plain text. Going the other way, the
  `nearest_works` from `novelty` are DOIs that belong in the composer library.
- **`manuscript-copyedit`** — the natural next step once a target is fixed: it
  calibrates the prose against that journal's recent papers.
- **`scientific-paper-writing`** — structural/IMRaD work before choosing.
- **`doc-tools`** — text extraction from PDF/DOCX manuscripts.
- **`probast-tripod-ai`** — if the manuscript is a prediction model, its reporting
  gaps are usually the real barrier to a higher tier.

## What this skill will not do

- **It will not produce a Clarivate JIF or a Scopus CiteScore from thin air.** Both
  are licensed. Supply an export, or accept the labelled proxy.
- **It will not declare a journal predatory.** It reports checkable signals; the
  person submitting makes the call.
- **It will not confirm an EISZ waiver.** The roster ships unverified and the deals
  are annual, per-title and quota-capped — always send the user to eisz.mtak.hu to
  confirm before they count on it.
- **It will not read author guidelines.** Word limits, article types and submission
  requirements live on the journal's own page. The empirical length and type profile
  is a good approximation and no substitute — tell the user to verify both before
  submitting.

## References

- `references/scoring.md` — the weights, why each, and how to override them
- `references/data-sources.md` — what each source gives, its limits and failure modes
- `references/red-flags.md` — the integrity screen and how to read a flag
- `references/novelty-impact.md` — the novelty rubric and the optimisation levers
