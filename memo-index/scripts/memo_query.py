#!/usr/bin/env python3
"""Ask the corpus a question; get back only the claims that answer it.

This is the tier that saves the tokens. Reading a 500-token memo to use one fact in it
costs 500 tokens. Querying for that fact costs ~25. The corpus can be a thousand files;
the answer is still ~25 tokens per claim, because you never load what didn't match.

  memo_query.py --memo-dir .memo "how do sessions expire"
  memo_query.py --memo-dir .memo "session ttl" --limit 5 --json
  memo_query.py --memo-dir .memo "retry policy" --expand C3   # pull the source region
  memo_query.py --memo-dir .memo --feedback C3:helpful        # teach the ranker

Ranking combines three independent things, because each catches a different failure:
  relevance  does the claim match the question           (FTS5 BM25 + keyword weights)
  utility    is the claim worth reading at all           (trust x specificity x feedback)
  trust      did it survive validation                    (gates everything to zero)

A claim that matches perfectly but was REFUTED must never surface — that is the
hallucination guard, and it is enforced on the status string in every path that can
print a claim (search, --expand), not on a number that a --floor can move. A claim that
is true but vague ("handles various things") ranks below one that pins something down,
because retrieving it wastes the reader's budget and tempts an answer built on nothing.

If a memo changed after the index was built, the index may be describing a claim whose
status has since flipped. That is refused, not warned about: an index that cannot prove
it is current cannot prove a claim is not refuted.
"""

import argparse
import json
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from memo_db import (connect, extract_keywords, index_state, stem,  # noqa: E402
                     STOP, STATUS_W)

# Claims below this never surface regardless of match quality. This governs vagueness,
# which is a matter of degree. It is NOT what keeps bad statuses out — see below.
UTILITY_FLOOR = 0.15

# Anything at or above this is worth showing even on a weak keyword match.
STRONG_UTILITY = 0.5

# Known-false, or describing a file that no longer exists in that shape. No flag, no
# floor, no keyword match brings these back. Previously REFUTED was excluded by an
# explicit check but UNSUPPORTED was excluded only because trust 0.15 x ... happened to
# land on the floor — so `--floor 0`, which the empty-result message actively suggested,
# surfaced unsupported claims. Statuses are gated by name now.
BLOCKED_STATUS = frozenset({"REFUTED", "STALE"})

# Leads, not answers: shown only with --include-unproven, always flagged.
UNPROVEN_STATUS = frozenset({"NEEDS_AGENT", "PENDING", "UNSUPPORTED"})


def fts_query(q: str) -> str:
    """Build the FTS match expression from the meaningful terms only.

    Stopwords have to go before BM25 sees them: "how are chunks split" scored by all
    four terms buries the one claim about chunking under everything that says "is" a
    lot. Terms are stemmed to match how they were indexed, and the raw form is kept
    alongside so an exact identifier still hits.
    """
    terms = []
    for t in re.split(r"[^\w]+", q.lower()):
        if len(t) < 2 or t in STOP:
            continue
        s = stem(t)
        terms.append(f'"{s}"*' if s != t else f'"{t}"*')
    return " OR ".join(dict.fromkeys(terms)) if terms else ""


def status_allowed(status: str, include_unproven: bool) -> bool:
    """The one gate. Every path that can print a claim goes through it."""
    if status in BLOCKED_STATUS:
        return False
    if status in UNPROVEN_STATUS and not include_unproven:
        return False
    return True


