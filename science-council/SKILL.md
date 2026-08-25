---
name: science-council
description: Convene a multi-model scientific council (Claude, ChatGPT, Gemini, xAI, DeepSeek, local Ollama) that debates a question under an adversarial protocol — every panelist must argue both FOR and AGAINST every claim — validates the survivors with deterministic R checks and real citation lookups (Crossref/Europe PMC), and stores the validated claims in an indexed DuckDB knowledge base. R is the orchestrator, the statistics engine and the database layer. Use when a question deserves more than one model's opinion: scientific/medical claims, "is this actually true?", contested evidence, literature-grounded reasoning, meta-analysis-style synthesis, or when the user asks to cross-check models against each other, wants claims validated rather than generated, or wants to search/extend the validated claim store. Hungarian triggers — tudományos állítás ellenőrzése, validálás, konszenzus, több modell megbeszélése, tények mellett és ellen, indexált adatbázis.
---

# Science Council

A deliberation harness where several independent LLMs act as panelists on a
scientific question, and **R** is the chair: it fans out the calls, enforces the
protocol, runs the arithmetic, and owns the evidence store.

The premise: a single model's answer is an opinion. An answer that survived
independent proposal, mandatory two-sided argument from every panelist,
recomputation in R, and citation resolution against Crossref/Europe PMC is
something you can build on. Only the latter is written to the database.

## Ground rules the protocol enforces

1. **Round 1 is blind.** Panelists commit before seeing each other — no anchoring.
2. **Right and duty to argue both sides.** Every panelist owes a *pro* AND a
   *contra* on every claim, including claims it intends to vote against (or for).
   A panelist that supplies only one side has its vote on that claim **discarded**
   (`R/council.R`, `discharged` flag).
3. **Facts outrank votes.** An R check that fails refutes a claim no matter how
   the panel voted. A DOI that does not resolve marks its claim contested.
4. **Dissent is recorded, not smoothed away.** Surviving minority positions stay
   in the report and the database.
5. **Only validated claims enter the store** — with their full provenance.

## Quick start

```bash
cd ~/.claude/skills/science-council

./bin/council doctor                          # who is reachable right now
./bin/council science                         # Claude Science envs + connectors
./bin/council run --question "..." --rounds 2 # convene
./bin/council search --query "..." --status validated
./bin/council report --qid q_xxx --out report.md   # full deliberation record (md)
./bin/council stats-report --qid q_xxx        # R statistics: charts + HTML
./bin/council stats                           # store + panelist calibration
```

Always run `doctor` first if anything looks off — it live-probes every provider,
the R sandbox and the citation APIs, and it is the fastest way to see which
panelist is down.

## How to drive this as Claude

**Pick the panel deliberately.** `--panel` takes seats, not just providers:
`gemini,ollama/llama3.1:8b,ollama/qwen2.5-coder:7b` seats three distinct minds, two
of them free and local. A seat is `provider` or `provider/model-override`. Two
reachable seats is the minimum; three or more makes the agreement statistics
meaningful. **Include at least one frontier seat** (claude/openai/gemini) — without
one, no R checks run at all, by design: a small local model asked to author a check
writes code that refutes true claims, and a failing check overrides the whole panel.
An all-local panel still debates and still gets its citations checked.

**Sessions are slow by design** (minutes: N panelists × claims × rounds, plus
local model latency). Launch with `run_in_background: true`, then read the log.
Never wrap it in a short timeout. Do **not** wait on it with
`until ! pgrep -f "council run"` — that pattern matches the waiting shell's own
command line and hangs forever. Wait on the task notification, or `pgrep -f Rscript`.

**Report honestly.** Lead with what got validated *and* what got refuted or
contested — a session where the panel disagreed is a result, not a failure. Cite
the consensus score and κ. If a citation failed to resolve, say so plainly: that
is a model having fabricated evidence, and it is the single most valuable thing
the run found.

**Do not launder the output.** Never present a `contested` or `unresolved` claim
as settled, and never fill a gap with your own knowledge without labelling it as
yours rather than the council's.

