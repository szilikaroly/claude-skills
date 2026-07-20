#!/usr/bin/env python3
"""Write subagent verdicts back into the memo files.

Step 4 of the pipeline spawns one isolated subagent per NEEDS_AGENT claim. Collect
their verdicts into a JSON file and apply them here.

  apply_verdicts.py --memo-dir .memo --verdicts verdicts.json

verdicts.json:
  [
    {"source": "src/auth.ts", "id": "C3", "status": "CONFIRMED",
     "note": "L88 rotates the token", "line": 88},
    {"source": "src/auth.ts", "id": "C7", "status": "UNSUPPORTED",
     "note": "file says nothing about TTL defaults"}
  ]

Claims are marked, never deleted. A visibly REFUTED claim tells the next reader
"the local model believed this and was wrong" — which stops the same wrong idea
from being quietly regenerated later. A deleted claim teaches nobody anything.
"""

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile

CLAIM_RE = re.compile(r"^- \[(C\d+)\] \[(\w+)\] (.*?)\s*(@\S.*?)?\s*(<!--.*-->)?\s*$")
VALID = {"CONFIRMED", "REFUTED", "UNSUPPORTED"}


def atomic_write(path: pathlib.Path, text: str) -> None:
    """Replace a memo in one step, or not at all.

    Writing in place truncates first, so an interruption between truncate and write
    leaves a half-memo — or an empty one — with no backup and nothing to say it is
    damaged. Every claim in that file is then silently gone from the index.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--memo-dir", default=".memo", type=pathlib.Path)
    ap.add_argument("--verdicts", required=True, type=pathlib.Path)
    args = ap.parse_args()

    verdicts = json.loads(args.verdicts.read_text(encoding="utf-8"))
    if isinstance(verdicts, dict):
        verdicts = verdicts.get("verdicts", [])

    by_source: dict[str, dict[str, dict]] = {}
    for v in verdicts:
        st = v.get("status", "").upper()
        if st not in VALID:
            sys.exit(f"bad status {st!r} for {v.get('source')}:{v.get('id')} — "
                     f"expected one of {sorted(VALID)}")
        by_source.setdefault(v["source"], {})[v["id"]] = v

    memo_dir = args.memo_dir.resolve()
    applied = missed = 0

    for mp in sorted(memo_dir.glob("*.memo.md")):
        text = mp.read_text(encoding="utf-8")
        m = re.search(r"^source: (.*)$", text, re.M)
        if not m or m.group(1) not in by_source:
            continue
        wanted = by_source[m.group(1)]
        out = []

        for line in text.splitlines():
            cm = CLAIM_RE.match(line)
            if not cm:
                out.append(line)
                continue
            cid, status, body, anchor, _ = cm.groups()
            if cid not in wanted or status != "NEEDS_AGENT":
                out.append(line)
                continue
            v = wanted.pop(cid)
            note = v.get("note", "").replace("--", "—")
            if v.get("line"):
                note = f"L{v['line']}: {note}"
            out.append(f"- [{cid}] [{v['status'].upper()}] {body} {anchor or ''}"
                       + (f"  <!-- verified: {note} -->" if note else ""))
            applied += 1

        # Refresh the frontmatter tally.
        tally: dict[str, int] = {}
        for line in out:
            cm = CLAIM_RE.match(line)
            if cm:
                tally[cm.group(2)] = tally.get(cm.group(2), 0) + 1
        summary = ", ".join(f"{v} {k.lower()}" for k, v in sorted(tally.items()))
        out = [re.sub(r"^verified: .*$", f"verified: {summary or 'no claims'}", l)
               for l in out]

        atomic_write(mp, "\n".join(out) + "\n")
        missed += len(wanted)
        for cid in wanted:
            print(f"  warn: {m.group(1)}:{cid} not found or not pending", file=sys.stderr)

    print(f"applied {applied} verdict(s)" + (f", {missed} unmatched" if missed else ""))
    print("next: scripts/index_build.py --memo-dir <dir>   (refresh the ledger)")


if __name__ == "__main__":
    main()
