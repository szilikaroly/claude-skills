# Protocol and schema

## Why this shape

Ask five models the same question and you get five confident answers and no way
to tell which to believe. Averaging them rewards the most fluent, not the most
correct. This protocol is built around three ideas:

**Independence before influence.** Round 1 is blind. Models are sycophantic — a
panelist that sees "the others said yes" tends to say yes. So the first vote is
committed in isolation, and only then does the council see itself.

**The duty to argue both sides.** A panelist may not simply vote. It owes the
strongest honest case *for* and *against* every claim, including the side it
rejects. This is the core of the design: it forces each model to generate the
evidence that would sink its own position, which is exactly the evidence a
confident model otherwise never surfaces. A panelist that supplies only one
side has failed its duty, and its vote on that claim is **discarded** — not
counted as uncertain, discarded, because an unargued vote is an assertion.

**Facts are not votes.** Where a claim can be settled by computation or by
looking a citation up, the panel's opinion is irrelevant. Those checks run
outside the models and override them.

## The five phases

| Phase | What happens | Who decides |
|---|---|---|
| 1. Propose | Each panelist independently decomposes the question into atomic falsifiable claims, with confidence and citations. Blind. | panelists |
| 2. Merge | Near-duplicate claims across panelists are merged (token Jaccard ≥ 0.6). Co-proposal grants no vote. | R |
| 3. Adversarial review | Every panelist supplies pro + contra + verdict + confidence on every claim. | panelists, policed by R |
| 4. Deterministic checks | R-testable claims get code written, screened and executed in a subprocess. All citations resolved against Crossref and Europe PMC. | R alone |
| 5. Deliberate & score | Panel sees the full record including check results, may revise. Weighted consensus, Fleiss' κ, Krippendorff's α, classification, persistence. | panelists then R |

Rounds 2+ repeat phase 3 with the record visible. The latest round is
authoritative; earlier rounds stay in the database.

## Classification

Set by `classify_claim()` in `R/validate.R`:

- **validated** — score ≥ threshold (default 0.70), ≥ 2 valid votes, no failed R check
- **refuted** — score ≤ −threshold, **or** the R check failed regardless of votes
- **contested** — panel split with a live refutation, or every supporting citation failed to resolve
- **unresolved** — fewer than 2 valid votes, or collective uncertainty

`consensus_score` ∈ [−1, +1]:

```
score = Σ(direction × weight × confidence) / Σ(weight × confidence)
```

`direction` is +1 support, −1 refute, **0 for uncertain** — so uncertainty
dilutes toward zero rather than cancelling out, and a panel that is collectively
unsure cannot validate anything.

`weight` is the panelist's calibration weight, clamped to [0.25, 2.0] so one bad
session cannot silence a panelist and one good streak cannot let it rule.

## Agreement statistics

Both are reported; neither gates anything.

- **Fleiss' κ** — chance-corrected agreement across panelists over claims.
  Landis & Koch bands are printed for orientation only.
- **Krippendorff's α (nominal)** — handles missing ratings natively. Prefer it
  when a panelist errored out mid-run, which κ handles badly.

Low agreement means the question is genuinely open. That is information, and the
report says so rather than hiding it behind a number.

## Calibration

After every session, each panelist's vote on each settled claim is compared to
where the claim landed, and a Brier score is accumulated. Weight is
`clamp(accuracy × 2, 0.25, 2.0)`. Note the circularity: "correct" means "matched
the council's own conclusion", so calibration measures conformity with the
council, not truth. It is a useful signal about a chronically contrarian or
chronically agreeable panelist — it is not a leaderboard, and it should not be
read as one.

## Citation verification

`R/evidence.R`. Both APIs are keyless and free; be polite with them.

1. A **DOI** is looked up in Crossref, then Europe PMC. If neither resolves, the
   citation is fabricated — recorded as such, permanently.
2. A **PMID** is looked up in Europe PMC (a bare 6-8 digit citation counts as one).
3. **Free text** gets a title search. This can never mark a citation `verified`;
   at best it is "plausible but unconfirmed". An identifier-free citation is not
   evidence.

Then, separately, **relevance**. Resolving an identifier only proves the paper
exists — and models cite real papers from entirely the wrong field. An early test
run cited PMID 29669923 (a paper on protein folding) in support of a blood-pressure
claim; it resolves perfectly. So each resolved citation is judged for topical fit
against its claim by a local Ollama model, with lexical overlap as fallback
(`judge_relevance()`), and **both** `verified` and `relevant` are required before a
citation counts as evidence. A claim whose every citation fails this gate is marked
`contested` no matter how the panel voted — which is why a `contested` verdict can
sit next to a unanimous vote. `claims.status_reason` always records which rule fired.

