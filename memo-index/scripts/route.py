#!/usr/bin/env python3
"""Pick the right local model for a job, and order work so we don't thrash.

Three models, three genuinely different strengths — this is not redundancy:

  qwen2.5-coder:7b  4.7GB   code: declarations, signatures, call structure
  llama3.1:8b       4.9GB   prose: papers, docs, argument structure
  gemma4:12b        7.6GB   262k context, vision, thinking — the escalation model

The 262k window matters where chunking destroys meaning (a whole paper at once).
Vision matters because the text pipeline is blind to figures. Thinking matters
because it is the only local model that can stand in for a subagent — which is what
makes the whole pipeline work offline.

Measured constraint on a 16GB machine: only one model is resident at a time, and a
switch costs ~20s. So routing per file and letting the order fall out at random is
how you turn a 3-minute run into a 15-minute one. plan() batches by model instead.

  route.py --plan files.json        # print a batched execution plan
  route.py --for code               # which model would handle this profile
  route.py --check                  # which of the three are actually pulled
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

MODELS = {
    "code": os.environ.get("MEMO_MODEL_CODE", "qwen2.5-coder:7b"),
    "prose": os.environ.get("MEMO_MODEL_PROSE", "llama3.1:8b"),
    "generic": os.environ.get("MEMO_MODEL_PROSE", "llama3.1:8b"),
    # Escalation: long-context, vision, and thinking all land on the same model.
    "long": os.environ.get("MEMO_MODEL_BIG", "gemma4:12b"),
    "vision": os.environ.get("MEMO_MODEL_BIG", "gemma4:12b"),
    "reason": os.environ.get("MEMO_MODEL_BIG", "gemma4:12b"),
}

# Context windows we can actually use. Exceeding these silently truncates, and a
# truncated model invents line numbers for the part it never saw.
CTX = {
    "qwen2.5-coder:7b": 8192,
    "llama3.1:8b": 8192,
    "gemma4:12b": int(os.environ.get("MEMO_BIG_NUM_CTX", "65536")),
}

VISION_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}

# Above this many tokens, chunking a document into 8k pieces costs more meaning than
# the big model's slower inference costs time — send it to the 262k window whole.
LONG_DOC_TOKENS = 6000


def model_for(profile: str) -> str:
    return os.environ.get("MEMO_MODEL") or MODELS.get(profile, MODELS["generic"])


def ctx_for(model: str) -> int:
    return CTX.get(model, 8192)


def classify(path: str, profile: str, est_tokens: int, whole_doc_ok: bool = True) -> str:
    """Decide which model role a file needs.

    Order matters: vision beats everything (nothing else can read the file at all),
    then length (nothing else can hold it), then the profile specialist.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in VISION_EXT:
        return "vision"
    if whole_doc_ok and profile in ("prose", "generic") and est_tokens > LONG_DOC_TOKENS:
        return "long"
    return profile


def available() -> set:
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=3) as r:
            return {m["name"] for m in json.loads(r.read()).get("models", [])}
    except (urllib.error.URLError, OSError, KeyError):
        return set()


def online() -> bool:
    """Is the Anthropic API reachable? Decides whether semantic validation can use
    subagents or must fall back to the local thinking model."""
    try:
        req = urllib.request.Request("https://api.anthropic.com/v1/models",
                                     method="HEAD")
        urllib.request.urlopen(req, timeout=4)
        return True
    except urllib.error.HTTPError:
        return True  # any HTTP answer means the network is there
    except (urllib.error.URLError, OSError):
        return False


def plan(items: list[dict]) -> list[dict]:
    """Group work by model so each one loads once.

    items: [{"path":..., "profile":..., "est_tokens":...}, ...]
    """
    have = available()
    batches: dict[str, list] = {}
    fallbacks = []

    for it in items:
        role = classify(it["path"], it.get("profile", "generic"),
                        it.get("est_tokens", 0))
        m = model_for(role)
        if m not in have:
            # Degrade rather than fail: a memo from the wrong specialist still gets
            # verified, and a missing memo does not.
            alt = model_for(it.get("profile", "generic"))
            if alt in have and alt != m:
                fallbacks.append({"path": it["path"], "wanted": m, "using": alt,
                                  "role": role})
                m = alt
            else:
                fallbacks.append({"path": it["path"], "wanted": m, "using": None,
                                  "role": role})
                continue
        batches.setdefault(m, []).append({**it, "role": role})

    # Cheapest-to-load first, so the common case finishes before any big-model wait.
    order = sorted(batches.items(), key=lambda kv: 0 if "7b" in kv[0] else
                                                   (1 if "8b" in kv[0] else 2))
    return [{"model": m, "num_ctx": ctx_for(m), "files": fs} for m, fs in order], fallbacks


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", type=argparse.FileType("r"),
                    help="JSON list of {path, profile, est_tokens}")
    ap.add_argument("--for", dest="profile", help="print the model for a profile/role")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.profile:
        print(model_for(args.profile))
        return

    if args.check:
        have = available()
        print(f"ollama models present: {len(have)}")
        for role in ("code", "prose", "long", "vision", "reason"):
            m = model_for(role)
            print(f"  {role:<7} -> {m:<20} {'ok' if m in have else 'MISSING'}")
        net = online()
        print(f"\nnetwork: {'online' if net else 'OFFLINE'}")
        print("semantic validation will use: " +
              ("subagents" if net else f"{model_for('reason')} (local thinking model)"))
        return

    if args.plan:
        batches, fb = plan(json.load(args.plan))
        for b in batches:
            print(f"[{b['model']}] num_ctx={b['num_ctx']}  {len(b['files'])} file(s)")
            for f in b["files"][:6]:
                print(f"    {f['role']:<7} {f['path']}")
            if len(b["files"]) > 6:
                print(f"    ... +{len(b['files'])-6} more")
        for f in fb:
            where = f["using"] or "SKIPPED"
            print(f"  ! {f['path']}: wanted {f['wanted']} -> {where}", file=sys.stderr)
        print(f"\n{len(batches)} model load(s) instead of {sum(len(b['files']) for b in batches)} "
              f"— each switch costs ~20s on 16GB")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
