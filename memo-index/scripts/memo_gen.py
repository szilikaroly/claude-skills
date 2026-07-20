#!/usr/bin/env python3
"""Generate line-anchored memos from source files using a LOCAL model.

The point: raw material never reaches the main context. A local model reads each
chunk and emits atomic claims, each with a line anchor that a later script can
verify by string match. See SKILL.md for the full pipeline.

  memo_gen.py --root . --include 'src/**/*.py' --memo-dir .memo
  memo_gen.py --root docs --include '**/*.md' --profile prose
  memo_gen.py --root . --include 'src/**/*.ts' --dry-run     # cost estimate only

Memos are keyed by source SHA-256, so re-runs only touch changed files.
Stdlib only — no pip install required.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
NUM_CTX = int(os.environ.get("MEMO_NUM_CTX", "8192"))

# Model per profile: a code-tuned model reads declarations well but flattens prose,
# and vice versa. MEMO_MODEL overrides both if you'd rather run one model.
PROFILE_MODELS = {
    "code": os.environ.get("MEMO_MODEL_CODE", "qwen2.5-coder:7b"),
    "prose": os.environ.get("MEMO_MODEL_PROSE", "llama3.1:8b"),
    "generic": os.environ.get("MEMO_MODEL_PROSE", "llama3.1:8b"),
}


def model_for(profile: str) -> str:
    return os.environ.get("MEMO_MODEL") or PROFILE_MODELS[profile]

CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".h",
            ".cpp", ".hpp", ".rb", ".php", ".swift", ".kt", ".cs", ".sh", ".sql",
            ".r", ".jl", ".lua", ".pl", ".pm", ".scala", ".ex", ".exs", ".zsh", ".bash"}
PROSE_EXT = {".md", ".txt", ".rst", ".org", ".tex"}

# A script with no extension is still code. Guessing from the shebang beats defaulting
# to the generic profile, which chunks on fixed line counts instead of declarations and
# hands the file to the prose model — measurably worse anchors on source.
SHEBANG_RE = re.compile(r"^#!.*\b(python|node|ruby|perl|bash|sh|zsh|Rscript|lua|julia)\b")

# Chunk sizes are per-profile: code splits on declarations, prose on headings.
# Both cap at MAX_CHUNK_LINES so the local model's context never overflows —
# an overflowing model silently drops the tail and invents line numbers for it.
MAX_CHUNK_LINES = 160

DECL_RE = re.compile(
    r"^(?:export\s+)?(?:async\s+)?"
    r"(?:def |class |function |const \w+\s*=\s*(?:async\s*)?\(|func |fn |type |interface |impl |struct |var |public |private |protected )"
)
HEADING_RE = re.compile(r"^#{1,6}\s+\S|^\S.*\n[=-]{3,}$")

PROMPTS = {
    "code": """You are indexing source code so a reader can decide whether this file is worth
opening, and jump to the right line if it is. They will NOT read the file itself.

Read the numbered lines below and output ATOMIC CLAIMS about what is there.

WHAT EARNS A CLAIM — only things a reader would be annoyed to have missed:
- what a function/class does, its signature, what it returns
- side effects: writes, network calls, mutations of shared state
- non-obvious behaviour: defaults, retries, error paths, hardcoded values
- relationships: what calls what, what owns what

Being terse is the job. A file with 4 real claims beats one with 30 padded ones. If a
chunk holds nothing worth indexing, output TOPIC and no claims — that is a correct answer.

Rules that matter:
- One fact per claim. Not "handles auth and logging" — that is two claims.
- Every claim MUST end with an anchor: @L<line> `<exact snippet copied from that line>`
- The snippet must be COPIED CHARACTER-FOR-CHARACTER from the line you cite. Do not
  retype it from memory, do not tidy it. It will be checked by string match and a
  claim with a wrong snippet is thrown away.
- Only claim what the lines show. No guesses about callers, config, or intent.
- If a claim is about meaning rather than text (what a module is *for*, what a
  design implies), mark it @semantic instead of an @L anchor.
- At most 6 claims for this chunk, usually fewer.

Then one TOPIC line: a 6-10 word description of what this chunk is.

Format exactly:
TOPIC: <short description>
- <claim> @L42 `def create_session(user_id, ttl):`
- <claim> @semantic

Good: `create_session` stores into the module-level _store and returns the session @L38 `_store[user_id] = s`
Bad:  Defines a function called create_session @L34 `def create_session(user_id: str, ttl: int) -> dict:`
""",
    "prose": """You are indexing a document so a reader can decide whether it is worth opening,
and jump to the right line if it is. They will NOT read the document itself.

Read the numbered lines below and output ATOMIC CLAIMS about what it says.

WHAT EARNS A CLAIM — the substance a reader came for:
- findings, results, conclusions — with their numbers
- methods, sample sizes, conditions, limitations the author admits
- definitions and named entities that the rest of the text leans on
- claims the author argues for (as opposed to mentions in passing)

