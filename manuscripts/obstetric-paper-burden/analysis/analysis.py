#!/usr/bin/env python3
"""Micro-costing of paper consumption in obstetric and antenatal documentation.

Reads a page-count workbook plus params.yaml and writes every number the
manuscript reports to tables/ and figures/. Cost outputs whose parameters are
still null in params.yaml are suppressed rather than guessed.

    python3 analysis.py --data pagecounts.xlsx [--params params.yaml] [--outdir ..]
"""
import argparse, json, math, statistics as st
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import openpyxl
import yaml

# Validated categorical slots 1-4 (light mode) from the reference palette.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
OTHER = "#8a897f"   # >5 categories fold to neutral; hues are never cycled
INK, INK_2, GRID = "#0b0b0b", "#52514e", "#d8d7d2"
BOOT_N, SEED = 10000, 20260905


# ---------------------------------------------------------------- statistics
def quantile(v, p):
    v = sorted(v)
    k = (len(v) - 1) * p
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def t_crit(df):
    """Two-sided 95% Student t, adequate for the n used here."""
    table = {10: 2.228, 20: 2.086, 30: 2.042, 40: 2.021, 50: 2.009,
             60: 2.000, 80: 1.990, 99: 1.984, 100: 1.984, 120: 1.980}
    return table.get(df, min(table.items(), key=lambda kv: abs(kv[0] - df))[1])


def describe(v):
    n = len(v)
    m, sd = st.mean(v), (st.stdev(v) if n > 1 else 0.0)
    half = t_crit(n - 1) * sd / math.sqrt(n) if n > 1 else 0.0
    return {"n": n, "mean": m, "sd": sd, "ci_low": m - half, "ci_high": m + half,
            "median": st.median(v), "q1": quantile(v, .25), "q3": quantile(v, .75),
            "min": min(v), "max": max(v), "sum": sum(v)}


def boot_mean_ci(v, n_boot=BOOT_N, seed=SEED):
    import random
    rng = random.Random(seed)
    n = len(v)
    means = sorted(st.mean(rng.choices(v, k=n)) for _ in range(n_boot))
    return means[int(.025 * n_boot)], means[int(.975 * n_boot)]


# ---------------------------------------------------------------------- load
def load(data_path, params):
    ws = openpyxl.load_workbook(data_path, data_only=True)[params["dataset"]["sheet"]]
    rows = list(ws.iter_rows(values_only=True))
    header = {h: i for i, h in enumerate(rows[0]) if h}
    cols, missing = {}, []
    for d in params["documents"]:
        if d["column"] not in header:
            missing.append(d["column"]); continue
        idx = header[d["column"]]
        mult = d.get("pages_per_unit", 1)   # recorded units -> printed pages
        cols[d["key"]] = [r[idx] * mult for r in rows[1:] if r[idx] is not None]
    if missing:
        raise SystemExit(f"columns absent from workbook: {missing}")
    lengths = {len(v) for v in cols.values()}
    if len(lengths) != 1:
        raise SystemExit(f"ragged columns: {[(k, len(v)) for k, v in cols.items()]}")
    return cols, lengths.pop(), header, rows


def sheets_of(d, pages, sc):
    """Physical sheets a document occupies under one scenario.

    A pre-printed booklet is manufactured double-sided whatever our printing
    policy is, so it is halved in every scenario, not only the duplex one.
    """
    if d.get("preprinted_duplex") or sc.get("duplex"):
        return math.ceil(pages / 2)
    return pages


def cross_check(params, cols, header, rows, n):
    """Reconcile our per-episode total against the workbook's own SUM column."""
    name = params["dataset"].get("total_column")
    if not name or name not in header:
        return None
    idx = header[name]
    theirs = [r[idx] for r in rows[1:n + 1]]
    unit = {d["key"]: d.get("pages_per_unit", 1) for d in params["documents"]}
    ours = [sum(cols[k][i] / unit[k] for k in cols) for i in range(n)]
    diff = [a - b for a, b in zip(ours, theirs) if a is not None and b is not None]
    return {"compared": len(diff), "disagreements": sum(1 for d in diff if d != 0),
            "max_abs_diff": max((abs(d) for d in diff), default=0)}