### Claude's own seat (bridge mode)

`doctor` reports a **Claude reachability mode**:

- `api` — `ANTHROPIC_API_KEY` is set. Claude runs autonomously inside R. Best.
- `cli` — the `claude` CLI is logged in (`claude /login` in a terminal). Also autonomous.
- `bridge` — neither. R writes each Claude prompt to `runs/bridge/req_*.json` and
  blocks; **you**, the driving session, answer it.

Bridge mode is the only way Claude gets a seat without a key — and it is where
Claude earns its place, because you can ground your answers with WebSearch, the
`deep-research` skill and any connected Life Sciences connectors, which no other
panelist can do. To serve the bridge:

1. Launch the run in the background with `SCICOUNCIL_BRIDGE_ACTIVE=1` and
   `claude` in `--panel`.
2. Poll `runs/bridge/` for `req_*.json`. Read the file: it has `prompt`,
   `system`, `want_json`.
3. Answer it *as a panelist under the protocol above* — research the claims
   properly, argue both sides, never invent a DOI.
4. Write `runs/bridge/resp_<id>.json` containing `{"text": "<your JSON answer>"}`,
   where `<id>` is the request's id. R picks it up within a second.

Requests time out after `SCICOUNCIL_BRIDGE_TIMEOUT` (default 1200s), so serve them promptly.
`./bin/council bridge --status` lists pending requests; `--clear` empties the queue.

## What comes back

`council run` prints each claim with its status, consensus score and vote split,
then the session id. `report` expands that into the full record: every panelist's
pro and contra, every vote and rationale, the R check code and its result, and
each citation with its resolution status.

- `validated` — panel consensus ≥ threshold, no surviving refutation, any R check passed
- `refuted` — consensus against, **or** the R check failed (arithmetic wins)
- `contested` — real disagreement, or the supporting citations do not resolve
- `unresolved` — too few valid votes, or the panel is collectively uncertain

Every claim carries a `status_reason` saying which rule fired — read it. A
`contested` verdict sitting next to a unanimous vote is not a bug: it means the
panel agreed but the evidence it offered does not exist.

`consensus_score` ∈ [-1, +1] is confidence-weighted and calibration-weighted:
a panelist whose past votes matched the settled outcome carries more weight
(`calibration` table, weight ∈ [0.25, 2.0]). Fleiss' κ and Krippendorff's α
report how much the panel actually agreed — α is the honest one when a panelist
dropped out mid-run.

## Two things worth knowing before you trust a session

**Citations are checked twice.** A DOI/PMID must *resolve* (Crossref, Europe PMC)
**and** be *on-topic* for its claim — models cite real papers from the wrong field,
and a real DOI about protein folding is not evidence about blood pressure. Both
gates must pass before a citation counts. In the first live session, 7 of 22
offered DOIs did not exist at all. Expect this; it is the point.

**R checks only bite on decidable claims.** Mathematical and probabilistic claims
(and questions about what a statistical method can show) are genuinely settled by
computation. Empirical claims about the world usually are *not* — unless real data
is at hand (see below), a model asked to check one will cheerfully simulate data
with the effect baked in and report `passed = TRUE`. That is circular. A
`passed = TRUE` on an empirical claim is a red flag: read `r_checks.code`.

## Claude Science ↔ R

If Claude for Life Sciences is installed locally (`~/.claude-science/`), checks
automatically run in its **`r-stats-methodologist`** conda environment instead of
system R — detected by `check_rscript()`, overridable with `SCICOUNCIL_RSCRIPT`.

This is not cosmetic. That environment ships **metafor**, **meta** and **metadat**,
and `metadat` carries *real published meta-analytic datasets*. So a check can fit an
actual random-effects model on actual trial data rather than simulating its own
answer — the difference between evidence and circularity. The check prompt tells
the author which packages exist and instructs it to use a real dataset when one
genuinely matches, and to return `NA` rather than substitute simulated data when
none does.