In the first hypertension session, 7 of 22 DOIs offered by panelists did not exist.
That number is the honest measure of what this layer is for.

## What R checks can and cannot settle

This matters more than it looks, and the first real sessions made it obvious.

A check is only worth running when the claim is **decidable by computation**:

- **Mathematical / probabilistic** claims (a Monty Hall win rate, a distribution's
  property, an algebraic identity) — R settles these outright, and the check can
  genuinely fail. This is where the layer earns its keep.
- **Statistical-methodology** claims ("a null meta-regression coefficient shows
  there is no mediation") — R can settle whether the *inference* is licensed, e.g.
  by computing the power of the cited test. A low-power finding turns a confident
  "no effect" into "under-determined", which no amount of panel voting would catch.
- **Recomputation** from data actually present — recompute the statistic and compare.

A check is worthless, and worse than nothing, when the claim is **empirical about
the world** ("aerobic exercise lowers SBP by 5-9 mmHg") **and no real data is at
hand**. A model asked for a check will happily *simulate* data with the effect
built in and report `passed = TRUE`. That is circular: it verifies the simulator's
assumption, not the world. Such checks must return `passed = NA`.

The exception, and the reason the Claude Science link matters: when checks run in
`~/.claude-science/conda/envs/r-stats-methodologist` (auto-detected by
`check_rscript()`), **metafor** and **metadat** are available, and `metadat` ships
real published meta-analytic datasets. An empirical claim that one of those
datasets actually covers can then be checked for real — fit the random-effects
model, compare the pooled estimate to what the claim asserts. Verified working: a
check fitting `rma()` on `metadat::dat.bcg` (13 BCG trials) returns pooled
log RR = -0.715, p = 7.05e-05 in ~4 seconds. The prompt instructs the author to use
a real dataset when one genuinely matches and to return `NA` rather than substitute
a simulation when none does.

The prompt in `RCODE_PROMPT` says so explicitly ("code that always passes is
worthless"), but models still reach for simulation. **Treat any `passed = TRUE`
on an empirical claim as a red flag and read the code** — `r_checks.code` is
stored for exactly this reason. Empirical claims are meant to be carried by the
citation layer, not by R.

## Who may author a check

A failing check refutes a claim outright, overriding the entire panel. That
authority is only safe if the code is competent, and small local models are not.

Observed, not hypothetical: asked to check *"the probability of winning by staying
is 1/3"* (provably true), `llama3.1:8b` wrote code with no relationship to the
Monty Hall problem — it sampled `rbinom(1, 2, 0.33)` and compared maxima of
unrelated draws — and returned `passed = FALSE`. Under "facts outrank votes" that
garbage would have refuted a true claim, with veto power over three panelists.

So `pick_check_author()` only accepts a frontier seat (`SCICOUNCIL_CHECK_AUTHORS`,
default `claude,openai,gemini,deepseek,xai`). If the panel has none, **no checks
run at all** and the session says so. An unchecked claim is honest; a wrongly
refuted one is not.

**Being on the panel is not enough — the seat must have spoken.** Also observed:
a session where Gemini was on the panel but exhausted its daily quota. It was
still chosen as check author, and all five checks died with HTTP 429 — five
claims that could have been settled went unchecked while the log implied checks
had been attempted properly. `run_checks()` now takes a `live` set (seats that
actually produced a proposal or a review this session) and picks the author only
from it. When a frontier seat is present but mute, the session says exactly that
rather than silently failing every check.

**Bridge-authored code arrives double-encoded.** Observed on a live run: every one
of twelve checks authored through the Claude bridge failed with
`unexpected ','` because the sandbox received the raw envelope
(`{"code": "...", "explanation": "..."}`, escapes intact) instead of the R inside
it. The same code ran fine when passed to `run_r_check()` directly, which is what
made it slow to spot — the failure lived in transport, not in the check. `run_r_check()`
now calls `unwrap_code()` first: if the payload is a JSON object carrying a `code`
field, it unwraps it. Diagnose this class of failure from `r_checks.error`, which
stores the sandbox's stderr verbatim; the log line only ever shows `N/A`.

This does not make checks trustworthy, only less dangerous. The code is stored in
`r_checks.code` for every check. Read it before believing a refutation.

## R check sandbox

`run_r_check()` runs LLM-written code in a separate `Rscript --vanilla` process
with a timeout and a static deny-list (`system`, `unlink`, file writes, network,
`install.packages`, `eval(parse(...))`, …). The code must assign
`result <- list(passed = , detail = )`.

This is a guardrail against a model doing something careless, **not a security
boundary**. A determined adversary can defeat a regex. Never run a session on
untrusted input; set `SCICOUNCIL_ALLOW_RCODE=false` if in doubt, and claims will
go unchecked rather than be wrongly validated.

## Statistics generation (R as a maker, not only a judge)

Validation asks "is this claim defensible?". Statistics generation asks "what do
the numbers actually say?" — a separate job, and the reason R owns the whole stack
rather than merely refereeing.

`R/stats.R` (library) + `R/stats_runner.R` (entry point) run in the
`r-stats-methodologist` specialist env and produce, from any session:

1. **Descriptive statistics** — consensus mean/sd/median/range, validated share,
   verdict counts, R-check pass/fail/inconclusive, citation resolution rates.
2. **Four ggplot2 figures** — consensus per claim (diverging, coloured by status),
   vote composition, panelist calibration weights, citation grounding.
3. **A self-contained HTML report** — figures base64-embedded as data URIs, so the
   file is portable with no asset directory.
4. **Optional random-effects meta-analysis** — `council_meta()` wraps
   `metafor::rma()` (REML) and draws a forest plot, reporting pooled estimate, CI,
   p, I² and τ². Driven by a JSON spec naming either a `metadat` dataset with a
   column mapping, or raw `yi`/`vi` vectors.

**Why the JSON hand-off.** The stats env ships ggplot2/metafor/metadat but *not*
duckdb. Rather than force a package into a curated environment, system R exports
the session with `export_session_json()` and the specialist env reads that. The
two R worlds share a file format, not a library set — so either side can be
swapped without touching the other.

Charts degrade individually: a figure that fails to render is skipped with a
message rather than killing the report, because a session with one odd-shaped
table should still yield the other three figures.

## Schema

```
questions(id PK, question, domain, context, status, panel, rounds, created_at, finished_at)

claims(id PK, question_id → questions.id, claim, claim_norm, claim_type, proposed_by,
       testable_in_r, status, consensus_score, agreement_kappa, krippendorff_alpha,
       n_support, n_refute, n_uncertain, r_check_passed,
       citations_verified, citations_total, final_round, created_at, validated_at)

arguments(id PK, claim_id, panelist, model, round, stance ∈ {pro, contra},
          argument, evidence, strength, created_at)      -- both stances, every panelist

verdicts(id PK, claim_id, panelist, model, round, verdict ∈ {support, refute,
         uncertain, discarded}, confidence, rationale, revised, created_at)

r_checks(id PK, claim_id, author, code, passed, result, error, runtime_sec, created_at)

evidence(id PK, claim_id, panelist, source_type, citation, doi, pmid, url,
         verified, verify_note, resolved_title, year,
         relevant, relevance_note, created_at)
         -- verified = the identifier resolves; relevant = the paper is on-topic.
         -- BOTH are required before a citation counts as evidence.

calibration(panelist PK, model, n_votes, n_correct, brier_sum, weight, updated_at)

claim_links(from_claim, to_claim, relation ∈ {supports, contradicts, depends_on}, note)
```

Append-only in spirit: `claims` and `questions` are upserted per session, but
arguments, verdicts, checks and evidence only ever accumulate. FTS index on
`claims.claim`, rebuilt after each run (DuckDB's FTS index is a snapshot, not
live — a claim inserted without a reindex is invisible to `match_bm25`, which is
why `db_search()` falls back to `LIKE`).

## Known limits

- **Correlated panelists.** These models share training data and failure modes.
  Agreement is evidence, not proof — a claim the whole industry gets wrong will
  sail through. The R check and the citation lookup exist precisely because
  votes cannot catch this.
- **The claim merger is lexical.** Token Jaccard misses paraphrases that share
  no vocabulary; two panelists can end up arguing the same claim in two seats.
- **Calibration is conformity, not accuracy.** See above.
- **Round 2 anchoring.** Deliberation reintroduces the influence that round 1
  avoided. The prompt tells panelists to hold a well-evidenced minority position,
  but it cannot force them to.
