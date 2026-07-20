#!/usr/bin/env python3
"""The keyword -> claim -> content index, and the scoring that keeps it honest.

Why a database instead of files you read: reading a memo costs its whole length even
when one claim in it mattered. Querying costs only the claims that matched. That single
change is where the big savings come from — everything else here serves it.

Three tiers, each one you only pay for if the tier above justified it:

  keyword   ~2 tokens    what the claim is about; searched, never read in bulk
  claim     ~20 tokens   the atomic fact + its anchor
  content   ~200 tokens  the real source region, read only when the claim earns it

  memo_db.py --build --memo-dir .memo      # (re)index memos into .memo/index.db
  memo_db.py --stats --memo-dir .memo      # corpus health, utility distribution

Scoring exists to fight two different failures at once. Low-utility claims waste tokens;
low-relevance keyword matches surface claims that don't answer the question and invite a
wrong answer built on a true-but-irrelevant fact. Both are pruned before retrieval.

The index also records the sha256 of every memo it read. A memo edited after the build
means the index is describing text that no longer exists — including, in the worst case,
a claim the validator has since marked REFUTED. Retrieval refuses a stale index rather
than answering from it.

Stdlib only — SQLite with FTS5 ships with Python.
"""

import argparse
import hashlib
import json
import math
import pathlib
import re
import sqlite3
import sys

# Must stay in step with verify_anchors.py, which writes `<!-- note -->` comments onto
# the claims it judges. A claim line that does not match this is not silently dropped:
# CLAIMISH_RE catches it so both this indexer and index_build.py can report it.
CLAIM_RE = re.compile(
    r"^- \[(C\d+)\] \[(\w+)\] (.*?) (?:@L(\d+) `([^`]*)`|@semantic L(\d+)-(\d+))"
    r"\s*(?:<!--.*-->)?\s*$"
)
CLAIMISH_RE = re.compile(r"^- \[C\d+\]")
FM_RE = re.compile(r"^(\w+): (.*)$")

# Bumped whenever the on-disk shape changes. An index written by an older version is
# detected and rebuilt rather than half-read.
SCHEMA_VERSION = "2"

# Trust weight per validation status. REFUTED is 0.0 rather than negative because a
# refuted claim should simply never surface — not fight its way back via a strong
# keyword match. These weights rank; they are not the gate. Retrieval gates on the
# status string itself (see memo_query.BLOCKED_STATUS), because a gate made of
# arithmetic stops being a gate the moment someone passes --floor 0.
STATUS_W = {
    "CONFIRMED": 1.0,
    "DRIFTED": 0.9,
    "NEEDS_AGENT": 0.5,   # unproven; usable as a lead, flagged as such
    "PENDING": 0.4,
    "UNSUPPORTED": 0.15,
    "REFUTED": 0.0,
    "STALE": 0.0,
}

STOP = set("""a an the and or but if then than that this these those is are was were be been
being of in on at to for from by with without into over under as it its his her their our your
my we you they he she i not no do does did done can could should would may might must will
shall have has had having each other more most some any all both few many much such only own
same so too very just also however therefore thus which who whom whose what when where why how
function method class value values return returns used use uses using set sets get gets
""".split())

IDENT_SPLIT = re.compile(r"[^A-Za-z0-9]+")
CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?%?\b")


def _depluralize(w: str) -> str:
    """Singularize, keeping the silent-e words reversible.

    The whole point is that the singular and the plural land on the *same* token. A
    rule that maps `files` -> `fil` while `file` -> `file` is worse than no rule: the kw
    table matches on equality, so a query for "pipelines" then misses every claim
    indexed under "pipeline" and scores a flat 0.0.
    """
    if w.endswith("ies") and len(w) - 3 >= 3:
        return w[:-3] + "y"
    # processes -> process, matches -> match, boxes -> box, statuses -> status
    if w.endswith(("sses", "shes", "ches", "xes", "zes", "ses")) and len(w) - 2 >= 3:
        return w[:-2]
    # "process"/"class"/"analysis" must not become "proces"/"clas"/"analysi".
    if w.endswith(("ss", "us", "is")):
        return w
    if w.endswith("es") and len(w) - 2 >= 3:
        return w[:-1]          # files -> file, lines -> line, caches -> cache
    if w.endswith("s") and len(w) - 1 >= 3:
        return w[:-1]
    return w