# ------------------------------------------------------------------ scenario
def doc_counts(params, cols, n):
    """Physical documents per episode, the unit staff actually handle.

    A referral is one document. A paired report is one document per referral,
    however many pages it runs to. An unpaired report is counted from its pages
    via pages_per_document.
    """
    out = {}
    for d in params["documents"]:
        k = d["key"]
        if d.get("pairs_with"):
            out[k] = list(cols[d["pairs_with"]])
        elif d.get("pages_per_unit"):
            out[k] = [1] * n                      # one bound booklet
        else:
            per = d.get("pages_per_document")
            out[k] = [v / per for v in cols[k]] if per else list(cols[k])
    return out


def scenario_quantities(params, cols, n, sc):
    """Per-episode quantities under one scenario.

    Three quantities differ and must not be conflated. Physical sheets drive
    archive volume and include purchased items. Self-printed sheets drive paper
    cost. Printed pages drive toner cost - and duplex does not reduce them,
    because the same number of pages still has ink put on it.
    """
    drop = set(sc.get("drop") or [])
    live = [d for d in params["documents"] if d["key"] not in drop]
    sheets, own_sheets, pages = [], [], []
    for i in range(n):
        sheets.append(sum(sheets_of(d, cols[d["key"]][i], sc) for d in live))
        own_sheets.append(sum(sheets_of(d, cols[d["key"]][i], sc)
                              for d in live if not d.get("purchased")))
        pages.append(sum(cols[d["key"]][i] for d in live if not d.get("purchased")))
    return sheets, own_sheets, pages


def physical(sheets, params):
    ph, n_sheets = params["physical"], sheets
    mass_g = n_sheets * ph["sheet_area_m2"] * ph["grammage_g_m2"]
    lm = n_sheets / ph["sheets_per_linear_metre"]
    return {"sheets": n_sheets, "mass_kg": mass_g / 1000, "mass_t": mass_g / 1e6,
            "linear_m": lm, "floor_m2": lm / ph["linear_metres_per_m2_floor"]}


def paper_cost(sheets, params, which="paper_per_ream_net"):
    c = params["costs"]
    return sheets * c[which] / c["sheets_per_ream"]


def consumable_cost(own_sheets, pages, purchase, params, ream="paper_per_ream_net",
                    ppp="print_per_page"):
    """Toner + paper + purchased pre-printed items.

    If the print price is all-in, paper is not added on top.
    """
    c = params["costs"]
    printing = pages * c[ppp]
    paper = 0.0 if c.get("print_cost_includes_paper") else paper_cost(own_sheets, params, ream)
    return {"printing": printing, "paper": paper, "purchased": purchase,
            "total": printing + paper + purchase}


def retention_weighted_stock(params, cols, n, episodes, sc):
    """Steady-state archive holding: annual sheets x retention years, per document."""
    drop = set(sc.get("drop") or [])
    total = 0.0
    for d in params["documents"]:
        if d["key"] in drop:
            continue
        per_ep = st.mean([sheets_of(d, v, sc) for v in cols[d["key"]]])
        total += per_ep * episodes * d["retention_years"]
    return total


# --------------------------------------------------------------------- plots
def style(ax):
    ax.set_facecolor("none")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID); ax.spines[s].set_linewidth(0.8)
    ax.tick_params(colors=INK_2, labelsize=8, length=3, width=0.8)
    ax.yaxis.grid(True, color=GRID, lw=0.6); ax.set_axisbelow(True)


def hue(i):
    return SERIES[i] if i < len(SERIES) else OTHER


