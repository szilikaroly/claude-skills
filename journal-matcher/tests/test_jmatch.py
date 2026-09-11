#!/usr/bin/env python3
"""Offline test suite for journal-matcher.

    python3 tests/test_jmatch.py [-v]

Every network call is intercepted and answered from tests/fixtures.py, which
holds payloads written to the documented shape of each API. Nothing here touches
the network, so it runs in CI and in a sandbox.

What this proves: parsing, field extraction, the integrity screen, scoring,
hard filters, rendering, and error handling all behave on realistic input.
What it cannot prove: that the live APIs still return that shape. Re-run
`jmatch.py profile "The Lancet"` against the network after any API change.
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
import tempfile
import traceback
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE))

import fixtures as F  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


refdata = _load("refdata", ROOT / "scripts" / "refdata.py")
J = _load("jmatch", ROOT / "scripts" / "jmatch.py")


# ---------------------------------------------------------------- fake network

class Unrouted(Exception):
    pass


CALLS: list[str] = []


def fake_json(url, *, ttl=None, retries=3):
    CALLS.append(url)
    q = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(q.query)
    filt = params.get("filter", [""])[0]
    group = params.get("group_by", [""])[0]

    if "doaj.org" in q.netloc:
        if "1475-2840" in urllib.parse.unquote(q.path):
            return F.DOAJ_HIT
        return F.DOAJ_MISS

    if "openalex" in q.netloc:
        if q.path.startswith("/sources/"):
            key = q.path.rsplit("/", 1)[-1]
            if key in ("S49861241", "issn:0140-6736", "issn:1474-547X"):
                return F.SOURCE_LANCET
            if key in ("S99999999", "issn:2999-0001"):
                return F.SOURCE_FARM
            raise J.ApiError(f"404 for {url}")
        if q.path == "/sources":
            name = params.get("search", [""])[0].lower()
            if "lancet" in name:
                return {"results": [F.SOURCE_LANCET]}
            if "global journal" in name:
                return {"results": [F.SOURCE_FARM]}
            return {"results": []}
        if q.path == "/works":
            if group == "primary_location.source.id":
                return F.GROUP_BY_SOURCE
            if group == "type":
                return F.GROUP_BY_TYPE
            if group == "authorships.countries":
                if getattr(fake_json, "no_countries_key", False):
                    return {"group_by": []}          # simulate the newer key being unavailable
                return (F.GROUP_BY_COUNTRY_NARROW if "S99999999" in filt else F.GROUP_BY_COUNTRY)
            if group == "institutions.country_code":
                return F.GROUP_BY_COUNTRY
            if group == "publication_year":
                return F.GROUP_BY_YEAR
            if filt.startswith("doi:"):
                return F.WORKS_BY_DOI
            if params.get("sort", [""])[0].startswith("relevance"):
                return F.NEAREST_WORKS
            return F.WORKS_SAMPLE
    raise Unrouted(url)


def fake_xml(url, *, ttl=None):
    CALLS.append(url)
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    term = params.get("term", [""])[0]
    db = params.get("db", [""])[0]
    if "esearch" in url:
        if db == "pubmed":
            return ET.fromstring(F.ESEARCH_PMIDS)
        if "2999-0001" in term or "Global Journal" in term:
            return ET.fromstring(F.ESEARCH_HIT.replace("101089123", "101777777"))
        if "0140-6736" in term or "1474-547X" in term or "Lancet" in term:
            return ET.fromstring(F.ESEARCH_HIT)
        return ET.fromstring(F.ESEARCH_EMPTY)
    if "esummary" in url:
        uid = params.get("id", [""])[0]
        return ET.fromstring(F.ESUMMARY_NOT_MEDLINE if uid == "101777777" else F.ESUMMARY_MEDLINE)
    if "efetch" in url:
        return ET.fromstring(F.EFETCH_FAST if getattr(fake_xml, "fast", False) else F.EFETCH_NORMAL)
    raise Unrouted(url)


J.get_json = fake_json
J.get_xml = fake_xml


# ---------------------------------------------------------------- scimago fixture

SCIMAGO = ["Rank;Sourceid;Title;Type;Issn;SJR;SJR Best Quartile;H index;Country;Publisher;Coverage;Categories"]
for i in range(1, 101):
    q = "Q1" if i <= 25 else "Q2" if i <= 50 else "Q3" if i <= 75 else "Q4"
    issn = "01406736, 1474547X" if i == 1 else f"1000{i:04d}"
    SCIMAGO.append(f"{i};3{i};Journal {i};journal;{issn};{str(round(40 / i, 3)).replace('.', ',')};"
                   f"{q};{160 - i};United Kingdom;Elsevier BV;1990-2024;Cardiology ({q})")


def install_scimago(tmp: Path):
    """Point refdata at a temp data dir holding a synthetic Scimago file plus the
    repo's real curated CSVs — otherwise the shipped EISZ roster silently vanishes
    and every EISZ assertion passes for the wrong reason."""
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "scimago-2024.csv").write_text("\n".join(SCIMAGO))
    for name in ("eisz.csv", "acceptance_rates.csv"):
        src = ROOT / "data" / name
        if src.exists() and not (tmp / name).exists():
            (tmp / name).write_text(src.read_text())
    refdata.DATA = tmp
    refdata._SCIMAGO = None


# ---------------------------------------------------------------- harness

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  ok   " if cond else "  FAIL ") + name + (f"\n         {detail}" if detail and not cond else ""))


def section(t):
    print(f"\n\033[1m{t}\033[0m")


# ---------------------------------------------------------------- tests

def t_text_parsing():
    section("manuscript parsing")
    txt = ("SGLT2 inhibitors and heart failure outcomes in type 2 diabetes\n"
           "Abstract\n"
           "Background: patients with diabetes are at risk. We evaluated empagliflozin.\n"
           "Introduction\nThis must be excluded.\n")
    title, abstract = J.split_title_abstract(txt)
    check("title = first non-empty line",
          title == "SGLT2 inhibitors and heart failure outcomes in type 2 diabetes", title)
    check("'Abstract' heading stripped", not abstract.lower().startswith("abstract"), abstract[:40])
    check("text after 'Introduction' excluded", "must be excluded" not in abstract, abstract)

    terms = J.query_terms(title + " " + abstract)
    check("stopwords dropped", not ({"patients", "background", "we", "the"} & set(terms)), str(terms))
    check("drug names kept", "empagliflozin" in terms, str(terms))

    hu = "Az SGLT2-gátlók hatása\nÖsszefoglaló\nA szívelégtelenség kockázata.\nBevezetés\nkihagyandó"
    _t, a = J.split_title_abstract(hu)
    check("Hungarian Összefoglaló/Bevezetés handled",
          "kihagyandó" not in a and not a.lower().startswith("összefoglal"), a)


def t_discover():
    section("candidate discovery")
    CALLS.clear()
    cands = J.discover("Heart failure and empagliflozin\nWe studied SGLT2 inhibitors in diabetes.")
    ids = [c["source_id"] for c in cands]
    check("S-id extracted from OpenAlex URL key", ids[:2] == ["S49861241", "S99999999"], str(ids))
    check("'unknown' group dropped", "unknown" not in ids, str(ids))
    check("count below per_source_min dropped", "S1111" not in ids, str(ids))
    check("topic_hits carried", cands[0]["topic_hits"] == 41, str(cands[0]))
    check("search restricted to recent years",
          f"from_publication_date:{J.THIS_YEAR - 5}" in CALLS[0], CALLS[0])


def t_cited():
    section("reference-list overlap")
    text = "see 10.1016/S0140-6736(21)01234-5 and 10.1056/NEJMoa2107038. Also 10.1161/CIRC.119.044321."
    cited, n = J.journals_cited(text)
    check("DOIs extracted", n == 3, f"n={n}")
    top = cited[0]
    check("citations aggregated per journal", top["source_id"] == "S49861241" and top["cited_by_manuscript"] == 2,
          str(top))
    check("work with a null source skipped", all(c["source_id"] for c in cited), str(cited))


def t_profile_good(tmp):
    section("profile — indexed, subscription journal")
    install_scimago(tmp)
    p = J.profile("The Lancet")
    check("OpenAlex fields", p["journal"] == "The Lancet" and p["publisher"] == "Elsevier BV", str(p)[:120])
    check("impact proxy read from summary_stats", p["impact_proxy_2y"] == 31.7, str(p.get("impact_proxy_2y")))
    check("recent works/year: last 3 complete years, chronological, current year excluded",
          p["works_per_year_recent"] == [1980, 2050, 2100], str(p["works_per_year_recent"]))
    check("Scimago: Scopus + quartile + D1", p["scopus_indexed"] and p["quartile"] == "Q1" and p["d1"],
          f"q={p.get('quartile')} d1={p.get('d1')}")
    check("MEDLINE indexing status Y -> True", p["medline_indexed"] is True, str(p.get("nlm_indexing_status")))
    check("NLM title read from nested Item, not the list container",
          p.get("nlm_title") == "The Lancet", repr(p.get("nlm_title")))
    check("not in DOAJ (subscription)", p["in_doaj"] is False)
    check("EISZ matched on publisher 'Elsevier BV'", p.get("eisz_status") == "unverified", str(p.get("eisz_status")))
    check("turnaround median from PubMed history", p["days_submission_to_acceptance_median"] == 99,
          str(p.get("days_submission_to_acceptance_median")))
    check("articles without history dates excluded from n", p["turnaround_n"] == 3, str(p.get("turnaround_n")))
    check("article-type mix as percentages",
          p["article_type_mix"] == {"article": 72, "review": 18, "editorial": 10}, str(p.get("article_type_mix")))
    check("median reference count", p["median_references"] == 48, str(p.get("median_references")))
    check("unparseable page numbers ignored", p.get("median_pages") == 11, str(p.get("median_pages")))
    check("author countries as shares", p["author_countries"]["Germany"] == 33, str(p.get("author_countries")))
    check("no flags on a clean journal", p["flags"] == [], str(p["flags"]))


def t_profile_bad(tmp):
    section("profile — special-issue farm")
    install_scimago(tmp)
    fake_xml.fast = True
    try:
        p = J.profile("Global Journal of Advanced Clinical Insights")
    finally:
        fake_xml.fast = False
    f = " | ".join(p["flags"])
    check("not MEDLINE-indexed flagged", "not currently MEDLINE-indexed" in f, f)
    check("OA but not in DOAJ flagged", "not listed in DOAJ" in f, f)
    check("not in Scopus flagged", "Scopus source list" in f, f)
    check("measured 5-day turnaround flagged", "implausibly fast" in f, f)
    check("APC without indexing flagged", "neither MEDLINE-indexed nor in DOAJ" in f, f)
    check("output explosion flagged", "output exploded" in f, f)
    check("narrow authorship flagged", "narrow authorship base" in f, f)
    check("not in DOAJ at all", p["in_doaj"] is False, str(p.get("in_doaj")))

    # The two DOAJ-declared flags need a journal that IS in DOAJ, so test them directly.
    declared = J.red_flags({"works_count": 100, "issn_l": "1475-2840", "is_oa": True,
                            "in_doaj": True, "in_pubmed": True, "medline_indexed": True,
                            "doaj_weeks_to_publication": 2, "doaj_plagiarism_screening": False})
    check("DOAJ-declared 2 weeks flagged", any("too fast for real peer review" in x for x in declared),
          str(declared))
    check("DOAJ 'no plagiarism screening' flagged",
          any("plagiarism screening" in x for x in declared), str(declared))


def t_scoring(tmp):
    section("scoring & filters")
    install_scimago(tmp)
    good = dict(source_id="S1", journal="Good", publisher="Elsevier BV", topic_hits=40,
                cited_by_manuscript=5, impact_proxy_2y=20.0, medline_indexed=True, in_pubmed=True,
                in_doaj=False, is_oa=False, apc_usd=None, days_submission_to_acceptance_median=90,
                quartile="Q1", d1=True, scopus_indexed=True, scimago_loaded=True, flags=[])
    weak = dict(good, journal="Weak", topic_hits=5, impact_proxy_2y=1.0, quartile="Q4", d1=False,
                acceptance_rate=0.75)
    rows = J.rank([good, weak])
    check("better journal ranks first", rows[0]["journal"] == "Good", str([r["journal"] for r in rows]))
    check("all components present",
          set(rows[0]["score_parts"]) == {"scope", "impact", "standing", "indexing", "speed",
                                          "access", "acceptance"}, str(rows[0]["score_parts"]))
    check("D1 gets full standing", rows[0]["score_parts"]["standing"] == 12.0, str(rows[0]["score_parts"]))
    check("75% acceptance scores below neutral",
          rows[1]["score_parts"]["acceptance"] < J.WEIGHTS["acceptance"] * 0.5,
          str(rows[1]["score_parts"]["acceptance"]))

    fast = dict(good, journal="Fast", days_submission_to_acceptance_median=9)
    slow = dict(good, journal="Slow", days_submission_to_acceptance_median=60)
    r = {x["journal"]: x for x in J.rank([fast, slow])}
    check("9-day turnaround scores below a 60-day one",
          r["Fast"]["score_parts"]["speed"] < r["Slow"]["score_parts"]["speed"],
          f"{r['Fast']['score_parts']['speed']} vs {r['Slow']['score_parts']['speed']}")

    unk = dict(good, journal="Unknown", days_submission_to_acceptance_median=None)
    check("unknown turnaround is neutral, not zero",
          J.rank([unk])[0]["score_parts"]["speed"] == J.WEIGHTS["speed"] * 0.5)

    # A missing apc_usd means unknown, not free. Regression: it used to score full marks,
    # which silently inflated every journal OpenAlex has no price for.
    apc_unknown = {k: v for k, v in good.items() if k != "is_oa"}
    apc_unknown.update(journal="APC unknown", apc_usd=None)
    check("unknown APC is neutral, not full marks",
          J.rank([apc_unknown])[0]["score_parts"]["access"] == J.WEIGHTS["access"] * 0.5,
          str(J.rank([apc_unknown])[0]["score_parts"]["access"]))
    check("subscription journal with no APC scores full access marks",
          J.rank([dict(good, journal="Subscription", is_oa=False, apc_usd=None)])[0]
          ["score_parts"]["access"] == J.WEIGHTS["access"])
    check("an OA journal that charges scores below a free one, with no budget stated",
          J.rank([dict(good, journal="Charges", is_oa=True, apc_usd=2000)])[0]
          ["score_parts"]["access"] < J.WEIGHTS["access"])

    eisz = dict(good, journal="EISZ", is_oa=True, apc_usd=3000, publisher="Wiley")
    eisz.update(refdata.eisz_lookup("Wiley", "EISZ"))
    scored = J.rank([eisz], apc_budget=1000)[0]
    check("EISZ discounts APC to 25%", scored["apc_effective_usd"] == 750, str(scored.get("apc_effective_usd")))
    check("discounted APC fits the budget", scored["score_parts"]["access"] > 0,
          str(scored["score_parts"]["access"]))

    flagged = dict(good, journal="Flagged", flags=["a", "b", "c", "d"])
    check("4 flags cost 32 points", J.rank([flagged])[0]["score_penalty"] == 32)

    class A:
        require_scopus = True; require_medline = False; d1_only = False
        min_quartile = "Q2"; eisz_only = False; exclude_flagged = False
    cands = [good,
             dict(good, journal="NoScopus", scopus_indexed=False),
             dict(good, journal="Q3", quartile="Q3"),
             dict(good, journal="NoQ", quartile=None)]
    kept, dropped = J.apply_filters(cands, A)
    check("Scopus filter applied", "NoScopus" in [n for n, _ in dropped], str(dropped))
    check("quartile filter applied", "Q3" in [n for n, _ in dropped], str(dropped))
    check("unknown quartile excluded by a quartile floor", "NoQ" in [n for n, _ in dropped], str(dropped))
    check("dropped journals report a reason", all(w for _, w in dropped), str(dropped))
    check("only the qualifying journal kept", [c["journal"] for c in kept] == ["Good"], str(kept))

    A.d1_only, A.min_quartile = True, None
    kept2, _ = J.apply_filters([good, dict(good, journal="NotD1", d1=False)], A)
    check("D1 filter applied", [c["journal"] for c in kept2] == ["Good"], str(kept2))


def t_novelty():
    section("novelty scan")
    n = J.novelty("Empagliflozin in heart failure\nWe studied SGLT2 inhibitors. "
                  "Refs: 10.1056/NEJMoa2107038 10.1016/S0140-6736(21)01234-5 10.1161/CIRC.119.044321")
    check("year curve parsed and sorted", list(n["works_per_year"])[0] == 2020, str(n.get("works_per_year")))
    check("growth computed", n["topic_growth"] > 1, str(n.get("topic_growth")))
    check("saturation labelled", "crowded" in n["topic_saturation"], str(n.get("topic_saturation")))
    check("nearest works with DOI stripped of the URL prefix",
          n["nearest_works"][0]["doi"] == "10.1056/NEJMoa2107038", str(n["nearest_works"][0]))
    # fixture reference years: 2023, 2021, 2014, 2020 -> median 2020.5, two within 5y
    check("reference median year computed", n["reference_median_year"] == 2020, str(n.get("reference_median_year")))
    check("recent-reference share computed", n["references_last_5y_pct"] == 50,
          str(n.get("references_last_5y_pct")))


def t_render(tmp):
    section("rendering")
    install_scimago(tmp)
    rows = J.rank([dict(source_id="S1", journal="Good", publisher="Elsevier BV", topic_hits=40,
                        impact_proxy_2y=20.0, medline_indexed=True, in_pubmed=True, in_doaj=False,
                        is_oa=False, quartile="Q1", d1=True, scopus_indexed=True, scimago_loaded=True,
                        days_submission_to_acceptance_median=90, flags=["something odd"],
                        article_type_mix={"article": 80}, author_countries={"Germany": 40},
                        apc_usd=None, acceptance_rate=0.2)])
    md = J.as_markdown(rows, [("Rejected Journal", "not Scopus-indexed")])
    check("proxy marked with ~", "20.0~" in md, md[:200])
    check("proxy disclaimer present", "proxy, not the impact" in md)
    check("flags section rendered", "Integrity flags" in md and "something odd" in md)
    check("empirical profile section rendered", "actually publishes" in md and "article 80%" in md)
    check("exclusions rendered", "Rejected Journal — not Scopus-indexed" in md)
    check("acceptance rate as a percentage", "| 20 |" in md, [l for l in md.splitlines() if "Good" in l][:1])
    header_cols = md.splitlines()[0].count("|") - 1
    body_cols = md.splitlines()[2].count("|") - 1
    check("table row width matches the header", header_cols == body_cols, f"{header_cols} vs {body_cols}")

    with_jif = J.rank([dict(rows[0], jif=54.4)])
    check("real JIF printed bare, no ~", "| 54.4 |" in J.as_markdown(with_jif))

    check("novelty markdown renders", "Saturation" in J.novelty_markdown(
        {"title": "x", "works_per_year": {2024: 10}, "topic_saturation": "active", "topic_growth": 1.1}))
    probes = J.call_probes([{"journal": "The Lancet", "publisher": "Elsevier BV",
                             "homepage": "https://www.sciencedirect.com/journal/the-lancet"}])
    check("call probes derive the publisher URL",
          any("call-for-papers" in u for u in probes[0]["fetch"]), str(probes[0]["fetch"]))
    check("calls markdown renders", "special issue" in J.calls_markdown(probes))


def t_refdata(tmp):
    section("reference data")
    install_scimago(tmp)
    idx = refdata.load_scimago()
    check("both ISSN forms indexed from one row", len(idx) == 101, str(len(idx)))
    check("hyphenated ISSN matches unhyphenated file",
          refdata.scimago_lookup(["0140-6736"])["quartile"] == "Q1")
    check("alternate ISSN with an X check digit matches",
          refdata.scimago_lookup(["1474-547X"])["sjr"] == 40.0)
    check("D1 cut is the top decile of the category",
          refdata.scimago_lookup(["1000-0010"])["d1"] and not refdata.scimago_lookup(["1000-0011"])["d1"])
    check("unknown ISSN -> not Scopus-indexed",
          refdata.scimago_lookup(["9999-9999"]) == {"scimago_loaded": True, "scopus_indexed": False})
    check("comma decimal separator parsed", refdata.scimago_lookup(["1000-0002"])["sjr"] == 20.0)

    (tmp / "eisz.csv").write_text(
        "# a comment line that must not become the header\n"
        "publisher,publisher_pattern,model,status,as_of,url,note\n"
        "Wiley,\\bwiley\\b,read&publish,verified,2026-01,https://eisz.mtak.hu/,\n")
    check("comment lines skipped in curated CSVs",
          refdata.eisz_lookup("Wiley", "")["eisz_status"] == "verified",
          str(refdata.eisz_lookup("Wiley", "")))
    check("no match reports plainly",
          refdata.eisz_lookup("MDPI AG", "")["eisz_status"].startswith("no agreement"))

    (tmp / "acceptance_rates.csv").write_text(
        "# sourced rows only\nissn,journal,acceptance_rate,desk_reject_rate,year,source\n"
        "0140-6736,The Lancet,0.05,0.75,2024,journal metrics page\n")
    r = refdata.rate_lookup(["0140-6736"])
    check("acceptance rate loaded", r["acceptance_rate"] == 0.05 and r["desk_reject_rate"] == 0.75, str(r))

    (tmp / "jcr.csv").write_text("Journal name;ISSN;eISSN;JIF\nThe Lancet;0140-6736;1474-547X;98.4\n")
    import os
    os.environ["JMATCH_JCR_CSV"] = str(tmp / "jcr.csv")
    check("JCR export parsed, semicolon-delimited",
          refdata.jcr_lookup(["0140-6736"]).get("jif") == 98.4, str(refdata.jcr_lookup(["0140-6736"])))
    del os.environ["JMATCH_JCR_CSV"]

    refdata.DATA = tmp / "empty"
    refdata._SCIMAGO = None
    check("missing scimago file degrades to a flag, not a crash",
          refdata.scimago_lookup(["0140-6736"]) == {"scimago_loaded": False})
    refdata._SCIMAGO = None


def t_errors():
    section("error handling")
    try:
        J.resolve_source("A Journal That Does Not Exist")
        check("unknown journal raises ApiError", False, "no exception")
    except J.ApiError:
        check("unknown journal raises ApiError", True)

    try:
        J.read_manuscript("paper.pdf")
        check("PDF input refused with extraction advice", False, "no exception")
    except SystemExit as e:
        check("PDF input refused with extraction advice", "pdftotext" in str(e), str(e))

    try:
        J.discover("the and or of in on for")
        check("empty query rejected", False, "no exception")
    except SystemExit:
        check("empty query rejected", True)

    check("_iqr survives a tiny sample", J._iqr([5.0, 9.0]) == (5.0, 9.0))

    fake_json.no_countries_key = True
    try:
        out = J.author_countries("S49861241")
    finally:
        fake_json.no_countries_key = False
    check("falls back to institutions.country_code when authorships.countries is empty",
          out.get("author_countries", {}).get("Germany") == 33, str(out))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "data"
        for t in (t_text_parsing, t_discover, t_cited,
                  lambda: t_profile_good(tmp), lambda: t_profile_bad(tmp),
                  lambda: t_scoring(tmp), t_novelty, lambda: t_render(tmp),
                  lambda: t_refdata(tmp), t_errors):
            try:
                t()
            except Exception:  # noqa: BLE001 — a crashing test is a failing test
                FAIL.append((getattr(t, "__name__", "test"), traceback.format_exc()))
                print(f"  FAIL {getattr(t, '__name__', 'lambda')} raised\n{traceback.format_exc()}")

    print(f"\n\033[1m{len(PASS)} passed, {len(FAIL)} failed\033[0m")
    for name, detail in FAIL:
        print(f"  - {name}: {detail.splitlines()[-1] if detail else ''}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
