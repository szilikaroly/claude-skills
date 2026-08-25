#!/usr/bin/env python3
"""Measure how full the context window actually is, and hand off cleanly before it isn't.

Claude Code writes a transcript per session, and every assistant record carries the usage
block the API returned. The live context is just the last one:

    input_tokens + cache_creation_input_tokens + cache_read_input_tokens

That is a measurement, not an estimate — it is what the model was actually charged for on
the last turn.

  ctx_watch.py                      # current usage, percent, verdict
  ctx_watch.py --json
  ctx_watch.py --threshold 70 --handoff .memo/HANDOFF.md   # write a resume file if over
  ctx_watch.py --hook               # PostToolUse hook mode: silent until over threshold

What this CANNOT do, and no script can: start a new context. /clear is the user's
action. So at the threshold the honest move is to make the *next* context cheap — write
a handoff file that says what was indexed and what remains, so a fresh session resumes
from ~200 tokens instead of re-deriving everything.

The structural fix is to not fill the window in the first place: generation and
validation belong in subagents, whose contexts are disposable. This watcher is the
backstop for when that isn't enough, not the plan.
"""

import argparse
import json
import os
import pathlib
import sys

# Context window of the model running the session.
#
# This used to be a hardcoded 200000, which produced nonsense on any session running a
# larger window: a measured context of 561k was reported as "281% full", a number that
# cannot describe a window it exceeds. The percentage was wrong, not the measurement.
#
# Resolution order, most trustworthy first:
#   1. MEMO_CTX_WINDOW, if the user set it. An explicit override always wins.
#   2. The model id from the transcript, looked up in MODEL_WINDOWS.
#   3. The observed context itself. A request that carried N input tokens proves the
#      window is at least N, whatever any table claims, so round N up to the next known
#      tier. This is the rule that keeps the output honest when the table goes stale.
WINDOW_ENV = os.environ.get("MEMO_CTX_WINDOW")
DEFAULT_WINDOW = 200_000
TIERS = (200_000, 500_000, 1_000_000, 2_000_000)

# Prefix match, longest first, so "claude-opus-4-8" beats a bare "claude-opus".
MODEL_WINDOWS = {
    "claude-opus-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-fable-5": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
}


def window_for_model(model: str | None) -> int | None:
    """Look a model id up in the table, longest prefix wins. None if unknown."""
    if not model:
        return None
    for prefix in sorted(MODEL_WINDOWS, key=len, reverse=True):
        if model.startswith(prefix):
            return MODEL_WINDOWS[prefix]
    return None


def resolve_window(ctx_tokens: int, model: str | None) -> tuple[int, str]:
    """Return (window, how_it_was_determined)."""
    if WINDOW_ENV:
        try:
            return int(WINDOW_ENV), "MEMO_CTX_WINDOW"
        except ValueError:
            pass
    window = window_for_model(model)
    source = f"model {model}" if window else "default"
    if window is None:
        window = DEFAULT_WINDOW
    # A context larger than the assumed window disproves the assumption. Trust the
    # measurement and step up to the smallest tier that can actually hold it.
    if ctx_tokens > window:
        for tier in TIERS:
            if tier >= ctx_tokens:
                return tier, f"inferred from {ctx_tokens:,} observed tokens"
        return ctx_tokens, f"inferred from {ctx_tokens:,} observed tokens"
    return window, source


# Kept so existing imports of WINDOW keep working; no longer used for the percentage.
WINDOW = int(WINDOW_ENV) if (WINDOW_ENV or "").isdigit() else DEFAULT_WINDOW


def find_transcript() -> pathlib.Path | None:
    """Locate this session's transcript.

    The scratchpad path ends in the session UUID, and transcripts are named by it, so a
    script can find its own session without being told. Falls back to the newest
    transcript for the cwd's project when there is no scratchpad hint.
    """
    proj = pathlib.Path.home() / ".claude" / "projects"
    if not proj.exists():
        return None

    for var in ("CLAUDE_SCRATCHPAD", "CLAUDE_SESSION_DIR", "TMPDIR"):
        v = os.environ.get(var, "")
        for part in pathlib.Path(v).parts if v else []:
            if len(part) == 36 and part.count("-") == 4:
                hit = list(proj.rglob(f"{part}.jsonl"))
                if hit:
                    return hit[0]

    slug = "-" + str(pathlib.Path.cwd()).strip("/").replace("/", "-")
    cands = list((proj / slug).glob("*.jsonl")) if (proj / slug).exists() else []
    if not cands:
        cands = list(proj.rglob("*.jsonl"))
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


TAIL_BYTES = 512 * 1024