def fig_composition(params, cols, out):
    fams, order = {}, []
    for d in params["documents"]:
        fams.setdefault(d["family"], {"referral": 0.0, "result": 0.0, "record": 0.0})
        fams[d["family"]][d["role"]] += st.mean(cols[d["key"]])
        if d["family"] not in order:
            order.append(d["family"])
    order.sort(key=lambda f: -sum(fams[f].values()))
    fig, ax = plt.subplots(figsize=(6.6, 3.3), dpi=300)
    y = range(len(order))
    left = [0.0] * len(order)
    parts = [("referral", "//", "Referral"), ("result", "", "Report"), ("record", "..", "Record")]
    for role, hatch, lab in parts:
        vals = [fams[f][role] for f in order]
        if not any(vals):
            continue
        ax.barh(list(y), vals, left=left, height=.62, label=lab,
                color=[hue(order.index(f)) for f in order],
                edgecolor="white", linewidth=1.4, hatch=hatch)
        left = [a + b for a, b in zip(left, vals)]
    for i, f in enumerate(order):
        tot = sum(fams[f].values())
        ax.text(tot + .8, i, f"{tot:.1f}", va="center", fontsize=8, color=INK)
    ax.set_yticks(list(y)); ax.set_yticklabels(order, fontsize=8.5, color=INK)
    ax.invert_yaxis(); style(ax); ax.xaxis.grid(True, color=GRID, lw=.6); ax.yaxis.grid(False)
    ax.set_xlabel("Mean printed pages per care episode", fontsize=8.5, color=INK_2)
    handles = [plt.Rectangle((0, 0), 1, 1, fc="white", ec=INK_2, hatch=h, lw=.8)
               for _, h, _ in parts]
    ax.legend(handles, [l for _, _, l in parts], frameon=False, fontsize=8,
              loc="lower right", labelcolor=INK_2)
    fig.tight_layout(); fig.savefig(out, bbox_inches="tight", facecolor="white"); plt.close(fig)


def fig_distribution(totals, out):
    fig, ax = plt.subplots(figsize=(6.6, 2.9), dpi=300)
    ax.hist(totals, bins=range(int(min(totals)) - 1, int(max(totals)) + 4, 4),
            color=SERIES[0], edgecolor="white", linewidth=1.2)
    m = st.mean(totals)
    ax.axvline(m, color=INK, lw=1.6, ls=(0, (4, 2)))
    ax.text(m, ax.get_ylim()[1] * .96, f"  mean {m:.1f}", fontsize=8, color=INK, va="top")
    style(ax)
    ax.set_xlabel("Printed pages per care episode", fontsize=8.5, color=INK_2)
    ax.set_ylabel("Episodes", fontsize=8.5, color=INK_2)
    fig.tight_layout(); fig.savefig(out, bbox_inches="tight", facecolor="white"); plt.close(fig)


def fig_scenarios(rows, out):
    fig, ax = plt.subplots(figsize=(6.6, 3.0), dpi=300)
    labs = [r["label"] for r in rows]
    vals = [r["national"]["mass_t"] for r in rows]
    x = range(len(rows))
    ax.bar(list(x), vals, width=.55, color=[SERIES[0]] + [SERIES[2]] * (len(rows) - 1),
           edgecolor="white", linewidth=1.4)
    for i, (v, r) in enumerate(zip(vals, rows)):
        ax.text(i, v + max(vals) * .03, f"{v:.1f} t", ha="center", fontsize=8, color=INK)
        if i:
            ax.text(i, v + max(vals) * .11, f"−{100 * (1 - v / vals[0]):.0f}%",
                    ha="center", fontsize=8, color=INK_2)
    ax.set_xticks(list(x)); ax.set_xticklabels(labs, fontsize=8.5, color=INK)
    ax.set_ylim(0, max(vals) * 1.22); style(ax)
    ax.set_ylabel("Paper per year, national (tonnes)", fontsize=8.5, color=INK_2)
    fig.tight_layout(); fig.savefig(out, bbox_inches="tight", facecolor="white"); plt.close(fig)


