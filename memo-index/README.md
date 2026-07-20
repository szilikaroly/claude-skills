# memo-index

Cuts context-token spend by reading your material with a **local** model, indexing it as
verifiable claims, and letting you **query** it instead of reading it.

`SKILL.md` is what Claude reads. This file is for you.

---

## The idea in one paragraph

Reading files into context is the biggest token cost in a long session, and you pay it
again every session. So: a local model (your machine, zero API tokens) reads the material
and emits atomic **claims**, each anchored to a source line. You never read the memos —
you query them, and get back only the claims that matched. A small local model is not
trustworthy, which is why nothing it says survives unverified: most claims are checked by
string match (free), and only genuinely semantic ones cost a subagent.

**The bargain: the local model is allowed to be sloppy, because nothing it says survives
unverified.**

## Install

Already installed at `~/.claude/skills/memo-index/`. Claude loads it automatically when a
task matches its description.

Models (one-time, ~14 GB total):

```bash
scripts/route.py --check                 # what's present, and whether you're online
scripts/setup_local_model.sh --install   # pulls what's missing
```

| role | model | why this one |
|---|---|---|
| code | `qwen2.5-coder:7b` | declarations, signatures, call structure |
| prose | `llama3.1:8b` | papers, docs, argument structure |
| long / vision / reason | `gemma4:12b` | 262k context, reads images, and **stands in for subagents when offline** |

A `PostToolUse` hook is configured in `~/.claude/settings.json`: it warns at 70% context.
Silent below that, ~22 ms per call.

## Use

```bash
# 1. is it worth it? (calls no model)
scripts/memo_gen.py --root . --include 'src/**/*.py' --dry-run

# 2. generate — SLOW (minutes/file). Run it in the background. CPU, not tokens.
scripts/memo_gen.py --root . --include 'src/**/*.py' --memo-dir .memo

# 3. verify for free, then index
scripts/verify_anchors.py --memo-dir .memo --root .
scripts/memo_db.py --memo-dir .memo --build

# 4. query — this is the part that saves tokens
scripts/memo_query.py --memo-dir .memo "how do sessions expire"
scripts/memo_query.py --memo-dir .memo --expand src/auth.py:C3   # the actual source
```

**Do not `cat` a memo.** Reading one costs its whole length to use one fact in it. The
memos exist to be reindexed and to be human-readable, not to enter a context.

## Measured results

Benchmark: `scripts/benchmark.py --out /tmp/bench`. Endpoints are string-matched against
the source, so the skill can and does fail.

| corpus | endpoints | saved |
|---|---|---|
| small (1 file, ~900 tok) | 1/2 | 91.9% |
| medium (10 files, ~27k tok) | 4/5 | 99.7% |
| large (9 files, ~17k tok) | 2/4 → rerunning with table extraction | ~99% |

A query costs **~60 tokens flat**, whatever the corpus size. That's why saving grows with
scale — and why on one small file, plain `Read` legitimately wins. The economics gate in
`SKILL.md` is not a formality.

Generation is slow (80 min for 27k tokens of Python) but it is **CPU on your machine,
zero API tokens**, and it's paid once per file version, not per query.

## Things this project learned the hard way

Each of these was a real bug that shipped and was caught by measurement, not by reading
the code. They're in the source as comments where they bite.

- **You cannot prompt a 7B model into discipline.** Told explicitly, with anti-examples,
  never to claim imports, `qwen2.5-coder:7b` produced *more* import trivia than before.
  Trivia is filtered in code now. If you find a new junk pattern, extend
  `TRIVIAL_TEXT_RE` — do not put it back in the prompt.

- **Metrics can reward failure.** The first benchmark reported **"100% saved" on 0/11
  endpoints** — a perfect score for answering nothing, because "no claims" scored as "no
  tokens spent". Saving is now computed only over endpoints that actually passed, and the
  pass rate is the first column.

- **A verifier must survive its own output.** `verify_anchors.py` wasn't idempotent: the
  annotations it wrote broke its own regex on the next run, so refuted claims vanished
  from the tally and memos read 100% CONFIRMED *because* the failures had become
  invisible. The trailing `(?:<!--.*-->)?` in `CLAIM_RE` is load-bearing.

- **A guard with an off switch is not a guard.** Refuted claims could be surfaced with
  `--floor 0`. Trust is now a hard exclusion independent of any threshold — no flag
  brings a known falsehood back.

- **Silent wrong answers beat loud ones only in appearance.** Without stemming, "how are
  chunks split" didn't return nothing — it returned a confidently irrelevant claim. Index
  and query stem identically; change both or neither.

- **One transient error killed whole runs.** `call_ollama` called `sys.exit()`. A single
  model eviction (any second job on a 16 GB machine triggers one) left 8 of 10 files
  unmemoized, and the benchmark scored it clean. It retries per chunk now, and refuses to
  write an empty memo — an empty memo whose hash says "current" is worse than none.

- **Tables were invisible.** The fact "prose uses llama3.1" sat in a markdown table row,
  in plain text, unclaimed, because the heading chunker swallowed the table and the model
  narrated the section instead of reading the rows. Tables now get their own row-level
  pass.

## Honest limits

- **A script cannot open a new context.** `/clear` is yours. `ctx_watch.py` measures and
  warns; `session_index.py` makes the next context cheap. Anything claiming more is lying.
- **Memos are lossy.** Never edit a file based on one — `--expand` and read the source.
- **Prose coverage is weaker than code.** Anchors are strong for declarations, weaker for
  arguments. Expect more `NEEDS_AGENT` and more misses on papers than on source.
- **"No answer" is a real answer.** If the corpus doesn't say, the honest output is that
  it doesn't say. See `references/coverage-plan.md` — coverage is raised by escalating to
  the source, never by loosening the trust gate.

## Files

| | |
|---|---|
| `SKILL.md` | what Claude reads — the pipeline and the gates |
| `references/memo-format.md` | memo format, claim/anchor grammar, statuses |
| `references/local-model.md` | model choice, sizing, tuning, offline fallback |
| `references/retrieval.md` | keyword→claim→content, scoring, tuning |
| `references/context-budget.md` | the 70% protocol, hook setup |
| `references/coverage-plan.md` | why endpoints fail and the plan to fix them |