Say what the text SAYS about a thing, not that it mentions the thing. "The paper discusses
mortality" is worthless; "mortality fell 12% in the treatment arm" is the claim.

Being terse is the job. Four real findings beat thirty hedged summaries. If a chunk is
throat-clearing with nothing worth indexing, output TOPIC and no claims — that is a
correct answer.

Rules that matter:
- One fact per claim. Split compound statements.
- Every claim MUST end with an anchor: @L<line> `<exact phrase copied from that line>`
- The phrase must be COPIED CHARACTER-FOR-CHARACTER from the line you cite. It will be
  checked by string match; a wrong phrase means the claim is thrown away.
- Numbers and named entities matter most — anchor those precisely. A number you retype
  from memory instead of copying is the single most damaging thing you can produce here.
- Do not add background knowledge. Only what these lines say.
- If a claim is about the argument as a whole rather than a specific line, mark it
  @semantic instead of an @L anchor.
- At most 6 claims for this chunk, usually fewer.

Then one TOPIC line: a 6-10 word description of what this chunk covers.

Format exactly:
TOPIC: <short description>
- <claim> @L88 `mortality fell by 12% in the treatment arm`
- <claim> @semantic

Good: mortality fell 12% in the treatment arm vs control @L88 `mortality fell by 12% in the treatment arm`
Bad:  The paper presents its results in this section @L85 `## Results`
""",
}
PROMPTS["generic"] = PROMPTS["prose"]

# Tables are the most fact-dense thing in technical prose and the easiest to miss: the
# heading-based chunker swallows them into a surrounding section, and the model narrates
# the section instead of reading the rows. Measured: "which local model is used for prose"
# failed on a corpus where the answer sat in a table row, in plain text, unclaimed.
# Each row is already an atomic fact with a natural anchor, so they get their own prompt.
PROMPTS["table"] = """The lines below are a table. Every row is already a fact.

Output ONE claim per data row — not per column, not a summary of the table.

Rules:
- Skip the header row and any separator row.
- State the row as a sentence that stands on its own without the table: a reader who
  sees only your claim must still know what it is about. Use the header names to do that.
- Every claim MUST end with @L<line> `<exact text copied from that row>`, copied
  character-for-character. It is checked by string match.
- Do not editorialize, rank, or explain the table. Just say what each row says.

Format exactly:
TOPIC: <what this table lists, 6-10 words>
- <row as a standalone fact> @L35 `| prose | MEMO_MODEL_PROSE | llama3.1:8b | 4.7 GB |`