def fig_accrual(rows, params, out):
    yrs = list(range(0, 31))
    fig, ax = plt.subplots(figsize=(6.6, 3.0), dpi=300)
    for i, r in enumerate(rows):
        per_yr = r["national"]["floor_m2"]
        ax.plot(yrs, [per_yr * y for y in yrs], lw=2, color=hue(i), label=r["label"])
        ax.text(30.4, per_yr * 30, f" {r['label']}", fontsize=7.5, va="center",
                color=hue(i))
    style(ax); ax.set_xlim(0, 30); ax.margins(x=0)
    ax.set_xlabel("Years of accrual at constant volume", fontsize=8.5, color=INK_2)
    ax.set_ylabel("Archive floor area, national (m²)", fontsize=8.5, color=INK_2)
    fig.tight_layout(); fig.savefig(out, bbox_inches="tight", facecolor="white"); plt.close(fig)


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--params", default=str(Path(__file__).with_name("params.yaml")))
    ap.add_argument("--outdir", default=str(Path(__file__).resolve().parent.parent))
    a = ap.parse_args()

    params = yaml.safe_load(Path(a.params).read_text())
    out = Path(a.outdir)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(parents=True, exist_ok=True)

    cols, n, header, rows = load(a.data, params)
    docs = doc_counts(params, cols, n)
    R = {"n_episodes": n, "params_file": Path(a.params).name}
    R["cross_check"] = cross_check(params, cols, header, rows, n)

    # per-document descriptives
    R["documents"] = {}
    for d in params["documents"]:
        s = describe(cols[d["key"]])
        s.update(label=d["label"], family=d["family"], role=d["role"],
                 retention_years=d["retention_years"])
        R["documents"][d["key"]] = s

    # referral -> report amplification
    R["amplification"] = {}
    for d in params["documents"]:
        if d.get("pairs_with"):
            ratios = {round(b / c, 4) for c, b in zip(cols[d["pairs_with"]], cols[d["key"]]) if c}
            R["amplification"][d["key"]] = {
                "referral": d["pairs_with"], "ratios": sorted(ratios),
                "fixed": len(ratios) == 1, "value": sorted(ratios)[0] if len(ratios) == 1 else None}

    totals = [sum(cols[d["key"]][i] for d in params["documents"]) for i in range(n)]
    R["total_pages"] = describe(totals)
    lo, hi = boot_mean_ci(totals)
    R["total_pages"]["boot_ci_low"], R["total_pages"]["boot_ci_high"] = lo, hi

    # role split
    R["role_split"] = {}
    for role in ("referral", "result", "record"):
        keys = [d["key"] for d in params["documents"] if d["role"] == role]
        v = [sum(cols[k][i] for k in keys) for i in range(n)] if keys else [0] * n
        R["role_split"][role] = {"mean": st.mean(v),
                                 "share": st.mean(v) / st.mean(totals) if st.mean(totals) else 0}

    # scenarios
    sc_rows, costs = [], params["costs"]
    for sc in params["scenarios"]:
        drop_keys = set(sc.get("drop") or [])
        roles = set(params["time"]["handling_roles"])
        printed_docs = [d["key"] for d in params["documents"]
                        if d["key"] not in drop_keys and not d.get("purchased")
                        and d["role"] in roles]
        docs_case = [sum(docs[k][i] for k in printed_docs) for i in range(n)]
        bought = [d for d in params["documents"]
                  if d["key"] not in drop_keys and d.get("purchased") and d.get("unit_price")]
        buy_case = [sum(docs[d["key"]][i] * d["unit_price"] for d in bought) for i in range(n)]
        per_case, own_case, page_case = scenario_quantities(params, cols, n, sc)
        stt = describe(per_case)
        row = {"key": sc["key"], "label": sc["label"], "note": sc.get("note", ""),
               "sheets_per_episode": stt,
               "self_printed_sheets_per_episode": describe(own_case),
               "printed_pages_per_episode": describe(page_case),
               "documents_per_episode": describe(docs_case)}
        for lvl, eps in (("local", params["scale"]["local_episodes_per_year"]),
                         ("national", params["scale"]["national_episodes_per_year"])):
            annual = stt["mean"] * eps
            annual_own = st.mean(own_case) * eps
            annual_pages = st.mean(page_case) * eps
            p = physical(annual, params)
            p["episodes"] = eps
            p["printed_pages"] = annual_pages
            p["self_printed_sheets"] = annual_own
            annual_buy = st.mean(buy_case) * eps
            cc = consumable_cost(annual_own, annual_pages, annual_buy, params)
            p["print_cost_net"] = cc["printing"]
            p["paper_cost_net"] = cc["paper"]
            p["purchased_cost_net"] = cc["purchased"]
            p["consumable_cost_net"] = cc["total"]
            p["consumable_cost_gross"] = cc["total"] * (1 + costs["vat_rate"])
            p["paper_cost_gross"] = p["paper_cost_net"] * (1 + costs["vat_rate"])
            p["consumable_cost_net_low"] = consumable_cost(
                annual_own, annual_pages, annual_buy, params, "paper_per_ream_net_low",
                "print_per_page_low")["total"]
            p["consumable_cost_net_high"] = consumable_cost(
                annual_own, annual_pages, annual_buy, params, "paper_per_ream_net_high",
                "print_per_page_high")["total"]
            stock = retention_weighted_stock(params, cols, n, eps, sc)
            ps = physical(stock, params)
            p["steady_state"] = ps
            p["steady_state"]["floor_value_huf"] = ps["floor_m2"] * costs["floor_value_per_m2"]
            p["steady_state"]["floor_value_huf_low"] = ps["floor_m2"] * costs["floor_value_per_m2_low"]
            p["steady_state"]["floor_value_huf_high"] = ps["floor_m2"] * costs["floor_value_per_m2_high"]
            env = params["environment"]
            p["consumable_cost_per_episode_net"] = cc["total"] / eps
            p["consumable_cost_per_episode_gross"] = p["consumable_cost_gross"] / eps
            p["paper_cost_per_episode_net"] = p["paper_cost_net"] / eps
            p["steady_state"]["floor_value_per_episode"] = (
                p["steady_state"]["floor_value_huf"] / eps)
            tm = params["time"]
            docs_yr = st.mean(docs_case) * eps
            p["documents"] = docs_yr
            p["handling_hours"] = docs_yr * tm["seconds_per_document"] / 3600
            p["handling_hours_low"] = docs_yr * tm["seconds_per_document_low"] / 3600
            p["handling_hours_high"] = docs_yr * tm["seconds_per_document_high"] / 3600
            p["handling_fte"] = p["handling_hours"] / tm["annual_hours_per_fte"]
            p["handling_fte_low"] = p["handling_hours_low"] / tm["annual_hours_per_fte"]
            p["handling_fte_high"] = p["handling_hours_high"] / tm["annual_hours_per_fte"]
            p["handling_minutes_per_episode"] = (
                st.mean(docs_case) * tm["seconds_per_document"] / 60)
            se = params["space_equivalents"]
            p["steady_state"]["consulting_rooms"] = (
                p["steady_state"]["floor_m2"] / se["antenatal_consulting_room_m2"])
            p["steady_state"]["delivery_rooms"] = (
                p["steady_state"]["floor_m2"] / se["delivery_room_m2"])
            p["co2e_t"] = p["mass_t"] * env["co2e_t_per_t"]
            p["water_m3"] = p["mass_t"] * env["water_m3_per_t"]
            p["wood_t"] = p["mass_t"] * env["wood_t_per_t"]
            row[lvl] = p
        sc_rows.append(row)
    R["scenarios"] = sc_rows

    # discounted 10-year paper spend, national, per scenario
    r, H = params["discounting"]["rate"], params["discounting"]["horizon_years"]
    disc = sum(1 / (1 + r) ** t for t in range(H))
    for row in sc_rows:
        for lvl in ("local", "national"):
            row[lvl]["consumable_cost_net_10y_discounted"] = (
                row[lvl]["consumable_cost_net"] * disc)
    R["discount_factor_sum"] = disc

    # one-way sensitivity on the steady-state floor value, base scenario
    base = sc_rows[0]
    ph, sens = params["physical"], []
    stock = base["national"]["steady_state"]["sheets"]
    for name, lo_, hi_, kind in [
            ("Sheets per linear metre", ph["sheets_per_linear_metre_low"],
             ph["sheets_per_linear_metre_high"], "density"),
            ("Linear metres per m² floor", ph["linear_metres_per_m2_floor_low"],
             ph["linear_metres_per_m2_floor_high"], "shelving"),
            ("Property value per m²", costs["floor_value_per_m2_low"],
             costs["floor_value_per_m2_high"], "value"),
            ("Episodes per year", params["scale"]["national_episodes_low"],
             params["scale"]["national_episodes_high"], "volume")]:
        vals = []
        for v in (lo_, hi_):
            spl = ph["sheets_per_linear_metre"]; lpm = ph["linear_metres_per_m2_floor"]
            val = costs["floor_value_per_m2"]; s = stock
            if kind == "density": spl = v
            elif kind == "shelving": lpm = v
            elif kind == "value": val = v
            else: s = stock * v / params["scale"]["national_episodes_per_year"]
            vals.append(s / spl / lpm * val)
        sens.append({"parameter": name, "low": min(vals), "high": max(vals)})
    R["sensitivity_floor_value"] = {
        "base": base["national"]["steady_state"]["floor_value_huf"], "rows": sens}

    R["not_monetised"] = {
        "handling_time": "absorbed into existing staff duties, not a staffed post; "
                         "reported in hours and FTE-equivalent only"}
    R["suppressed_outputs"] = [k for k in costs
                               if costs[k] is None and k != "archivist_huf_per_hour"]

    (out / "tables" / "results.json").write_text(json.dumps(R, indent=2, ensure_ascii=False))

    fig_composition(params, cols, out / "figures" / "fig1_composition.png")
    fig_distribution(totals, out / "figures" / "fig2_distribution.png")
    fig_scenarios(sc_rows, out / "figures" / "fig3_scenarios.png")
    fig_accrual(sc_rows, params, out / "figures" / "fig4_accrual.png")

    write_tables(R, params, out / "tables")
    print(f"n={n}  mean pages/episode={R['total_pages']['mean']:.2f} "
          f"(95% CI {R['total_pages']['ci_low']:.2f}-{R['total_pages']['ci_high']:.2f})")
    print("suppressed (parameter still null):", R["suppressed_outputs"] or "none")
    print("not monetised by design:", ", ".join(R["not_monetised"]))


