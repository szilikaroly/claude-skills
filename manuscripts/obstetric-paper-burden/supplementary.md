# Supplementary material

Paper consumption in obstetric and antenatal documentation: a micro-costing study with national extrapolation

---

# SUPPLEMENTARY METHODS

## Derivation of physical quantities

Each recorded page is one printed A4 side. Sheets are derived from pages by scenario: single-sided printing gives one sheet per page, double-sided gives the page count halved and rounded up, and the antenatal booklet is treated as double-sided in every scenario because it is purchased pre-printed. Mass follows from A4 area and grammage at 4.99 g per sheet. Shelf length follows from a filed-record density of 10,000 sheets per linear metre, which allows for folders and covers, and archive floor area from 5.5 linear metres of shelving per m² inclusive of an aisle allowance. The last two are conventions rather than measurements and are the two largest sources of uncertainty in the headline capital figure.

## Retention and the steady-state archive

Documents were assigned statutory retention periods by type: 30 years for outpatient antenatal documentation and 50 years for inpatient ward and delivery documentation. At steady state — constant annual volume, each cohort discarded on expiry — the archive holds, for each document type, its annual page volume multiplied by its retention period. This steady-state stock, not the annual flow, determines the floor area a health system must own, and it is what the reported property value prices.

## The four quantities

Printing is charged per printed page, paper per physical sheet, storage per archived sheet, and handling per document. These diverge under duplex printing, which halves sheets but leaves pages and documents unchanged, and under electronic result delivery, which reduces all four. Conflating them is the commonest way a paper-reduction business case reaches the wrong conclusion, and it is why duplex printing appears attractive on paper cost and is in fact an archive intervention.

## Handling time

One document is counted for each referral and each diagnostic report, irrespective of page count: a referral is one document, a paired report is one document per referral, and ultrasound reports are counted at one page per report. Ward and delivery documentation are excluded, being written during care rather than printed on demand. Time is reported in hours and full-time equivalents and is deliberately not monetised, for the reasons given in the main text.

## Environmental conversion

Greenhouse-gas emissions use a cradle-to-gate factor of 950 kg CO2e per tonne of paper, the production-weighted mean of a systematic review and meta-analysis of 45 paper-making life-cycle assessments, with sensitivity across the grade-level spread observed in 252 mills. Water uses the published consumptive (green plus blue) range of 300 to 2600 m³ per tonne for printing and writing paper, accounting for current recovery rates; the source publishes no central value, so its midpoint is used as the point estimate and the full range is carried through every reported figure. Both exclude printing energy, transport, decades of archive conditioning and end-of-life destruction; grey water is excluded. Neither constitutes a life-cycle assessment.

## Statistical and computational methods

Confidence intervals for the mean are from Student's *t* and were corroborated by a 10,000-replicate non-parametric bootstrap with a fixed seed. Scenario outputs are deterministic functions of the observed sample mean and the parameters in supplementary table S1, so uncertainty is expressed as one-way sensitivity analysis rather than probabilistic simulation. Analyses were performed in Python 3. The analysis script and the parameter file are provided; substituting a local unit price and re-running regenerates every table and figure in this paper.

---

# SUPPLEMENTARY TABLE S1

Model parameters, base-case values, sensitivity ranges and sources.

| Parameter | Base case | Sensitivity range | Source |
|---|---|---|---|
| Paper, HUF per 500-sheet ream (net) | 1,445 | 1,430 to 1,460 | KEF centralised public procurement framework |
| Printing, HUF per printed page | 7 | 5 to 10 | Author-supplied; prevailing Hungarian cost-per-page charge |
| Antenatal booklet, HUF each | 70 | — | Author-supplied purchase price |
| Archive floor, HUF per m² | 1,560,000 | 1,200,000 to 1,900,000 | NAV property valuation for the archive site |
| VAT rate | 27% | — | Hungarian standard rate |
| Discount rate | 3.7% | — | National health technology assessment guidance |
| A4 sheet area, m² | 0.06237 | — | ISO 216 A4 |
| Grammage, g/m² | 80 | — | Specification of the procured paper |
| Sheets per linear metre of shelving | 10,000 | 8,000 to 12,000 | Convention; filed records including covers |
| Linear metres of shelving per m² floor | 5.5 | 4 to 7 | Convention; shelving with aisle allowance |
| Handling time, seconds per document | 30 | 20 to 60 | Author-supplied conservative lower bound |
| Annual hours per FTE | 1,720 | — | Hungarian full-time working year |
| Greenhouse gas, kg CO2e per tonne | 950 | 608 to 1,978 | Sun 2018, meta-analysis of 45 paper-making LCAs |
| Water, m³ per tonne | 1,450 | 300 to 2,600 | van Oel & Hoekstra 2012, midpoint of published range |
| Antenatal consulting room, m² | 18 | — | Typical room area |
| Care episodes per year, departmental | 2,000 | — | Departmental delivery volume |
| Care episodes per year, national | 77,500 | 70,000 to 85,000 | KSH, live births 2024 |

Retention periods were 30 years for outpatient antenatal documentation and 50 years for inpatient ward and delivery documentation. Every value above is held in the analysis parameter file; changing one and re-running regenerates all tables and figures.

---

# SUPPLEMENTARY TABLE S2

Full cost, staff-time, space and environmental breakdown by scenario, at departmental and national scale.

