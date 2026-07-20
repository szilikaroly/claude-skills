---
name: memo-index
description: 'Cuts context-token spend on large or repeatedly-read material by generating compressed, line-anchored memos with a LOCAL model (zero API tokens), keeping only a tiny queryable index in context, and validating each claim separately — deterministically where possible, with one isolated subagent per remaining claim. Handles code, prose, tables (extracted row-by-row in code), and images/figures/charts/scanned pages (read by a local vision model). Use this whenever a task means ingesting more material than comfortably fits in context: reading a large codebase or many source files to answer a question, digesting a pile of papers/PDFs/web sources, extracting facts from tables or figures, working through a long document set, or any "read all of X and tell me Y". Also use when the user mentions token usage, context budget, context running out, "too expensive", memoizing/caching file summaries, or wants claims fact-checked against their sources. Do NOT use for a single small file read once — plain Read is cheaper.'
---

# memo-index

## Why this exists

Reading raw material into the main context is the single biggest token cost in most
long sessions, and it is paid *repeatedly* — the same file gets re-read in the next
session, and the one after that.

This skill moves that cost somewhere cheap. A local model (running on the user's own
machine, costing zero API tokens) reads the raw material and emits a **memo**: a short,
line-anchored set of atomic claims. The main context sees only a one-line index entry
per source, and pulls a full memo (~300-600 tokens) only when it turns out to be relevant.

The obvious objection: **small local models get things wrong, and a confident wrong memo
is worse than no memo at all.** That is the whole reason for the validation half of this
skill. Every claim carries a machine-checkable anchor, so most claims are verified by
`grep`, not by a model. Only genuinely semantic claims escalate to a subagent — one per
claim, in a fresh context that never sees the memo, so it cannot be talked into agreeing.

Read the two halves as a single bargain: *the local model is allowed to be sloppy because
nothing it says survives unverified.*

## The economics gate — check this first

This skill is a net loss on small jobs. Generating and validating a memo costs real
tokens; it only wins when it displaces a bigger cost. Before starting, sanity-check:

**Use memo-index when at least one is true:**
- Total raw material is roughly >2,000 lines / >80KB across the files in scope.
- The same material will be consulted more than once (across turns or sessions) — the
  memo is written to disk and survives, so the second read is nearly free.
- You need to *locate* something across many files and will only fully read one or two.
- The user explicitly asked to conserve tokens or fact-check claims.

**Skip it — just use Read — when:**
- It's a handful of small files you'll read once. Read is cheaper and exact.
- You need verbatim content (editing code, quoting precisely). Memos are lossy by design;
  never edit a file based on a memo, always Read the region first.
- The material is already dense and short (a config file, a README).

If it's borderline, say so in one sentence and pick. Don't run the whole pipeline to
save 3,000 tokens while spending 8,000 on validation.

## Setup: the local model

The pipeline needs a local model — by default two, since a code-tuned model reads
declarations well but flattens prose. Check once per session:

```bash
scripts/route.py --check
```

If it reports a model missing, run `scripts/setup_local_model.sh --install`. That
downloads and installs software, so **ask the user before running it** — state what gets
installed and how large it is. See `references/local-model.md` for model choice, sizing
by RAM, and what to do if the user declines.

If the user has no local model and doesn't want one, the pipeline still works: pass
`--generator=subagent` to `memo_gen.py`, which routes generation to a cheap subagent
instead. That costs API tokens but still keeps raw material out of the *main* context,
which is most of the benefit. Say plainly that this is the fallback.

## The pipeline

Four steps. Steps 1-3 are scripts; only step 4 spends model tokens, and only on what
survived step 3.

### 1. Scope and generate

Pick the file set deliberately — memoizing a whole repo including `node_modules` is how
this skill becomes the problem it was meant to solve. Then:

```bash
scripts/memo_gen.py --root <dir> --include 'src/**/*.ts' --dry-run   # check the gate first
scripts/memo_gen.py --root <dir> --include 'src/**/*.ts' --memo-dir .memo
```

`--dry-run` prints the projected saving and calls no model — use it to settle the
economics gate above with a number instead of a guess.

The real run is **slow — minutes, not seconds** (a local 7B model on ordinary hardware).
Run it as a background task rather than blocking on it; that latency is the trade you're
making, spending idle CPU instead of token budget.

`memo_gen.py` hashes each source, skips any file whose memo is already current, chunks
the rest, and has the local model emit claims chunk by chunk (chunking keeps line numbers
honest — a model asked to summarize 2,000 lines at once invents line numbers).

Files change. The memo stores the source's SHA-256, so a re-run regenerates only what
moved. Never hand-edit a memo — the hash will still say "fresh" and you'll be reasoning
from a stale claim.

### 2. Build the index, then QUERY it — do not read it

```bash
scripts/memo_db.py --memo-dir .memo --build     # keyword -> claim -> content index
scripts/index_build.py --memo-dir .memo         # human-readable INDEX.md mirror
```

