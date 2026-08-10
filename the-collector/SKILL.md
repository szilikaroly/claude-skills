---
name: the-collector
description: The Collector — harvest PubMed literature into a local corpus — search by topic or raw PubMed query, pull structured metadata (title, journal, abstract, DOI, PMC ID, publication types), and download the Open Access full texts as PDF or JATS XML through the legitimate PMC OA / Europe PMC routes. Use this whenever the user wants to find, collect, screen or download papers: "keress narratív review-kat a mikrobiomról", "gyűjtsd össze az elmúlt 5 év cikkeit X-ről", "töltsd le a teljes szövegeket", "mi van a szakirodalomban erről", building a reference base for a manuscript or grant, screening candidates for a review, checking what a journal has published recently, or any PubMed / NCBI / Entrez / MEDLINE / E-utilities request. Also use when the user has an existing harvest CSV and wants it screened, summarised or deduplicated. Hungarian triggers — irodalomkutatás, szakirodalom gyűjtés, PubMed keresés, cikkek letöltése, teljes szöveg, absztraktok, review-k keresése, hivatkozási alap.
---

# The Collector

`bin/collect` is a single CLI that does the whole retrieval side: query PubMed,
parse the records, and fetch Open Access full texts. Run it, then do the
judgement work — screening, summarising, thematic grouping — yourself on the
CSV it produces. The script deliberately contains no LLM calls; separating
mechanical retrieval from judgement is what makes the harvest reproducible and
the screening auditable.

## Running a harvest

```bash
~/.claude/skills/the-collector/bin/collect --outdir ~/Documents/PubMed_Downloads --xml-fallback
```

Credentials resolve in this order: `--api-key`/`--email` → `NCBI_API_KEY` /
`NCBI_EMAIL` environment variables → `~/.config/ncbi/env`. That file is already
set up (mode 600), so normally you pass nothing. With a key the script runs at
8 requests/sec; without one it drops to 2.5 and says so.

Useful flags:

| Flag | Effect |
|---|---|
| `--query '<PubMed syntax>'` | Raw query; bypasses the built-in topics entirely |
| `--query-name <slug>` | Folder/label for a `--query` run (default `egyedi`) |
| `--topic <name>` | Restrict to one built-in topic; repeatable |
| `--retmax N` | Hits per topic (default 50, hard ceiling 9999) |
| `--years N` | Last N years only |
| `--strict` | Require the literal phrase "narrative review" in title/abstract |
| `--no-pdf` | Metadata only — fast, use this for a first look |
| `--xml-fallback` | Save JATS full text when no PDF exists |

Built-in topics: `belgyogyaszat`, `mikrobiom`, `szuleszet_nogyogyaszat`,
`AI_in_medicine`, `obezitologia`, `diabetologia`, `egeszseg_gazdasag`.
They are defined in the `TOPICS` dict at the top of `bin/collect` — edit that dict
to add a standing topic, or just use `--query` for a one-off.

Reruns are cheap: an already-downloaded PDF is skipped, so widening `--retmax`
on a second pass only fetches what's new.

## Choosing the query

Default runs combine the topic with a narrative-review filter and exclude
`systematic review[pt]`, `meta-analysis[pt]`, `guideline[pt]`, trials, and
titles containing those phrases.

Two things worth knowing, because they change what the user gets:

- **PubMed has no "narrative review" publication type.** Without `--strict` the
  filter falls back to `review[pt]`, which is broad and pulls in plenty of
  non-narrative reviews. With `--strict` only papers that literally say
  "narrative review" survive — precise, but it misses narrative reviews that
  never use the phrase. Start with `--strict --no-pdf` to see how thin the yield
  is, then decide with the user whether to loosen it.
- **The `NOT` clause is applied to the whole record.** A genuine narrative review
  that merely discusses meta-analyses in its abstract can be excluded. If a
  known paper is missing, this is usually why.

For anything beyond the built-in topics, write real PubMed syntax — MeSH terms
(`"microbiota"[mh]`), field tags (`[tiab]`, `[ti]`, `[dp]`), and Booleans all
work, since the string is passed to `esearch` untouched.

## Reading the results

Everything lands in `<outdir>/osszesitett_lista.csv`, with PDFs and XML under
`<outdir>/<topic>/<pmid>.{pdf,xml}`.

Columns: `tema, pmid, cim, folyoirat, publikacio_datuma, doi, pmc_id,
pubmed_url, absztrakt, statusz, licenc, fajl, publikacio_tipusok`.

Do not cat the CSV — the abstracts alone will flood the context. Pull only the
columns you need:

```bash
python3 -c "
import pandas as pd, sys
df = pd.read_csv(sys.argv[1])
print(df[['pmid','cim','folyoirat','publikacio_datuma','statusz']].to_string(index=False))
" ~/Documents/PubMed_Downloads/osszesitett_lista.csv
```