def stem(w: str) -> str:
    """Crude suffix normalization, applied identically at index and query time.

    Without this, a question asking about "chunks" misses every claim indexed under
    `chunk_lines`, and the ranker quietly returns the next-best irrelevant thing instead
    of nothing — which is worse, because a confident wrong answer reads like an answer.
    Measured: this was the difference between the top hit being `chunk_lines` and being
    an unrelated claim about refusal warnings.

    The trailing-e strip at the end is what makes the plural rules symmetric: file/files,
    line/lines, cache/caches/caching and source/sources all converge on one token. It is
    lossy (`code` -> `cod`) but it is lossy in the same direction for every input, which
    is the only property equality matching actually needs.

    A real stemmer (Porter) would be more correct, but it is not in the stdlib and the
    long-tail gains are small next to the plural/gerund cases that dominate here.
    """
    if len(w) <= 3 or w.isdigit():
        return w
    b = _depluralize(w)
    if b == w:
        if w.endswith("ing") and len(w) - 3 >= 3:
            b = w[:-3]
        elif w.endswith("ed") and len(w) - 2 >= 3:
            b = w[:-2]
    if len(b) > 3 and b.endswith("e"):
        b = b[:-1]
    return b


SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
  rowid       INTEGER PRIMARY KEY,
  source      TEXT NOT NULL,
  memo        TEXT NOT NULL DEFAULT '',
  claim_id    TEXT NOT NULL,
  text        TEXT NOT NULL,
  status      TEXT NOT NULL,
  anchor_line INTEGER,
  anchor_snip TEXT,
  region_lo   INTEGER,
  region_hi   INTEGER,
  specificity REAL DEFAULT 0,
  idf         REAL DEFAULT 0,
  hits        INTEGER DEFAULT 0,
  helpful     INTEGER DEFAULT 0,
  unhelpful   INTEGER DEFAULT 0,
  depth       INTEGER DEFAULT 0,
  utility     REAL DEFAULT 0,
  UNIQUE(memo, source, claim_id)
);
CREATE TABLE IF NOT EXISTS kw (
  keyword     TEXT NOT NULL,
  claim_rowid INTEGER NOT NULL,
  weight      REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS kw_word ON kw(keyword);
CREATE INDEX IF NOT EXISTS kw_claim ON kw(claim_rowid);
CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(
  text, keywords, content=''
);
CREATE TABLE IF NOT EXISTS memos (
  path        TEXT PRIMARY KEY,
  memo_sha    TEXT NOT NULL,
  source      TEXT,
  source_sha  TEXT,
  claims_n    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

# Columns/tables a current index must have. Anything less is an old index.
_REQUIRED_CLAIM_COLS = {"memo", "source", "claim_id", "status", "utility"}


def split_ident(s: str) -> list[str]:
    """create_session / createSession / CODE_EXT -> component words."""
    out = []
    for part in IDENT_SPLIT.split(s):
        if not part:
            continue
        out.extend(w.lower() for w in CAMEL_SPLIT.split(part) if w)
    return out


def extract_keywords(text: str, snippet: str | None) -> dict[str, float]:
    """Deterministic keyword extraction — no model, so it costs nothing and can't lie.

    Weights encode how much a match on this term should count. An identifier lifted
    from the anchored source line is the strongest possible signal that the claim is
    really about that thing; a common English word from the claim prose is the weakest.
    """
    kws: dict[str, float] = {}

    def add(w: str, weight: float):
        w = w.strip().lower()
        if len(w) < 2 or w in STOP or w.isdigit() and len(w) < 2:
            return
        w = stem(w)
        if w in STOP:
            return
        kws[w] = max(kws.get(w, 0), weight)

    # Identifiers the model quoted in backticks: it explicitly said the claim is about these.
    for m in re.findall(r"`([^`]+)`", text):
        add(m, 3.0)
        for w in split_ident(m):
            add(w, 2.0)

    # Identifiers from the anchored source line — grounded in the file, not the prose.
    if snippet:
        for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", snippet):
            add(ident, 2.5)
            for w in split_ident(ident):
                add(w, 1.5)

    # Numbers are what people search papers for, and what models most often fabricate.
    for n in NUMBER_RE.findall(text):
        add(n, 2.0)

    # Remaining prose words: weak, but they're how a natural-language query finds anything.
    for w in split_ident(re.sub(r"`[^`]*`", " ", text)):
        add(w, 1.0)

    return kws


VAGUE_RE = re.compile(
    r"^(?:this|it|the (?:code|file|module|function|script|document|paper))\b|"
    r"\b(?:various|several|some|things?|stuff|etc|and so on|handles?|manages?|deals? with)\b",
    re.I,
)


def specificity(text: str, snippet: str | None) -> float:
    """How much a claim actually pins down.

    "handles various things" and "returns None when the session is missing" are both
    grammatical claims about a function; only one of them is worth retrieving. Vague
    claims are the ones that survive verification while telling the reader nothing, so
    utility has to see past status alone.
    """
    s = 0.0
    if snippet:
        s += 0.3
    if re.search(r"`[^`]+`", text):
        s += 0.25
    if NUMBER_RE.search(text):
        s += 0.2
    words = len(text.split())
    if 5 <= words <= 28:
        s += 0.15          # long claims are usually two claims wearing a trenchcoat
    if VAGUE_RE.search(text):
        s -= 0.35
    return max(0.0, min(1.0, s + 0.1))


def utility(status: str, spec: float, idf: float, hits: int, helpful: int,
            unhelpful: int) -> float:
    """One number deciding whether a claim is worth a reader's tokens.

    Trust weights the ranking — but it is not what keeps a refuted claim out. Retrieval
    gates on status directly; this number only orders the claims that were allowed
    through. Above that gate, we combine what the claim says (specificity), how
    discriminating its vocabulary is (idf), and what actually happened when it was
    retrieved (feedback). Feedback starts neutral and only moves once there is evidence.
    """
    trust = STATUS_W.get(status, 0.3)
    if trust == 0.0:
        return 0.0
    fb = 0.5
    if helpful or unhelpful:
        fb = helpful / (helpful + unhelpful)
    elif hits:
        fb = 0.45   # retrieved but never marked useful: mild negative evidence
    return round(trust * (0.45 * spec + 0.30 * min(idf, 1.0) + 0.25 * fb), 4)


def parse_memo_text(text: str):
    """The one memo parser. Returns (frontmatter, claims, unparseable_lines).

    index_build.py imports this too. When the two scripts had a regex each, a memo with
    unanchored claims was indexed as 2 claims here and reported as 4 in INDEX.md — the
    trust ratio in the index described a corpus that did not exist. A line that looks
    like a claim but does not parse is returned separately so both callers can count it
    the same way, out loud, instead of one of them silently believing it.
    """
    fm: dict[str, str] = {}
    claims: list[dict] = []
    unparseable: list[str] = []
    for ln in text.splitlines():
        m = FM_RE.match(ln)
        if m and m.group(1) in ("source", "sha256", "profile", "lines", "raw_tokens_est"):
            fm.setdefault(m.group(1), m.group(2))
        c = CLAIM_RE.match(ln)
        if c:
            cid, status, ctext, aline, snip, rlo, rhi = c.groups()
            claims.append({
                "claim_id": cid, "status": status, "text": ctext.strip(),
                "anchor_line": int(aline) if aline else None,
                "anchor_snip": snip,
                "region_lo": int(rlo) if rlo else None,
                "region_hi": int(rhi) if rhi else None,
            })
        elif CLAIMISH_RE.match(ln):
            unparseable.append(ln.strip())
    return fm, claims, unparseable


def parse_memo(mp: pathlib.Path):
    return parse_memo_text(mp.read_text(encoding="utf-8"))


def file_sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def schema_is_current(db: sqlite3.Connection) -> bool:
    """False for an index written before this schema — caller rebuilds instead of crashing."""
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(claims)")}
    except sqlite3.DatabaseError:
        return False
    if not cols:
        return True          # fresh file, nothing built yet
    if not _REQUIRED_CLAIM_COLS <= cols:
        return False
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    if "memos" not in tables:
        return False
    try:
        v = db.execute("SELECT v FROM meta WHERE k = 'schema_version'").fetchone()
    except sqlite3.DatabaseError:
        return False
    return bool(v) and v[0] == SCHEMA_VERSION


def connect(memo_dir: pathlib.Path, create: bool = True) -> sqlite3.Connection:
    """Open the index. `create=False` refuses to conjure one.

    Opening a SQLite file creates it, and running the schema makes it a *valid, empty*
    index. That is how `--stats` on a never-built directory used to leave behind a
    database whose every answer was "no claims matched" — a reader cannot tell that from
    "the corpus does not contain that fact". Readers pass create=False.
    """
    dbp = memo_dir / "index.db"
    if not create and not dbp.exists():
        raise FileNotFoundError(str(dbp))
    db = sqlite3.connect(dbp)
    db.row_factory = sqlite3.Row
    if create:
        db.executescript(SCHEMA)
        db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('schema_version', ?)",
                   (SCHEMA_VERSION,))
        db.commit()
    return db


def index_state(memo_dir: pathlib.Path) -> tuple[str, str]:
    """('missing'|'old-schema'|'empty'|'stale'|'ok', human-readable detail).

    Freshness is checked against the sha256 of each memo *file*, not the source it
    describes. The failure this exists for: build the index, then re-run verification so
    a claim flips CONFIRMED -> REFUTED in the memo, then query without rebuilding. The
    index still holds the CONFIRMED row and happily serves a claim that is now known to
    be false. Nothing about that is detectable from the database alone.
    """
    dbp = memo_dir / "index.db"
    if not dbp.exists():
        return "missing", str(dbp)
    try:
        db = sqlite3.connect(dbp)
        db.row_factory = sqlite3.Row
        try:
            if not schema_is_current(db):
                return "old-schema", "index was built by an older version of memo_db.py"
            indexed = {r["path"]: r["memo_sha"] for r in db.execute(
                "SELECT path, memo_sha FROM memos")}
            n = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        finally:
            db.close()
    except sqlite3.DatabaseError as e:
        return "old-schema", f"index unreadable ({e})"

    on_disk = {p.name: p for p in sorted(memo_dir.glob("*.memo.md"))}
    added = sorted(set(on_disk) - set(indexed))
    removed = sorted(set(indexed) - set(on_disk))
    changed = sorted(n for n, p in on_disk.items()
                     if n in indexed and file_sha(p) != indexed[n])
    if added or removed or changed:
        bits = []
        if changed:
            bits.append(f"{len(changed)} memo(s) edited since the build "
                        f"({', '.join(changed[:3])}{'...' if len(changed) > 3 else ''})")
        if added:
            bits.append(f"{len(added)} new memo(s) not indexed")
        if removed:
            bits.append(f"{len(removed)} indexed memo(s) no longer on disk")
        return "stale", "; ".join(bits)
    if not n:
        return "empty", "no claims indexed"
    return "ok", ""


def build(memo_dir: pathlib.Path) -> dict:
    dbp = memo_dir / "index.db"
    if dbp.exists():
        probe = sqlite3.connect(dbp)
        probe.row_factory = sqlite3.Row
        migrating = not schema_is_current(probe)
        prior = {}
        if migrating:
            # Best-effort rescue of hard-won feedback, then start the file over rather
            # than trying to read half of an older shape.
            try:
                prior = {(r["source"], r["claim_id"]):
                         (r["hits"], r["helpful"], r["unhelpful"], r["depth"])
                         for r in probe.execute(
                             "SELECT source, claim_id, hits, helpful, unhelpful, depth"
                             " FROM claims")}
            except sqlite3.DatabaseError:
                prior = {}
        probe.close()
        if migrating:
            dbp.unlink()
            db = connect(memo_dir)
            cur = db.cursor()
            print("old index schema detected — rebuilt from scratch", file=sys.stderr)
        else:
            db = connect(memo_dir)
            cur = db.cursor()
            prior = {(r["source"], r["claim_id"]):
                     (r["hits"], r["helpful"], r["unhelpful"], r["depth"])
                     for r in cur.execute("SELECT source, claim_id, hits, helpful,"
                                          " unhelpful, depth FROM claims")}
    else:
        db = connect(memo_dir)
        cur = db.cursor()
        prior = {}

    # Feedback is earned across sessions and must survive a rebuild, so preserve the
    # counters keyed by (source, claim_id) rather than truncating the table.
    cur.execute("DELETE FROM kw")
    cur.execute("DELETE FROM claims")
    cur.execute("DELETE FROM memos")
    # A contentless FTS5 table rejects DELETE; it takes a command row instead.
    cur.execute("INSERT INTO claims_fts (claims_fts) VALUES ('delete-all')")

    all_claims = []
    memo_rows = []
    unparseable_total = 0
    dupes = 0
    for mp in sorted(memo_dir.glob("*.memo.md")):
        raw = mp.read_text(encoding="utf-8")
        fm, claims, unparseable = parse_memo_text(raw)
        src = fm.get("source", mp.stem)
        unparseable_total += len(unparseable)
        seen = set()
        kept = 0
        for c in claims:
            if c["claim_id"] in seen:
                # Two claims with the same id inside one memo: index the first, say so.
                dupes += 1
                continue
            seen.add(c["claim_id"])
            kws = extract_keywords(c["text"], c["anchor_snip"])
            spec = specificity(c["text"], c["anchor_snip"])
            h, hp, un, dp = prior.get((src, c["claim_id"]), (0, 0, 0, 0))
            all_claims.append({**c, "source": src, "memo": mp.name, "kws": kws,
                               "spec": spec, "hits": h, "helpful": hp, "unhelpful": un,
                               "depth": dp})
            kept += 1
        memo_rows.append((mp.name, hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                          src, fm.get("sha256"), kept))

    # IDF over the corpus: a keyword on every claim ("function", "returns") carries no
    # information and must not pull matches. This is computed, not guessed.
    N = max(1, len(all_claims))
    df: dict[str, int] = {}
    for c in all_claims:
        for k in c["kws"]:
            df[k] = df.get(k, 0) + 1

    def idf(k: str) -> float:
        return math.log((N + 1) / (df.get(k, 0) + 1)) / math.log(N + 1)

    for c in all_claims:
        mean_idf = (sum(idf(k) for k in c["kws"]) / len(c["kws"])) if c["kws"] else 0.0
        u = utility(c["status"], c["spec"], mean_idf, c["hits"], c["helpful"], c["unhelpful"])
        # Plain INSERT, not INSERT OR REPLACE: with (memo, source, claim_id) unique there
        # is nothing legitimate left to replace. REPLACE was silently deleting the row a
        # second chunk-memo of the same source collided with, leaving its kw and FTS rows
        # pointing at a dead rowid — and the build still reported both claims as indexed.
        cur.execute(
            "INSERT INTO claims (source, memo, claim_id, text, status, anchor_line,"
            " anchor_snip, region_lo, region_hi, specificity, idf, hits, helpful,"
            " unhelpful, depth, utility) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (c["source"], c["memo"], c["claim_id"], c["text"], c["status"],
             c["anchor_line"], c["anchor_snip"], c["region_lo"], c["region_hi"],
             round(c["spec"], 3), round(mean_idf, 3), c["hits"], c["helpful"],
             c["unhelpful"], c["depth"], u))
        rid = cur.lastrowid
        for k, w in c["kws"].items():
            cur.execute("INSERT INTO kw (keyword, claim_rowid, weight) VALUES (?,?,?)",
                        (k, rid, round(w * (0.4 + idf(k)), 3)))
        cur.execute("INSERT INTO claims_fts (rowid, text, keywords) VALUES (?,?,?)",
                    (rid, c["text"], " ".join(c["kws"])))

    cur.executemany(
        "INSERT OR REPLACE INTO memos (path, memo_sha, source, source_sha, claims_n)"
        " VALUES (?,?,?,?,?)", memo_rows)

    # Belt and braces: any kw row not pointing at a live claim is unreachable weight that
    # would only ever produce a phantom match dropped later at retrieval.
    cur.execute("DELETE FROM kw WHERE claim_rowid NOT IN (SELECT rowid FROM claims)")
    orphans = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    indexed = cur.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    cur.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('claims', ?)", (str(indexed),))
    cur.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,))
    db.commit()

    stats_out = {
        # Reported from the table, not from the list we hoped we wrote.
        "claims": indexed,
        "memos": len(memo_rows),
        "keywords": cur.execute("SELECT COUNT(DISTINCT keyword) FROM kw").fetchone()[0],
        "prunable": cur.execute("SELECT COUNT(*) FROM claims WHERE utility < 0.15").fetchone()[0],
        "carried_feedback": len(prior),
        "unparseable": unparseable_total,
        "duplicates": dupes,
        "orphans": orphans,
    }
    db.close()
    return stats_out