Good: the prose profile uses model llama3.1:8b, set via MEMO_MODEL_PROSE, 4.7 GB @L35 `| prose | MEMO_MODEL_PROSE | llama3.1:8b | 4.7 GB |`
Bad:  the table lists models for each profile @L33 `| profile | env var | default |`
"""

TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def table_cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def table_claims(lines: list[str], lo: int, hi: int) -> list[dict]:
    """Turn a markdown table into claims WITHOUT a model.

    Measured: asking llama3.1 to emit table-row claims produced none, and asking it to
    copy the row as an anchor produced paraphrases that failed string match. But the exact
    row text is already in hand — so the anchor is the literal row (always verifies) and
    the claim is header=value pairs (so a query on any column finds the row). This is the
    skill's core move applied to tables: do it in code where the model only adds errors.
    """
    rows = lines[lo - 1:hi]
    out = []

    # A header row is only a header if the separator follows it. A block that opens with
    # the separator has no header at all — synthesizing col1..colN there is honest, where
    # consuming the first data row as a header silently loses that row AND mislabels
    # every row after it.
    if len(rows) >= 2 and not TABLE_SEP_RE.match(rows[0]) and TABLE_SEP_RE.match(rows[1]):
        header = table_cells(rows[0])
        start = 2
    else:
        header = []
        start = 0

    for idx in range(start, len(rows)):
        raw = rows[idx]
        ln = lo + idx
        if TABLE_SEP_RE.match(raw):
            continue
        cells = table_cells(raw)
        if header and cells == header:
            continue  # a repeated header row, not data
        # Ragged rows are kept whole in both directions: a cell past the end of the header
        # gets a positional name rather than being truncated away, and a short row simply
        # stops. A cell whose text happens to equal its own column name is still data --
        # dropping it (`source` under a `source` column) leaves a claim that no longer says
        # what it is about.
        pairs = []
        for i in range(max(len(header), len(cells))):
            val = cells[i] if i < len(cells) else ""
            if not val:
                continue
            name = header[i] if i < len(header) and header[i] else f"col{i + 1}"
            pairs.append(f"{name}={val}")
        if not pairs:
            continue
        # Strip backticks from the anchor: a table row often contains them (markdown code
        # spans), and they collide with the backtick that delimits the anchor snippet in
        # the memo format. verify_anchors strips them from the source line too, so the
        # match still holds. The claim text keeps them — it is not used as an anchor.
        snippet = raw.strip().replace("`", "")
        out.append({"text": "; ".join(pairs).replace("`", ""), "anchor_line": ln,
                    "anchor_snippet": snippet, "status": "PENDING", "note": "table"})
    return out

CLAIM_RE = re.compile(r"^[-*]\s+(.*?)\s*(@L(\d+)\s*`([^`]*)`|@semantic)\s*$")

# Small models ignore "don't claim imports" no matter how the prompt is worded — tested,
# it gets worse, not better. So trivia is filtered in code instead of being asked for.
# This is the same bargain as the rest of the pipeline: let the model be sloppy, then
# enforce the standard mechanically.
TRIVIAL_ANCHOR_RE = re.compile(
    r"^\s*(?:import\s|from\s+[\w.]+\s+import\b|#include\b|require\s*\(|use\s+\w+::|"
    r"package\s|using\s+\w+;|@?export\s*\{|const\s+\w+\s*=\s*require)"
)
TRIVIAL_TEXT_RE = re.compile(
    r"^(?:imports?\b|includes?\s+the\b|"
    r"defines?\s+(?:a\s+)?(?:function|class|method|variable|constant)\s+(?:called|named)\b|"
    r"declares?\s+(?:a\s+)?(?:function|class|variable|constant)\b|"
    r"this\s+(?:line|statement)\s+)",
    re.I,
)


def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def est_tokens(text: str) -> int:
    """Rough token estimate. ~4 chars/token is close enough for a ledger."""
    return max(1, len(text) // 4)


def pick_profile(path: pathlib.Path, override: str | None) -> str:
    if override:
        return override
    ext = path.suffix.lower()
    if ext in CODE_EXT:
        return "code"
    if ext in PROSE_EXT:
        return "prose"
    if not ext:
        # `bin/council`, `hooks/pre-commit` and friends carry their language in the
        # shebang, not the name. Without this they fall to generic.
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                if SHEBANG_RE.match(fh.readline()):
                    return "code"
        except OSError:
            pass
    return "generic"


def find_tables(lines: list[str]) -> list[tuple[int, int]]:
    """Locate markdown tables as their own 1-indexed inclusive ranges.

    A table needs a separator row to count — otherwise any line with pipes in it (a code
    sample, a regex alternation) would be mistaken for one.

    Two tables printed back to back are separated by nothing but a new header/separator
    pair, so a run of pipe-prefixed lines can hold several tables. Walking the whole run
    as one table labels the second table's rows with the FIRST table's column names — and
    because the anchor is the literal row text, verify_anchors confirms every one of those
    mislabelled claims. So each separator row starts a new table here.
    """
    out, i, n = [], 0, len(lines)
    while i < n:
        if not TABLE_ROW_RE.match(lines[i]):
            i += 1
            continue
        j = i
        while j < n and TABLE_ROW_RE.match(lines[j]):
            j += 1
        block = lines[i:j]

        # Each separator opens a table; its header is the row above it (if there is one).
        starts: list[int] = []
        for k, b in enumerate(block):
            if not TABLE_SEP_RE.match(b):
                continue
            s = k - 1 if k >= 1 else 0
            if starts and s <= starts[-1]:
                continue  # consecutive separators — same table
            starts.append(s)

        for idx, s in enumerate(starts):
            e = starts[idx + 1] - 1 if idx + 1 < len(starts) else len(block) - 1
            seg = block[s:e + 1]
            data = [r for r in seg if not TABLE_SEP_RE.match(r)]
            # header + separator + >=1 data row, or (headerless) separator + >=1 data row
            headed = not TABLE_SEP_RE.match(seg[0])
            if len(data) >= (2 if headed else 1):
                out.append((i + 1 + s, i + 1 + e))
        i = j
    return out


def chunk_lines(lines: list[str], profile: str) -> list[tuple[int, int]]:
    """Return (start_line, end_line) 1-indexed inclusive ranges.

    Splitting on structure rather than byte count keeps each chunk semantically whole,
    which is what lets a 7B model say something true about it.
    """
    n = len(lines)
    if n == 0:
        return []

    if profile == "generic":
        out = []
        i = 0
        while i < n:
            out.append((i + 1, min(i + MAX_CHUNK_LINES, n)))
            i += MAX_CHUNK_LINES - 15  # overlap so boundary facts aren't lost
        return out

    matcher = DECL_RE if profile == "code" else HEADING_RE
    boundaries = [i for i, ln in enumerate(lines) if matcher.match(ln)]
    if not boundaries or boundaries[0] != 0:
        boundaries.insert(0, 0)

    out = []
    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] - 1 if idx + 1 < len(boundaries) else n - 1
        # Merge runaway chunks down to MAX_CHUNK_LINES.
        while end - start + 1 > MAX_CHUNK_LINES:
            out.append((start + 1, start + MAX_CHUNK_LINES))
            start += MAX_CHUNK_LINES
        if end >= start:
            out.append((start + 1, end + 1))

    # Coalesce tiny adjacent chunks — a 3-line chunk wastes a whole model call.
    merged: list[tuple[int, int]] = []
    for s, e in out:
        if merged and (e - merged[-1][0] + 1) <= MAX_CHUNK_LINES and (e - s) < 25:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
VISION_MODEL = os.environ.get("MEMO_MODEL_BIG", "gemma4:12b")

IMAGE_PROMPT = """You are describing an image so someone can find it and know what it shows
without opening it. This may be a figure, chart, screenshot, diagram, or scanned page.