def search(db: sqlite3.Connection, question: str, limit: int, floor: float,
           include_unproven: bool):
    qkws = extract_keywords(question, None)
    cur = db.cursor()

    # Two retrieval paths that fail differently: FTS catches natural-language phrasing,
    # the keyword table catches exact identifiers a BM25 tokenizer would dilute. Union
    # them so "createSession" and "how are sessions made" both land.
    scores: dict[int, dict] = {}

    fq = fts_query(question)
    if fq:
        try:
            raw = [(r["rowid"], abs(r["rank"])) for r in cur.execute(
                "SELECT rowid, rank FROM claims_fts WHERE claims_fts MATCH ?"
                " ORDER BY rank LIMIT ?", (fq, limit * 8))]
        except sqlite3.OperationalError:
            raw = []  # malformed query — the keyword path still works
        if raw:
            # BM25 on a *contentless* FTS5 table has no stored document lengths, so its
            # magnitudes are ~5e-06, not the ~1-10 the old `abs(rank)/10` assumed. That
            # made every FTS-only hit score ~5e-07, and since score multiplies relevance
            # by utility, ~0 relevance annihilated utility: all hits tied at 0.0 and the
            # ranking collapsed to insertion order, so --limit truncated the one good
            # answer. Normalize against the best rank in this result set instead, which
            # is scale-free and lands on the same 0..1 range the keyword path uses.
            top = max(v for _, v in raw)
            for rid, v in raw:
                scores.setdefault(rid, {"fts": 0.0, "kw": 0.0})
                scores[rid]["fts"] = 0.35 + 0.65 * (v / top) if top > 0 else 0.35

    if qkws:
        marks = ",".join("?" * len(qkws))
        for r in cur.execute(
            f"SELECT claim_rowid rid, SUM(weight) s FROM kw WHERE keyword IN ({marks})"
            f" GROUP BY claim_rowid ORDER BY s DESC LIMIT ?",
            (*qkws.keys(), limit * 8)):
            scores.setdefault(r["rid"], {"fts": 0.0, "kw": 0.0})
            scores[r["rid"]]["kw"] = min(1.0, r["s"] / 8.0)

    if not scores:
        return []

    marks = ",".join("?" * len(scores))
    rows = {r["rowid"]: r for r in cur.execute(
        f"SELECT rowid, * FROM claims WHERE rowid IN ({marks})", tuple(scores))}

    out = []
    for rid, s in scores.items():
        r = rows.get(rid)
        if not r:
            continue
        # Trust is not a threshold. A refuted claim is a known falsehood and a stale one
        # describes a file that no longer exists in that shape; no --floor, no keyword
        # match, and no amount of utility may bring either back. The floor below governs
        # vagueness, which is a matter of degree — this is not.
        if not status_allowed(r["status"], include_unproven):
            continue
        if r["utility"] < floor:
            continue
        relevance = max(s["fts"], s["kw"]) * 0.6 + min(s["fts"], s["kw"]) * 0.4
        # The 0.25 floor on the relevance factor is what stops a near-zero relevance from
        # multiplying utility away and flattening the ordering.
        score = round((0.25 + 0.75 * relevance) * (0.5 + 0.5 * r["utility"]), 4)
        out.append({
            "rowid": rid, "source": r["source"], "id": r["claim_id"],
            "text": r["text"], "status": r["status"], "line": r["anchor_line"],
            "region": [r["region_lo"], r["region_hi"]],
            "utility": r["utility"], "relevance": round(relevance, 3), "score": score,
            "depth": r["depth"],
        })

    out.sort(key=lambda x: (-x["score"], -x["utility"], x["source"], x["id"]))
    return out[:limit]


def bump_hits(db, rowids):
    db.executemany("UPDATE claims SET hits = hits + 1 WHERE rowid = ?",
                   [(r,) for r in rowids])
    db.commit()


def expand(db, memo_dir: pathlib.Path, claim_ref: str, root: pathlib.Path,
           include_unproven: bool = False, debug_blocked: bool = False):
    """Tier 3: the actual source. Only worth it once a claim has proved relevant.

    This selects by id, so it used to be the hole in the guard: search would refuse to
    show a REFUTED claim, then every successful query printed a hint advertising
    --expand, which happily printed that same claim in full along with its source region.
    The gate is the same function search() uses.
    """
    src, cid = (claim_ref.rsplit(":", 1) if ":" in claim_ref else (None, claim_ref))
    # The qualifier may name the source or the memo: chunked memos of one source restart
    # their claim ids, so `big.py:C1` cannot be resolved but `big.part2.memo.md:C1` can.
    q = "SELECT * FROM claims WHERE claim_id = ?" + (
        " AND (source = ? OR memo = ?)" if src else "")
    rows = list(db.execute(q, (cid, src, src) if src else (cid,)))
    if not rows:
        sys.exit(f"no claim {claim_ref}")
    if len(rows) > 1:
        where = sorted({(r["source"], r["memo"]) for r in rows})
        if not src:
            sys.exit(f"{claim_ref} is ambiguous across "
                     f"{[s for s, _ in where]} — qualify it as source:{cid}")
        # Chunked memos of one source restart their claim ids, so source:C1 can still be
        # ambiguous. Never guess which one the caller meant.
        sys.exit(f"{claim_ref} is ambiguous across memos {[m for _, m in where]}"
                 f" — the claim ids restart per memo; disambiguate by memo")
    r = rows[0]

    if not status_allowed(r["status"], include_unproven):
        if r["status"] in BLOCKED_STATUS and not debug_blocked:
            sys.exit(
                f"refusing to expand {claim_ref}: status is {r['status']}.\n"
                f"A {r['status']} claim is a known-bad statement about the source; "
                f"printing it next to real source code is exactly how it gets believed.\n"
                f"Read the file directly if you need that region.")
        if r["status"] not in BLOCKED_STATUS:
            sys.exit(f"refusing to expand {claim_ref}: status is {r['status']} "
                     f"(unproven). Re-run with --include-unproven if you are exploring, "
                     f"or validate it first.")
        print("!" * 72, file=sys.stderr)
        print(f"!! {r['status']} CLAIM — NOT A FACT. Shown only because --debug-blocked "
              f"was passed.", file=sys.stderr)
        print(f"!! Do not quote, summarize, or answer from the text below.",
              file=sys.stderr)
        print("!" * 72, file=sys.stderr)

    p = root / r["source"]
    if not p.exists():
        sys.exit(f"source missing: {p}")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if r["anchor_line"]:
        lo, hi = max(1, r["anchor_line"] - 8), min(len(lines), r["anchor_line"] + 12)
    else:
        lo, hi = r["region_lo"] or 1, min(len(lines), r["region_hi"] or len(lines))
    print(f"# {r['source']}:{lo}-{hi}  ({r['claim_id']} [{r['status']}])")
    print(f"# claim: {r['text']}\n")
    for i in range(lo, hi + 1):
        print(f"{i}: {lines[i-1]}")


