#!/usr/bin/env python3
"""Dynamic validation: spend verification effort where it actually matters.

The old pipeline validated every claim the moment it was generated. That pays for claims
nobody ever retrieves, which on a real corpus is most of them. This one is hybrid:

  eager   high-utility claims are validated up front, because they are the ones a query
          will surface, and a lead you cannot trust at retrieval time is a lead you have
          to stop and pay for mid-thought
  lazy    everything else waits until something actually asks for it
  depth   scales with stakes: a passing mention gets one verifier, a claim you are about
          to build a conclusion on gets a panel with different lenses

Depth is the part worth understanding. Three verifiers who all ask "is this true?" mostly
agree with each other — redundancy, not rigor. Three verifiers asking "does the source say
this?", "does anything contradict it?", "would this still hold at the edges?" fail in
different ways, which is what actually catches a wrong claim.

  validate.py --memo-dir .memo --pending            # what is unvalidated, by utility
  validate.py --memo-dir .memo --plan --eager       # work orders for high-utility claims
  validate.py --memo-dir .memo --plan --claims C3,C7 --depth 3
  validate.py --memo-dir .memo --local --claims C3  # offline: use the local thinking model
  validate.py --memo-dir .memo --apply verdicts.json

--plan emits JSON for the caller to fan out as subagents (smart, costs API tokens).
--local runs the same prompts through gemma4 (free, offline, slower, less sharp).
Both write verdicts in the same shape, so the pipeline does not care which ran.
"""

import argparse
import json
import os
import pathlib
import re
import sqlite3
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from memo_db import connect  # noqa: E402
import route  # noqa: E402

# Claims at or above this are validated eagerly: they are what queries return, so an
# unproven one would stall a real answer.
EAGER_UTILITY = 0.55

# The only statuses a verdict may set. Kept identical to apply_verdicts.VALID so the two
# writers cannot disagree about what a verdict is allowed to say.
APPLICABLE_STATUS = {"CONFIRMED", "REFUTED", "UNSUPPORTED"}

LENSES = {
    "literal": (
        "Does the source text state this claim, literally? Quote the line that does, or "
        "say plainly that no line does. Do not reason about what the code probably means "
        "— only what it says."),
    "contradiction": (
        "Try to REFUTE this claim from the source. Look for anything that contradicts it: "
        "a different value, an opposite branch, a later override. Report a contradiction "
        "if you find one; if you genuinely cannot, say so."),
    "edges": (
        "Does this claim hold in every case the source covers, or only the common one? "
        "Check error paths, empty input, and early returns. A claim true only on the happy "
        "path is UNSUPPORTED as stated."),
}

VERDICT_SHAPE = """Reply as JSON only:
{"status": "CONFIRMED|REFUTED|UNSUPPORTED", "line": <int or null>, "note": "<one sentence>"}

CONFIRMED  the source states or plainly entails the claim
REFUTED    the source contradicts it
UNSUPPORTED the source does not settle it — this is the right answer more often than it
           feels, and a claim that might be true elsewhere is still UNSUPPORTED here."""


def build_prompt(claim: dict, src_text: str, lens: str) -> str:
    if claim.get("image"):
        # The verifier must look at the picture, not at text. A subagent does this with
        # the Read tool (it renders images); the --local path re-runs the vision model.
        return f"""Open and look at the image file below. You have not seen any description
of it, and you should not assume the claim is true.

IMAGE: {claim['source']}

CLAIM about the image: "{claim['text']}"

Judge the claim against what you actually see in the image. Confirm only what is visibly
there; do not infer values that are not legible.

{VERDICT_SHAPE}"""
    return f"""You are checking one claim against one piece of source. You have not seen
any summary of this file, and you should not assume the claim is true.

--- SOURCE: {claim['source']} lines {claim['lo']}-{claim['hi']} ---
{src_text}
--- END SOURCE ---

CLAIM: "{claim['text']}"

{LENSES[lens]}

{VERDICT_SHAPE}"""


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}


def is_image(source: str) -> bool:
    return pathlib.Path(source).suffix.lower() in IMAGE_EXT