`./bin/council science` prints the whole picture: install path, daemon state, every
specialist env with the interpreters it actually has and the roles bound to it, and
the authorized connectors.

Two specialist roles are wired beyond the R stats env:

- **`crosscheck` → `chirality-data`** (scipy, statsmodels, patsy). `science_crosscheck_ancova()`
  re-fits an ANCOVA in statsmodels and returns the group-effect p-value. Because the
  checks and the statistics layer both run in R, a mis-specified model would reproduce
  itself rather than show up; a second implementation catches that. Verified: statsmodels
  reproduces this project's p-values to four decimals (0.5137, 0.2008).
- **`biolit` → `case-report-assistant`** (biopython/Entrez, python-docx, reportlab) and
  **`figures` → `figs`** (matplotlib) are bound and available, though the citation layer
  currently uses Crossref/Europe PMC directly and charts come from ggplot2.

Five of the eleven envs on a stock install are half-provisioned — the interpreter file
exists but is not executable. `science_env_bin()` tests the execute bit rather than mere
existence, so those are reported as empty instead of being routed to and failing at the
point of use.

Claude's own seat is the other half of the link. Through bridge mode it brings web
search and the authorized Life Sciences connectors into the debate, which no
API-only panelist can do — and the bridge request now *tells* the serving session
which connectors are live (`science_connector_briefing()` in `R/science.R`), so the
Claude seat is instructed to ground claims in the user's own Drive/Microsoft 365
documents and in BioRender figures rather than reasoning from memory. The request
JSON carries a `connectors` array listing them.

## R as a statistics maker

Validation is one job; **generating statistics is the other**. `R/stats.R` +
`R/stats_runner.R` turn a session into real analysis, executed in the
`r-stats-methodologist` env (ggplot2 4.x, metafor, meta, metadat):

```bash
./bin/council stats-report --qid q_xxx                    # charts + HTML report
./bin/council stats-report --qid q_xxx --meta spec.json   # + real meta-analysis
./bin/council run --question "..." --stats                # generate it inline
```

Output lands in `runs/<qid>/`: four ggplot2 PNGs (consensus per claim, vote
composition, panelist calibration, citation grounding) and a **self-contained
HTML report** — PNGs base64-embedded, no external files, opens anywhere — with
descriptive statistics, agreement coefficients and KPI tiles.

Pass `--meta` a JSON spec to fit a genuine random-effects model and draw a forest
plot. Either a `metadat` dataset with a column mapping:

```json
{"dataset":"dat.bcg","measure":"RR","ai":"tpos","bi":"tneg",
 "ci":"cpos","di":"cneg","slab":"author"}
```

or raw effect sizes: `{"yi":[...],"vi":[...],"slab":[...]}`. The report then
carries pooled estimate, CI, p, I², τ² and the forest plot. Verified: `dat.bcg`
(13 BCG trials) → pooled log RR −0.715 [−1.067, −0.362].

Because the stats env has no `duckdb`, system R exports the session via
`export_session_json()` and the specialist env reads that JSON — the two R worlds
stay cleanly separated. `council_meta()` in `R/stats.R` is callable directly for
any dataset, independent of a council session.

## Specialist capabilities

Beyond running R in `r-stats-methodologist`, three specialist environments carry
capabilities the council could not otherwise reach (`R/specialists.R`):

- **`biolit` → PubMed via biopython/Entrez.** Not a duplicate of the Crossref and
  Europe PMC lookups: Entrez returns MeSH terms and *publication types*, which answer
  a question the harness could not previously ask — **what kind of evidence is this**.
  `specialist_evidence_level(pmids)` classifies each citation on a six-level hierarchy
  (systematic review → RCT → non-randomised trial → observational → case report →
  narrative). A claim carried by a randomised trial and the same claim carried by a
  case report are not equally supported; until now they were treated identically.
  `specialist_pubmed_search(query, pubtype = "Randomized Controlled Trial")` filters by
  design, which Crossref cannot express.
- **`crosscheck` → statsmodels.** Re-fits an ANCOVA in a second implementation. Because
  the checks and the statistics layer both run in R, a mis-specified model would
  reproduce itself rather than show up.
