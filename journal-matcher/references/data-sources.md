# Sources: what each one actually gives, and where it fails

Every field in the output traces to one of these. When a user asks "where does that
number come from", the answer must be here.

## OpenAlex — `api.openalex.org`

Free, no key, generous rate limits (much better inside the polite pool: set
`JMATCH_EMAIL`). It is the backbone of this skill.

| Used for | Endpoint |
|---|---|
| candidate discovery | `works?filter=title_and_abstract.search:…&group_by=primary_location.source.id` |
| journal metrics | `sources/S…` → `summary_stats`, `apc_usd`, `counts_by_year`, `is_oa`, `is_in_doaj` |
| article types published | `works?filter=…&group_by=type` |
| length proxies | `works?…&select=biblio,referenced_works_count` |
| author provenance | `works?…&group_by=authorships.countries` |
| novelty / scoop check | `works?…&sort=relevance_score:desc` |

**Limits.** `2yr_mean_citedness` is *not* the Journal Impact Factor — different
corpus, different window handling, different document-type rules. Never present it
as one. `apc_usd` is a list price and is frequently stale or absent; it does not
know about waivers, membership discounts or national deals. `biblio` page numbers
are missing or nonsensical for many born-digital journals, so `median_pages` is
absent more often than not. Abstracts are inverted-index encoded, so full-text
similarity is not available — the topic match is term-based, which is why the
`nearest_works` list must be read by a human and not trusted as a duplicate detector.

## Scimago / Scopus — `scimagojr.com` (downloaded once per year)

The single file behind the `Scopus`, `Q` and `D1` columns. Semicolon-delimited,
~30k rows. Fetch with `jmatch.py sync`; if the download fails, get it by hand from
the "Download data" link on scimagojr.com and save it as `data/scimago-<year>.csv`.

**Scopus indexation** is operationalised as *presence in the Scimago file for that
year*, because Scimago is built from the Scopus corpus. This is a good approximation
and not identical to the Scopus Source List: a journal newly accepted into Scopus
appears in the source list before it has an SJR, so a very new journal can be
Scopus-indexed and still absent here. When the answer matters, check Elsevier's
source list directly.

**D1 is computed, not read.** The file carries a per-category quartile but no decile.
This skill ranks each subject category by SJR and marks the top 10% as D1; a journal
is D1 if it is top-decile in *any* of its categories. That is the usual Hungarian and
Spanish convention — but institutions differ on whether "any category" or "the best
category" counts, so state the basis when reporting it.

Quartiles are **per category**. A journal is Q1 in cardiology and Q3 in general
medicine at the same time. The table shows the best quartile; the detail block shows
the categories.

## JCR — user-supplied export (`data/jcr.csv` or `$JMATCH_JCR_CSV`)

The only way to get a real Impact Factor. Licensed, so nothing ships with it. Any
CSV with an ISSN column and a JIF-ish column works. When present, the real JIF is
used for scoring and printed bare; when absent, the proxy is printed with `~`.

## DOAJ — `doaj.org/api`

The only free source for **APC amount and currency, licence, review process and
declared publication timeline** for open-access journals. Covers only DOAJ-listed
journals, so a subscription or hybrid journal returns nothing — that absence is not
a negative signal on its own, but OA-and-not-in-DOAJ is.

`review_process_weeks` is **self-declared by the publisher**, which is exactly why a
very low value is treated as a red flag rather than a feature.

## NLM Catalog + PubMed — `eutils.ncbi.nlm.nih.gov`

**Indexing.** `esearch db=nlmcatalog` then `esummary` gives `currentindexingstatus`.
Note the distinction the output preserves: *currently indexed for MEDLINE* is not the
same as *appears in PubMed*. A great many journals deposit in PMC and appear in
PubMed searches while never having been MEDLINE-indexed; users routinely conflate
these, and CV assessment does not.

**Turnaround.** `efetch` returns `PubMedPubDate` history entries with `PubStatus`
`received` / `accepted` / `entrez`. The median of `accepted − received` across a
recent sample is a *measured* review time — the only such number available for free,
and far more trustworthy than any journal's own claim.

Its limits: not all publishers deposit history dates, and those that do are
self-selecting (often the faster OA ones). Under ~10 usable articles the median is
noise — the output carries `turnaround_n` so this can be checked. Rate limits are
3 requests/second without an API key; `JMATCH_EMAIL` and the on-disk cache keep the
skill inside that comfortably.

## EISZ — `data/eisz.csv` (curated, unverified seed)

Publisher-level regex rules only. EISZ agreements are renegotiated annually, often
cover part of a publisher's list, and are usually capped by an article quota that
can be exhausted mid-year. A match means **"check the current title list at
eisz.mtak.hu"** and nothing stronger. Verify a row, then set `status=verified` and
fill `as_of`.

## Acceptance and desk-reject rates — `data/acceptance_rates.csv` (curated, empty)

No free API publishes these. Ships empty by design; each row must cite a primary
source. In order of reliability: the journal's own "Journal metrics"/"About" page;
the publisher's annual transparency or peer-review report (BMJ, PLOS, Wiley,
Springer Nature and Frontiers publish these); an editorial in the journal itself.
Never enter a figure from an aggregator that does not name its source — that
ecosystem is full of invented numbers, and an invented acceptance rate is worse
than a blank cell.

## Calls for papers and special issues — no source

Genuinely not available programmatically. `jmatch.py calls` emits per-journal search
queries and publisher URLs for Claude to run with WebSearch/WebFetch. Anything
reported here must come from a page that was actually fetched, with the deadline and
guest editors named.

## Caching

Every HTTP response is cached under `~/.cache/journal-matcher` for 7 days
(`JMATCH_CACHE_TTL` to change, `JMATCH_CACHE` to relocate). Re-running a shortlist
while discussing it costs nothing. Delete the directory to force a refresh.