def feedback(db, spec: str):
    """Teach the ranker. Feedback is the only signal that reflects real usefulness
    rather than a guess made at index time, so it outweighs specificity over time."""
    ref, verdict = spec.rsplit(":", 1)
    src, cid = (ref.split(":", 1) if ":" in ref else (None, ref))
    col = {"helpful": "helpful", "unhelpful": "unhelpful"}.get(verdict)
    if not col:
        sys.exit("verdict must be 'helpful' or 'unhelpful'")
    q = f"UPDATE claims SET {col} = {col} + 1 WHERE claim_id = ?" + (
        " AND source = ?" if src else "")
    cur = db.execute(q, (cid, src) if src else (cid,))
    db.commit()
    print(f"recorded {verdict} for {ref} ({cur.rowcount} claim(s))")
    print("run memo_db.py --build to fold it into utility scores")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="?")
    ap.add_argument("--memo-dir", default=".memo", type=pathlib.Path)
    ap.add_argument("--root", type=pathlib.Path, help="source root (default: memo-dir parent)")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--floor", type=float, default=UTILITY_FLOOR)
    ap.add_argument("--include-unproven", action="store_true",
                    help="also return NEEDS_AGENT claims (leads, not answers)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--expand", metavar="CLAIM", help="print the source region for a claim")
    ap.add_argument("--debug-blocked", action="store_true",
                    help="debugging only: expand a REFUTED/STALE claim behind a banner")
    ap.add_argument("--feedback", metavar="CLAIM:helpful|unhelpful")
    args = ap.parse_args()

    memo_dir = args.memo_dir.resolve()
    root = (args.root or memo_dir.parent).resolve()

    state, detail = index_state(memo_dir)
    if state == "missing":
        sys.exit(f"no index at {memo_dir/'index.db'} — run memo_db.py --build first")
    if state == "old-schema":
        sys.exit(f"index at {memo_dir/'index.db'} is unusable: {detail}\n"
                 f"run memo_db.py --build --memo-dir {memo_dir}")
    if state == "empty":
        # Not the same sentence as "nothing matched". An empty index means there is no
        # corpus; a reader told "no claims matched" concludes the fact is absent from the
        # material, which is a different and wrong conclusion.
        sys.exit(f"index at {memo_dir/'index.db'} contains no claims — nothing has been "
                 f"indexed yet.\nrun memo_gen.py, then memo_db.py --build "
                 f"--memo-dir {memo_dir}")
    if state == "stale" and not args.feedback:
        sys.exit(f"STALE INDEX — refusing to answer.\n"
                 f"  {detail}\n"
                 f"The index cannot prove a claim is still CONFIRMED (or still not "
                 f"REFUTED) when the memos have moved under it.\n"
                 f"run memo_db.py --build --memo-dir {memo_dir}")

    db = connect(memo_dir, create=False)

    if args.feedback:
        if state == "stale":
            print(f"WARNING: index is stale ({detail}) — rebuild before querying.",
                  file=sys.stderr)
        return feedback(db, args.feedback)
    if args.expand:
        return expand(db, memo_dir, args.expand, root, args.include_unproven,
                      args.debug_blocked)
    if not args.question:
        ap.print_help()
        return

    hits = search(db, args.question, args.limit, args.floor, args.include_unproven)
    if hits:
        bump_hits(db, [h["rowid"] for h in hits])

    if args.json:
        print(json.dumps({"question": args.question, "claims": hits}, indent=2))
        return

    if not hits:
        print("no claims matched above the utility floor.")
        print("try --include-unproven, or --floor 0 to see everything (including junk).")
        print("(refuted and stale claims are never returned by any flag.)")
        return

    approx = sum(len(h["text"]) // 4 + 12 for h in hits)
    print(f"# {len(hits)} claim(s) for: {args.question}   (~{approx} tokens)\n")
    for h in hits:
        loc = f"{h['source']}:{h['line']}" if h["line"] else \
              f"{h['source']}:{h['region'][0]}-{h['region'][1]}"
        flag = "" if h["status"] in ("CONFIRMED", "DRIFTED") else f"  [{h['status']}]"
        print(f"[{h['id']}] {h['text']}{flag}")
        print(f"      {loc}   score {h['score']}  utility {h['utility']}")
    print(f"\nsource for any of these: memo_query.py --expand <source>:<id>")
    unproven = [h for h in hits if h["status"] in UNPROVEN_STATUS]
    if unproven:
        print(f"{len(unproven)} unproven — validate before relying on them: "
              f"validate.py --claims {','.join(h['id'] for h in unproven)}")


if __name__ == "__main__":
    main()
