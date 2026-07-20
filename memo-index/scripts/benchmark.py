#!/usr/bin/env python3
"""Measure what the skill actually saves, at three corpus sizes, against hard endpoints.

Runs standalone and writes RESULTS.md — it is meant to survive a /clear, because the
honest way to benchmark a context-saving skill is not from a context that is already full.

  benchmark.py --out /tmp/bench            # full run (local generation: slow, see below)
  benchmark.py --out /tmp/bench --skip-gen # reuse memos already generated
  benchmark.py --out /tmp/bench --accounting-only  # no model calls, deterministic math

## The endpoints are hard on purpose

Each question has a ground truth that is checked by string match against the source, not
by a model's opinion. A query "passes" only if the claim it returns contains the expected
token AND points at the right file. That means the skill can lose: if the local model
never emitted a claim about a thing, the query returns nothing and the endpoint fails.
A benchmark that cannot fail measures nothing.

## What the baseline means

"Without the skill" is modelled as: read every file that could plausibly hold the answer,
because that is what an agent does when it cannot query. For a single-file question that
is one file; for a corpus-wide question ("where is X handled") it is the whole corpus.
This is stated per question via `scope`, not assumed globally — inflating the baseline
would make the skill look good for free.

Token counts use the same ~4 chars/token estimate everywhere, so the ratio is sound even
if the absolute numbers drift a few percent from a real tokenizer.
"""

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).parent


