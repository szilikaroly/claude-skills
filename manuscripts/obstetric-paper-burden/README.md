# Obstetric paper burden — micro-costing study

English-language original research manuscript on paper consumption in obstetric and
antenatal documentation, with a fully reproducible analysis pipeline.

```
manuscript.md              the manuscript (IMRaD, ~3,515 words body)
analysis/params.yaml       every parameter, with a status flag (firm / assumed / todo)
analysis/analysis.py       reads the workbook + params, writes tables/ and figures/
tables/results.json        every computed number, machine-readable
tables/table[1-4]*.md      manuscript tables
figures/fig[1-4]*.png      manuscript figures, 300 dpi
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
| Paper per year, current practice | 1.24 t · 718k HUF net | 48.1 t · 27.8 M HUF net |
| Paper per care episode | 359 HUF net (456 gross) | same |
| Steady-state archive | 148 m² · 231.5 M HUF | 5,751 m² · **8.97 bn HUF** |
| Property immobilised per episode | 115,772 HUF | same |
| Duplex printing (no investment) | −45.5% sheets · releases 105.5 M HUF | releases **4.09 bn HUF** |
| Electronic report delivery | −78.2% sheets · releases 174.8 M HUF | releases **6.77 bn HUF** |

## Still open

1. **Sampling frame** (§2.1) — were the 100 episodes consecutive, and over what period?
   Exclusions? This is the largest remaining Methods gap.
2. **Report length** — report pages were derived at the institutional 4:1 convention, not
   counted. Independent measurement would firm up the 69% figure.
3. **Costs not yet monetised** — printing per page, archivist hourly cost, handling minutes
   per episode, retention-appraisal minutes per dossier. All four would raise the cost of
   current practice, so present figures are a floor.
4. **Two citations to add** — the Hungarian decree governing 30/50/70-year retention, and
   the HTA guideline specifying the 3.7% discount rate.
5. **Archival constants** — shelving density and floor efficiency are the two largest
   sources of uncertainty and can be measured directly in the institution's archive.

Author-input gaps are marked `⟦…⟧` in the manuscript; `grep -c '⟦' manuscript.md` counts them.