def region_for(r: sqlite3.Row, root: pathlib.Path):
    """Return (body, lo, hi). body is None when there is nothing to verify against.

    None means skip, and the caller must say why out loud. A claim that quietly drops out
    of the plan stays NEEDS_AGENT forever while the run reports success.
    """
    p = root / r["source"]
    if not p.exists():
        return None, 0, 0
    if is_image(r["source"]):
        # An image has no text region. The "source" a verifier checks against is the
        # picture itself: a subagent reads it with the Read tool, the local path re-runs
        # vision. Reading the bytes as text here would feed the verifier garbage.
        return f"[IMAGE FILE: {p}]", 0, 0
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if r["anchor_line"]:
        lo, hi = max(1, r["anchor_line"] - 12), min(len(lines), r["anchor_line"] + 15)
    else:
        lo = max(1, r["region_lo"] or 1)
        hi = min(len(lines), r["region_hi"] or len(lines))
    if lo > hi or not lines:
        # An anchor past the end of the file clamps to lo > hi, and the join below then
        # produces "" rather than None — so the job is built anyway and a verifier is
        # asked to judge a claim against an empty source region under a nonsense heading
        # like "lines 888-6". Whatever it answers is meaningless. Skip it instead.
        return None, lo, hi
    body = "\n".join(f"{i}: {lines[i-1]}" for i in range(lo, hi + 1))
    if not body.strip():
        return None, lo, hi
    return body, lo, hi


def select_claims(db, args) -> list:
    q = "SELECT rowid, * FROM claims WHERE status IN ('NEEDS_AGENT','PENDING')"
    params = []
    if args.claims:
        ids = [c.strip() for c in args.claims.split(",")]
        q += f" AND claim_id IN ({','.join('?'*len(ids))})"
        params += ids
    if args.eager:
        q += " AND utility >= ?"
        params.append(args.eager_threshold)
    if args.source:
        q += " AND source = ?"
        params.append(args.source)
    q += " ORDER BY utility DESC"
    if args.limit:
        q += f" LIMIT {int(args.limit)}"
    return list(db.execute(q, params))


def depth_for(utility: float, override: int | None) -> int:
    """How many independent verifiers this claim earns.

    Utility is the best available proxy for stakes: a claim the ranker will surface often
    is one that will be believed often. Anything a caller knows better, --depth overrides.
    """
    if override:
        return override
    if utility >= 0.7:
        return 3
    if utility >= 0.4:
        return 2
    return 1


def lenses_for(depth: int) -> list:
    return ["literal", "contradiction", "edges"][:max(1, min(3, depth))]