- **`figures` → matplotlib.** A fallback renderer for installs with python but no R.
  ggplot2 is the better one and is what `stats-report` uses.

`./bin/council science --probe` tests each rather than assuming it. Three envs are
deliberately *not* wired: `compute-provider-modal` needs a Modal account and would fail
at the point of use for most people, `claude-science-mcp` is the daemon's own server,
and five envs on a stock install are half-provisioned (interpreter present, not
executable).

## Petals — the human elements of a manuscript

```bash
./bin/council petals --question "..." --material facts.txt [--qid q_xxx] [--out petals.md]
```

Scientific prose has been sanded smooth: the passive voice, the absent author, the
result that "was observed". Somebody was surprised. Somebody noticed a rash and ordered
a test that changed a patient's life. Somebody had been wrong in the previous draft.
Removing that does not make a paper more objective — it removes the reasoning that
produced the finding. A **petal** is one short, true, human passage attached to one
place in a manuscript.

Six kinds: `motivation` (why this was really attempted), `surprise` (including being
wrong earlier), `clinical_narrative` (a patient whose course carried information),
`uncertainty` (a doubt genuinely held), `patient_impact` (the concrete consequence),
`craft` (a judgement call and its cost).

**The guard is structural, not advisory.** "Make it human" can mean restoring what was
stripped out, or manufacturing sentiment that never happened — and a reader cannot tell
the two apart on the page. So a petal is never accepted for reading well. Each must
carry an **anchor**: a specific fact, event or record, of a kind that can be checked.
`petal_verify()` rejects a petal with no anchor, one whose text states figures its
anchor does not contain (the commonest drift — a real 92.7% becoming a rounder 98.4%),
and one claiming to come from the council record when no claim matches. Anchors of kind
`record` cannot be machine-checked — only the clinician knows whether a consultation
happened as described — so those are rendered flagged **[needs author confirmation]**
rather than silently trusted.

Rejected petals are rendered too, struck through with the reason, so the same idea is
not reintroduced later without evidence.

### How petals behave as a set

```bash
./bin/council petals-interact --qid q_xxx [--tension]
```

Verifying petals one at a time is not enough, because three things only appear in the
composition:

- **Redundancy** — several petals drawn from one event, so a single case carries a
  third of the manuscript's human weight.
- **Crowding** — petals stacked into one section, which turns the Discussion into a
  sequence of set pieces with the argument lost between them.
- **Composed overclaim** — the one that matters. Every petal is true and the portrait
  they compose is not. Passages in the register of *we noticed, we were careful, we
  admitted our error* add up to a claim about the authors that no single petal makes
  and no anchor supports.

Demonstrated on this project's own six-petal set: keeping only the three petals that
credit the authors — each individually anchored and each passing verification —
produces *"every petal credits the authors and none costs them; a set this flattering
reads as curated, whatever each anchor says."* No per-petal check can catch that,
because nothing is wrong with any petal.

`petal_valence()` scores whether a petal costs the authors something or credits them; a
healthy set carries both. The analysis also flags anchor concentration, section
imbalance, and petals resting on a single patient each — in this design the individuals
with the most quotable courses are usually the ones who left the study.

`--tension` adds the one judgement code cannot make: whether two passages undercut one
another (*"every record was checked"* beside *"we found four duplicates late"*). Two
guards, both learned here:

- **Frontier seats only.** Asked to judge three test pairs, `llama3.1:8b` flagged all
  three at severity 1.0 including two unrelated ones — the same failure that bars local
  models from authoring R checks. Override with `SCICOUNCIL_TENSION_ANY_SEAT=1`.
- **An unreachable model is not a clean result.** The first version returned "none" for
  every pair while Gemini was serving 503s, and the output was indistinguishable from a
  manuscript with no tension in it. `petal_tension()` now reports `judged` and `failed`
  separately and says plainly when nothing could be judged.

