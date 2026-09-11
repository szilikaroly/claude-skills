# The score, and how to argue with it

The score is a weighted sum out of ~100, printed component-by-component in the JSON
output as `score_parts`. It exists to order a shortlist, not to make the decision.
Any recommendation that cannot be defended without pointing at the score is a bad
recommendation.

## Weights

| Component | Max | What it measures | Why this weight |
|---|---|---|---|
| `scope` | 35 | how many papers on this topic the journal published in the window (85%), plus how often the manuscript cites it (15%) | The only component that is about *this manuscript*. Scope mismatch is the dominant desk-reject cause, so it dominates the score. |
| `impact` | 20 | real JIF if a JCR export was supplied, otherwise the OpenAlex 2-year mean citedness | Matters, and is the thing users over-weight on their own. Deliberately smaller than scope. |
| `access` | 12 | APC against the stated budget, after any EISZ discount | For most authors this is a hard constraint dressed up as a preference. |
| `standing` | 12 | D1 (full marks) else Q1 1.0 / Q2 0.7 / Q3 0.4 / Q4 0.15 | This is what assessment committees read. Separate from `impact` because a Q1 journal in a small field can have a modest JIF. |
| `indexing` | 8 | MEDLINE 5, PubMed-only 2, Scopus 2, DOAJ-or-subscription 1 | Low weight because it is usually a *hard filter* (`--require-scopus`), and a filter should not also be paid for in points. |
| `speed` | 8 | median submission→acceptance | Linear from 240 days down; below 21 days it is *penalised*, not rewarded. |
| `acceptance` | 5 | curated acceptance rate | Rarely populated, so it is kept small; above 60% it scores *lower*, since a journal that takes almost everything is not selecting. |

Every integrity flag subtracts **8 points**. Four flags will sink a journal below
anything on the list, which is intended: those are journals to look at hard before
submitting, not journals to rank politely.

Scope and impact use a square-root curve. Linear scaling lets one enormous journal
flatten everything else to near-zero; the square root keeps the mid-field readable.

## Deliberate asymmetries

**Fast is not always good.** Under 21 days submission-to-acceptance scores 0.3, not
1.0. Real peer review of a clinical paper does not finish in three weeks, and the
journals that advertise it are the ones the integrity screen exists for.

**A high acceptance rate is a weak negative.** A 70% acceptance rate says the journal
is not selecting; that is a fact about the journal's standing, not a convenience.

**Unknown is neutral, never zero.** Missing turnaround, acceptance rate or APC scores
0.5 of the component. Absent data should not be punished as if it were bad data —
most journals simply do not publish these numbers.

**EISZ discounts the APC by 75%, and does not zero it.** The roster is unverified,
the deals are quota-capped, and quotas run out in the autumn. Modelling it as a
certain waiver would produce a confident wrong answer about money.

## Overriding

The weights are the `WEIGHTS` dict at the top of the scoring section in
`scripts/jmatch.py`. Change them when the user's situation actually differs — a
habilitation file where only D1 counts, a thesis deadline where speed dominates —
and **say in the output that you changed them, and to what.** A score computed on
custom weights that is presented as the default score is worse than no score.

Prefer a hard filter to a re-weight where the constraint is genuinely binary. "Must
be Scopus-indexed" is `--require-scopus`, not "raise the indexing weight".

## What the score cannot see

Ranked lists invite exactly the errors they cannot detect. Check these by hand,
every time:

- **Article type.** The journal may take none of the type on offer.
- **Readership.** Topic-adjacent and audience-wrong is still wrong.
- **Editorial appetite right now.** A journal that just ran a themed issue on the
  topic may be full of it, or may be actively looking for more. Only the calls sweep
  and the recent tables of contents show this.
- **Conflicts.** A competitor on the editorial board is a real consideration.
- **The invitation on the table.** A genuine guest-editor invitation usually beats a
  higher-scoring cold submission.
