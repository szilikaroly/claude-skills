#!/usr/bin/env python3
"""Reference data that no free JSON API exposes: Scopus/Scimago quartiles, EISZ, rates.

Three tiers, in order of trust:

  1. Downloadable authoritative files  — Scimago journal rank (SJR, quartile,
     publisher country, subject categories). Derived from Scopus, so presence in
     the current-year file is this skill's operational test for "Scopus-indexed".
  2. Institution-licensed exports the user supplies — JCR (real Clarivate JIF),
     Scopus Source List (CiteScore, percentile). Never fabricated when absent.
  3. Curated local CSVs under data/ — EISZ agreements, acceptance/desk-reject
     rates. Every row carries a source and a date, and unverified rows say so.
"""
from __future__ import annotations

import csv
import io
import os
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("JMATCH_DATA", HERE / "data"))
THIS_YEAR = date.today().year
SCIMAGO_URL = "https://www.scimagojr.com/journalrank.php?out=xls&year={year}"


def norm_issn(s: str) -> str:
    """Scimago prints ISSNs unhyphenated; OpenAlex hyphenates. Compare stripped."""
    return re.sub(r"[^0-9X]", "", (s or "").upper())


# ------------------------------------------------------------------ Scimago / Scopus

def scimago_path(year: int | None = None) -> Path:
    year = year or _latest_scimago_year()
    return DATA / f"scimago-{year}.csv"


def _latest_scimago_year() -> int:
    years = sorted(int(m.group(1)) for p in DATA.glob("scimago-*.csv")
                   if (m := re.search(r"scimago-(\d{4})\.csv$", p.name)))
    return years[-1] if years else THIS_YEAR - 1