`--material` is the only permitted source of fact. Pass real things: verified
statistics, the council record, documented clinical events. What is not in that file
cannot legitimately appear in a petal.

## The rule of the assembly

```bash
./bin/council assembly --claim "..." --material facts.txt [--panel a,b,c] [--out record.md]
```

One claim, the whole panel, every petal lens, then cross-examination — the full
argument from everyone in a single markdown record.

**No panelist may lie or invent data. Every panelist may foreground one fact over the
others and read it differently.**

The second half is not a loophole. Honest scientific disagreement almost never turns on
the facts being different; it turns on which fact is treated as decisive. Two truthful
readers of the same cohort can disagree about whether a 93.2% completion rate or four
unexplained discontinuations settles the question. Forbidding that would produce not
rigour but unanimity — panelists reciting the same safe reading.

What the rule needs is a companion, because emphasis without disclosure is exactly how
cherry-picking works: cite the supporting facts, omit the ones that cut the other way,
invent nothing, mislead completely. So each panelist must declare **what it foregrounded
and what it set aside to do so**, and the record prints that declaration beside the
argument rather than burying it.

The emphasis map then shows three patterns, of which only the last is a fault:

- **Shared** — panelists foreground the same fact. Agreement about what matters, even
  where the verdicts differ.
- **Divided** — panelists foreground different facts. This is the disagreement in its
  clearest form, and usually more informative than the votes.
- **Unclaimed** — a fact someone set aside and nobody foregrounded. No one lied and no
  one argued from it either, so it leaves the assembly unexamined.

Demonstrated live on this project: the two local seats foregrounded the same fact
(overlap 0.69) while Claude foregrounded a different one (0.04) — and three facts were
set aside by someone and claimed by no one, including the zero-intolerance figure that
the claim under examination actually rests on. The votes were unanimous; the emphasis
map showed the panel was not.

**One defect the first run exposed.** The cross-examination record initially carried
verdicts, arguments and lenses but *not* the emphasis declarations, so panelists could
attack a conclusion but not its weighting. A declaration nobody can see is not a
disclosure. `assembly_format_record()` now circulates it, and round 2 explicitly asks
each panelist to say whether another's ordering is defensible.

## The store

DuckDB at `db/council.duckdb`, FTS index on claim text (rebuilt after each run).
`questions` → `claims` → (`arguments`, `verdicts`, `r_checks`, `evidence`),
plus `calibration` and `claim_links`. Nothing is deleted; a claim's history is
reconstructible. Query it directly for anything the CLI does not cover:

```r
con <- DBI::dbConnect(duckdb::duckdb(), "db/council.duckdb", read_only = TRUE)
DBI::dbGetQuery(con, "SELECT claim, consensus_score FROM claims WHERE status='validated'")
```

Full schema and protocol rationale: `references/protocol.md`.

## Configuration

`.env` next to this file, `chmod 600`, git-ignored. `SCICOUNCIL_PANEL`,
per-provider keys and models, `SCICOUNCIL_MAX_ROUNDS`, `SCICOUNCIL_CONSENSUS_THRESHOLD`,
`SCICOUNCIL_ALLOW_RCODE`. Adding a provider is a key plus a line in the panel list;
xAI and DeepSeek are wired and idle, waiting for keys.

Claude Science plumbing: `CLAUDE_SCIENCE_HOME` (default `~/.claude-science`) and
`SCICOUNCIL_RSCRIPT` to pin a specific Rscript, overriding specialist-env routing
for both checks and statistics.

## Safety

- **R checks execute model-written code.** A separate process, a timeout, and a
  static deny-list (no `system`, no network, no file writes) make this a guardrail
  against accidents — **not a security sandbox**. Never point a session at
  untrusted input. Set `SCICOUNCIL_ALLOW_RCODE=false` to disable entirely; claims
  then simply go unchecked rather than failing.
- **Cost.** Hosted panelists bill per session. Local Ollama seats are free —
  prefer them for exploratory runs.
- Keep `.env` out of any repository. If a key ever lands in a chat or a log,
  rotate it.
