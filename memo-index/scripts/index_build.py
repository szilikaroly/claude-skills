#!/usr/bin/env python3
"""Build .memo/INDEX.md — the only file that belongs in the main context by default.

One line per source: path, topic, trust level, size. Read the index, decide which
two or three memos actually bear on the question, read only those. That selectivity
is where the token saving actually comes from — memos you never open cost nothing.

  index_build.py --memo-dir .memo

Also writes the ledger: raw tokens vs. index+memo tokens. Report it honestly, including
when it's negative — a skill that quietly loses money is worse than one that says so.
"""

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from memo_db import parse_memo_text  # noqa: E402

TOPIC_RE = re.compile(r"^- L\d+-\d+: (.*)$")

# The first chunk of a file is almost always imports and constants, so its topic
# ("Imports and Setup") is the least informative line in the memo — and it was what
# the index used to show. The index line is the one thing that decides whether anyone
# opens the memo at all, so it has to describe the file, not its preamble.
BORING_TOPIC_RE = re.compile(
    r"^(?:imports?\b|includes?\b|setup\b|configuration\b|constants?\b|"
    r"module[- ]level\b|header\b|preamble\b|boilerplate\b|(?:imports?|configuration)\s+and\s+setup)",
    re.I,
)


def pick_topic(topics: list[str]) -> str:
    """Choose the topic that actually characterizes the file."""
    substantive = [t for t in topics if t and not BORING_TOPIC_RE.match(t.strip())]
    if substantive:
        # Longest tends to be the most specific; boring topics are terse by nature.
        return max(substantive, key=len)
    return topics[0] if topics else "(no topic)"

TRUST_ORDER = ["CONFIRMED", "DRIFTED", "NEEDS_AGENT", "UNSUPPORTED", "REFUTED"]


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--memo-dir", default=".memo", type=pathlib.Path)
    args = ap.parse_args()

    memo_dir = args.memo_dir.resolve()
    rows, raw_total, memo_total = [], 0, 0

    unparseable_total = 0
    for mp in sorted(memo_dir.glob("*.memo.md")):
        text = mp.read_text(encoding="utf-8")
        # One parser, shared with memo_db.py. With a looser regex of its own, this script
        # counted claims memo_db could not index — a memo with 4 claims, 2 of them
        # unanchored, was indexed as 2 and advertised here as trust 3/4. The index line is
        # what decides whether anyone opens the memo; it has to describe the same corpus
        # the query layer will actually search.
        fm, claims, unparseable = parse_memo_text(text)
        topics = [t.group(1) for t in
                  (TOPIC_RE.match(ln) for ln in text.splitlines()) if t]
        tally = {}
        for c in claims:
            tally[c["status"]] = tally.get(c["status"], 0) + 1
        unparseable_total += len(unparseable)

        raw = int(fm.get("raw_tokens_est", 0) or 0)
        mt = est_tokens(text)
        raw_total += raw
        memo_total += mt

        total = sum(tally.values())
        good = tally.get("CONFIRMED", 0) + tally.get("DRIFTED", 0)
        pending = tally.get("NEEDS_AGENT", 0)
        trust = f"{good}/{total}" if total else "0/0"
        if pending:
            trust += f" (+{pending} unchecked)"
        if unparseable:
            trust += f" (+{len(unparseable)} unparseable)"

        rows.append({
            "source": fm.get("source", mp.stem),
            "profile": fm.get("profile", "?"),
            "lines": fm.get("lines", "?"),
            "topic": pick_topic(topics),
            "trust": trust,
            "memo": mp.name,
            "raw": raw,
            "mt": mt,
        })

    index_body = [
        "# Memo index",
        "",
        "One line per source. Read this, pick the 2-3 memos that bear on your question,",
        "read only those. `trust` = claims that survived verification / total claims.",
        "Never edit a source from a memo — Read the real region first.",
        "",
        "| source | topic | trust | lines | memo |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: r["source"]):
        topic = r["topic"][:60].replace("|", "\\|")
        index_body.append(
            f"| `{r['source']}` | {topic} | {r['trust']} | {r['lines']} | `{r['memo']}` |")

    saved = raw_total - memo_total
    pct = (100 * saved // raw_total) if raw_total else 0

    def with_ledger(self_tokens: int) -> list[str]:
        return index_body + [
            "",
            "## Ledger",
            "",
            f"- raw material if read directly: **~{raw_total:,} tokens**",
            f"- all memos if fully read:       **~{memo_total:,} tokens**",
            f"- index alone (this file):       **~{self_tokens:,} tokens**",
            f"- saved by memoizing:            **~{saved:,} tokens ({pct}%)**",
            "",
            "The real saving is larger than the line above: in practice you read the index",
            "plus a couple of memos, not every memo. It is smaller if the memos were wrong —",
            "check `trust` before believing the number.",
            "",
        ]

    # "index alone" used to be measured on index_body *before* the Ledger section was
    # appended — it was computed inside the list literal doing the appending. It printed
    # ~88 for a 193-token file. This is the one number the reader is asked to trust, so
    # measure the finished file, including the line stating the measurement, by iterating
    # to a fixed point (the count is stable within an iteration or two).
    self_tokens = est_tokens("\n".join(with_ledger(0)))
    for _ in range(5):
        nxt = est_tokens("\n".join(with_ledger(self_tokens)) + "\n")
        if nxt == self_tokens:
            break
        self_tokens = nxt
    index_body = with_ledger(self_tokens)

    out = memo_dir / "INDEX.md"
    out.write_text("\n".join(index_body) + "\n", encoding="utf-8")

    print(f"wrote {out}  ({len(rows)} memos)")
    if unparseable_total:
        print(f"NOTE: {unparseable_total} claim-like line(s) do not parse and are not "
              f"indexed — they are counted as unparseable, never as verified.")
    print(f"raw ~{raw_total:,} tok -> memos ~{memo_total:,} tok  (saved ~{saved:,}, {pct}%)")
    # `raw_total and ...` used to short-circuit the whole warning when raw_tokens_est was
    # absent — precisely the case where the ledger reads "saved ~-18, 0%" and most needs
    # to say so out loud.
    if not raw_total:
        print("NOTE: no raw_tokens_est in the memos — the saving above is not measured, "
              "and 0% means unknown, not break-even.")
    elif saved < 0:
        print("NOTE: net loss. The memos cost more than the raw material; plain Read "
              "would have been cheaper here.")
    elif saved < 5000:
        print("NOTE: thin margin. For a set this small, plain Read may have been cheaper.")


if __name__ == "__main__":
    main()