| Cost component | University department (2,000/y) | Hungary (77,500/y) |
|---|---|---|
| **Current practice** | | |
| Printing (toner, device), per year | 1.63 M | 63.08 M |
| Paper, per year | 672.04k | 26.04 M |
| Purchased antenatal booklets, per year | 140.00k | 5.42 M |
| Consumables, per year (net) | 2.44 M | 94.54 M |
| Consumables, per year (gross, incl. VAT) | 3.10 M | 120.07 M |
| Consumables, 10 y discounted (net) | 20.83 M | 807.21 M |
| Consumables, per care episode (net) | 1,220 | 1,220 |
| Archive property value at steady state | 231.54 M | 8.97 bn |
| Property value per care episode | 115,772 | 115,772 |
| Archive floor area (steady state) | 148 m² | 5,751 m² |
| — as antenatal consulting rooms | 8 | 320 |
| Documents printed and filed, per year | 91,280 | 3,537,100 |
| Handling time, hours per year | 761 | 29,476 |
| Handling time, FTE | 0.44 (0.29–0.88) | 17.1 (11.4–34.3) |
| Handling time, minutes per care episode | 22.8 | 22.8 |
| Greenhouse gas, t CO2e per year | 1.18 (0.75–2.45) | 45.7 (29.2–95.1) |
| Greenhouse gas, kg CO2e per care episode | 0.59 | 0.59 |
| Water, m³ per year | 1,798 (372–3,224) | 69,679 (14,416–124,942) |
| Water, litres per care episode | 899 | 899 |
| **Duplex printing** | | |
| Printing (toner, device), per year | 1.63 M | 63.08 M |
| Paper, per year | 345.36k | 13.38 M |
| Purchased antenatal booklets, per year | 140.00k | 5.42 M |
| Consumables, per year (net) | 2.11 M | 81.88 M |
| Consumables, per year (gross, incl. VAT) | 2.68 M | 103.99 M |
| Consumables, 10 y discounted (net) | 18.04 M | 699.13 M |
| Consumables, per care episode (net) | 1,057 | 1,057 |
| Archive property value at steady state | 126.00 M | 4.88 bn |
| Property value per care episode | 62,998 | 62,998 |
| Archive floor area (steady state) | 81 m² | 3,130 m² |
| — as antenatal consulting rooms | 4 | 174 |
| Documents printed and filed, per year | 91,280 | 3,537,100 |
| Handling time, hours per year | 761 | 29,476 |
| Handling time, FTE | 0.44 (0.29–0.88) | 17.1 (11.4–34.3) |
| Handling time, minutes per care episode | 22.8 | 22.8 |
| Greenhouse gas, t CO2e per year | 0.64 (0.41–1.34) | 24.9 (15.9–51.8) |
| Greenhouse gas, kg CO2e per care episode | 0.32 | 0.32 |
| Water, m³ per year | 980 (203–1,758) | 37,988 (7,860–68,116) |
| Water, litres per care episode | 490 | 490 |
| **Electronic result delivery** | | |
| Printing (toner, device), per year | 494.62k | 19.17 M |
| Paper, per year | 110.28k | 4.27 M |
| Purchased antenatal booklets, per year | 140.00k | 5.42 M |
| Consumables, per year (net) | 744.90k | 28.86 M |
| Consumables, per year (gross, incl. VAT) | 946.03k | 36.66 M |
| Consumables, 10 y discounted (net) | 6.36 M | 246.45 M |
| Consumables, per care episode (net) | 372 | 372 |
| Archive property value at steady state | 56.78 M | 2.20 bn |
| Property value per care episode | 28,392 | 28,392 |
| Archive floor area (steady state) | 36 m² | 1,410 m² |
| — as antenatal consulting rooms | 2 | 78 |
| Documents printed and filed, per year | 35,300 | 1,367,875 |
| Handling time, hours per year | 294 | 11,399 |
| Handling time, FTE | 0.17 (0.11–0.34) | 6.6 (4.4–13.3) |
| Handling time, minutes per care episode | 8.8 | 8.8 |
| Greenhouse gas, t CO2e per year | 0.26 (0.16–0.53) | 9.9 (6.4–20.7) |
| Greenhouse gas, kg CO2e per care episode | 0.13 | 0.13 |
| Water, m³ per year | 392 (81–703) | 15,184 (3,142–27,226) |
| Water, litres per care episode | 196 | 196 |
| **Paperless pathway** | | |
| Printing (toner, device), per year | 0 | 0 |
| Paper, per year | 0 | 0 |
| Purchased antenatal booklets, per year | 140.00k | 5.42 M |
| Consumables, per year (net) | 140.00k | 5.42 M |
| Consumables, per year (gross, incl. VAT) | 177.80k | 6.89 M |
| Consumables, 10 y discounted (net) | 1.20 M | 46.32 M |
| Consumables, per care episode (net) | 70 | 70 |
| Archive property value at steady state | 13.61 M | 527.56 M |
| Property value per care episode | 6,807 | 6,807 |
| Archive floor area (steady state) | 9 m² | 338 m² |
| — as antenatal consulting rooms | 0 | 19 |
| Documents printed and filed, per year | 0 | 0 |
| Handling time, hours per year | 0 | 0 |
| Handling time, FTE | 0.00 (0.00–0.00) | 0.0 (0.0–0.0) |
| Handling time, minutes per care episode | 0.0 | 0.0 |
| Greenhouse gas, t CO2e per year | 0.08 (0.05–0.16) | 2.9 (1.9–6.1) |
| Greenhouse gas, kg CO2e per care episode | 0.04 | 0.04 |
| Water, m³ per year | 116 (24–208) | 4,486 (928–8,043) |
| Water, litres per care episode | 58 | 58 |

All figures 2024 HUF. Printing 7 HUF per printed page (a double-sided sheet costs 14 HUF); paper 2.89 HUF per sheet; antenatal booklet purchased at its unit price and carrying neither printing nor paper charge. Archivist labour and clinician handling time are quantified but not costed, so all monetary totals are floors.
