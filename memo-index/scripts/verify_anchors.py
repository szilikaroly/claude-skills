#!/usr/bin/env python3
"""Verify memo claims by string match — no model, no tokens.

Each claim carries @L<line> `<snippet>`. The snippet must actually appear at (or
very near) that line in the source. This catches the local model's most common
failure mode — citing a symbol or line that isn't there — for free, so that only
genuinely semantic claims cost anything to check.

  verify_anchors.py --memo-dir .memo              # human summary
  verify_anchors.py --memo-dir .memo --json       # NEEDS_AGENT work list for step 4

Verdicts written back into the memo files in place:
  CONFIRMED    snippet found at/near the cited line
  REFUTED      snippet not found anywhere in the source
  DRIFTED      snippet found, but at a different line (anchor fixed automatically)
  NEEDS_AGENT  semantic claim; no string match can settle it
  STALE        source changed since the memo was generated — regenerate it

A string match can only ever produce CONFIRMED, DRIFTED or REFUTED about the *anchor*.
It never settles what a claim asserts. Nothing in here may promote a semantic claim to
CONFIRMED — the whole point of the pipeline is that no unchecked local-model output
leaves this script wearing a high-trust marker.
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile

# The trailing (?:<!--...-->)? is load-bearing: this script writes notes onto claims it
# has judged, and without it a second run would fail to re-parse its own output. Those
# claims would then be passed through untallied — the memo would report 100% confirmed
# precisely because the refuted ones had become invisible. Keep this idempotent.
CLAIM_RE = re.compile(
    r"^- \[C(\d+)\] \[(\w+)\] (.*?) (?:@L(\d+) `([^`]*)`|@semantic L(\d+)-(\d+))"
    r"\s*(?:<!--(.*)-->)?\s*$"
)
# Anything that *looks* like a claim. A line that matches this but not CLAIM_RE is
# malformed, and silence about it is the dangerous outcome: it keeps whatever marker it
# happens to carry and drops out of the tally, so a memo can report "100% CONFIRMED"
# precisely because its unverifiable claims became invisible. Count those out loud.
CLAIMISH_RE = re.compile(r"^- \[C\d+\]")
FM_RE = re.compile(r"^(\w+): (.*)$")

# Written into the comment of a drifted claim and read back on the next run. Drift is a
# permanent fact about the claim — the local model cited a line the snippet was not on —
# and re-running must not be able to launder it into CONFIRMED by re-reading the anchor
# this script itself corrected.
DRIFT_MARK_RE = re.compile(r"drifted from L(\d+)")

# How far from the cited line we'll still call it a hit. Local models are often
# off by a line or two after a chunk boundary; that's drift, not fabrication.
DRIFT_WINDOW = 3

# Beyond DRIFT_WINDOW we will still look, but only within this many lines and only for a
# snippet specific enough that finding it elsewhere means something. A whole-file scan
# that takes the first hit is not drift detection: in a file with any repetition it finds
# `return None` somewhere and dresses a fabricated anchor up as a 0.9-trust DRIFTED.
FAR_WINDOW = 60
# Normalized length below which a snippet is too generic to relocate on. `return x`,
# `pass`, `}` and friends appear everywhere; matching them proves nothing.
FAR_MIN_LEN = 24


def atomic_write(path: pathlib.Path, text: str) -> None:
    """Replace a file's contents or leave them untouched — never truncate in between.

    These memos are the only record that a claim was ever checked. A crash (or a KeyError
    a few claims into the loop) partway through an in-place write leaves a half-memo with
    no backup, and the claims that vanished look like claims that never existed.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def safe_snip(s: str) -> str:
    """Make a source line safe to sit inside the `...` of an anchor.

    A backtick in the snippet closes the anchor delimiter early, and CLAIM_RE then fails
    on the very line this script just wrote. The claim keeps its marker forever and stops
    being counted — an unverified CONFIRMED that no tally can see. U+02CB looks like a
    backtick and parses like a letter.
    """
    return s.replace("`", "ˋ")


