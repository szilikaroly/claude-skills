#!/usr/bin/env python3
"""jmatch — target-journal matching for a manuscript.

Stdlib only. Public APIs, no keys required:
  OpenAlex     https://api.openalex.org        candidate discovery, venue metrics, topics
  DOAJ         https://doaj.org/api            OA status, APC, review/publication time
  NLM Catalog  eutils.ncbi.nlm.nih.gov         MEDLINE indexing status
  PubMed       eutils.ncbi.nlm.nih.gov         received->accepted turnaround (real dates)
  Crossref     https://api.crossref.org        publisher, ISSN cross-check, output volume

Set JMATCH_EMAIL to join the OpenAlex/NCBI polite pool (much better rate limits).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refdata  # noqa: E402  (local module, same directory)

EMAIL = os.environ.get("JMATCH_EMAIL", "").strip()
CACHE = Path(os.environ.get("JMATCH_CACHE", Path.home() / ".cache" / "journal-matcher"))
CACHE_TTL = int(os.environ.get("JMATCH_CACHE_TTL", 7 * 24 * 3600))
UA = f"jmatch/1.0 (journal-matcher skill{'; mailto:' + EMAIL if EMAIL else ''})"
THIS_YEAR = date.today().year

# Words that carry no topical signal in a biomedical title/abstract.
STOP = set("""a an the and or of in on for with without to from by as at is are was were be been being
this that these those we our us it its their they them he she his her not no than then thus therefore
study studies patient patients group groups result results method methods conclusion conclusions
background objective objectives aim aims purpose introduction discussion significant significantly
associated association compared comparison using used use also may can could should would however
between among during after before within high low increased decreased effect effects outcome outcomes
data analysis analyses trial trials cohort case cases control controls risk factor factors year years
new novel role evaluation assessment based both all more most such which who whom while when where""".split())


# ---------------------------------------------------------------- HTTP + cache

class ApiError(RuntimeError):
    pass


def _cache_path(url: str) -> Path:
    return CACHE / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")


def get_json(url: str, *, ttl: int = CACHE_TTL, retries: int = 3) -> dict:
    """GET a JSON endpoint, with an on-disk cache and polite backoff."""
    cp = _cache_path(url)
    if ttl and cp.exists() and time.time() - cp.stat().st_mtime < ttl:
        try:
            return json.loads(cp.read_text())
        except json.JSONDecodeError:
            cp.unlink(missing_ok=True)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read().decode("utf-8", "replace")
            data = json.loads(body)
            CACHE.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(data))
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
            last = e
            code = getattr(e, "code", None)
            if code in (400, 404):        # a real answer: "no such thing"
                raise ApiError(f"{code} for {url}") from e
            time.sleep(2 ** attempt)
    raise ApiError(f"{url} failed after {retries} tries: {last}")


def get_xml(url: str, *, ttl: int = CACHE_TTL) -> ET.Element:
    cp = _cache_path(url + "#xml")
    if ttl and cp.exists() and time.time() - cp.stat().st_mtime < ttl:
        return ET.fromstring(cp.read_text())
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                text = r.read().decode("utf-8", "replace")
            CACHE.mkdir(parents=True, exist_ok=True)
            cp.write_text(text)
            return ET.fromstring(text)
        except Exception as e:  # noqa: BLE001 - eutils returns HTML on overload
            if attempt == 2:
                raise ApiError(f"{url}: {e}") from e
            time.sleep(2 ** attempt)
    raise ApiError(url)


def _eutils(path: str, **params) -> str:
    params = {k: v for k, v in params.items() if v is not None}
    if EMAIL:
        params.setdefault("email", EMAIL)
        params.setdefault("tool", "jmatch")
    return f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/{path}?" + urllib.parse.urlencode(params)


def _oa(path: str, **params) -> str:
    if EMAIL:
        params.setdefault("mailto", EMAIL)
    return f"https://api.openalex.org/{path}?" + urllib.parse.urlencode(params, safe=":,|+<>-")


# ---------------------------------------------------------------- manuscript input

def read_manuscript(path: str) -> str:
    """Plain text in; for PDF/DOCX pipe through the doc-tools extractors first."""
    if path == "-":
        return sys.stdin.read()
    p = Path(path)
    if p.suffix.lower() in {".pdf", ".docx", ".doc", ".pptx", ".tex"}:
        raise SystemExit(
            f"{p.name}: extract the text first, e.g.\n"
            f"  pdftotext {p} | jmatch.py discover --text -"
        )
    return p.read_text(errors="replace")


def query_terms(text: str, n: int = 12) -> list[str]:
    """Highest-signal content words, for the OpenAlex free-text query."""
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        w = w.strip("-")
        if w in STOP or len(w) < 4:
            continue
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:n]]


def split_title_abstract(text: str) -> tuple[str, str]:
    """First non-empty line is the title; abstract is what follows, to ~350 words."""
    lines = [ln.strip() for ln in text.splitlines()]
    title = next((ln for ln in lines if ln), "")
    rest = " ".join(ln for ln in lines[lines.index(title) + 1:] if ln) if title else text
    m = re.search(r"\b(abstract|összefoglal[óo]|kivonat)\b[:\s]*", rest, re.I)
    if m:
        rest = rest[m.end():]
    m = re.search(r"\b(introduction|bevezet[ée]s|keywords|kulcsszavak)\b", rest, re.I)
    if m:
        rest = rest[:m.start()]
    return title, " ".join(rest.split()[:350])


# ---------------------------------------------------------------- 1. discovery

def discover(text: str, *, limit: int = 25, years: int = 5, per_source_min: int = 2) -> list[dict]:
    """Which journals actually publish work like this?

    Groups OpenAlex works matching the manuscript's title+abstract by their source.
    This is evidence, not vibes: a journal ranks because it demonstrably published
    N papers on this topic in the recency window.
    """
    title, abstract = split_title_abstract(text)
    q = " ".join(query_terms(title + " " + abstract, 14))
    if not q:
        raise SystemExit("no usable terms in the manuscript text")
    filt = f"title_and_abstract.search:{q},from_publication_date:{THIS_YEAR - years}-01-01,type:article"
    data = get_json(_oa("works", filter=filt, group_by="primary_location.source.id", per_page=200))
    out = []
    for g in data.get("group_by", []):
        if not g.get("key") or g["key"] == "unknown" or g.get("count", 0) < per_source_min:
            continue
        out.append({
            "source_id": g["key"].rsplit("/", 1)[-1],
            "journal": g.get("key_display_name") or "",
            "topic_hits": g["count"],
        })
    return out[:limit]


def journals_cited(text: str, *, limit: int = 40) -> list[dict]:
    """Where the manuscript's own references were published.

    A journal you cite five times is a journal whose readers are your readers —
    and reviewers notice when the reference list points somewhere else entirely.
    """
    dois = sorted({d.rstrip(".,);]").lower() for d in re.findall(r"10\.\d{4,9}/[^\s\"<>]+", text)})
    counts: dict[str, dict] = {}
    for chunk in (dois[i:i + 50] for i in range(0, len(dois), 50)):
        filt = "doi:" + "|".join(chunk)
        try:
            data = get_json(_oa("works", filter=filt, per_page=50, select="id,primary_location"))
        except ApiError:
            continue
        for w in data.get("results", []):
            src = ((w.get("primary_location") or {}).get("source") or {})
            sid = (src.get("id") or "").rsplit("/", 1)[-1]
            if not sid:
                continue
            e = counts.setdefault(sid, {"source_id": sid, "journal": src.get("display_name", ""), "cited_by_manuscript": 0})
            e["cited_by_manuscript"] += 1
    ranked = sorted(counts.values(), key=lambda e: -e["cited_by_manuscript"])
    return ranked[:limit], len(dois)


# ---------------------------------------------------------------- 2. journal dossier

def resolve_source(name_or_issn: str) -> dict:
    """Accept a journal name, an ISSN, or an OpenAlex S-id."""
    s = name_or_issn.strip()
    if re.fullmatch(r"[Ss]\d+", s):
        return get_json(_oa(f"sources/{s.upper()}"))
    if re.fullmatch(r"\d{4}-\d{3}[\dXx]", s):
        return get_json(_oa(f"sources/issn:{s.upper()}"))
    data = get_json(_oa("sources", search=s, per_page=5))
    results = data.get("results", [])
    if not results:
        raise ApiError(f"no OpenAlex source matches {s!r}")
    return results[0]


def openalex_profile(src: dict) -> dict:
    stats = src.get("summary_stats") or {}
    counts = {c["year"]: c.get("works_count", 0) for c in src.get("counts_by_year", [])}
    recent = [counts.get(y, 0) for y in range(THIS_YEAR - 3, THIS_YEAR)]
    return {
        "source_id": (src.get("id") or "").rsplit("/", 1)[-1],
        "journal": src.get("display_name"),
        "publisher": src.get("host_organization_name"),
        "issn_l": src.get("issn_l"),
        "issns": src.get("issn") or [],
        "type": src.get("type"),
        "homepage": src.get("homepage_url"),
        "is_oa": src.get("is_oa"),
        "is_in_doaj": src.get("is_in_doaj"),
        "is_core": src.get("is_core"),          # OpenAlex "core" (CWTS Leiden-ranked) set
        "apc_usd": src.get("apc_usd"),
        "works_count": src.get("works_count"),
        "works_per_year_recent": recent,
        "cited_by_count": src.get("cited_by_count"),
        "impact_proxy_2y": stats.get("2yr_mean_citedness"),   # NOT the Clarivate JIF
        "h_index": stats.get("h_index"),
        "i10_index": stats.get("i10_index"),
        "topics": [t.get("display_name") for t in (src.get("topics") or [])[:8]],
    }


def doaj_profile(issns: list[str]) -> dict:
    """DOAJ is the only free source for APC, licence and real review timelines."""
    for issn in issns:
        try:
            data = get_json("https://doaj.org/api/search/journals/" +
                            urllib.parse.quote(f'issn:"{issn}"'))
        except ApiError:
            continue
        results = data.get("results") or []
        if not results:
            continue
        b = results[0].get("bibjson", {})
        apc = b.get("apc") or {}
        maxes = apc.get("max") or []
        return {
            "in_doaj": True,
            "doaj_apc": bool(apc.get("has_apc")),
            "doaj_apc_amount": (maxes[0].get("price") if maxes else None),
            "doaj_apc_currency": (maxes[0].get("currency") if maxes else None),
            "doaj_licences": [l.get("type") for l in (b.get("license") or [])],
            "doaj_review_process": [r.get("type") for r in (b.get("editorial", {}) or {}).get("review_process", [])],
            "doaj_weeks_to_publication": (b.get("editorial", {}) or {}).get("review_process_weeks")
                                          or b.get("publication_time_weeks"),
            "doaj_plagiarism_screening": (b.get("plagiarism", {}) or {}).get("detection"),
            "doaj_preservation": bool((b.get("preservation") or {}).get("has_preservation")),
        }
    return {"in_doaj": False}


def medline_status(issns: list[str], journal: str = "") -> dict:
    """Currently indexed for MEDLINE, or merely deposited in PMC? Not the same thing."""
    for term in [f"{i}[ISSN]" for i in issns] + ([f'"{journal}"[Title]' if journal else ""]):
        if not term:
            continue
        try:
            root = get_xml(_eutils("esearch.fcgi", db="nlmcatalog", term=f"{term} AND ncbijournals[All Fields]",
                                   retmax="1", retmode="xml"))
        except ApiError:
            continue
        ids = [e.text for e in root.iter("Id")]
        if not ids:
            continue
        try:
            summ = get_xml(_eutils("esummary.fcgi", db="nlmcatalog", id=ids[0], retmode="xml"))
        except ApiError:
            continue
        # nlmcatalog nests Items inside List/Structure containers whose own text is
        # whitespace; keep only Items that carry real text so a container cannot win.
        fields = {}
        for i in summ.iter("Item"):
            txt = (i.text or "").strip()
            if i.get("Name") and txt:
                fields.setdefault(i.get("Name"), txt)
        status = fields.get("currentindexingstatus", "")
        return {
            "nlm_id": ids[0],
            "in_pubmed": True,
            "medline_indexed": status.strip() in ("Y", "Currently indexed"),
            "nlm_indexing_status": status or "unknown",
            "nlm_title": fields.get("Title") or fields.get("TitleMain") or journal,
        }
    return {"in_pubmed": False, "medline_indexed": False, "nlm_indexing_status": "not found in NLM Catalog"}


def turnaround(issns: list[str], *, sample: int = 60) -> dict:
    """Median days received -> accepted, from PubMed's own article history dates.

    This is measured, not advertised. Journals publish nothing about their real
    review speed; PubMed carries the dates the publisher deposited.
    """
    issn = next(iter(issns), None)
    if not issn:
        return {}
    try:
        root = get_xml(_eutils("esearch.fcgi", db="pubmed",
                               term=f'"{issn}"[TA] OR "{issn}"[IS]',
                               retmax=str(sample), sort="pub+date", retmode="xml"))
        pmids = [e.text for e in root.iter("Id")]
        if not pmids:
            return {}
        arts = get_xml(_eutils("efetch.fcgi", db="pubmed", id=",".join(pmids), retmode="xml"))
    except ApiError:
        return {}

    deltas, pubdeltas = [], []
    for art in arts.iter("PubmedArticle"):
        dates = {}
        for pd in art.iter("PubMedPubDate"):
            st = pd.get("PubStatus")
            try:
                dates[st] = date(int(pd.findtext("Year")), int(pd.findtext("Month")), int(pd.findtext("Day")))
            except (TypeError, ValueError):
                continue
        if "received" in dates and "accepted" in dates:
            d = (dates["accepted"] - dates["received"]).days
            if 0 <= d <= 1500:
                deltas.append(d)
        if "accepted" in dates and ("entrez" in dates or "pubmed" in dates):
            d = ((dates.get("entrez") or dates["pubmed"]) - dates["accepted"]).days
            if 0 <= d <= 1500:
                pubdeltas.append(d)
    out = {"turnaround_n": len(deltas)}
    if deltas:
        out["days_submission_to_acceptance_median"] = int(statistics.median(deltas))
        out["days_submission_to_acceptance_iqr"] = [int(x) for x in _iqr(deltas)]
    if pubdeltas:
        out["days_acceptance_to_pubmed_median"] = int(statistics.median(pubdeltas))
    return out


def _iqr(xs: list[float]) -> tuple[float, float]:
    xs = sorted(xs)
    if len(xs) < 4:
        return (xs[0], xs[-1])
    q = statistics.quantiles(xs, n=4)
    return (q[0], q[2])


def article_types_and_length(source_id: str, *, years: int = 3, sample: int = 200) -> dict:
    """What the journal actually publishes, measured — not what its scope page claims.

    Author guidelines say "original articles up to 3500 words"; the published
    record says what really gets through. Reference count and page extent are the
    two length proxies OpenAlex carries, and both are usable as a sanity check
    against a manuscript that is twice or half the journal's norm.
    """
    since = f"{THIS_YEAR - years}-01-01"
    base = f"primary_location.source.id:{source_id},from_publication_date:{since}"
    out: dict = {}
    try:
        g = get_json(_oa("works", filter=base, group_by="type", per_page=50))
        total = sum(x.get("count", 0) for x in g.get("group_by", []))
        if total:
            out["article_type_mix"] = {
                x["key_display_name"]: round(100 * x["count"] / total)
                for x in g["group_by"][:6] if x.get("count")
            }
            out["works_in_window"] = total
    except ApiError:
        pass
    try:
        w = get_json(_oa("works", filter=base, per_page=str(min(sample, 200)),
                         select="biblio,referenced_works_count,type", sort="publication_date:desc"))
        refs, pages = [], []
        for r in w.get("results", []):
            n = r.get("referenced_works_count")
            if isinstance(n, int) and 0 < n < 800:
                refs.append(n)
            b = r.get("biblio") or {}
            try:
                fp, lp = int(b.get("first_page")), int(b.get("last_page"))
                if 0 < lp - fp < 100:
                    pages.append(lp - fp + 1)
            except (TypeError, ValueError):
                pass
        if refs:
            out["median_references"] = int(statistics.median(refs))
            out["references_iqr"] = [int(x) for x in _iqr(refs)]
        if pages:
            out["median_pages"] = int(statistics.median(pages))
    except ApiError:
        pass
    return out


def author_countries(source_id: str, *, years: int = 3, top: int = 8) -> dict:
    """Where the journal's authors are.

    Two uses. Practical: a journal publishing almost nothing from your region may
    read your paper as out-of-scope for its audience. Diagnostic: a journal whose
    authorship collapsed into one or two countries within a few years is often a
    journal whose editorial board did too.
    """
    since = f"{THIS_YEAR - years}-01-01"
    filt = f"primary_location.source.id:{source_id},from_publication_date:{since}"
    for key in ("authorships.countries", "institutions.country_code"):
        try:
            g = get_json(_oa("works", filter=filt, group_by=key, per_page=50))
        except ApiError:
            continue
        groups = [x for x in g.get("group_by", []) if x.get("key") and x["key"] != "unknown"]
        total = sum(x["count"] for x in groups)
        if not total:
            continue
        share = {x["key_display_name"] or x["key"]: round(100 * x["count"] / total)
                 for x in groups[:top]}
        out = {"author_countries": share, "author_country_n": len(groups)}
        top_share = max(share.values()) if share else 0
        if top_share >= 60 and len(groups) < 15:
            out["author_country_concentration"] = top_share
        return out
    return {}


def profile(name_or_issn: str, *, deep: bool = True) -> dict:
    src = resolve_source(name_or_issn)
    p = openalex_profile(src)
    issns = [i for i in ([p["issn_l"]] + p["issns"]) if i]
    seen, uniq = set(), []
    for i in issns:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    p.update(doaj_profile(uniq))
    p.update(medline_status(uniq, p["journal"] or ""))
    p.update(refdata.scimago_lookup(uniq, p["journal"] or ""))
    p.update(refdata.jcr_lookup(uniq))
    p.update(refdata.eisz_lookup(p.get("publisher") or "", p.get("journal") or ""))
    p.update(refdata.rate_lookup(uniq))
    if deep:
        p.update(turnaround(uniq))
        p.update(article_types_and_length(p["source_id"]))
        p.update(author_countries(p["source_id"]))
    p["flags"] = red_flags(p)
    return p


# ---------------------------------------------------------------- 3. integrity screen

def red_flags(p: dict) -> list[str]:
    """Positive-signal screening. These are prompts to look closer, never a verdict.

    Deliberately not a blacklist: those are stale, contested, and unfair to new
    legitimate journals. What is checkable is whether the journal shows the marks
    of a real one — indexing, a registered ISSN, a plausible review timeline.
    """
    f = []
    if not p.get("in_pubmed"):
        f.append("not in the NLM Catalog — no PubMed record at all")
    elif not p.get("medline_indexed"):
        f.append("in PubMed but not currently MEDLINE-indexed (check whether that is PMC-only deposit)")
    if p.get("is_oa") and not p.get("in_doaj"):
        f.append("open access but not listed in DOAJ")
    if p.get("works_count") is not None and not p.get("issn_l") and not p.get("issns"):
        f.append("no registered ISSN")
    wk = p.get("doaj_weeks_to_publication")
    if isinstance(wk, (int, float)) and wk <= 3:
        f.append(f"advertises ~{wk} weeks to publication — too fast for real peer review")
    ta = p.get("days_submission_to_acceptance_median")
    if isinstance(ta, int) and ta <= 14 and p.get("turnaround_n", 0) >= 10:
        f.append(f"median {ta} days submission→acceptance across {p['turnaround_n']} papers — implausibly fast")
    if p.get("in_doaj") and p.get("doaj_plagiarism_screening") is False:
        f.append("DOAJ record states no plagiarism screening")
    apc = p.get("apc_usd") or 0
    if apc and not p.get("medline_indexed") and not p.get("in_doaj"):
        f.append(f"charges ~${apc} while neither MEDLINE-indexed nor in DOAJ")
    if p.get("scimago_loaded") and not p.get("scopus_indexed"):
        f.append("not in the Scimago/Scopus source list for that year")
    conc = p.get("author_country_concentration")
    if conc:
        top = max(p.get("author_countries", {}).items(), key=lambda kv: kv[1], default=("?", 0))
        f.append(f"{conc}% of authors from one country ({top[0]}) across few countries — narrow authorship base")
    recent = p.get("works_per_year_recent") or []
    if len(recent) >= 3 and recent[0] and recent[-1] > 8 * max(recent[0], 1):
        f.append(f"output exploded {recent[0]}→{recent[-1]} papers/year — check for special-issue farming")
    return f


# ---------------------------------------------------------------- 4. scoring

WEIGHTS = {"scope": 35, "impact": 20, "standing": 12, "indexing": 8,
           "speed": 8, "access": 12, "acceptance": 5}
QUARTILE_POINTS = {"Q1": 1.0, "Q2": 0.7, "Q3": 0.4, "Q4": 0.15}


def score_one(p: dict, ctx: dict) -> dict:
    """Transparent additive score. Every component is shown, so it can be argued with."""
    parts = {}

    hits, maxhits = p.get("topic_hits", 0), max(ctx.get("max_hits", 1), 1)
    cited, maxcited = p.get("cited_by_manuscript", 0), max(ctx.get("max_cited", 1), 1)
    # scope carries a bonus for the reference-list overlap rather than a separate axis:
    # citing a journal is corroboration of fit, not an independent virtue.
    parts["scope"] = WEIGHTS["scope"] * (0.85 * (hits / maxhits) ** 0.5
                                         + 0.15 * (cited / maxcited) ** 0.5)

    jif = p.get("jif")
    imp = jif if isinstance(jif, (int, float)) else (p.get("impact_proxy_2y") or 0)
    maximp = max(ctx.get("max_impact", 1), 1e-9)
    parts["impact"] = WEIGHTS["impact"] * min(imp / maximp, 1.0) ** 0.5

    if p.get("d1"):
        parts["standing"] = WEIGHTS["standing"]
    else:
        parts["standing"] = WEIGHTS["standing"] * QUARTILE_POINTS.get(p.get("quartile") or "", 0.3)

    parts["indexing"] = (5 if p.get("medline_indexed") else 2 if p.get("in_pubmed") else 0) \
                        + (2 if p.get("scopus_indexed") else 0) \
                        + (1 if p.get("in_doaj") or not p.get("is_oa") else 0)

    ar = p.get("acceptance_rate")
    if ar is None:
        parts["acceptance"] = WEIGHTS["acceptance"] * 0.5     # unknown: neutral
    elif ar > 0.6:
        parts["acceptance"] = WEIGHTS["acceptance"] * 0.4     # very high is a quality signal, inverted
    else:
        parts["acceptance"] = WEIGHTS["acceptance"] * min(1.0, ar / 0.35)

    ta = p.get("days_submission_to_acceptance_median")
    if ta is None:
        parts["speed"] = WEIGHTS["speed"] * 0.5           # unknown: neither rewarded nor punished
    elif ta < 21:
        parts["speed"] = WEIGHTS["speed"] * 0.3           # suspiciously fast is not a selling point
    else:
        parts["speed"] = WEIGHTS["speed"] * max(0.0, min(1.0, (240 - ta) / 200))

    budget = ctx.get("apc_budget")
    apc = p.get("apc_usd")
    eisz = (p.get("eisz_status") or "").startswith(("verified", "unverified"))
    if eisz and apc:
        # An EISZ read&publish deal is the difference between "affordable" and "not".
        # Treated as a strong discount, not a certainty, because the roster is unverified
        # and the deals are quota-capped.
        apc = apc * 0.25
        p["apc_effective_usd"] = round(apc)
    if budget is None:
        parts["access"] = WEIGHTS["access"] * (1.0 if not apc else 0.7)
    elif apc is None:
        parts["access"] = WEIGHTS["access"] * 0.7
    elif apc <= budget:
        parts["access"] = WEIGHTS["access"] * (1.0 - 0.3 * apc / max(budget, 1))
    else:
        parts["access"] = 0.0

    penalty = 8 * len(p.get("flags") or [])
    total = round(sum(parts.values()) - penalty, 1)
    return {**p,
            "score_parts": {k: round(v, 1) for k, v in parts.items()},
            "score_penalty": penalty,
            "score": total}


QUARTILE_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}


def apply_filters(cands: list[dict], a) -> tuple[list[dict], list[tuple[str, str]]]:
    """Hard gates, applied before scoring. Excluded journals are reported, not
    silently dropped — knowing that a good-fit journal failed the Scopus gate is
    itself information."""
    kept, dropped = [], []
    for c in cands:
        why = None
        if getattr(a, "require_scopus", False) and not c.get("scopus_indexed"):
            why = "not Scopus-indexed"
        elif getattr(a, "require_medline", False) and not c.get("medline_indexed"):
            why = "not MEDLINE-indexed"
        elif getattr(a, "d1_only", False) and not c.get("d1"):
            why = "not D1"
        elif getattr(a, "min_quartile", None):
            q = c.get("quartile")
            if not q or QUARTILE_ORDER.get(q, 9) > QUARTILE_ORDER[a.min_quartile]:
                why = f"quartile {q or 'unknown'} below {a.min_quartile}"
        elif getattr(a, "eisz_only", False) and (c.get("eisz_status") or "").startswith("no agreement"):
            why = "no EISZ agreement in the local roster"
        elif getattr(a, "exclude_flagged", False) and c.get("flags"):
            why = f"{len(c['flags'])} integrity flag(s)"
        (dropped.append((c.get("journal", "?"), why)) if why else kept.append(c))
    return kept, dropped


def rank(cands: list[dict], *, apc_budget: float | None = None) -> list[dict]:
    def imp(c):
        return c["jif"] if isinstance(c.get("jif"), (int, float)) else (c.get("impact_proxy_2y") or 0)
    ctx = {
        "max_hits": max((c.get("topic_hits", 0) for c in cands), default=1),
        "max_cited": max((c.get("cited_by_manuscript", 0) for c in cands), default=1),
        "max_impact": max((imp(c) for c in cands), default=1),
        "apc_budget": apc_budget,
    }
    return sorted((score_one(c, ctx) for c in cands), key=lambda c: -c["score"])


# ---------------------------------------------------------------- 5. novelty & impact

def novelty(text: str, *, years: int = 8, neighbours: int = 12) -> dict:
    """How new is this, and how much room is left in the topic?

    Two hard numbers behind a judgement that is usually made by feel:
      * the publication curve for the manuscript's own topic — a field going from
        30 to 900 papers a year is one where "novel" has a short shelf life, and
        where speed matters more than prestige;
      * the nearest already-published works, so a scoop is found before a reviewer
        finds it. Read these titles. If one of them is your paper, the target
        journal is not the problem.
    """
    title, abstract = split_title_abstract(text)
    q = " ".join(query_terms(title + " " + abstract, 14))
    since = f"{THIS_YEAR - years}-01-01"
    filt = f"title_and_abstract.search:{q},from_publication_date:{since}"
    out = {"query_terms": q.split(), "title": title}

    try:
        g = get_json(_oa("works", filter=filt, group_by="publication_year", per_page=50))
        curve = {int(x["key"]): x["count"] for x in g.get("group_by", []) if str(x["key"]).isdigit()}
        out["works_per_year"] = dict(sorted(curve.items()))
        ys = sorted(curve)
        if len(ys) >= 4:
            early = sum(curve[y] for y in ys[:2]) or 1
            late = sum(curve[y] for y in ys[-3:-1]) or 0   # -1 is usually incomplete
            out["topic_growth"] = round(late / early, 2)
            out["topic_saturation"] = ("crowded and still growing" if late > 300 and out["topic_growth"] > 1.5
                                       else "crowded, flat" if late > 300
                                       else "active" if late > 60
                                       else "sparse — niche journal, or a framing problem")
    except ApiError:
        pass

    try:
        w = get_json(_oa("works", filter=filt, per_page=str(neighbours), sort="relevance_score:desc",
                         select="id,doi,title,publication_year,cited_by_count,primary_location"))
        out["nearest_works"] = [{
            "title": r.get("title"),
            "year": r.get("publication_year"),
            "doi": (r.get("doi") or "").replace("https://doi.org/", ""),
            "cited_by": r.get("cited_by_count"),
            "journal": (((r.get("primary_location") or {}).get("source") or {}).get("display_name")),
        } for r in w.get("results", [])]
    except ApiError:
        pass

    dois = re.findall(r"10\.\d{4,9}/[^\s\"<>]+", text)
    if dois:
        try:
            data = get_json(_oa("works", filter="doi:" + "|".join(d.rstrip(".,);]").lower() for d in dois[:50]),
                                per_page=50, select="publication_year"))
            yrs = [w["publication_year"] for w in data.get("results", []) if w.get("publication_year")]
            if yrs:
                out["reference_median_year"] = int(statistics.median(yrs))
                out["references_last_5y_pct"] = round(100 * sum(1 for y in yrs if y >= THIS_YEAR - 5) / len(yrs))
        except ApiError:
            pass
    return out


# ---------------------------------------------------------------- 6. calls & special issues

CALL_SOURCES = {
    "mdpi": "https://www.mdpi.com/journal/{slug}/special_issues",
    "frontiers": "https://www.frontiersin.org/research-topics",
    "springer": "https://link.springer.com/journal/{slug}/updates",
    "elsevier": "https://www.sciencedirect.com/journal/{slug}/about/call-for-papers",
    "wiley": "https://onlinelibrary.wiley.com/journal/{slug}",
}


def call_probes(journals: list[dict]) -> list[dict]:
    """Open calls and special issues are not in any API — so hand back the exact
    probes to run, rather than pretending to have scraped them.

    Claude runs these with WebSearch/WebFetch and fills in the findings. A live
    special issue is often the single highest-yield route: guest-edited, on a
    stated theme, with a real deadline and — where the invitation is genuine
    rather than a mass mail — a materially better acceptance rate than the
    journal's cold-submission queue.
    """
    out = []
    for j in journals:
        name = j.get("journal") or ""
        pub = (j.get("publisher") or "").lower()
        home = j.get("homepage") or ""
        slug = ""
        m = re.search(r"/(?:journal|journals)/([a-z0-9\-]+)", home, re.I)
        if m:
            slug = m.group(1)
        direct = [t.format(slug=slug) for k, t in CALL_SOURCES.items()
                  if slug and k in pub] or ([home] if home else [])
        out.append({
            "journal": name,
            "publisher": j.get("publisher"),
            "web_search": [
                f'"{name}" special issue call for papers {THIS_YEAR}',
                f'"{name}" "call for papers" deadline',
                f'"{name}" guest editor special issue submission deadline',
            ],
            "fetch": direct,
            "judge": "open call on-topic? deadline reachable? guest editors credible and "
                     "named with affiliations? is the APC different inside the special issue?",
        })
    return out


# ---------------------------------------------------------------- 7. output

def _imp_cell(r) -> tuple[str, str]:
    """Real JIF when the user supplied a JCR export; otherwise the OpenAlex proxy,
    visibly marked so it is never quoted as an impact factor."""
    if isinstance(r.get("jif"), (int, float)):
        return f"{r['jif']:.1f}", "IF"
    v = r.get("impact_proxy_2y")
    return (f"{v:.1f}~" if isinstance(v, (int, float)) else "—"), "proxy"


def as_markdown(rows: list[dict], dropped: list[tuple[str, str]] | None = None) -> str:
    cols = ["#", "Journal", "Publisher", "Score", "Scope hits", "IF / proxy", "Q", "D1",
            "Scopus", "MEDLINE", "APC (USD)", "EISZ", "Sub→acc (d)", "Accept %", "Flags"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for i, r in enumerate(rows, 1):
        apc, eff = r.get("apc_usd"), r.get("apc_effective_usd")
        ta = r.get("days_submission_to_acceptance_median")
        ar = r.get("acceptance_rate")
        imp, _kind = _imp_cell(r)
        eisz = r.get("eisz_status") or ""
        out.append("| " + " | ".join(str(x) for x in [
            i,
            (r.get("journal") or "?")[:44],
            (r.get("publisher") or "—")[:22],
            r.get("score", "—"),
            r.get("topic_hits", "—"),
            imp,
            r.get("quartile") or "—",
            "yes" if r.get("d1") else "",
            "yes" if r.get("scopus_indexed") else ("no" if r.get("scimago_loaded") else "?"),
            "yes" if r.get("medline_indexed") else ("PubMed only" if r.get("in_pubmed") else "no"),
            (f"{apc:,.0f}" + (f" → ~{eff:,.0f}" if eff else "")) if apc else "—",
            "unverified" if eisz == "unverified" else ("yes" if eisz.startswith("verified") else "—"),
            ta if ta is not None else "—",
            f"{100 * ar:.0f}" if isinstance(ar, (int, float)) else "—",
            ("⚠ " + str(len(r["flags"]))) if r.get("flags") else "",
        ]) + " |")

    flagged = [r for r in rows if r.get("flags")]
    if flagged:
        out += ["", "**Integrity flags** — reasons to look closer, not verdicts.", ""]
        for r in flagged:
            out.append(f"- **{r['journal']}** — " + "; ".join(r["flags"]))

    detail = [r for r in rows if r.get("article_type_mix") or r.get("author_countries")]
    if detail:
        out += ["", "**What each journal actually publishes** (last 3 years)", ""]
        for r in detail:
            bits = []
            if r.get("article_type_mix"):
                bits.append("types: " + ", ".join(f"{k} {v}%" for k, v in r["article_type_mix"].items()))
            if r.get("median_references"):
                iqr = r.get("references_iqr") or []
                bits.append(f"refs: median {r['median_references']}" + (f" (IQR {iqr[0]}–{iqr[1]})" if iqr else ""))
            if r.get("median_pages"):
                bits.append(f"~{r['median_pages']} pages")
            if r.get("author_countries"):
                top = list(r["author_countries"].items())[:4]
                bits.append("authors: " + ", ".join(f"{k} {v}%" for k, v in top))
            if r.get("subject_categories"):
                bits.append("Scopus cats: " + ", ".join(r["subject_categories"][:3]))
            out.append(f"- **{r['journal']}** — " + " · ".join(bits))

    if dropped:
        out += ["", "**Excluded by the hard filters**", ""]
        for name, why in dropped:
            out.append(f"- {name} — {why}")

    out += ["",
            "_`IF / proxy`: a bare number is the real Clarivate JIF from the supplied JCR export; "
            "a `~` suffix is the OpenAlex 2-year mean citedness, which is a **proxy, not the impact "
            "factor**. `Q`/`D1` come from the Scimago (Scopus) file — D1 = top decile by SJR within a "
            "subject category. `EISZ` marks a publisher-level agreement in the local roster that still "
            "needs confirming at eisz.mtak.hu; the arrow in the APC column is the discount that would "
            "imply, not a quoted price. `Sub→acc` is the median of PubMed-deposited received/accepted "
            "dates. Acceptance rates are only shown where a curated, sourced value exists._"]
    return "\n".join(out)


def novelty_markdown(n: dict) -> str:
    out = [f"## Novelty & impact scan — {n.get('title', '')[:90]}", ""]
    if n.get("works_per_year"):
        pts = ", ".join(f"{y}: {c}" for y, c in n["works_per_year"].items())
        out += [f"**Topic volume** — {pts}"]
        if n.get("topic_saturation"):
            out += [f"**Saturation** — {n['topic_saturation']} (×{n.get('topic_growth')} over the window)"]
    if n.get("reference_median_year"):
        out += ["", f"**Your reference list** — median year {n['reference_median_year']}, "
                    f"{n.get('references_last_5y_pct', '?')}% from the last 5 years"]
    if n.get("nearest_works"):
        out += ["", "**Closest published work — read these before choosing a journal**", "",
                "| Year | Title | Journal | Cited | DOI |", "|---|---|---|---|---|"]
        for w in n["nearest_works"]:
            out.append(f"| {w['year']} | {(w['title'] or '')[:70]} | {(w['journal'] or '')[:28]} "
                       f"| {w['cited_by']} | {w['doi']} |")
    out += ["", "_Volume is from OpenAlex title+abstract search on the extracted terms; "
                "it measures how crowded the topic is, not how good the manuscript is._"]
    return "\n".join(out)


def calls_markdown(probes: list[dict]) -> str:
    out = ["## Open calls & special issues — probes to run", "",
           "No API lists these. Run each search, fetch each URL, and record what you find.", ""]
    for pr in probes:
        out.append(f"### {pr['journal']}")
        for q in pr["web_search"]:
            out.append(f"- search: `{q}`")
        for u in pr["fetch"]:
            out.append(f"- fetch: {u}")
        out += [f"- judge: {pr['judge']}", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- CLI

def cmd_sync(a) -> None:
    try:
        print("scimago:", refdata.sync_scimago(a.year, force=a.force), file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — network/format problems both need the same advice
        print(f"scimago sync failed: {e}\n"
              f"  Download by hand: https://www.scimagojr.com/journalrank.php  (Download data)\n"
              f"  Save it as {refdata.DATA}/scimago-{a.year}.csv", file=sys.stderr)
    print(refdata.status())


def cmd_discover(a) -> None:
    _emit(a, discover(read_manuscript(a.text), limit=a.limit, years=a.years))


def cmd_profile(a) -> None:
    _emit(a, [profile(j, deep=not a.fast) for j in a.journal])


def cmd_novelty(a) -> None:
    n = novelty(read_manuscript(a.text), years=a.years)
    _write(a, json.dumps(n, indent=2, ensure_ascii=False) if a.format == "json" else novelty_markdown(n))


def cmd_calls(a) -> None:
    js = ([profile(j, deep=False) for j in a.journal] if a.journal
          else json.loads(Path(a.shortlist).read_text()))
    probes = call_probes(js)
    _write(a, json.dumps(probes, indent=2, ensure_ascii=False) if a.format == "json"
           else calls_markdown(probes))


def cmd_match(a) -> None:
    text = read_manuscript(a.text)
    if a.refs:
        text += "\n" + Path(a.refs).read_text(errors="replace")   # composer export, .bib, plain DOIs
    cands = discover(text, limit=a.limit, years=a.years)
    cited, n_dois = journals_cited(text)
    bysrc = {c["source_id"]: c for c in cands}
    for c in cited:
        if c["source_id"] in bysrc:
            bysrc[c["source_id"]]["cited_by_manuscript"] = c["cited_by_manuscript"]
        elif a.include_cited and c["cited_by_manuscript"] >= 2:
            bysrc[c["source_id"]] = {**c, "topic_hits": 0}

    full = []
    for c in bysrc.values():
        try:
            pr = profile(c["source_id"], deep=not a.fast)
        except ApiError as e:
            print(f"  ! {c['journal']}: {e}", file=sys.stderr)
            continue
        full.append({**pr, **{k: v for k, v in c.items() if k in ("topic_hits", "cited_by_manuscript")}})
        print(f"  · {pr.get('journal')}", file=sys.stderr)

    kept, dropped = apply_filters(full, a)
    rows = rank(kept, apc_budget=a.apc_budget)[:a.top]
    if n_dois:
        print(f"  ({n_dois} DOIs found in the input)", file=sys.stderr)
    if not refdata.load_scimago():
        print("  ! no Scimago file — Q/D1/Scopus columns will be blank. Run: jmatch.py sync",
              file=sys.stderr)
    _write(a, json.dumps(rows, indent=2, ensure_ascii=False) if a.format == "json"
           else as_markdown(rows, dropped))


def _emit(a, rows) -> None:
    _write(a, json.dumps(rows, indent=2, ensure_ascii=False) if a.format == "json" else as_markdown(rows))


def _write(a, text: str) -> None:
    if a.out:
        Path(a.out).write_text(text + "\n")
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jmatch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-f", "--format", choices=("md", "json"), default="md")
    ap.add_argument("-o", "--out")
    sub = ap.add_subparsers(dest="cmd", required=True)

    y = sub.add_parser("sync", help="download the Scimago/Scopus reference file; report data status")
    y.add_argument("--year", type=int, default=THIS_YEAR - 1)
    y.add_argument("--force", action="store_true")
    y.set_defaults(func=cmd_sync)

    d = sub.add_parser("discover", help="candidate journals from the manuscript text")
    d.add_argument("--text", required=True, help="text file, or - for stdin")
    d.add_argument("--limit", type=int, default=25)
    d.add_argument("--years", type=int, default=5)
    d.set_defaults(func=cmd_discover)

    pr = sub.add_parser("profile", help="full dossier for one or more journals")
    pr.add_argument("journal", nargs="+", help="name, ISSN, or OpenAlex S-id")
    pr.add_argument("--fast", action="store_true", help="skip turnaround/type/country sampling")
    pr.set_defaults(func=cmd_profile)

    n = sub.add_parser("novelty", help="topic saturation + nearest published work (scoop check)")
    n.add_argument("--text", required=True)
    n.add_argument("--years", type=int, default=8)
    n.set_defaults(func=cmd_novelty)

    c = sub.add_parser("calls", help="probes for open calls for papers / special issues")
    c.add_argument("journal", nargs="*", help="journal names; omit and pass --shortlist")
    c.add_argument("--shortlist", help="a JSON file written by `match -f json`")
    c.set_defaults(func=cmd_calls)

    m = sub.add_parser("match", help="manuscript -> filtered, ranked shortlist (the whole pipeline)")
    m.add_argument("--text", required=True)
    m.add_argument("--refs", help="extra references to fold in: composer export, .bib, or plain DOIs")
    m.add_argument("--limit", type=int, default=25, help="candidates to profile")
    m.add_argument("--top", type=int, default=12, help="rows to report")
    m.add_argument("--years", type=int, default=5)
    m.add_argument("--apc-budget", type=float, default=None, help="USD you can actually pay")
    m.add_argument("--include-cited", action="store_true",
                   help="also consider journals the manuscript cites but the topic search missed")
    m.add_argument("--require-scopus", action="store_true", help="hard filter: Scopus-indexed only")
    m.add_argument("--require-medline", action="store_true")
    m.add_argument("--min-quartile", choices=("Q1", "Q2", "Q3", "Q4"))
    m.add_argument("--d1-only", action="store_true", help="top decile by SJR in a subject category")
    m.add_argument("--eisz-only", action="store_true", help="publishers in the local EISZ roster only")
    m.add_argument("--exclude-flagged", action="store_true")
    m.add_argument("--fast", action="store_true")
    m.set_defaults(func=cmd_match)

    a = ap.parse_args(argv)
    try:
        a.func(a)
    except ApiError as e:
        print(f"jmatch: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