Output ATOMIC CLAIMS about what is actually visible:
- for a chart/graph: what it plots, the axes, the trend, any labelled values
- for a diagram: the components and how they connect
- for a screenshot/scan: the salient text and what the interface or page is
- for a photo: the subject and setting

Rules:
- One fact per claim. State what you SEE, not what it might mean.
- Do NOT invent numbers or labels. If a value is unreadable, do not guess it — a
  fabricated figure is the worst thing you can produce here.
- If text is legible, quote it exactly inside the claim.
- 3 to 8 claims. A caption is not enough; a reader wants the contents.

Format exactly (no @L anchors — an image has no line numbers):
TOPIC: <what this image is, 6-10 words>
- <claim>
- <claim>
"""


def call_vision(image_path: pathlib.Path, retries: int = 3) -> str:
    """Describe an image with the local vision model.

    The text pipeline is blind to figures — a PDF converted to text loses every chart, and
    an image file is skipped as binary. gemma4 can see them, so a diagram or a scanned
    table becomes claims like any other source. Anchors are semantic (an image has no
    lines), so these are validated by a subagent or the thinking model, never by grep.
    """
    import base64
    try:
        b64 = base64.b64encode(image_path.read_bytes()).decode()
    except OSError as e:
        return ""
    payload = json.dumps({
        "model": VISION_MODEL, "prompt": IMAGE_PROMPT, "images": [b64],
        "stream": False, "options": {"temperature": 0.1},
    }).encode()
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/generate", data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())["response"]
        except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
    print(f"    vision model failed on {image_path.name}: {last}", file=sys.stderr)
    return ""


def parse_image_claims(raw: str, nlines_placeholder: int = 1) -> tuple[str, list[dict]]:
    """Image claims have no line anchors, so they are semantic by construction."""
    topic, claims = "", []
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("TOPIC:"):
            topic = topic or line[6:].strip()
            continue
        m = re.match(r"^[-*]\s+(.+)$", line)
        if not m:
            continue
        text = m.group(1).strip().rstrip("`").lstrip("`")
        if len(text) < 8 or is_trivial(text, None):
            continue
        claims.append({"text": text, "anchor_line": None, "anchor_snippet": None,
                       "status": "NEEDS_AGENT", "note": "image", "region": [1, 1]})
    return topic, claims


def call_ollama(prompt: str, body: str, model: str, retries: int = 3) -> str:
    """Ask the local model for one chunk's claims.

    Retries rather than dying. Ollama evicts and reloads models under memory pressure —
    on a 16GB machine any second job can trigger it — and the request in flight fails.
    An early version called sys.exit() here, so one transient blip during a long run
    killed everything after it: a 10-file corpus produced 2 memos and the benchmark then
    reported it as a clean result. Losing one chunk is acceptable; losing the run is not.
    """
    payload = json.dumps({
        "model": model,
        "prompt": f"{prompt}\n\n--- LINES ---\n{body}\n--- END ---\n",
        "stream": False,
        "options": {"num_ctx": NUM_CTX, "temperature": 0.1},
    }).encode()

    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate", data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())["response"]
        except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as e:
            last = e
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)   # a model reload takes ~20s; back off past it
                print(f"    local model error ({e}); retry {attempt+1}/{retries-1} "
                      f"in {wait}s", file=sys.stderr, flush=True)
                time.sleep(wait)

    print(f"    giving up on this chunk after {retries} attempts: {last}",
          file=sys.stderr, flush=True)
    return ""   # no claims from this chunk; the rest of the run continues


def is_trivial(text: str, snippet: str | None) -> bool:
    """Claims that are true but not worth a reader's tokens.

    An import line or a claim that merely restates a declaration's name tells the reader
    nothing they couldn't guess, while costing them the tokens to skip past it. Dropping
    these is what keeps the memo:source ratio low enough for the skill to pay off.
    """
    if snippet and TRIVIAL_ANCHOR_RE.match(snippet):
        return True
    return bool(TRIVIAL_TEXT_RE.match(text.strip()))


def parse_claims(raw: str, lo: int, hi: int) -> tuple[str, list[dict], int]:
    topic = ""
    claims = []
    seen_lines: set[int] = set()
    dropped = 0
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("TOPIC:"):
            topic = topic or line[6:].strip()
            continue
        m = CLAIM_RE.match(line)
        if not m:
            continue
        text, _, lineno, snippet = m.groups()
        if lineno:
            ln = int(lineno)
            # A cited line outside the chunk is a hallucination by construction.
            if not (lo <= ln <= hi):
                dropped += 1
                continue
            if is_trivial(text, snippet):
                dropped += 1
                continue
            # Two claims on one line means the model padded; keep the first.
            if ln in seen_lines:
                dropped += 1
                continue
            seen_lines.add(ln)
            claims.append({"text": text, "anchor_line": ln, "anchor_snippet": snippet,
                           "status": "PENDING", "note": ""})
        else:
            if is_trivial(text, None):
                dropped += 1
                continue
            claims.append({"text": text, "anchor_line": None, "anchor_snippet": None,
                           "status": "NEEDS_AGENT", "note": "semantic",
                           "region": [lo, hi]})
    return topic, claims, dropped


def memo_path(memo_dir: pathlib.Path, rel: pathlib.Path) -> pathlib.Path:
    """Memo filename for a source, guaranteed collision-free.

    Flattening os.sep to "__" alone makes a/b.py and a__b.py the same memo. The damage is
    not just a lost memo: on the next run each file's hash check reads the OTHER file's
    memo, both mismatch, both regenerate, and the pair ping-pongs forever while the run
    reports "generated 2". doc_ingest.staged_stem already solves this by hashing the path
    into the name; same treatment here. The readable part is truncated because the hash,
    not the prefix, is what carries uniqueness.
    """
    flat = rel.as_posix().replace("/", "__")
    digest = hashlib.sha1(rel.as_posix().encode("utf-8")).hexdigest()[:8]
    return memo_dir / f"{flat[:100]}-{digest}.memo.md"


def stale_path(mp: pathlib.Path) -> pathlib.Path:
    """Where a memo goes when it can no longer be trusted.

    Every consumer (index_build, verify_anchors, memo_db, memo_query, apply_verdicts)
    selects memos with glob("*.memo.md"), so parking the file under a different suffix
    makes it invisible to all of them at once without teaching each one a new status.
    The content is kept rather than deleted so a failed run is still debuggable.
    """
    return mp.with_name(mp.name + ".stale")


def invalidate_memo(mp: pathlib.Path, rel, reason: str) -> bool:
    """Retire a memo whose source has moved on. Returns True if one was there."""
    if not mp.exists():
        return False
    dead = stale_path(mp)
    if dead.exists():
        dead.unlink()
    mp.rename(dead)
    print(f"  -> STALE: previous memo for {rel} retired to {dead.name} ({reason})",
          file=sys.stderr)
    return True


def read_frontmatter(mp: pathlib.Path) -> dict:
    """Parse the memo's YAML-ish frontmatter in full.

    The old check sniffed the first 400 characters for the sha256 line. A long source path
    pushes that line out of the window, the check never matches, and the file regenerates
    on every single run forever.
    """
    fm: dict[str, str] = {}
    try:
        with mp.open(encoding="utf-8", errors="replace") as f:
            first = f.readline().rstrip("\n")
            if first.strip() != "---":
                return fm
            for line in f:
                line = line.rstrip("\n")
                if line.strip() == "---":
                    break
                m = re.match(r"^(\w+): (.*)$", line)
                if m:
                    fm.setdefault(m.group(1), m.group(2))
    except OSError:
        return {}
    return fm


def cache_key(digest: str, profile: str, model: str) -> str:
    """Freshness key: the source hash alone is not what the memo depends on.

    A memo records `profile:` and `generator:` because they change what it says — so
    regenerating the same corpus under a different profile or model must not report
    "generated 0, skipped 47". The prompt text and MAX_CHUNK_LINES go in for the same
    reason: editing a prompt used to leave the whole corpus frozen on the old one.
    """
    h = hashlib.sha256()
    h.update(digest.encode())
    h.update(b"\0" + profile.encode() + b"\0" + model.encode())
    h.update(b"\0" + str(MAX_CHUNK_LINES).encode())
    prompt = IMAGE_PROMPT if profile == "image" else PROMPTS.get(profile, "")
    h.update(b"\0" + prompt.encode())
    h.update(b"\0" + PROMPTS["table"].encode())
    return h.hexdigest()[:16]


def render_memo(rel, digest, nlines, profile, topics, claims, raw_tokens, model) -> str:
    lines = [
        "---",
        f"source: {rel}",
        f"sha256: {digest}",
        f"lines: {nlines}",
        f"profile: {profile}",
        f"generator: ollama/{model}",
        f"cache_key: {cache_key(digest, profile, model)}",
        f"raw_tokens_est: {raw_tokens}",
        "verified: pending",
        "---",
        "",
        f"# {rel}",
        "",
        "## Topics",
    ]
    for lo, hi, t in topics:
        lines.append(f"- L{lo}-{hi}: {t or '(untitled)'}")
    lines += ["", "## Claims", ""]
    for i, c in enumerate(claims, 1):
        if c["anchor_line"]:
            anchor = f"@L{c['anchor_line']} `{c['anchor_snippet']}`"
        else:
            anchor = f"@semantic L{c['region'][0]}-{c['region'][1]}"
        lines.append(f"- [C{i}] [{c['status']}] {c['text']} {anchor}")
    lines.append("")
    return "\n".join(lines)


def is_excluded(rel: pathlib.Path, excludes: list[str]) -> bool:
    """Does an exclude pattern cover this path?

    The old test was `rel.match(x) or x.strip("*/") in str(rel)`. PurePath.match is
    right-anchored, so "build/*" almost never matched; the substring fallback then matched
    ANYWHERE in the path, so the default excludes silently swallowed src/builder.py,
    src/distance.py and src/rebuild_index.py — uncounted in todo, skipped or skipped_bad,
    while the run cheerfully reported "generated 12, skipped 0". Likewise ".git/*" ate
    every .gitignore and .github/ path.

    So: match whole path SEGMENTS and real glob semantics, never bare substrings.
    """
    posix = rel.as_posix()
    parts = rel.parts
    for pat in excludes:
        pat = pat.strip()
        if not pat:
            continue
        if fnmatch.fnmatchcase(posix, pat) or fnmatch.fnmatchcase(rel.name, pat):
            return True
        # "build/*" and "build/**" mean "everything under a build directory".
        dirpat = pat
        for suffix in ("/**", "/*", "/"):
            if dirpat.endswith(suffix):
                dirpat = dirpat[: -len(suffix)]
                break
        if not dirpat:
            continue
        if "/" in dirpat or any(c in dirpat for c in "*?["):
            # multi-segment or globbed directory prefix: anchor it at the path root
            if posix == dirpat or posix.startswith(dirpat + "/"):
                return True
            if fnmatch.fnmatchcase(posix, dirpat + "/*"):
                return True
        elif dirpat in parts[:-1]:
            # a plain directory name excludes its whole subtree, at any depth
            return True
    return False


def iter_sources(root: pathlib.Path, patterns: list[str], excludes: list[str]):
    """Yield (path, rel) for every match, plus a count of what the excludes removed."""
    seen = set()
    for pat in patterns:
        for p in sorted(root.glob(pat)):
            if not p.is_file() or p in seen:
                continue
            rel = p.relative_to(root)
            seen.add(p)
            if is_excluded(rel, excludes):
                yield p, rel, True
                continue
            yield p, rel, False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", type=pathlib.Path)
    ap.add_argument("--include", action="append", required=True,
                    help="glob relative to --root; repeatable")
    ap.add_argument("--exclude", action="append", default=[
        "node_modules/*", ".git/*", "dist/*", "build/*", "*.min.js", ".memo/*",
        "vendor/*", "__pycache__/*", "*.lock"])
    ap.add_argument("--memo-dir", default=".memo", type=pathlib.Path)
    ap.add_argument("--profile", choices=["code", "prose", "generic"])
    ap.add_argument("--force", action="store_true", help="regenerate even if hash matches")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be done + token estimate, call no model")
    ap.add_argument("--generator", choices=["local", "subagent"], default="local",
                    help="'subagent' emits a work order instead of calling the local model")
    args = ap.parse_args()

    root = args.root.resolve()
    # Resolve --memo-dir against the CWD, not --root. Resolving against root meant
    # `--root staging --memo-dir staging/.memo` silently wrote to staging/staging/.memo:
    # the run reported "generated 2" and every later step said the memo dir did not exist.
    # A path the user types is relative to where they are standing.
    memo_dir = args.memo_dir.resolve()
    memo_dir.mkdir(parents=True, exist_ok=True)

    todo, images, skipped, total_raw = [], [], 0, 0
    excluded, unreadable, recoded = 0, [], []
    for path, rel, is_excl in iter_sources(root, args.include, args.exclude):
        if is_excl:
            excluded += 1
            continue
        digest = sha256_file(path)
        mp = memo_path(memo_dir, rel)
        is_image = path.suffix.lower() in IMAGE_EXT
        profile = "image" if is_image else pick_profile(path, args.profile)
        model = VISION_MODEL if is_image else model_for(profile)
        if mp.exists() and not args.force:
            fm = read_frontmatter(mp)
            fresh = fm.get("sha256") == digest
            # A memo written before cache_key existed has no key to compare; treat the
            # hash alone as current rather than forcing a whole-corpus regeneration.
            if fresh and "cache_key" in fm:
                fresh = fm["cache_key"] == cache_key(digest, profile, model)
            if fresh:
                skipped += 1
                continue
        if is_image:
            # An image has no text to read — it goes to the vision model instead.
            images.append((path, rel, digest))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Dropping the file silently was the bug: a latin-1 source vanished while the
            # summary still said "skipped 0". Decode leniently instead and say so — the
            # anchors are per-line string matches, so a few replaced bytes cost at most
            # the claims on those lines, where dropping the file costs all of them.
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                recoded.append(str(rel))
            except OSError as e:
                unreadable.append(f"{rel}: {e}")
                continue
        except OSError as e:
            unreadable.append(f"{rel}: {e}")
            continue
        raw_tok = est_tokens(text)
        total_raw += raw_tok
        todo.append((path, rel, text, digest, raw_tok))

    if recoded:
        print(f"note: {len(recoded)} file(s) were not valid UTF-8 and were decoded "
              f"leniently (errors=replace):", file=sys.stderr)
        for r in recoded[:8]:
            print(f"  {r}", file=sys.stderr)
    if unreadable:
        print(f"warning: {len(unreadable)} file(s) could not be read at all and are NOT "
              f"indexed:", file=sys.stderr)
        for u in unreadable[:8]:
            print(f"  {u}", file=sys.stderr)

    # Images are a separate list because they take a different model, but they are still
    # work. Reporting only `todo` meant a PDF staged into 1 .txt + 30 page PNGs looked
    # like "1 memo" — and the caller then told the user the corpus was fully indexed
    # while 30 figure/scanned pages had never been mentioned to anyone.
    IMAGE_MEMO_EST = 300

    if args.dry_run:
        print(f"would generate: {len(todo)} text memo(s) + {len(images)} image memo(s)"
              f"   already current: {skipped}")
        if excluded:
            print(f"excluded by --exclude: {excluded} file(s)")
        print(f"raw material:   ~{total_raw:,} tokens if read directly (text only; "
              f"images are unreadable without a vision pass)")
        memo_est = len(todo) * 450 + len(images) * IMAGE_MEMO_EST
        print(f"memo estimate:  ~{memo_est:,} tokens once indexed")
        saved = total_raw - memo_est
        verdict = "worth it" if saved > 5000 else "MARGINAL — plain Read may be cheaper"
        print(f"projected save: ~{saved:,} tokens  ({verdict})")
        if images:
            print(f"vision time:    ~{len(images) * 1.5:.0f} min for {len(images)} image(s)")
        for _, rel, _, _, t in todo[:40]:
            print(f"  {t:>7,} tok  {rel}")
        if len(todo) > 40:
            print(f"  ... and {len(todo) - 40} more")
        for _, rel, _ in images[:40]:
            print(f"  {'image':>7}     {rel}")
        if len(images) > 40:
            print(f"  ... and {len(images) - 40} more image(s)")
        return

    if args.generator == "subagent":
        # No local model: hand the chunk list to the caller, who fans out subagents.
        # Raw material still stays out of the main context, which is most of the win.
        order = []
        for path, rel, text, digest, _ in todo:
            profile = pick_profile(path, args.profile)
            lines = text.splitlines()
            order.append({
                "source": str(rel), "kind": "text", "profile": profile, "sha256": digest,
                "memo_path": str(memo_path(memo_dir, rel)),
                "chunks": chunk_lines(lines, profile),
                "prompt": PROMPTS[profile],
            })
        for path, rel, digest in images:
            order.append({
                "source": str(rel), "kind": "image", "profile": "image", "sha256": digest,
                "memo_path": str(memo_path(memo_dir, rel)),
                "chunks": [], "prompt": IMAGE_PROMPT,
            })
        print(json.dumps({"mode": "subagent", "work": order,
                          "counts": {"text": len(todo), "image": len(images)}}, indent=2))
        return

    skipped_bad, retired = [], []
    for path, rel, text, digest, raw_tok in todo:
        profile = pick_profile(path, args.profile)
        model = model_for(profile)
        lines = text.splitlines()
        ranges = chunk_lines(lines, profile)
        print(f"[{profile}/{model}] {rel}  ({len(lines)} lines, {len(ranges)} chunks)",
              file=sys.stderr, flush=True)

        # Tables are extracted deterministically, not by the model — see table_claims.
        # The model still narrates the surrounding prose, but the rows become claims in
        # code, with the literal row as anchor, so the fact in a table can never go
        # unclaimed or get refuted for a paraphrased snippet.
        tables = find_tables(lines) if profile in ("prose", "generic") else []
        topics, claims, dropped, failed = [], [], 0, 0
        for lo, hi in tables:
            tc = table_claims(lines, lo, hi)
            claims.extend(tc)
            topics.append((lo, hi, f"table: {tc[0]['text'][:40]}" if tc else "table"))
        if tables:
            print(f"    {len(tables)} table(s) -> {len(claims)} row claims (deterministic)",
                  file=sys.stderr)

        # Chunks in the generic profile overlap by 15 lines so a fact on a boundary is not
        # lost. seen_lines in parse_claims resets per chunk, so without a memo-wide set the
        # same finding comes back twice as two independent-looking claims — which reads to
        # a later consumer like corroboration.
        seen_lines: set[int] = {c["anchor_line"] for c in claims if c["anchor_line"]}
        seen_text: set[str] = {re.sub(r"\W+", " ", c["text"]).strip().lower()
                               for c in claims}

        table_lines = {ln for lo, hi in tables for ln in range(lo, hi + 1)}
        for lo, hi in ranges:
            body = "\n".join(f"{i}: {lines[i-1]}" for i in range(lo, hi + 1)
                             if i not in table_lines)
            if not body.strip():
                continue
            raw = call_ollama(PROMPTS[profile], body, model)
            if not raw:
                failed += 1
                continue
            topic, cs, d = parse_claims(raw, lo, hi)
            topics.append((lo, hi, topic))
            dropped += d
            for c in cs:
                key = re.sub(r"\W+", " ", c["text"]).strip().lower()
                if c["anchor_line"] and c["anchor_line"] in seen_lines:
                    dropped += 1
                    continue
                if key in seen_text:
                    dropped += 1
                    continue
                if c["anchor_line"]:
                    seen_lines.add(c["anchor_line"])
                seen_text.add(key)
                claims.append(c)

        # An empty memo whose hash says "current" is worse than no memo: every later run
        # skips it as done, and every query silently finds nothing. Refuse to write one.
        if not claims:
            print(f"  -> NO CLAIMS ({failed}/{len(ranges)} chunks failed) — memo not "
                  f"written so a re-run retries it", file=sys.stderr)
            # ...and refusing to write is not enough on its own. If a memo for a PREVIOUS
            # version of this file is already on disk, leaving it there is the worst of
            # both worlds: the source has changed (that is why we are regenerating), so
            # every anchor in it points into the old line numbering, and every later query
            # answers confidently from claims about code that no longer exists. Retire it.
            if invalidate_memo(memo_path(memo_dir, rel), rel, "regeneration produced no claims"):
                retired.append(str(rel))
            skipped_bad.append(str(rel))
            continue

        memo_path(memo_dir, rel).write_text(
            render_memo(rel, digest, len(lines), profile, topics, claims, raw_tok, model),
            encoding="utf-8")
        note = f", {failed} chunk(s) failed" if failed else ""
        print(f"  -> {len(claims)} claims ({dropped} trivial/dupe dropped{note})",
              file=sys.stderr)

    # Images: one vision pass each, claims are semantic (no line anchors in a picture).
    for path, rel, digest in images:
        print(f"[vision/{VISION_MODEL}] {rel}", file=sys.stderr, flush=True)
        raw = call_vision(path)
        if not raw:
            if invalidate_memo(memo_path(memo_dir, rel), rel, "vision pass failed"):
                retired.append(str(rel))
            skipped_bad.append(str(rel))
            continue
        topic, claims = parse_image_claims(raw)
        if not claims:
            print(f"  -> NO CLAIMS from vision — memo not written", file=sys.stderr)
            if invalidate_memo(memo_path(memo_dir, rel), rel, "vision produced no claims"):
                retired.append(str(rel))
            skipped_bad.append(str(rel))
            continue
        memo_path(memo_dir, rel).write_text(
            render_memo(rel, digest, 1, "image", [(1, 1, topic)], claims, 0, VISION_MODEL),
            encoding="utf-8")
        print(f"  -> {len(claims)} image claims", file=sys.stderr)

    ok = len(todo) + len(images) - len(skipped_bad)
    print(f"\ngenerated {ok} ({len(todo)} text, {len(images)} image), "
          f"skipped {skipped} (already current)", file=sys.stderr)
    if excluded:
        print(f"{excluded} file(s) matched --exclude and were not considered",
              file=sys.stderr)
    if recoded:
        print(f"{len(recoded)} file(s) decoded leniently (not valid UTF-8)", file=sys.stderr)
    if unreadable:
        print(f"{len(unreadable)} file(s) UNREADABLE and not indexed", file=sys.stderr)
    if retired:
        print(f"{len(retired)} stale memo(s) retired to *.memo.md.stale — their sources "
              f"changed but regeneration failed, so the old memos were no longer safe to "
              f"answer from:", file=sys.stderr)
        for r in retired[:8]:
            print(f"  {r}", file=sys.stderr)
    if skipped_bad:
        print(f"{len(skipped_bad)} file(s) produced NO claims and were not written:",
              file=sys.stderr)
        for b in skipped_bad[:8]:
            print(f"  {b}", file=sys.stderr)
        print("re-run to retry them; if they keep failing, check "
              "route.py --check", file=sys.stderr)
    print("next: scripts/verify_anchors.py --memo-dir <dir> --json", file=sys.stderr)


if __name__ == "__main__":
    main()