Read abstracts in batches when screening, and only for the rows still in play.
If the corpus is large enough that even the abstracts are too much, the
`memo-index` skill was built for exactly this.

`statusz` tells you what happened per record:

- `Letöltve (PDF)` / `Letöltve (XML)` — full text on disk, path in `fajl`
- `Nem Open Access` — abstract only; the paywalled PDF is not obtainable here
- `Nincs PMC azonosító` — never deposited in PMC
- `PDF nem érhető el` — OA, but neither route served a PDF (try `--xml-fallback`)

## Screening after the harvest

The retrieval is recall-oriented on purpose — it over-collects and leaves the
precision to you. A useful pass over the CSV:

1. Drop anything whose `publikacio_tipusok` reveals it as a systematic review,
   meta-analysis or trial that slipped through the filter.
2. Read titles and abstracts, and judge whether each is really a narrative
   review (or whatever the user actually wanted). Say which ones you dropped
   and why — a screening decision the user can't inspect is worth little.
3. Group the survivors thematically and flag overlaps with work the user
   already has. `science-monitor` knows their manuscripts in progress.
4. Note the `licenc` column before reusing any figure or passage: `CC BY` is
   permissive, `CC BY-NC-ND` forbids derivatives.

## PRISMA log and flow diagram — `bin/prisma`

Every `collect` run now appends a machine-readable line per query to
`<outdir>/kereses_naplo.jsonl`: the database, the search date, the exact query
string, PubMed's true match count (`count_total`) and how many records were
actually pulled (`retrieved`). That log *is* the reproducible search strategy a
reviewer asks for — recorded at search time, not reconstructed after.

`bin/prisma` turns that log plus your screening decisions into an auditable
PRISMA 2020 trail, from identification to the included set. It is deliberately
LLM-free like `collect`: you make the include/exclude calls, it records them and
computes the flow deterministically. State lives in `<outdir>/prisma/<project>.json`.

```bash
P=~/.claude/skills/the-collector/bin/prisma
"$P" --project endo-diet ingest                 # search log + CSV -> state
"$P" --project endo-diet dedup --auto           # duplicates by DOI, then title
"$P" --project endo-diet add-source --source Embase --count 210 --date 2026-08-10 \
     --query "endometriosis AND microbiome"     # a database collect didn't run
"$P" --project endo-diet template               # -> a decisions CSV of undecided records
#   fill decision (include/exclude), reason, phase (screen/eligibility), then:
"$P" --project endo-diet screen --from-csv .../endo-diet-szures.csv
"$P" --project endo-diet status                 # the flow, as numbers
"$P" --project endo-diet export --format all    # Methods block, PRISMA-S table, diagram, RIS/CSV
```

Single decisions work without a CSV: `screen --include 111 222`, or
`screen --exclude 555 --reason "off-topic" --phase screen`.

**Import (before) / export (after).** `import <file>` merges a prior
`<project>.json` (union of searches, records, and decisions — a recorded
include/exclude always wins over an undecided one) or applies a decisions CSV, so
you can resume or hand screening between people. `export` writes into
`<outdir>/prisma/export/<project>/`:

- `prisma-flow.md` — the flow as a Methods-ready paragraph + bullet counts;
- `search-strategy.md` — the PRISMA-S appendix: one row per database with
  interface, date, filters and hits, then every full query string;
- `prisma.flowchart.json` — a `figure-forge` flowchart spec; render the actual
  diagram with `ff.py flowchart --spec … --formats svg,png,tiff` (the command is
  printed for you);
- `included.ris` / `included.csv` — the included set for a reference manager.

**One honesty rule it enforces.** Identification counts come from PubMed's total
`count_total`, but screening runs on the `retmax`-capped set actually retrieved.
When those differ, `status` and the flow text print a prominent warning that the
search was not exhaustive — raise `--retmax` if you need a complete systematic
search rather than a recall-oriented sample.

## Why full texts come from where they do

Scraping `ncbi.nlm.nih.gov/pmc/articles/PMC…/pdf/` is what most example code
does, and NCBI blocks it — their policy forbids bulk download from the web
interface. So the script asks the **PMC OA Web Service** whether a paper is
actually in the Open Access subset (and under which licence), then fetches the
PDF from the OA link or from Europe PMC's render endpoint, and verifies the
`%PDF-` header before keeping the file. Non-OA papers stay metadata-only, which
is the honest outcome — don't add a scraping fallback to work around it.

Rate limits are respected by a token-bucket limiter. Leave it alone; NCBI blocks
IPs that exceed the published limits.

## Dependencies

`biopython`, `pandas`, `requests`, on the Anaconda python3 the shebang points
at. The script exits with a clear message naming the missing package. `pandas`
is optional — without it CSV writing falls back to the standard library.