def stats(memo_dir: pathlib.Path):
    state, detail = index_state(memo_dir)
    if state == "missing":
        print(f"no index at {memo_dir/'index.db'} — run memo_db.py --build first")
        return
    if state == "old-schema":
        print(f"{detail} — run memo_db.py --build to rebuild it")
        return
    db = connect(memo_dir, create=False)
    c = db.cursor()
    total = c.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    if not total:
        print("index is empty — run --build after memo_gen.py")
        db.close()
        return
    if state == "stale":
        print(f"WARNING: index is stale — {detail}")
        print("         numbers below describe the memos as they were at build time.\n")
    print(f"claims:   {total}")
    print(f"keywords: {c.execute('SELECT COUNT(DISTINCT keyword) FROM kw').fetchone()[0]}")
    print("\nby status:")
    for r in c.execute("SELECT status, COUNT(*) n, ROUND(AVG(utility),3) u"
                       " FROM claims GROUP BY status ORDER BY n DESC"):
        print(f"  {r['status']:<12} {r['n']:>4}   mean utility {r['u']}")
    print("\nutility distribution:")
    for lo, hi, label in ((0.0, 0.15, "prunable  "), (0.15, 0.35, "weak      "),
                          (0.35, 0.55, "useful    "), (0.55, 1.01, "strong    ")):
        n = c.execute("SELECT COUNT(*) FROM claims WHERE utility >= ? AND utility < ?",
                      (lo, hi)).fetchone()[0]
        bar = "#" * round(40 * n / total)
        print(f"  {label} {n:>4}  {bar}")
    print("\nmost discriminating keywords (rare = worth matching on):")
    for r in c.execute("SELECT keyword, COUNT(*) n FROM kw GROUP BY keyword"
                       " HAVING n <= 2 ORDER BY n LIMIT 8"):
        print(f"  {r['keyword']}")
    print("\nnoise keywords (on many claims — these pull junk matches):")
    for r in c.execute("SELECT keyword, COUNT(*) n FROM kw GROUP BY keyword"
                       " ORDER BY n DESC LIMIT 5"):
        print(f"  {r['keyword']:<20} on {r['n']} claims")
    db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--memo-dir", default=".memo", type=pathlib.Path)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    memo_dir = args.memo_dir.resolve()
    if not memo_dir.exists():
        sys.exit(f"no such memo dir: {memo_dir}")

    if args.build:
        s = build(memo_dir)
        print(f"indexed {s['claims']} claims, {s['keywords']} keywords "
              f"-> {memo_dir/'index.db'}")
        if s["carried_feedback"]:
            print(f"carried feedback for {s['carried_feedback']} claim(s) across rebuild")
        if s["duplicates"]:
            print(f"{s['duplicates']} duplicate claim id(s) within a memo — first kept, "
                  f"rest dropped")
        if s["unparseable"]:
            print(f"{s['unparseable']} claim-like line(s) could not be parsed (no anchor?) "
                  f"— not indexed, not counted as verified")
        if s["orphans"]:
            print(f"cleaned {s['orphans']} orphaned keyword row(s)")
        if s["prunable"]:
            print(f"{s['prunable']} claim(s) below the utility floor — they will not surface")
        print("next: memo_query.py --memo-dir <dir> '<your question>'")
    elif args.stats:
        stats(memo_dir)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
