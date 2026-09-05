# Obstetric paper burden — micro-costing study

English-language original research manuscript on paper consumption in obstetric and
antenatal documentation, with a fully reproducible analysis pipeline.

```
BMJ-HCI-submission.docx    the submission file, BMJ-formatted (upload this)
BMJ-HCI-supplementary.docx supplementary methods + tables S1-S2 (export to PDF before upload)
manuscript.md              markdown source of the submission file (~4,060 words body)
supplementary.md           markdown source of the supplementary file
build_docx.js              markdown -> BMJ-formatted .docx
analysis/params.yaml       every parameter, with a status flag (firm / assumed / todo)
analysis/analysis.py       reads the workbook + params, writes tables/ and figures/
tables/results.json        every computed number, machine-readable
tables/table[1-3]*.md      in-text tables
tables/tableS[12]*.md      supplementary tables
figures/fig[1-5]*.png      manuscript figures, 300 dpi
sync_tables.py             refresh the tables embedded in manuscript.md
renumber_refs.py           renumber references into first-citation order
```

## Reproducing every number

```bash
pip install openpyxl pyyaml matplotlib
python3 analysis/analysis.py --data /path/to/pagecounts.xlsx
```

Nothing is hard-coded in `analysis.py`. To change a unit price, a retention period,
a shelving assumption or a scenario definition, edit `params.yaml` and re-run; all
tables and figures regenerate. Parameters that are still `null` cause the dependent
outputs to be **suppressed rather than estimated** — the script reports which.

The page-count dataset is patient-derived and is deliberately **not** committed here.
Pass its path with `--data`.

## Headline results (n = 100 care episodes)

| | Departmental (2,000/y) | National (77,500/y) |
|---|---|---|
| Printed pages per episode | 131.3 (SD 10.5; 95% CI 129.2–133.4) | same |
| Physical sheets per episode | 124.3 | same |
| Diagnostic reports as share of pages | 61.7% | same |
| Referral → report amplification | exactly 4.0, all families, all episodes | same |
| Consumables per year, current practice | 2.30 M HUF net | 89.1 M HUF net |
| Consumables per care episode | 1,150 HUF net | same |
| — of which toner/device | 70.8% | 70.8% |
| Steady-state archive | 148 m² · 231.5 M HUF | 5,751 m² · **8.97 bn HUF** |
| Property immobilised per episode | 115,772 HUF | same |
| Duplex: capital released | 105.5 M HUF | **4.09 bn HUF** |
| Duplex: recurrent saved | 327k HUF/y (−14.2%) | 12.7 M HUF/y |
| Duplex: staff time saved | none | none |
| Electronic reports: capital released | 174.8 M HUF | **6.77 bn HUF** |
| Electronic reports: recurrent saved | 1.70 M HUF/y (−73.7%) | 65.7 M HUF/y |
| Electronic reports: staff time saved | 466 h/y | **10.5 FTE** |
| Handling time, current practice | 22.8 min/episode · 0.44 FTE | 17.1 FTE (11.4–34.3) |
| Archive as clinical space | 8 consulting rooms | 320 consulting rooms |

## Still open

1. **Sampling frame** (§2.1) — were the 100 episodes consecutive, and over what period?
   Exclusions? This is the largest remaining Methods gap.
2. **Report length** — report pages were derived at the institutional 4:1 convention, not
   counted. Independent measurement would firm up the 69% figure.
3. **Costs not yet monetised** — archivist hourly cost, handling minutes per episode,
   retention-appraisal minutes per dossier, and the purchase price of the antenatal booklet.
   All would raise the cost of current practice, so present figures are a floor. The missing
   booklet price is why the paperless scenario shows zero consumables.
4. **Printing charge interpretation** — 7 HUF/page is modelled as toner and device cost with
   paper added on top (so a duplex sheet costs 14 HUF + 2.89 HUF). If it is an all-in
   cost-per-page, set `costs.print_cost_includes_paper: true` and re-run.
5. **Two citations to add** — the Hungarian decree governing 30/50/70-year retention, and
   the HTA guideline specifying the 3.7% discount rate.
6. **Handling time is assumed, not measured** — 30 s/document is a conservative
   lower bound supplied by the authors, reported in hours and FTE rather than money
   because no wage rate is available. A time-and-motion study would replace it.
7. **Archival constants** — shelving density and floor efficiency are the two largest
   sources of uncertainty and can be measured directly in the institution's archive.

Author-input gaps are marked `⟦…⟧` in the manuscript; `grep -c '⟦' manuscript.md` counts them.

## Rebuilding the submission files

```bash
python3 analysis/analysis.py --data <xlsx>   # numbers, tables, figures
python3 sync_tables.py                       # push tables into manuscript.md
python3 renumber_refs.py                     # BMJ citation order
node build_docx.js                           # -> BMJ-HCI-submission.docx
sed 's#manuscript.md#supplementary.md#; s#BMJ-HCI-submission#BMJ-HCI-supplementary#' \
  build_docx.js > /tmp/b.js && node /tmp/b.js
```

The builder needs the npm `docx` package. It applies BMJ's general formatting rules:
sentence-case title, BOLD CAPS level-1 and bold lower-case level-2 headings, tables as
Word tables placed where first cited, statements before references, figure legends last.

## BMJ submission checklist

- [x] Word format, not PDF
- [x] Order: abstract, main text, tables in place, statements, references
- [x] Heading hierarchy BOLD CAPS / bold lower case / plain
- [x] Tables as Word tables, each under two pages; the long cost breakdown moved to S1
- [x] Vancouver references, first three authors then *et al*, numbered, hyphenated ranges
- [x] MeSH keywords
- [x] References numbered in order of first citation, every listed reference cited
- [x] Figures as separate 300 dpi files, cited in order, legends at the end
- [x] Figure legends do not refer to colour
- [x] Main text cut to 4,061 words; derivations and the full parameter and cost tables
      moved to supplementary methods and tables S1-S2
- [ ] Journal-specific word ceiling and table/figure maxima — **could not be retrieved**
      (informatics.bmj.com is blocked by this environment's egress policy); confirm before
      submitting
- [ ] Author names, institutions and emails are entered in ScholarOne, not in the file
- [ ] Export the supplementary file to PDF before upload
