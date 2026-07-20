# Coverage plan — no question left without a good answer

## The goal, stated so it can't backfire

Every question should get an answer that is **true**. That is not the same as every
question getting a claim. A corpus that genuinely doesn't discuss something must be able
to say so — "the sources don't cover this" is a good answer, and the only honest one.

The failure mode this plan exists to avoid: pressure to always produce an answer is
exactly what makes a system fabricate. The first benchmark run scored "100% saved" on
0/11 endpoints, because returning nothing was scored as costing nothing. A skill that may
never say "no" would have reported that as a triumph.

So: **maximize coverage, keep the honest floor.**

## The mechanism: escalate to the source, never invent

The skill already has the source on disk. If retrieval misses, don't guess — go get it.
Each tier costs more and is only paid when the tier above came up empty:

| tier | cost | when |
|---|---|---|
| 1. claim query | ~60 tok | always first |
| 2. grep the corpus for the question's identifiers | ~200 tok | tier 1 returned nothing |
| 3. read the best-matching region | ~500 tok | tier 2 located something |
| 4. "the corpus does not answer this" | ~10 tok | nothing found — a real answer |

This makes the worst case equal to the baseline (reading), never worse. Every question
gets a grounded answer or an honest refusal. Saving shrinks on misses; correctness does not.

## Measured status (2026-07-17)

| corpus | endpoints | saved | note |
|---|---|---|---|
| small | 1/2 | 91.9% | one small file — plain Read is legitimately competitive |
| medium | 4/5 | 99.7% | was 0/5 before the retry fix |
| large | 2/4 | ~99% | prose coverage gaps, see below |

**7/11 overall.** The medium jump (0/5 → 4/5) was entirely the `call_ollama` sys.exit bug:
one transient model eviction killed a whole run and left 8 of 10 files unmemoized. Fixed;
it now retries per chunk and refuses to write an empty memo.

## Root causes of the 4 remaining failures

1. **Tables are invisible to the prose profile.** "which local model is used for prose"
   failed although the fact is at `local-model.md:35` in a markdown table row. The prose
   chunker splits on headings and the model doesn't claim from table rows. Tables are the
   most fact-dense thing in technical prose — this is the biggest single gap.

2. **Claim density is too low.** "how is trivia filtered out" and "how are claims
   anchored" returned claims, just not the right ones. The anti-trivia fix over-corrected:
   "at most 6 claims per chunk" plus "no claims is a correct answer" made the model terse.
   The code-side trivia filter is doing the real work now, so the prompt can be loosened.

3. **Small corpus.** Not a bug. One file, two questions — the economics gate already says
   don't use the skill here. The benchmark should report this rather than fix it.

## Plan, in order of measured value

1. **Table extraction** (`--profile prose`): detect markdown/rst tables, chunk each table
   separately, and prompt for one claim per row. Rows are already atomic facts with
   natural anchors. Expect this alone to fix the `llama3.1` class of failure.

2. **Loosen the claim cap**: raise "at most 6" to "at most 10 for dense chunks", drop the
   "no claims is correct" line for chunks that contain definitions or tables. Keep the
   code-side filter — it is what makes loosening safe. Re-measure trivia share; if it
   climbs above ~5%, extend `TRIVIAL_TEXT_RE` rather than re-tightening the prompt
   (tested: tightening the prompt makes a 7B model worse, not better).

3. **Escalation tiers 2-4 in `memo_query.py`**: `--escalate` flag. Tier 2 greps the corpus
   for the question's high-IDF identifiers; tier 3 prints the best region; tier 4 says
   plainly that the corpus is silent. Report which tier answered, so the token cost is
   never hidden.

4. **Coverage audit** (`memo_db.py --gaps`): list sources whose claim density is far below
   the corpus median. Those are the files where generation half-failed. Cheap to compute,
   and it makes gap #1-style failures visible before a query hits them.

5. **Re-benchmark** after each step. The number that matters is endpoints passed, not
   tokens saved — a saving figure without a pass rate next to it is the metric that
   already lied once.

## What is explicitly not in the plan

- Lowering the utility floor or the trust gate to make more claims surface. Refuted and
  vague claims are excluded for a reason; surfacing them would raise the pass rate by
  answering with junk.
- Letting the model answer from parametric knowledge when the corpus is silent. The whole
  point is that claims are grounded in the source.