def write_tables(R, params, tdir):
    def huf(x):
        for div, suf in ((1e9, " bn"), (1e6, " M"), (1e3, "k")):
            if abs(x) >= div:
                return f"{x/div:,.2f}{suf}"
        return f"{x:,.0f}"

    t1 = ["| Document | Role | Retention (y) | Mean (SD) | Median (IQR) | Range | Share |",
          "|---|---|---|---|---|---|---|"]
    tot = R["total_pages"]["mean"]
    for k, d in R["documents"].items():
        t1.append(f"| {d['label']} | {d['role']} | {d['retention_years']} | "
                  f"{d['mean']:.2f} ({d['sd']:.2f}) | {d['median']:.0f} "
                  f"({d['q1']:.0f}–{d['q3']:.0f}) | {d['min']}–{d['max']} | "
                  f"{100*d['mean']/tot:.1f}% |")
    t1.append(f"| **All documents** | | | **{tot:.2f} ({R['total_pages']['sd']:.2f})** | "
              f"**{R['total_pages']['median']:.0f} ({R['total_pages']['q1']:.0f}–"
              f"{R['total_pages']['q3']:.0f})** | **{R['total_pages']['min']}–"
              f"{R['total_pages']['max']}** | 100% |")
    (tdir / "table1_documents.md").write_text("\n".join(t1) + "\n")

    # Table 2 (main): compact scenario outcomes, both scales
    t2 = ["| Scenario | Pages printed / episode | Sheets / episode | Documents / episode | "
          "Paper (t/y) | Consumables (HUF/y, net) | Handling (h/y) | Handling (FTE) | "
          "Archive floor (m²) | Property value (HUF) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for lvl in ("local", "national"):
        lab = params["scale"][f"{lvl}_label"]
        eps = params["scale"][f"{lvl}_episodes_per_year"]
        t2.append(f"| **{lab}, {eps:,} episodes/year** | | | | | | | | | |")
        for s in R["scenarios"]:
            p = s[lvl]
            t2.append(f"| {s['label']} | {s['printed_pages_per_episode']['mean']:.1f} | "
                      f"{s['sheets_per_episode']['mean']:.1f} | "
                      f"{s['documents_per_episode']['mean']:.1f} | {p['mass_t']:.2f} | "
                      f"{huf(p['consumable_cost_net'])} | {p['handling_hours']:,.0f} | "
                      f"{p['handling_fte']:.2f} | {p['steady_state']['floor_m2']:,.0f} | "
                      f"{huf(p['steady_state']['floor_value_huf'])} |")
    c = params["costs"]
    t2 += ["", f"Costs are 2024 HUF, net of VAT. Printing is charged per printed page at "
               f"{c['print_per_page']} HUF, so duplex printing halves sheets but not pages and "
               f"saves no toner. Handling time covers referrals and diagnostic reports at "
               f"{params['time']['seconds_per_document']} s per document and is reported as "
               f"displaced capacity, not costed. Property value is the capital immobilised by "
               f"the archive at steady state under statutory retention, not an annual rent. "
               f"Full cost components are given in supplementary table S1."]
    (tdir / "table2_scenarios.md").write_text("\n".join(t2) + "\n")

    # Table 3 (main): one-way sensitivity
    s = R["sensitivity_floor_value"]
    t3 = [f"Steady-state immobilised property value, national. Base case {huf(s['base'])} HUF.", "",
          "| Parameter varied | Low | High | Swing |", "|---|---|---|---|"]
    for r in sorted(s["rows"], key=lambda r: -(r["high"] - r["low"])):
        t3.append(f"| {r['parameter']} | {huf(r['low'])} | {huf(r['high'])} | "
                  f"{huf(r['high']-r['low'])} |")
    (tdir / "table3_sensitivity.md").write_text("\n".join(t3) + "\n")

    # Supplementary S1: full cost breakdown, both scales
    loc, nat = params["scale"]["local_label"], params["scale"]["national_label"]
    le, ne = (params["scale"]["local_episodes_per_year"],
              params["scale"]["national_episodes_per_year"])
    s1 = [f"| Cost component | {loc} ({le:,}/y) | {nat} ({ne:,}/y) |", "|---|---|---|"]
    for s in R["scenarios"]:
        l, n = s["local"], s["national"]
        s1 += [f"| **{s['label']}** | | |",
               f"| Printing (toner, device), per year | {huf(l['print_cost_net'])} | "
               f"{huf(n['print_cost_net'])} |",
               f"| Paper, per year | {huf(l['paper_cost_net'])} | {huf(n['paper_cost_net'])} |",
               f"| Purchased antenatal booklets, per year | {huf(l['purchased_cost_net'])} | "
               f"{huf(n['purchased_cost_net'])} |",
               f"| Consumables, per year (net) | {huf(l['consumable_cost_net'])} | "
               f"{huf(n['consumable_cost_net'])} |",
               f"| Consumables, per year (gross, incl. VAT) | {huf(l['consumable_cost_gross'])} | "
               f"{huf(n['consumable_cost_gross'])} |",
               f"| Consumables, 10 y discounted (net) | "
               f"{huf(l['consumable_cost_net_10y_discounted'])} | "
               f"{huf(n['consumable_cost_net_10y_discounted'])} |",
               f"| Consumables, per care episode (net) | "
               f"{l['consumable_cost_per_episode_net']:,.0f} | "
               f"{n['consumable_cost_per_episode_net']:,.0f} |",
               f"| Archive property value at steady state | "
               f"{huf(l['steady_state']['floor_value_huf'])} | "
               f"{huf(n['steady_state']['floor_value_huf'])} |",
               f"| Property value per care episode | "
               f"{l['steady_state']['floor_value_per_episode']:,.0f} | "
               f"{n['steady_state']['floor_value_per_episode']:,.0f} |",
               f"| Archive floor area (steady state) | {l['steady_state']['floor_m2']:,.0f} m² | "
               f"{n['steady_state']['floor_m2']:,.0f} m² |",
               f"| — as antenatal consulting rooms | "
               f"{l['steady_state']['consulting_rooms']:,.0f} | "
               f"{n['steady_state']['consulting_rooms']:,.0f} |",
               f"| Documents printed and filed, per year | {l['documents']:,.0f} | "
               f"{n['documents']:,.0f} |",
               f"| Handling time, hours per year | {l['handling_hours']:,.0f} | "
               f"{n['handling_hours']:,.0f} |",
               f"| Handling time, FTE | {l['handling_fte']:.2f} "
               f"({l['handling_fte_low']:.2f}–{l['handling_fte_high']:.2f}) | "
               f"{n['handling_fte']:.1f} "
               f"({n['handling_fte_low']:.1f}–{n['handling_fte_high']:.1f}) |",
               f"| Handling time, minutes per care episode | "
               f"{l['handling_minutes_per_episode']:.1f} | "
               f"{n['handling_minutes_per_episode']:.1f} |"]
    s1 += ["", f"All figures 2024 HUF. Printing {c['print_per_page']} HUF per printed page "
               f"(a double-sided sheet costs {2 * c['print_per_page']} HUF); paper "
               f"{c['paper_per_ream_net'] / c['sheets_per_ream']:.2f} HUF per sheet; antenatal "
               f"booklet purchased at its unit price and carrying neither printing nor paper "
               f"charge. Archivist labour and clinician handling time are quantified but not "
               f"costed, so all monetary totals are floors."]
    (tdir / "tableS1_cost_detail.md").write_text("\n".join(s1) + "\n")


if __name__ == "__main__":
    main()