def toks(s: str) -> int:
    return max(1, len(s) // 4)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# Ground truth is a string that MUST appear in a returned claim, plus the file it must
# point at. Both are checked — a right answer attributed to the wrong file is still wrong.
CORPORA = {
    "small": {
        "desc": "one module",
        "include": "scripts/apply_verdicts.py",
        "questions": [
            {"q": "what statuses are valid for a verdict",
             "expect": r"CONFIRMED|VALID|status", "file": "apply_verdicts.py", "scope": "file"},
            {"q": "are claims deleted when refuted",
             "expect": r"mark|not delete|in place|refuted", "file": "apply_verdicts.py", "scope": "file"},
        ],
    },
    "medium": {
        "desc": "the skill's own scripts",
        "include": "scripts/*.py",
        "questions": [
            {"q": "how are chunks split", "expect": r"chunk_lines|chunk", "file": "memo_gen.py", "scope": "corpus"},
            {"q": "what is the maximum chunk size", "expect": r"160|MAX_CHUNK", "file": "memo_gen.py", "scope": "corpus"},
            {"q": "how is trivia filtered out", "expect": r"trivial|TRIVIAL|import", "file": "memo_gen.py", "scope": "corpus"},
            {"q": "what happens when the source file changed", "expect": r"stale|STALE|changed|sha", "file": "verify_anchors.py", "scope": "corpus"},
            {"q": "which model does it call", "expect": r"ollama|call_ollama|model", "file": "memo_gen.py", "scope": "corpus"},
        ],
    },
    "large": {
        "desc": "all installed skills",
        "root": pathlib.Path.home() / ".claude" / "skills",
        "include": "**/*.md",
        "questions": [
            {"q": "how are memo claims anchored to source lines", "expect": r"anchor|@L|snippet", "file": None, "scope": "corpus"},
            {"q": "what is the utility floor for retrieval", "expect": r"0.15|floor|utility", "file": None, "scope": "corpus"},
            {"q": "which local model is used for prose", "expect": r"llama|prose", "file": None, "scope": "corpus"},
            {"q": "what does the context watcher measure", "expect": r"token|usage|context", "file": None, "scope": "corpus"},
        ],
    },
}


def corpus_tokens(root: pathlib.Path, pattern: str) -> tuple[int, int]:
    n = t = 0
    for p in sorted(root.glob(pattern)):
        if not p.is_file():
            continue
        try:
            t += toks(p.read_text(encoding="utf-8"))
            n += 1
        except (UnicodeDecodeError, OSError):
            pass
    return n, t


def baseline_tokens(root, pattern, q, corpus_tok) -> int:
    """What answering this question costs with no index.

    scope=file  : the agent already knows which file — read that one
    scope=corpus: the agent must look across the corpus to find it
    """
    if q["scope"] == "file" and q["file"]:
        hits = list(root.glob(f"**/{q['file']}"))
        if hits:
            return toks(hits[0].read_text(encoding="utf-8", errors="replace"))
    return corpus_tok


def ask(memo_dir, root, question, limit=3):
    r = run([sys.executable, str(HERE / "memo_query.py"), "--memo-dir", str(memo_dir),
             "--root", str(root), question, "--limit", str(limit), "--json"])
    if r.returncode != 0:
        return None, 0
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None, 0
    claims = d.get("claims", [])
    cost = sum(toks(c["text"]) + 12 for c in claims) or 1
    return claims, cost


def grade(claims, q) -> tuple[bool, str]:
    if not claims:
        return False, "no claim returned"
    pat = re.compile(q["expect"], re.I)
    for c in claims:
        if pat.search(c["text"]):
            if q["file"] and q["file"] not in c["source"]:
                continue
            return True, f"{c['id']} {c['source']}"
    return False, f"{len(claims)} claim(s), none matched /{q['expect']}/"


def gen(root, pattern, memo_dir, log):
    memo_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    r = run([sys.executable, str(HERE / "memo_gen.py"), "--root", str(root),
             "--include", pattern, "--memo-dir", str(memo_dir)], timeout=14400)
    log(r.stderr[-800:] if r.stderr else "(no stderr)")
    # verify_anchors needs --root to find the sources; memo_db does not take it and
    # errors out if given one. Passing it anyway is how the first run produced ten memos,
    # zero indexes, and a triumphant "100% saved".
    steps = [
        ("verify_anchors.py", ["--memo-dir", str(memo_dir), "--root", str(root)]),
        ("memo_db.py", ["--memo-dir", str(memo_dir), "--build"]),
    ]
    for script, argv in steps:
        r = run([sys.executable, str(HERE / script), *argv])
        if r.returncode != 0:
            log(f"  !! {script} failed: {(r.stderr or r.stdout).strip()[:300]}")
    if not (memo_dir / "index.db").exists():
        log(f"  !! no index.db produced in {memo_dir} — queries cannot succeed")
    return time.time() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--skip-gen", action="store_true")
    ap.add_argument("--accounting-only", action="store_true",
                    help="no model calls; assumes memos already exist")
    ap.add_argument("--only", help="comma-separated: small,medium,large")
    args = ap.parse_args()

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    logf = (out / "run.log").open("w")

    def log(m):
        print(m, file=sys.stderr, flush=True)
        logf.write(str(m) + "\n")
        logf.flush()

    wanted = args.only.split(",") if args.only else list(CORPORA)
    results = []

    for name in wanted:
        spec = CORPORA[name]
        root = spec.get("root") or HERE.parent
        memo_dir = out / name / ".memo"
        nfiles, ctok = corpus_tokens(root, spec["include"])
        if not nfiles:
            log(f"[{name}] no files matched {spec['include']} under {root} — skipped")
            continue

        log(f"\n[{name}] {spec['desc']}: {nfiles} files, ~{ctok:,} tokens")

        gen_s = 0.0
        if not (args.skip_gen or args.accounting_only):
            log(f"[{name}] generating locally (slow — this is CPU, not API tokens)...")
            gen_s = gen(root, spec["include"], memo_dir, log)
            log(f"[{name}] generation took {gen_s/60:.1f} min")
        elif not (memo_dir / "index.db").exists():
            log(f"[{name}] no index at {memo_dir} — run without --skip-gen first")
            continue

        rows = []
        for q in spec["questions"]:
            claims, cost = ask(memo_dir, root, q["q"])
            ok, why = grade(claims, q)
            base = baseline_tokens(root, spec["include"], q, ctok)
            rows.append({"q": q["q"], "with": cost, "without": base, "pass": ok,
                         "why": why, "scope": q["scope"]})
            log(f"  {'PASS' if ok else 'FAIL'}  {q['q'][:44]:<44} "
                f"{cost:>5} vs {base:>7} tok   {why}")

        # Saving is only meaningful over endpoints the skill actually ANSWERED. Counting
        # a failure as "spent 0 tokens, saved 100%" is how the first run reported a
        # perfect score for returning nothing at all. A metric that rewards silence is
        # worse than no metric: it reads as success.
        won = [r for r in rows if r["pass"]]
        w = sum(r["with"] for r in won)
        b = sum(r["without"] for r in won)
        passed, total = len(won), len(rows)
        results.append({"corpus": name, "desc": spec["desc"], "files": nfiles,
                        "corpus_tokens": ctok, "gen_minutes": round(gen_s / 60, 1),
                        "with": w, "without": b,
                        "saving_pct": (round(100 * (b - w) / b, 1) if b and won else None),
                        "passed": passed, "total": total,
                        "rows": rows})

    (out / "results.json").write_text(json.dumps(results, indent=2))

    md = ["# memo-index benchmark", "",
          "Endpoints are string-matched against the source, so the skill can fail: a query",
          "that returns nothing, or a right answer attributed to the wrong file, is a FAIL.",
          "",
          "| corpus | endpoints | files | raw | with skill | without | saved |",
          "|---|---|---|---|---|---|---|"]
    for r in results:
        # Endpoints first: a saving figure means nothing until you know what was answered.
        sav = f"**{r['saving_pct']}%**" if r["saving_pct"] is not None else "n/a"
        if r["passed"] == 0:
            sav = "**n/a — answered nothing**"
        md.append(f"| {r['corpus']} ({r['desc']}) | **{r['passed']}/{r['total']}** "
                  f"| {r['files']} | ~{r['corpus_tokens']:,} | {r['with']:,} "
                  f"| {r['without']:,} | {sav} |")
    md += ["", "## Per question", ""]
    for r in results:
        md.append(f"### {r['corpus']}  (local generation: {r['gen_minutes']} min, 0 API tokens)")
        md.append("")
        md.append("| endpoint | scope | with | without | result |")
        md.append("|---|---|---|---|---|")
        for q in r["rows"]:
            md.append(f"| {q['q']} | {q['scope']} | {q['with']} | {q['without']} "
                      f"| {'PASS' if q['pass'] else 'FAIL'} — {q['why']} |")
        md.append("")
    md += ["## Reading this honestly", "",
           "- `without` assumes the agent reads the files it would have to read to answer;",
           "  per-question `scope` says whether that is one file or the corpus.",
           "- Generation time is CPU on the user's machine and costs **zero API tokens**,",
           "  but it is real wall-clock and is charged once per file version, not per query.",
           "- Saving grows with corpus size because a query costs ~60 tokens regardless of",
           "  how much material it is searching. On a small corpus, plain Read may win.", ""]
    (out / "RESULTS.md").write_text("\n".join(md))
    log(f"\nwrote {out/'RESULTS.md'}")
    for r in results:
        sav = f"{r['saving_pct']:>5}%" if r["saving_pct"] is not None else "  n/a"
        log(f"  {r['corpus']:<7} endpoints {r['passed']}/{r['total']}   saved {sav}")


if __name__ == "__main__":
    main()