def safe_note(s: str) -> str:
    """Make text safe to sit inside an HTML comment (and keep it to one line)."""
    return re.sub(r"\s+", " ", s.replace("-->", "--›")).strip()[:160]


def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def parse_memo(mp: pathlib.Path) -> tuple[dict, list[str]]:
    lines = mp.read_text(encoding="utf-8").splitlines()
    fm = {}
    if lines and lines[0] == "---":
        for i, ln in enumerate(lines[1:], 1):
            if ln == "---":
                break
            m = FM_RE.match(ln)
            if m:
                fm[m.group(1)] = m.group(2)
    return fm, lines


def norm(s: str) -> str:
    """Collapse whitespace and drop backticks.

    Whitespace: a model that retypes indentation differently is still right. Backticks:
    table-row anchors have them stripped (they collide with the anchor delimiter), so the
    source side must strip them too for the match to hold. Both are formatting, not content.
    """
    return re.sub(r"\s+", " ", s.replace("`", "")).strip()


IDENT_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})`")


def locate_semantic(text: str, region: list[int], src_lines: list[str]):
    """Find the line a @semantic claim is probably *about*. Not a verdict.

    Small models label plenty of anchorable claims 'semantic' — "`CODE_EXT` holds the
    code file extensions" is about a specific line, not about meaning. Pointing the
    subagent at that line makes its job cheap and its answer better.

    What this must never do is decide the claim. An identifier occurring in the region
    says the claim is *about* something present; it says nothing about whether what the
    claim asserts is true. "`MAX_RETRIES` defaults to 3" next to `MAX_RETRIES = 9` names
    a real identifier on a real line and is flatly false. So the return value is a hint
    the claim carries into step 4 — the claim stays NEEDS_AGENT either way.

    Uniqueness is what makes the hint worth anything. An identifier on ten lines tells us
    nothing about which one was meant, so it produces no hint. The one exception is a
    definition site: `def f(...)` / `f = ...` is unambiguous even when f is used
    elsewhere, so a *single* definition wins regardless of how many usages there are.
    """
    idents = IDENT_RE.findall(text)
    if not idents:
        return None
    lo, hi = max(1, region[0]), min(len(src_lines), region[1])
    if lo > hi:
        return None
    for ident in idents:
        esc = re.escape(ident)
        hits = [i for i in range(lo - 1, hi)
                if re.search(rf"\b{esc}\b", src_lines[i])]
        if not hits:
            continue
        # A definition, not a mention. The keyword is required for the keyword form, and
        # the bare form must be an assignment — with the old optional keyword group a
        # plain call site `compute(x)` parsed as a definition of `compute`.
        defs = [i for i in hits if re.match(
            rf"\s*(?:(?:def|class|function|const|let|var)\s+{esc}\b"
            rf"|{esc}\s*(?::[^=]*)?=(?!=))",
            src_lines[i])]
        if len(defs) == 1:
            return defs[0] + 1, src_lines[defs[0]].strip()
        if len(hits) == 1:
            return hits[0] + 1, src_lines[hits[0]].strip()
    return None


def far_match(want: str, n: int, norm_lines: list[str]):
    """Relocate a snippet beyond the drift window — conservatively, or not at all.

    Three conditions, and all three matter. Bounded to FAR_WINDOW, because a match 400
    lines away is a different piece of code, not the same line that moved. Long enough to
    be distinctive, because `return None` occurs in every third function and finding one
    tells you nothing. Unique within the window, because if it matches twice we cannot
    say which one was meant — and guessing the first is exactly the whole-file first-hit
    behaviour this replaces.

    Failing all three is REFUTED, which is the honest answer: the snippet is not where
    the claim said it was, and we cannot show where it went.
    """
    if len(want) < FAR_MIN_LEN:
        return None
    lo = max(0, n - 1 - FAR_WINDOW)
    hi = min(len(norm_lines), n + FAR_WINDOW)
    hits = [i for i in range(lo, hi) if want in norm_lines[i]]
    if len(hits) != 1:
        return None
    return hits[0]


def check(memo_dir: pathlib.Path, root: pathlib.Path):
    results = []
    for mp in sorted(memo_dir.glob("*.memo.md")):
        fm, lines = parse_memo(mp)
        src_rel = fm.get("source", "")
        src = root / src_rel
        entry = {"memo": str(mp), "source": src_rel, "claims": []}

        if not src.exists():
            entry["error"] = "source file missing"
            results.append(entry)
            continue
        if fm.get("sha256") and sha256_file(src) != fm["sha256"]:
            entry["error"] = "STALE — source changed since memo was generated"
            results.append(entry)
            continue

        src_lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
        norm_lines = [norm(l) for l in src_lines]
        out = []

        for ln in lines:
            m = CLAIM_RE.match(ln)
            if not m:
                if CLAIMISH_RE.match(ln):
                    # Looks like a claim, doesn't parse as one. Never pass it through
                    # quietly wearing whatever marker it has.
                    entry["claims"].append({
                        "id": "?", "text": ln.strip()[:120], "status": "UNPARSEABLE",
                        "note": "claim line does not parse — cannot be verified",
                        "source": src_rel,
                    })
                out.append(ln)
                continue
            cid, _old, text, aline, snip, rlo, rhi, comment = m.groups()

            if aline is None:
                # Semantic. A subagent has to judge this; the most a string match can do
                # is tell that subagent where to look. Recording the hint inside the
                # comment (not as an @L anchor) keeps the claim semantic on the next run,
                # so this stays idempotent and cannot drift into the anchored branch.
                located = locate_semantic(text, [int(rlo), int(rhi)], src_lines)
                claim = {"id": f"C{cid}", "text": text, "status": "NEEDS_AGENT",
                         "region": [int(rlo), int(rhi)], "source": src_rel}
                hint = ""
                if located:
                    rline, rsnip = located
                    claim["anchor_hint"] = rline
                    claim["anchor_hint_snippet"] = rsnip
                    claim["note"] = f"located at L{rline}; assertion still unverified"
                    hint = f"  <!-- {safe_note(f'likely anchor L{rline}: {rsnip}')} -->"
                out.append(f"- [C{cid}] [NEEDS_AGENT] {text} @semantic L{rlo}-{rhi}{hint}")
                entry["claims"].append(claim)
                continue

            n = int(aline)
            want = norm(snip)
            # If this claim was already found to have drifted, that verdict is permanent.
            # The anchor below now points where *this script* moved it, so an exact hit
            # proves nothing about what the local model originally cited.
            drifted_from = DRIFT_MARK_RE.search(comment or "")
            status, note, fixed = "REFUTED", "snippet not found in source", n

            if not want:
                note = "empty snippet"
            elif not (0 < n <= len(norm_lines)):
                # An anchor pointing past the end of the file is not a near miss, it is a
                # line that does not exist. Hunting elsewhere for the snippet and calling
                # the result drift is how a fabricated citation earns 0.9 trust.
                note = (f"anchor L{n} is outside the source "
                        f"({len(norm_lines)} line(s)) — anchor fabricated")
            elif want in norm_lines[n - 1]:
                status, note = "CONFIRMED", ""
            else:
                lo = max(0, n - 1 - DRIFT_WINDOW)
                hi = min(len(norm_lines), n + DRIFT_WINDOW)
                near = [i for i in range(lo, hi) if want in norm_lines[i]]
                if near:
                    status = "DRIFTED"
                    note = f"drifted from L{n}, anchor corrected to L{near[0]+1}"
                    fixed = near[0] + 1
                else:
                    far = far_match(want, n, norm_lines)
                    if far is not None:
                        status = "DRIFTED"
                        note = (f"drifted from L{n}, anchor corrected to L{far+1} "
                                f"(beyond the drift window)")
                        fixed = far + 1
                    elif len(want) < FAR_MIN_LEN:
                        note = ("snippet not found near the cited line; too generic to "
                                "relocate on")

            if drifted_from and status in ("CONFIRMED", "DRIFTED"):
                # Already known to have drifted. Keep saying so, and keep saying it in the
                # original words: the first run's note names the line the local model
                # actually cited, and that is the fact re-running must not erase. Carrying
                # it through verbatim also makes this a true fixed point.
                status = "DRIFTED"
                note = safe_note(comment or "")

            out.append(f"- [C{cid}] [{status}] {text} @L{fixed} `{safe_snip(snip)}`"
                       + (f"  <!-- {safe_note(note)} -->" if note else ""))
            entry["claims"].append({
                "id": f"C{cid}", "text": text, "status": status,
                "line": fixed, "note": note, "source": src_rel,
            })

        # Refresh the frontmatter tally so a memo tells you its own trust level.
        tally = {}
        for c in entry["claims"]:
            tally[c["status"]] = tally.get(c["status"], 0) + 1
        summary = ", ".join(f"{v} {k.lower()}" for k, v in sorted(tally.items()))
        out = [re.sub(r"^verified: .*$", f"verified: {summary or 'no claims'}", l)
               for l in out]

        atomic_write(mp, "\n".join(out) + "\n")
        results.append(entry)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--memo-dir", default=".memo", type=pathlib.Path)
    ap.add_argument("--root", default=None, type=pathlib.Path,
                    help="source root (default: parent of --memo-dir)")
    ap.add_argument("--json", action="store_true",
                    help="emit the NEEDS_AGENT work list for step 4")
    args = ap.parse_args()

    memo_dir = args.memo_dir.resolve()
    root = (args.root or memo_dir.parent).resolve()
    results = check(memo_dir, root)

    if args.json:
        work = [c for r in results for c in r["claims"] if c["status"] == "NEEDS_AGENT"]
        # A memo whose source is missing or stale contributes no claims at all. Without
        # an explicit list, a caller reading needs_agent==[] concludes "everything is
        # verified" when the truth is "nothing was looked at". Say so in the payload.
        unusable = [{"memo": r["memo"], "source": r["source"], "error": r["error"]}
                    for r in results if "error" in r]
        bad = [c for r in results for c in r["claims"] if c["status"] == "UNPARSEABLE"]
        print(json.dumps({
            "needs_agent": work,
            "unusable_sources": unusable,
            "unparseable_claims": bad,
            "files": results,
        }, indent=2))
        if unusable or bad:
            print(f"!! {len(unusable)} unusable memo(s), {len(bad)} unparseable claim(s) "
                  f"— these were NOT verified", file=sys.stderr)
        return

    tally: dict[str, int] = {}
    checked = 0
    for r in results:
        if "error" in r:
            print(f"!! {r['source']}: {r['error']}")
            continue
        checked += 1
        for c in r["claims"]:
            tally[c["status"]] = tally.get(c["status"], 0) + 1

    total = sum(tally.values())
    if not total:
        print(f"\nno claims checked ({len(results) - checked} memo(s) unusable — "
              f"regenerate with memo_gen.py --force)")
        return
    print(f"\n{total} claims across {checked} memos — settled without a model:")
    for k in ("CONFIRMED", "DRIFTED", "REFUTED", "NEEDS_AGENT"):
        v = tally.get(k, 0)
        print(f"  {k:<12} {v:>4}  ({100*v//total}%)")

    na = tally.get("NEEDS_AGENT", 0)
    ref = tally.get("REFUTED", 0)
    bad = tally.get("UNPARSEABLE", 0)
    if bad:
        print(f"\nWARNING: {bad} claim line(s) could not be parsed and were NOT verified.")
        print("         They keep whatever marker they carry — treat them as unverified.")
    if ref > total * 0.4:
        print(f"\nWARNING: {100*ref//total}% refuted. The local model likely lost the plot")
        print("         on these files. Regenerate, or just Read them directly.")
    if na:
        print(f"\n{na} semantic claim(s) need a subagent each — see --json for the list.")
    else:
        print("\nNothing left for subagents. Pipeline done.")


if __name__ == "__main__":
    main()
