#!/usr/bin/env python3
"""Index the conversation itself, so a full context can be abandoned without losing it.

A session transcript is the one corpus that is guaranteed to matter and guaranteed to be
thrown away — /clear discards it, and everything decided in it goes too unless it was
written down. This turns it into the same queryable index as any other corpus.

  session_index.py --out .memo-session          # index the current session
  session_index.py --out .memo-session --session <uuid>
  session_index.py --list                       # sessions for this project

It extracts the parts worth keeping and drops the parts that made the context expensive
in the first place: tool outputs, file dumps, thinking. What remains is what the user
asked for, what was decided, and what was reported — the things that cannot be
reconstructed from the repo afterwards.

Run this BEFORE /clear, not after. The transcript survives, but the point is to have the
index ready when the fresh context starts.
"""

import argparse
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))


def find_project_dir() -> pathlib.Path | None:
    proj = pathlib.Path.home() / ".claude" / "projects"
    if not proj.exists():
        return None
    slug = "-" + str(pathlib.Path.cwd()).strip("/").replace("/", "-")
    return (proj / slug) if (proj / slug).exists() else proj


def current_session() -> str | None:
    for var in ("CLAUDE_SCRATCHPAD", "CLAUDE_SESSION_DIR", "TMPDIR"):
        v = os.environ.get(var, "")
        for part in pathlib.Path(v).parts if v else []:
            if len(part) == 36 and part.count("-") == 4:
                return part
    return None


def text_of(content) -> str:
    """Pull the human-readable text out of a message, ignoring tool traffic.

    Tool results are the bulk of a transcript and the least worth keeping: they are
    reproducible by re-running the tool, while a decision is not.
    """
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            out.append(b.get("text", ""))
    return "\n".join(out)


def extract(tp: pathlib.Path) -> list[dict]:
    turns = []
    for ln in tp.read_text(errors="replace").splitlines():
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        role = d.get("type")
        if role not in ("user", "assistant"):
            continue
        msg = d.get("message") or {}
        txt = text_of(msg.get("content"))
        if not txt or not txt.strip():
            continue
        # System reminders and tool-result envelopes are harness noise, not conversation.
        if txt.lstrip().startswith(("<system-reminder", "<task-notification",
                                    "[SYSTEM NOTIFICATION")):
            continue
        txt = re.sub(r"<system-reminder>.*?</system-reminder>", "", txt, flags=re.S).strip()
        if len(txt) < 15:
            continue
        turns.append({"role": role, "text": txt})
    return turns


def to_document(turns: list[dict], sid: str) -> str:
    """Render the session as a line-numbered document memo_gen can chunk and anchor into."""
    out = [f"# Session {sid}", ""]
    for i, t in enumerate(turns, 1):
        who = "USER" if t["role"] == "user" else "ASSISTANT"
        out.append(f"## [{i}] {who}")
        out.append("")
        # Collapse long assistant passages: the decisions matter, the prose around them
        # is what we are trying not to pay for twice.
        body = t["text"]
        if len(body) > 3000:
            body = body[:1500] + "\n\n[... trimmed ...]\n\n" + body[-1200:]
        out.append(body)
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path(".memo-session"))
    ap.add_argument("--session", help="session uuid (default: this one)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    pdir = find_project_dir()
    if not pdir:
        sys.exit("no ~/.claude/projects directory")

    if args.list:
        for p in sorted(pdir.glob("*.jsonl"), key=lambda p: -p.stat().st_mtime)[:12]:
            kb = p.stat().st_size // 1024
            print(f"  {p.stem}  {kb:>6} KB")
        return

    sid = args.session or current_session()
    hits = list(pdir.rglob(f"{sid}.jsonl")) if sid else []
    if not hits:
        cands = sorted(pdir.glob("*.jsonl"), key=lambda p: -p.stat().st_mtime)
        if not cands:
            sys.exit("no transcripts found")
        hits = [cands[0]]
        sid = hits[0].stem
        print(f"(no session hint — using most recent: {sid})", file=sys.stderr)

    turns = extract(hits[0])
    if not turns:
        sys.exit("no conversation content extracted")

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    doc = to_document(turns, sid)
    src_dir = out / "sources"
    src_dir.mkdir(exist_ok=True)
    dst = src_dir / f"session-{sid[:8]}.md"
    dst.write_text(doc, encoding="utf-8")

    raw_kb = hits[0].stat().st_size // 1024
    print(f"session {sid}")
    print(f"  transcript      {raw_kb:>6} KB")
    print(f"  turns kept      {len(turns):>6}")
    print(f"  document        {len(doc)//1024:>6} KB  (~{len(doc)//4:,} tokens)")
    print(f"  written to      {dst}")
    print()
    print("now index it (local model, zero API tokens):")
    print(f"  memo_gen.py --root {out} --include 'sources/*.md' --profile prose "
          f"--memo-dir {out}/.memo")
    print(f"  verify_anchors.py --memo-dir {out}/.memo --root {out}")
    print(f"  memo_db.py --memo-dir {out}/.memo --build")
    print()
    print("then, from a fresh context after /clear:")
    print(f"  memo_query.py --memo-dir {out}/.memo --root {out} '<what did we decide about X>'")


if __name__ == "__main__":
    main()
