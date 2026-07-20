# The local model

## Why local at all

Memo generation is the token-hungry step: every line of every source has to pass through
*some* model. Doing that on the user's own hardware makes it free — the API bill doesn't
move, and there's no context window to blow. A 7B model is not smart enough to be trusted,
which is precisely why the validation half of the pipeline exists. Cheap-and-checked beats
expensive-and-assumed.

## Install

```bash
scripts/setup_local_model.sh --check     # status
scripts/setup_local_model.sh --install   # downloads software — ask the user first
scripts/setup_local_model.sh --serve     # start the server if it's down
```

`--install` uses Homebrew if present, otherwise downloads Ollama.app from ollama.com
(~500MB) plus the model (a few GB). On Linux it uses the official install script. Both
are downloads of third-party software onto the user's machine, so get an explicit yes
before running it, and say how big it is.

On macOS without Homebrew the app has to be opened once (`open -a Ollama`) so it puts the
`ollama` CLI on PATH. The script tells you this; it's the one manual step.

## Choosing a model

`memo_gen.py` picks a model per profile, because a code-tuned model reads declarations
well but flattens prose, and a general model does the reverse:

| profile | env var | default | ~size |
|---|---|---|---|
| `code` | `MEMO_MODEL_CODE` | `qwen2.5-coder:7b` | 4.7 GB |
| `prose`, `generic` | `MEMO_MODEL_PROSE` | `llama3.1:8b` | 4.7 GB |

`MEMO_MODEL` overrides both if you'd rather run a single model — useful on a machine
tight enough that keeping two resident causes swapping.

**Images** go to a third model, the vision one (`MEMO_MODEL_BIG`, default `gemma4:12b`).
Any `.png/.jpg/.jpeg/.webp/.gif/.bmp/.tiff` file is sent to it as base64 via ollama's
`images` field; it emits claims about what is visible. Verified as reading a test chart
correctly (`Q3 Revenue: 47 million USD`, bar colours) and confirming its own claims on a
second pass. Image claims carry no line anchor, so they are always `NEEDS_AGENT` until a
second look (subagent Read, or `validate.py --local` re-vision) settles them. Vision is
slow — 1-2 min per image including model load — so run generation in the background.

Sizing by RAM:

| RAM | suggestion |
|---|---|
| 8 GB | one model only: `MEMO_MODEL=qwen2.5-coder:3b` (~2 GB) |
| 16 GB | the two defaults above — fine, one loads at a time |
| 32 GB+ | `qwen2.5-coder:14b` (~9 GB) for code; noticeably fewer refuted claims |
| 64 GB+ | `qwen2.5-coder:32b` (~20 GB); diminishing returns given verification |

Don't over-invest here. The pipeline is designed so a mediocre model is safe — a bigger
model mostly buys you a lower `REFUTED` rate, which saves regeneration time, not correctness.
If most claims are getting refuted, try one size up before concluding the approach doesn't
work on this material.

## Speed, and why generation belongs in the background

Local generation is slow in wall-clock terms — think ~30-60s per chunk, so a few minutes
for a handful of files and much longer for a real corpus. That's fine: it's spending the
user's idle CPU instead of their token budget, which is the whole trade. But it means
`memo_gen.py` should be run as a **background task**, not a foreground call you sit and
wait on. Kick it off, do something else, come back.

On a 16 GB machine, two models don't stay resident together. Ollama evicts and reloads
between a `code` file and a `prose` file, which adds ~20s each time it switches. If a run
is mixed and feels pathologically slow, either sort the run by profile (do all the code,
then all the prose) or pin one model with `MEMO_MODEL` for that run.

## Tuning

Environment variables read by `memo_gen.py`:

- `MEMO_MODEL` — model tag (above)
- `MEMO_NUM_CTX` — context window, default 8192. Must comfortably exceed chunk size plus
  prompt. Ollama defaults to 2048 if you don't set it, and an overflowing model silently
  drops the tail of the chunk and then invents line numbers for it — so this matters more
  than it looks.
- `OLLAMA_HOST` — default `http://127.0.0.1:11434`

Temperature is pinned at 0.1 in the script. Memo generation is extraction, not writing;
creativity here shows up as fabricated anchors.

## Don't try to fix a small model with prompt wording

Measured on this pipeline: `qwen2.5-coder:7b` was told, in an explicitly headed section
with anti-examples, *never claim an import*. It then produced five import claims out of
eleven — more trivia than the shorter prompt that hadn't asked at all. Lengthening a
prompt to add prohibitions reliably makes a 7B model worse, because the added text
competes for attention with the part that was working.

So `memo_gen.py` filters trivia in `parse_claims()` — import lines, name-restating claims
("defines a function called X"), and duplicate anchors are dropped in code, whatever the
model says. Same bargain as the rest of the skill: don't ask a small model for discipline,
enforce it mechanically.

If you find a new trivia pattern, add it to `TRIVIAL_ANCHOR_RE` / `TRIVIAL_TEXT_RE` rather
than to the prompt. The prompt should stay short.

## Chunking is the real quality lever

More than model size. `MAX_CHUNK_LINES` (160) exists because line-number accuracy collapses
when you ask a small model to index a big span — it starts counting approximately somewhere
around line 200 and never recovers. The `code` profile splits on declaration boundaries and
`prose` on headings so each chunk is a semantically whole thing the model can actually
characterize, rather than an arbitrary window.

If a specific file produces garbage, check its chunking first (`--dry-run` prints chunk
counts) before blaming the model.

## No local model?

Two honest options, in order:

1. **`memo_gen.py --generator=subagent`** — prints a JSON work order (chunk ranges + the
   per-profile prompt) instead of calling the local model. Fan out one subagent per file,
   have each write its memo, then run `verify_anchors.py` exactly as normal. This costs API
   tokens, but the raw material still never enters the *main* context, which is where the
   real budget pressure is. Use a cheap model for these — they're doing extraction, and
   everything they say gets verified anyway.

2. **Don't use the skill.** If the material is small enough that plain Read is affordable,
   that's the right answer and it's not a failure to say so. The economics gate in SKILL.md
   is there to be taken seriously.