def sync_scimago(year: int | None = None, *, force: bool = False) -> Path:
    """Scimago publishes the whole ranking as a semicolon CSV. ~30k rows, ~6 MB."""
    year = year or THIS_YEAR - 1
    out = DATA / f"scimago-{year}.csv"
    if out.exists() and not force:
        return out
    DATA.mkdir(parents=True, exist_ok=True)
    url = SCIMAGO_URL.format(year=year)
    req = urllib.request.Request(url, headers={"User-Agent": "jmatch/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        body = r.read()
    text = body.decode("utf-8-sig", "replace")
    if "Sourceid" not in text[:2000]:
        raise RuntimeError(f"{url} did not return the Scimago table (got {len(body)} bytes). "
                           f"Download it by hand from scimagojr.com and save as {out}")
    out.write_bytes(body)
    return out


_SCIMAGO: dict | None = None


def load_scimago(year: int | None = None) -> dict:
    """ISSN -> {sjr, quartile, d1, country, publisher, categories, coverage}.

    D1 is not a column in the file: it is computed here as the top decile by SJR
    *within a subject category*, which is what "D1" means in Hungarian and Spanish
    research assessment. A journal is D1 if it is top-10% in any of its categories.
    """
    global _SCIMAGO
    if _SCIMAGO is not None:
        return _SCIMAGO
    p = scimago_path(year)
    if not p.exists():
        _SCIMAGO = {}
        return _SCIMAGO

    rows = list(csv.DictReader(io.StringIO(p.read_text(encoding="utf-8-sig", errors="replace")),
                               delimiter=";"))
    # category -> [(sjr, rowindex)] so we can compute the decile cut per category
    by_cat: dict[str, list[tuple[float, int]]] = {}
    parsed = []
    for idx, r in enumerate(rows):
        sjr = _num(r.get("SJR"))
        cats = []
        for chunk in (r.get("Categories") or "").split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.match(r"^(.*?)\s*\((Q[1-4])\)$", chunk)
            cats.append((m.group(1).strip(), m.group(2)) if m else (chunk, None))
        parsed.append({
            "title": (r.get("Title") or "").strip(),
            "sjr": sjr,
            "quartile": (r.get("SJR Best Quartile") or "").strip() or None,
            "h_index": int(_num(r.get("H index")) or 0),
            "country": (r.get("Country") or "").strip(),
            "publisher": (r.get("Publisher") or "").strip(),
            "coverage": (r.get("Coverage") or "").strip(),
            "categories": cats,
            "issns": [norm_issn(x) for x in re.split(r"[,\s]+", r.get("Issn") or "") if norm_issn(x)],
            "scimago_rank": int(_num(r.get("Rank")) or 0),
        })
        if sjr:
            for cat, _q in cats:
                by_cat.setdefault(cat, []).append((sjr, idx))

    d1_idx: set[int] = set()
    for cat, entries in by_cat.items():
        entries.sort(key=lambda t: -t[0])
        cut = max(1, round(len(entries) * 0.10))
        d1_idx.update(i for _s, i in entries[:cut])

    index: dict[str, dict] = {}
    for idx, rec in enumerate(parsed):
        rec["d1"] = idx in d1_idx
        rec["scopus_indexed"] = True          # the Scimago file *is* the Scopus corpus
        for issn in rec["issns"]:
            index.setdefault(issn, rec)
    _SCIMAGO = index
    return index


def scimago_lookup(issns: list[str], title: str = "") -> dict:
    idx = load_scimago()
    if not idx:
        return {"scimago_loaded": False}
    for issn in issns:
        rec = idx.get(norm_issn(issn))
        if rec:
            return _scimago_out(rec)
    if title:
        t = title.strip().lower()
        for rec in idx.values():
            if rec["title"].lower() == t:
                return _scimago_out(rec)
    return {"scimago_loaded": True, "scopus_indexed": False}


def _scimago_out(rec: dict) -> dict:
    return {
        "scimago_loaded": True,
        "scopus_indexed": True,
        "sjr": rec["sjr"],
        "quartile": rec["quartile"],
        "d1": rec["d1"],
        "scimago_h_index": rec["h_index"],
        "publisher_country": rec["country"],
        "scimago_publisher": rec["publisher"],
        "scimago_coverage": rec["coverage"],
        "subject_categories": [f"{c} ({q})" if q else c for c, q in rec["categories"]][:6],
    }


def _csv_rows(path: Path, delim: str = ",") -> list[dict]:
    """DictReader, but `#` comment lines are stripped first so data files can
    carry their own provenance notes at the top."""
    if not path.exists():
        return []
    body = "\n".join(ln for ln in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
                     if not ln.lstrip().startswith("#"))
    return list(csv.DictReader(io.StringIO(body), delimiter=delim))


def _num(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# ------------------------------------------------------------------ JCR (real IF)

def load_jcr() -> dict:
    """Optional user-supplied Clarivate JCR export.

    The Journal Impact Factor is a licensed JCR metric. This skill never invents
    one. Point JMATCH_JCR_CSV at an export with ISSN and JIF columns and the real
    number is used; otherwise the OpenAlex 2-year mean citedness is reported, and
    labelled as a proxy, not as the IF.
    """
    p = os.environ.get("JMATCH_JCR_CSV") or (DATA / "jcr.csv")
    p = Path(p)
    if not p.exists():
        return {}
    out = {}
    head = p.read_text(encoding="utf-8-sig", errors="replace")[:4096]
    delim = ";" if head.count(";") > head.count(",") else ","
    for r in _csv_rows(p, delim):
            keys = {k.lower().strip(): (v or "").strip() for k, v in r.items() if k}
            jif = next((keys[k] for k in keys if k in
                        ("jif", "impact factor", "2023 jif", "journal impact factor",
                         "2024 jif", "5 year jif")), None)
            for k in keys:
                if "issn" in k and keys[k]:
                    for issn in re.split(r"[,\s;]+", keys[k]):
                        if norm_issn(issn):
                            out[norm_issn(issn)] = {"jif": _num(jif), "jif_source": p.name}
    return out


def jcr_lookup(issns: list[str]) -> dict:
    idx = load_jcr()
    for issn in issns:
        if norm_issn(issn) in idx:
            return idx[norm_issn(issn)]
    return {}


# ------------------------------------------------------------------ EISZ

def load_eisz() -> list[dict]:
    return [r for r in _csv_rows(DATA / "eisz.csv") if r.get("publisher_pattern")]


def eisz_lookup(publisher: str, journal: str = "") -> dict:
    """Publisher-level match against the EISZ read & publish roster.

    Deliberately weak on purpose: EISZ agreements are per-year, sometimes per-title,
    and often capped by an annual quota. A match here means "there has been an
    agreement with this publisher — go confirm the current title list", never
    "your APC is waived".
    """
    hay = f"{publisher} {journal}".lower()
    for row in load_eisz():
        try:
            if re.search(row["publisher_pattern"], hay, re.I):
                return {
                    "eisz_publisher": row.get("publisher") or publisher,
                    "eisz_status": row.get("status", "unverified"),
                    "eisz_model": row.get("model", ""),
                    "eisz_as_of": row.get("as_of", ""),
                    "eisz_note": row.get("note", ""),
                    "eisz_check_url": row.get("url", "https://eisz.mtak.hu/"),
                }
        except re.error:
            continue
    return {"eisz_status": "no agreement found in local roster"}


# ------------------------------------------------------------------ acceptance rates

def load_rates() -> dict:
    out = {}
    for r in _csv_rows(DATA / "acceptance_rates.csv"):
        for issn in re.split(r"[,\s;]+", r.get("issn", "")):
            if norm_issn(issn):
                out[norm_issn(issn)] = {
                    "acceptance_rate": _num(r.get("acceptance_rate")),
                    "desk_reject_rate": _num(r.get("desk_reject_rate")),
                    "rate_year": r.get("year", ""),
                    "rate_source": r.get("source", ""),
                }
    return out


def rate_lookup(issns: list[str]) -> dict:
    idx = load_rates()
    for issn in issns:
        if norm_issn(issn) in idx:
            return idx[norm_issn(issn)]
    return {}


def status() -> str:
    lines = ["reference data status:"]
    sc = scimago_path()
    lines.append(f"  scimago      {'OK  ' + sc.name if sc.exists() else 'MISSING — run: jmatch.py sync'}"
                 + (f"  ({len(load_scimago())} ISSNs)" if sc.exists() else ""))
    jcr = load_jcr()
    lines.append(f"  JCR (real IF) {'OK  ' + str(len(jcr)) + ' ISSNs' if jcr else 'not supplied — IF proxy will be used, and labelled as a proxy'}")
    lines.append(f"  EISZ roster   {len(load_eisz())} publisher rules  (verify at eisz.mtak.hu)")
    lines.append(f"  acceptance    {len(load_rates())} journals with curated rates")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        year = int(sys.argv[2]) if len(sys.argv) > 2 else THIS_YEAR - 1
        print(sync_scimago(year, force="--force" in sys.argv))
    print(status())
