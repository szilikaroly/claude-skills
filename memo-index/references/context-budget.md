# Context budget

## What is measurable, and what isn't

Claude Code writes a transcript per session under `~/.claude/projects/<slug>/<uuid>.jsonl`.
Every assistant record carries the API's usage block, so the live context is:

    input_tokens + cache_creation_input_tokens + cache_read_input_tokens

That is a **measurement** — what the model was actually charged on the last turn — not an
estimate from counting characters. `ctx_watch.py` reads it. The script finds its own
session because the scratchpad path ends in the session UUID.

What no script can do: **open a new context.** `/clear` is the user's action. A skill that
claims to "automatically start a fresh context" is lying. Be straight about this rather
than building something that pretends.

## The order of defence

1. **Don't fill it.** Generation and validation go in subagents, whose contexts are
   disposable. The main context should never see raw material. This is the actual fix;
   everything below is a backstop.
2. **Query, don't read.** See `retrieval.md`. Reading memo files re-fills the window with
   the thing you just spent CPU to compress.
3. **Watch and hand off.** At 70%, write `HANDOFF.md`, fill in what's in flight, and let
   the user `/clear`.

## The handoff

```bash
scripts/ctx_watch.py --threshold 70 --handoff .memo/HANDOFF.md
```

It records index state (sources, claims, what's still unvalidated) automatically. The one
section it cannot fill is **"What was in flight"** — the question being answered, what's
been decided, the next step. Fill that in before `/clear`. Everything else is recoverable
from the index; that part isn't.

Keep it short on purpose. Its whole value is that a fresh session absorbs it for ~200
tokens. A long handoff just moves the problem into the next window.

## Automatic warning (hook)

Add to `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/skills/memo-index/scripts/ctx_watch.py --hook --threshold 70 --handoff .memo/HANDOFF.md"
          }
        ]
      }
    ]
  }
}
```

`--hook` is silent below the threshold, so it costs nothing until it matters. Above it,
stdout reaches the model as a nudge with the next action, not just a number. It fires on
every tool call, so keep the threshold meaningful — a hook that cries wolf at 40% gets
ignored at 90%.

## Window size

The window is resolved in three steps, most trustworthy first:

1. **`MEMO_CTX_WINDOW`**, if you set it. An explicit override always wins.
2. **The model id in the transcript**, looked up in `MODEL_WINDOWS` in `ctx_watch.py`
   (Opus 4.8, Sonnet 5 and Fable 5 are 1M; Haiku 4.5 and the 4-series are 200k).
3. **The observed context itself.** A request that carried N input tokens proves the
   window is at least N, so N is rounded up to the next known tier.

Rule 3 is what keeps the output honest when the table goes stale. The script previously
assumed a flat 200k, and on a 1M-window session reported a measured 561k context as
"281% full" — a figure that cannot describe a window it exceeds. The measurement was
right; the denominator was not. Now a context larger than the assumed window disproves
the assumption rather than producing an impossible percentage.

Add a model to `MODEL_WINDOWS` when a new one ships. If you don't, rule 3 still keeps the
percentage sane once the context grows past 200k — it just won't be right *before* that,
so a genuinely new model is worth one line in the table.