**This is where the saving actually comes from, and it is easy to get wrong.** Reading a
memo costs its whole length even when one claim in it mattered. Querying costs only the
claims that matched:

```bash
scripts/memo_query.py --memo-dir .memo "how do sessions expire"
scripts/memo_query.py --memo-dir .memo --expand src/auth.ts:C3   # source, only if needed
```

Measured on a real corpus: reading every memo saved 80%; five queries answering real
questions cost ~59 tokens each and saved **97%**. The difference is entirely that you
never load what didn't match. So resist the pull to `cat` a memo — the memo files exist
to be human-readable and to be reindexed, not to enter your context.

A query returns claims already filtered by trust and utility: refuted claims cannot
surface at all (no flag overrides that — it's the hallucination guard), and vague ones
rank below specific ones. Read `references/retrieval.md` before tuning any of it.

The 97% is not a constant — it scales with corpus size. A query costs ~60 tokens whether
the corpus is 3 files or 3,000, so the ratio improves the more there is. On three small
files, plain Read may still win. The economics gate above is not a formality.

### 3. Deterministic validation (cheap — do this before any subagent)

```bash
scripts/verify_anchors.py --memo-dir .memo --json
```

Every claim is emitted with an anchor: a line number plus a literal snippet that must
appear at (or near) that line in the source. This script checks them by string match.
No model involved, so it costs nothing and catches the local model's most common failure
— confidently citing a symbol or line that doesn't exist.

Claims come out marked `CONFIRMED` (anchor holds), `REFUTED` (anchor points at something
else), or `NEEDS_AGENT` (the claim is semantic — "this module owns retry policy" — and
no string match can settle it).

Typically this resolves most claims for code and structured text, and fewer for prose,
where more claims are inherently semantic. If you see a file where nearly everything comes
back `REFUTED`, don't verify claim-by-claim — the local model lost the plot on that file.
Regenerate it, or just Read it and move on.

### 4. Semantic validation — hybrid, and scaled to stakes

Don't validate everything up front; that pays for claims nobody ever retrieves. Don't
validate nothing either; an unproven claim surfacing mid-answer stalls the work.

```bash
scripts/validate.py --memo-dir .memo --pending      # what's unproven, split eager/lazy
scripts/validate.py --memo-dir .memo --plan --eager # work orders for high-utility claims
scripts/validate.py --memo-dir .memo --plan --claims C3 --depth 3   # on demand
scripts/validate.py --memo-dir .memo --local --claims C3            # offline (gemma4)
```

`--plan` emits JSON work orders; fan them out as subagents, collect verdicts, apply with
`--apply`. Depth scales from utility: a passing mention gets one verifier, a claim you're
about to build a conclusion on gets three — each with a *different lens* (does the source
say it / does anything contradict it / does it hold at the edges). Three verifiers asking
the same question mostly agree with each other; that's redundancy, not rigor.

Verdicts merge biased toward doubt: one REFUTED outweighs any number of CONFIRMEDs, and
anything short of unanimous is UNSUPPORTED. A wrong CONFIRMED puts a false fact in an
answer; a wrong UNSUPPORTED costs one extra source read.

**Offline**, `--local` runs the same prompts through the local thinking model instead of
subagents. Slower and less sharp, but the pipeline never needs the network.

The rest of this section explains the isolation rule, which matters however you run it.

Only for the `NEEDS_AGENT` claims. Spawn them in parallel, one per claim.

The isolation matters more than anything else here. Each verifier gets the claim and the
source region — **never the memo, never the other claims, never the conclusion the claim
is meant to support.** A verifier that can see the surrounding narrative will pattern-match
to it and rubber-stamp. Ask it to reach its own verdict from the source alone:

```
Read <file> lines <start>-<end>.

Claim: "<claim text>"

Judge the claim against what you read, and nothing else. Answer CONFIRMED only if the
source states or plainly entails it. Answer REFUTED if the source contradicts it.
Answer UNSUPPORTED if the source simply doesn't settle it — a claim that might be true
elsewhere in the world is still UNSUPPORTED here.

UNSUPPORTED is the right answer more often than it feels; prefer it over a generous
CONFIRMED. Reply with the verdict, one sentence of reasoning, and the line number that
decided it.
```

Then write the verdicts back:

```bash
scripts/apply_verdicts.py --memo-dir .memo --verdicts <verdicts.json>
```

Claims are marked in place rather than deleted — `REFUTED` and `UNSUPPORTED` claims stay
visible with their mark. A struck-through claim tells the next reader "the local model
thought this and was wrong", which stops the same mistake from being regenerated. A
deleted claim teaches nothing.

## Context budget — the 70% rule

Generation and validation belong in **subagents**, whose contexts are disposable. That's
the structural fix: the main context never sees raw material, so it never fills. Do that
first; the watcher below is a backstop, not the plan.

```bash
scripts/ctx_watch.py                                    # measured, not estimated
scripts/ctx_watch.py --threshold 70 --handoff .memo/HANDOFF.md
```

It reads the real usage block from the session transcript, so the number is what the API
actually charged, not a guess. At 70%, write the handoff, fill in its "What was in
flight" section, and tell the user they can `/clear` and resume from the index for a
couple hundred tokens.

Be straight about the limit: **a script cannot open a new context.** `/clear` is the
user's action. Anything claiming otherwise is lying. What the skill can do is make the
next context cheap.

To have it warn automatically, add a PostToolUse hook running `ctx_watch.py --hook
--threshold 70 --handoff .memo/HANDOFF.md`; see `references/context-budget.md`.

## Using memos afterward

Reason from `CONFIRMED` claims. Treat `UNSUPPORTED` as a lead to check, not a fact.
Ignore `REFUTED`.

When you state something to the user that came from a memo, it's carrying a claim's
verification status with it — so if a conclusion rests on an `UNSUPPORTED` claim, say so
rather than presenting it flat. And before *editing* any file, Read the actual region.
Memos are for finding and deciding; they are never a substitute for the source when you're
about to change it.

## Domain notes

The pipeline is the same for all three; only the chunking and the claim style differ, and
`memo_gen.py` picks a profile from the file extension. Override with `--profile` if it
guesses wrong.

- **Code** (`--profile=code`): chunks on top-level declaration boundaries. Claims are about
  signatures, call relationships, and side effects. Anchors are nearly always checkable, so
  step 3 does most of the work here.
- **Prose / papers** (`--profile=prose`): chunks on headings and paragraphs. Claims are
  findings, methods, numbers. Anchors catch fabricated statistics well — a made-up figure
  won't string-match. Expect more `NEEDS_AGENT` than with code.
- **Mixed / unknown** (`--profile=generic`): fixed-size chunks with overlap. Weakest anchors;
  lean harder on step 4.

### Tables

Tables inside prose are extracted **deterministically — no model at all**. The model was
tested on this and failed twice: it emitted no row claims, and when asked to copy a row as
an anchor it paraphrased and the anchor failed string-match. But the exact row text is
already in hand, so `memo_gen.py` builds one claim per row in code, with the literal row as
its anchor (always verifies) and `header=value` pairs as the text (so a query on any column
finds the row). This is the skill's core move applied to tables: do it in code where the
model only adds errors. Measured: a fact living only in a markdown table row went from
unfindable to a CONFIRMED answer at ~58 tokens.

### Images (figures, charts, screenshots, scanned pages)

`memo_gen.py` sends any image file (`.png/.jpg/.webp/...`) to the vision model
(`gemma4:12b`) instead of skipping it as binary. It emits claims about what is visibly
there — chart values, diagram components, legible text. Image claims have no line anchors
(a picture has no lines), so they are `NEEDS_AGENT` by construction and validated by a
**second independent look**: a subagent reads the image with the Read tool, or offline
`validate.py --local` re-runs vision. The verifier never sees the first description, only
the claim and the picture. Do not let the model guess an illegible value — a fabricated
figure is the worst thing it can produce, and the prompt says so.

This is what makes PDFs first-class: convert a PDF to text for the prose **and** extract its
page images for the figures — the text pipeline is blind to charts, the vision pass is not.
`memo_gen.py` still won't parse a raw binary that is neither text nor a known image format.

### Ingesting documents (`doc_ingest.py`)

`scripts/doc_ingest.py` does both halves of that in one pass. It turns any document into
staged `.txt` (via the `doc-tools` extractors) and, for PDFs, one `.png` per page, then
prints a JSON manifest:

```bash
scripts/doc_ingest.py papers/*.pdf report.docx model.xlsx --outdir staging/
scripts/doc_ingest.py refs/*.pdf --outdir staging/ --no-images   # text only, much faster
```

Feed each record's `text` to the prose profile and its `images` to the vision profile —
that is the PDF split described above, already done. Handles `.pdf .docx .doc .odt .rtf
.pptx .ppt .xlsx .xls .ods .csv .tex`; legacy binary formats need LibreOffice installed.

Staged names keep the source extension and a hash of the full path
(`a/report.pdf` → `report_pdf-3f2a1b9c.txt`), so neither `report.pdf` vs `report.docx` nor
`a/report.pdf` vs `b/report.pdf` can collide. Without the hash the second silently
overwrote the first while the manifest reported both as fine — glob across directories and
you lost a file.

A file that fails to convert lands in the manifest with an `error` key and the rest of the
batch still runs — check for those before assuming full coverage.

Rasterizing is the expensive part: ~1s and ~200KB per page at the default 150 dpi. Pass
`--no-images` when the sources are text-only papers, and `--max-pages` (default 100) is a
per-PDF cap that warns on stdout when it truncates rather than silently dropping pages.

## Reference

- `references/memo-format.md` — the memo file format, claim/anchor grammar, and how to
  read the marks. Read this if you're writing a memo by hand or debugging odd script output.
- `references/local-model.md` — installing the runtime, model choice by RAM, prompt tuning
  per profile, and the no-local-model fallback.