def call_local(prompt: str, model: str) -> dict:
    payload = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"num_ctx": route.ctx_for(model), "temperature": 0.0},
    }).encode()
    req = urllib.request.Request(f"{route.OLLAMA_HOST}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            resp = json.loads(r.read())["response"]
    except urllib.error.URLError as e:
        return {"status": "UNSUPPORTED", "line": None, "note": f"local model failed: {e}"}
    m = __import__("re").search(r"\{.*\}", resp, __import__("re").S)
    if not m:
        return {"status": "UNSUPPORTED", "line": None, "note": "unparseable local verdict"}
    try:
        d = json.loads(m.group(0))
        if d.get("status") not in ("CONFIRMED", "REFUTED", "UNSUPPORTED"):
            return {"status": "UNSUPPORTED", "line": None, "note": "bad status from model"}
        return d
    except json.JSONDecodeError:
        return {"status": "UNSUPPORTED", "line": None, "note": "unparseable local verdict"}


def call_local_vision(prompt: str, source: str, root: pathlib.Path, model: str) -> dict:
    """Verify an image claim by looking at the image again with the vision model.

    A second independent look is the image equivalent of re-reading the source region —
    the model is not shown its own earlier description, only the claim and the picture.
    """
    import base64
    p = root / source
    try:
        b64 = base64.b64encode(p.read_bytes()).decode()
    except OSError as e:
        return {"status": "UNSUPPORTED", "line": None, "note": f"cannot read image: {e}"}
    payload = json.dumps({
        "model": model, "prompt": prompt, "images": [b64], "stream": False,
        "options": {"temperature": 0.0},
    }).encode()
    req = urllib.request.Request(f"{route.OLLAMA_HOST}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            resp = json.loads(r.read())["response"]
    except (urllib.error.URLError, OSError) as e:
        return {"status": "UNSUPPORTED", "line": None, "note": f"vision failed: {e}"}
    m = re.search(r"\{.*\}", resp, re.S)
    if not m:
        return {"status": "UNSUPPORTED", "line": None, "note": "unparseable vision verdict"}
    try:
        d = json.loads(m.group(0))
        if d.get("status") in ("CONFIRMED", "REFUTED", "UNSUPPORTED"):
            return d
    except json.JSONDecodeError:
        pass
    return {"status": "UNSUPPORTED", "line": None, "note": "bad vision verdict"}


def merge(verdicts: list[dict]) -> dict:
    """Combine a panel into one verdict, biased toward doubt.

    A single REFUTED outweighs any number of CONFIRMEDs: verifiers that agree a claim is
    fine may simply not have looked where the problem is, but one that found a real
    contradiction did look. Anything short of unanimous confirmation is UNSUPPORTED, not
    a majority-rules CONFIRMED — the cost of a wrong CONFIRMED is a false fact in an
    answer, while the cost of a wrong UNSUPPORTED is one extra source read.
    """
    if not verdicts:
        # An empty panel means nobody looked. `all()` is vacuously true on an empty list,
        # so the CONFIRMED branch below would claim unanimous confirmation from zero
        # verifiers — and then raise IndexError reaching for verdicts[0].
        return {"status": "UNSUPPORTED", "line": None,
                "note": "no verifier returned a verdict"}
    if any(v["status"] == "REFUTED" for v in verdicts):
        ref = next(v for v in verdicts if v["status"] == "REFUTED")
        return {"status": "REFUTED", "line": ref.get("line"),
                "note": ref.get("note", "")[:160]}
    if all(v["status"] == "CONFIRMED" for v in verdicts):
        return {"status": "CONFIRMED", "line": verdicts[0].get("line"),
                "note": verdicts[0].get("note", "")[:160]}
    uns = next((v for v in verdicts if v["status"] == "UNSUPPORTED"), verdicts[0])
    return {"status": "UNSUPPORTED", "line": uns.get("line"),
            "note": uns.get("note", "")[:160]}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--memo-dir", default=".memo", type=pathlib.Path)
    ap.add_argument("--root", type=pathlib.Path)
    ap.add_argument("--claims", help="comma-separated claim ids")
    ap.add_argument("--source", help="restrict to one source file")
    ap.add_argument("--eager", action="store_true",
                    help="select high-utility unvalidated claims")
    ap.add_argument("--eager-threshold", type=float, default=EAGER_UTILITY)
    ap.add_argument("--depth", type=int, choices=[1, 2, 3],
                    help="override verifier count (default: scaled from utility)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--pending", action="store_true", help="list unvalidated claims")
    ap.add_argument("--plan", action="store_true", help="emit subagent work orders as JSON")
    ap.add_argument("--local", action="store_true",
                    help="run verification with the local thinking model (offline)")
    ap.add_argument("--apply", type=pathlib.Path, help="apply a verdicts JSON file")
    args = ap.parse_args()

    memo_dir = args.memo_dir.resolve()
    root = (args.root or memo_dir.parent).resolve()
    if not (memo_dir / "index.db").exists():
        sys.exit("no index.db — run memo_db.py --build first")
    db = connect(memo_dir)

    if args.apply:
        verdicts = json.loads(args.apply.read_text())
        applied, missed, rejected = 0, [], []
        for v in verdicts if isinstance(verdicts, list) else verdicts.get("verdicts", []):
            # An unrecognised status lands in claims.status where the trust table has no
            # entry for it and the REFUTED suppression never fires — a typo like "ok" or
            # a stray "CONFIRMED " would quietly disable the hallucination guard for
            # that claim. Reject it instead of writing it.
            st = str(v.get("status", "")).strip().upper()
            if st not in APPLICABLE_STATUS:
                rejected.append(f"{v.get('source')}:{v.get('id')} status={v.get('status')!r}")
                continue
            cur = db.execute(
                "UPDATE claims SET status=?, depth=? WHERE source=? AND claim_id=?",
                (st, v.get("depth", 1), v["source"], v["id"]))
            # A verdicts file written against a different path spelling (src/auth.ts vs
            # auth.ts) matches nothing. Counting the attempt rather than the write is how
            # "applied 12 verdict(s)" gets printed over an index that did not change.
            if cur.rowcount:
                applied += 1
            else:
                missed.append(f"{v.get('source')}:{v.get('id')}")
        db.commit()
        print(f"applied {applied} verdict(s) to the index")
        if missed:
            print(f"WARNING: {len(missed)} verdict(s) matched no claim — the index is "
                  f"unchanged for these. Check the source path spelling:", file=sys.stderr)
            for m in missed[:10]:
                print(f"  no such claim: {m}", file=sys.stderr)
            if len(missed) > 10:
                print(f"  ... and {len(missed) - 10} more", file=sys.stderr)
        if rejected:
            print(f"WARNING: {len(rejected)} verdict(s) rejected for an invalid status "
                  f"(allowed: {', '.join(sorted(APPLICABLE_STATUS))}):", file=sys.stderr)
            for r_ in rejected[:10]:
                print(f"  rejected: {r_}", file=sys.stderr)
        print("run memo_db.py --build to recompute utility, then apply_verdicts.py "
              "to mirror into the .memo.md files")
        return

    if args.pending:
        rows = list(db.execute(
            "SELECT claim_id, source, status, utility, substr(text,1,58) t FROM claims"
            " WHERE status IN ('NEEDS_AGENT','PENDING') ORDER BY utility DESC"))
        if not rows:
            print("nothing pending — every claim is either verified or refuted")
            return
        eager = [r for r in rows if r["utility"] >= args.eager_threshold]
        print(f"{len(rows)} unvalidated claim(s); {len(eager)} above the eager "
              f"threshold ({args.eager_threshold})\n")
        for r in rows[:25]:
            mark = "EAGER" if r["utility"] >= args.eager_threshold else "lazy "
            print(f"  [{mark}] {r['utility']:.2f}  {r['claim_id']:<5} {r['source']}")
            print(f"           {r['t']}")
        if len(rows) > 25:
            print(f"  ... +{len(rows)-25} more")
        print(f"\neager pass:  validate.py --plan --eager")
        print(f"cost saved:  {len(rows)-len(eager)} claim(s) stay lazy until something asks")
        return

    rows = select_claims(db, args)
    if not rows:
        print("no matching unvalidated claims")
        return

    jobs = []
    for r in rows:
        body, lo, hi = region_for(r, root)
        if body is None:
            continue
        claim = {"source": r["source"], "id": r["claim_id"], "text": r["text"],
                 "lo": lo, "hi": hi, "image": is_image(r["source"])}
        d = depth_for(r["utility"], args.depth)
        for lens in lenses_for(d):
            jobs.append({"claim": claim, "lens": lens, "depth": d,
                         "utility": r["utility"],
                         "prompt": build_prompt(claim, body, lens)})

    if args.plan:
        print(json.dumps({
            "mode": "subagent",
            "note": "Run each job as an isolated subagent. Do not let a verifier see the "
                    "memo, the other claims, or the other verdicts. Collect results into "
                    "verdicts.json as [{source, id, status, line, note, depth}] and apply "
                    "with --apply.",
            "jobs": jobs,
        }, indent=2))
        return

    if args.local:
        model = route.model_for("reason")
        if model not in route.available():
            sys.exit(f"{model} not pulled — this is the offline verification model.\n"
                     f"pull it, or use --plan and run subagents instead.")
        print(f"verifying {len(rows)} claim(s) with {model} ({len(jobs)} passes)...",
              file=sys.stderr)
        by_claim: dict = {}
        for j in jobs:
            if j["claim"].get("image"):
                v = call_local_vision(j["prompt"], j["claim"]["source"], root, model)
            else:
                v = call_local(j["prompt"], model)
            key = (j["claim"]["source"], j["claim"]["id"])
            by_claim.setdefault(key, {"depth": j["depth"], "vs": []})["vs"].append(v)
            print(f"  {j['claim']['id']} [{j['lens']}] -> {v['status']}", file=sys.stderr)
        out = []
        for (src, cid), d in by_claim.items():
            m = merge(d["vs"])
            out.append({"source": src, "id": cid, "depth": d["depth"], **m})
        print(json.dumps(out, indent=2))
        print(f"\napply with: validate.py --apply <this file>", file=sys.stderr)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