def read_usage(tp: pathlib.Path) -> dict | None:
    """Read the most recent usage block.

    Only the tail is parsed. Transcripts reach megabytes, and this runs from a PostToolUse
    hook on every single tool call — parsing the whole file each time would add latency to
    everything the user does, and a hook that makes the session feel slow gets removed,
    which costs more than it saves. The last record is all we need; the tail always has it.
    """
    last = None
    last_model = None
    try:
        size = tp.stat().st_size
        with tp.open("rb") as f:
            if size > TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
                f.readline()   # discard the partial line the seek landed in
            chunk = f.read().decode("utf-8", errors="replace")
        for ln in chunk.splitlines():
            if '"usage"' not in ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            msg = d.get("message") or {}
            u = msg.get("usage")
            if u and isinstance(u, dict) and "input_tokens" in u:
                last = u
                last_model = msg.get("model") or last_model
    except OSError:
        return None
    if not last:
        return None
    ctx = (last.get("input_tokens", 0)
           + last.get("cache_creation_input_tokens", 0)
           + last.get("cache_read_input_tokens", 0))
    window, window_source = resolve_window(ctx, last_model)
    return {"context_tokens": ctx, "window": window,
            "window_source": window_source, "model": last_model,
            "pct": round(100 * ctx / window, 1),
            "output_tokens": last.get("output_tokens", 0)}


def write_handoff(path: pathlib.Path, usage: dict, memo_dir: pathlib.Path | None):
    """A resume note for the next context.

    Keep it short on purpose: its whole value is that a fresh session can absorb it for a
    couple hundred tokens and carry on. A long handoff just moves the problem.
    """
    lines = [
        "# Handoff",
        "",
        f"Written because context reached {usage['pct']}% "
        f"({usage['context_tokens']:,}/{usage['window']:,} tokens).",
        "",
        "## Resume like this",
        "",
        "1. `/clear` to start a fresh context.",
        "2. Do NOT re-read the sources. The index already holds them.",
    ]
    if memo_dir and (memo_dir / "index.db").exists():
        try:
            sys.path.insert(0, str(pathlib.Path(__file__).parent))
            from memo_db import connect
            db = connect(memo_dir)
            total = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
            pend = db.execute("SELECT COUNT(*) FROM claims WHERE status IN"
                              " ('NEEDS_AGENT','PENDING')").fetchone()[0]
            srcs = db.execute("SELECT COUNT(DISTINCT source) FROM claims").fetchone()[0]
            lines += [
                f"3. Query it: `scripts/memo_query.py --memo-dir {memo_dir} '<question>'`",
                "",
                "## Index state",
                "",
                f"- {srcs} source(s), {total} claim(s) indexed at `{memo_dir}`",
                f"- {pend} claim(s) still unvalidated "
                f"(`validate.py --pending` to see them)",
            ]
            db.close()
        except Exception as e:  # a broken index must not block the handoff
            lines.append(f"3. Index at `{memo_dir}` (could not read state: {e})")
    else:
        lines.append(f"3. No index built yet.")

    lines += [
        "",
        "## What was in flight",
        "",
        "<!-- Fill this in before /clear: the question being answered, what has been",
        "     decided, and the single next step. Everything else is recoverable from",
        "     the index — this section is the part that isn't. -->",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--threshold", type=float, default=70.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hook", action="store_true",
                    help="silent unless over threshold (for PostToolUse hooks)")
    ap.add_argument("--handoff", type=pathlib.Path,
                    help="write a resume file when over threshold")
    ap.add_argument("--memo-dir", type=pathlib.Path, default=pathlib.Path(".memo"))
    args = ap.parse_args()

    tp = find_transcript()
    if not tp:
        if not args.hook:
            print("no transcript found — cannot measure context", file=sys.stderr)
        return 0
    usage = read_usage(tp)
    if not usage:
        if not args.hook:
            print(f"no usage data in {tp.name}", file=sys.stderr)
        return 0

    over = usage["pct"] >= args.threshold
    if args.handoff and over:
        md = args.memo_dir.resolve() if args.memo_dir.exists() else None
        write_handoff(args.handoff, usage, md)

    if args.json:
        print(json.dumps({**usage, "over_threshold": over,
                          "threshold": args.threshold}, indent=2))
        return 0

    if args.hook:
        if over:
            # Hook stdout is surfaced to the model, so say what to DO, not just a number.
            print(f"[memo-index] context at {usage['pct']}% "
                  f"({usage['context_tokens']:,}/{usage['window']:,}).")
            if args.handoff:
                print(f"Handoff written to {args.handoff}. Fill in 'What was in flight', "
                      f"then tell the user to /clear and resume from it.")
            else:
                print("Move remaining bulk work into subagents, or write a handoff and "
                      "suggest /clear.")
        return 0

    bar = "#" * round(30 * min(1.0, usage["pct"] / 100))
    print(f"context: {usage['context_tokens']:,} / {usage['window']:,} "
          f"= {usage['pct']}%")
    print(f"         [{bar:<30}]")
    if over:
        print(f"\nover the {args.threshold}% threshold.")
        print("A script cannot open a new context — /clear is yours to run. What helps:")
        print("  - push remaining generation/validation into subagents (fresh contexts)")
        print("  - write a handoff: ctx_watch.py --handoff .memo/HANDOFF.md")
        print("  - then /clear and resume from the index, not from the sources")
    else:
        head = (args.threshold - usage["pct"]) / 100 * usage["window"]
        print(f"\n~{head:,.0f} tokens of headroom before the {args.threshold}% mark.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
