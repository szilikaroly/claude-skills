## stats.R — R as a statistics MAKER, not just a validator.
##
## Everything here runs in the Claude Science r-stats-methodologist environment
## (ggplot2, metafor, meta, knitr). It turns a council session — or an external
## dataset — into actual statistics: descriptive summaries, agreement analysis,
## ggplot2 charts, and, when quantitative effect data exists, a real
## random-effects meta-analysis with a forest plot. Output is a self-contained
## HTML report plus PNG figures.
##
## The functions below are written to be sourced and run BY the stats specialist
## Rscript (see stats_report_generate), so they assume ggplot2/metafor are present.

## No DuckDB here: the stats specialist env (r-stats-methodologist) has jsonlite,
## ggplot2 and metafor but not duckdb. System R exports the session to JSON
## (db.R::export_session_json); this module reads that JSON. Keeps the two R
## worlds cleanly separated.
suppressPackageStartupMessages({ library(jsonlite) })

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a) == 1 && is.na(a))) b else a

## Load a session exported by export_session_json() into a list of data.frames.
load_session_json <- function(path) {
  j <- fromJSON(path, simplifyDataFrame = TRUE)
  as_df <- function(x) if (is.null(x) || length(x) == 0) data.frame() else as.data.frame(x, stringsAsFactors = FALSE)
  list(question = j$question %||% "", qid = j$qid %||% "",
       claims = as_df(j$claims), verdicts = as_df(j$verdicts),
       evidence = as_df(j$evidence), r_checks = as_df(j$r_checks),
       calibration = as_df(j$calibration))
}

sc_theme <- function() {
  ggplot2::theme_minimal(base_size = 12) +
    ggplot2::theme(
      panel.grid.minor = ggplot2::element_blank(),
      plot.title = ggplot2::element_text(face = "bold"),
      plot.subtitle = ggplot2::element_text(color = "grey40"),
      legend.position = "bottom")
}

STATUS_COLORS <- c(validated = "#1a9850", contested = "#f0a202",
                   refuted = "#d73027", unresolved = "#9e9e9e")
VOTE_COLORS <- c(support = "#1a9850", uncertain = "#9e9e9e",
                 refute = "#d73027", discarded = "#cccccc")

## ---- descriptive statistics of a session ------------------------------------
session_descriptives <- function(session) {
  cl <- session$claims; vd <- session$verdicts
  ev <- session$evidence; rc <- session$r_checks
  if (!nrow(cl)) stop("No claims in session")

  status_tab <- as.data.frame(table(factor(cl$status,
                    levels = c("validated","contested","refuted","unresolved"))))
  names(status_tab) <- c("status","n")

  list(
    n_claims = nrow(cl),
    status = status_tab,
    consensus = c(mean = mean(cl$consensus_score), sd = sd(cl$consensus_score),
                  min = min(cl$consensus_score), max = max(cl$consensus_score),
                  median = median(cl$consensus_score)),
    kappa = cl$agreement_kappa[1], alpha = cl$krippendorff_alpha[1],
    pct_validated = 100 * mean(cl$status == "validated"),
    n_votes = nrow(vd),
    vote_breakdown = as.data.frame(table(vd$verdict)),
    citations = list(total = nrow(ev),
                     resolved = sum(ev$verified, na.rm = TRUE),
                     on_topic = sum(ev$verified & (is.na(ev$relevant) | ev$relevant), na.rm = TRUE)),
    r_checks = list(n = nrow(rc),
                    passed = sum(rc$passed %in% TRUE),
                    failed = sum(rc$passed %in% FALSE),
                    inconclusive = sum(is.na(rc$passed))),
    claims = cl, verdicts = vd, evidence = ev)
}

## ---- charts ------------------------------------------------------------------
## Each returns a ggplot; save_charts() writes them as PNG and returns paths.

chart_consensus <- function(cl) {
  library(ggplot2)
  d <- cl[order(cl$consensus_score), ]
  d$claim_short <- ifelse(nchar(d$claim) > 60, paste0(substr(d$claim, 1, 57), "..."), d$claim)
  d$claim_short <- factor(d$claim_short, levels = d$claim_short)
  ggplot(d, aes(x = consensus_score, y = claim_short, fill = status)) +
    geom_col() +
    geom_vline(xintercept = 0, color = "grey50") +
    scale_fill_manual(values = STATUS_COLORS, name = "status") +
    scale_x_continuous(limits = c(-1, 1)) +
    labs(title = "Weighted consensus per claim",
         subtitle = "+1 = unanimous confident support · -1 = unanimous confident refutation",
         x = "consensus score", y = NULL) +
    sc_theme()
}

chart_votes <- function(cl, vd) {
  library(ggplot2)
  vd <- merge(vd, cl[, c("id","claim")], by.x = "claim_id", by.y = "id")
  vd$claim_short <- ifelse(nchar(vd$claim) > 46, paste0(substr(vd$claim, 1, 43), "..."), vd$claim)
  vd$verdict <- factor(vd$verdict, levels = names(VOTE_COLORS))
  ggplot(vd, aes(y = claim_short, fill = verdict)) +
    geom_bar() +
    scale_fill_manual(values = VOTE_COLORS, name = "verdict", drop = FALSE) +
    labs(title = "Vote composition per claim",
         subtitle = "final-round verdicts across the panel", x = "votes", y = NULL) +
    sc_theme()
}

chart_calibration <- function(cal) {
  library(ggplot2)
  if (is.null(cal) || !nrow(cal)) return(NULL)
  cal <- cal[order(cal$weight), ]
  cal$acc <- 100 * cal$n_correct / pmax(cal$n_votes, 1)
  cal$panelist <- factor(cal$panelist, levels = cal$panelist)
  ggplot(cal, aes(x = weight, y = panelist)) +
    geom_col(fill = "#3b78b0") +
    geom_vline(xintercept = 1, linetype = "dashed", color = "grey40") +
    geom_text(aes(label = sprintf("%.0f%% match · %d votes", acc, n_votes)),
              hjust = -0.05, size = 3, color = "grey30") +
    scale_x_continuous(limits = c(0, 2.2)) +
    labs(title = "Panelist calibration weight",
         subtitle = "shrunk toward 1.0; >1 = track record of matching the settled outcome",
         x = "weight", y = NULL) +
    sc_theme()
}

chart_citations <- function(ev) {
  library(ggplot2)
  if (!nrow(ev)) return(NULL)
  cls <- ifelse(!ev$verified, "does not resolve",
         ifelse(is.na(ev$relevant), "resolves (relevance unjudged)",
         ifelse(ev$relevant, "resolves + on-topic", "resolves but off-topic")))
  d <- as.data.frame(table(cls)); names(d) <- c("class","n")
  pal <- c("does not resolve" = "#d73027", "resolves but off-topic" = "#f0a202",
           "resolves (relevance unjudged)" = "#9e9e9e", "resolves + on-topic" = "#1a9850")
  ggplot(d, aes(x = n, y = reorder(class, n), fill = class)) +
    geom_col() +
    scale_fill_manual(values = pal, guide = "none") +
    labs(title = "Citation grounding",
         subtitle = "a citation is evidence only if it resolves AND is on-topic",
         x = "citations", y = NULL) +
    sc_theme()
}

save_charts <- function(desc, calibration, outdir) {
  library(ggplot2)
  dir.create(outdir, recursive = TRUE, showWarnings = FALSE)
  paths <- list()
  add <- function(name, plot, h = 4.5) {
    ok <- tryCatch({ if (is.null(plot)) return(invisible())
      p <- file.path(outdir, paste0(name, ".png"))
      ggsave(p, plot, width = 9, height = h, dpi = 130, bg = "white"); paths[[name]] <<- p; TRUE },
      error = function(e) { message("chart ", name, " skipped: ", conditionMessage(e)); FALSE })
  }
  add("consensus", chart_consensus(desc$claims), h = max(3, 0.5 * nrow(desc$claims) + 1.5))
  add("votes", chart_votes(desc$claims, desc$verdicts), h = max(3, 0.45 * nrow(desc$claims) + 1.5))
  add("calibration", chart_calibration(calibration))
  add("citations", chart_citations(desc$evidence))
  paths
}

## ---- real meta-analysis ------------------------------------------------------
## The genuine "statistics maker": given effect data, fit a random-effects model
## and draw a forest plot. `data` is a data.frame with yi (effect) + vi (variance),
## OR the name of a metadat dataset plus a measure and column mapping.
council_meta <- function(data, measure = "GEN", outdir = NULL, slab = NULL) {
  suppressPackageStartupMessages({ library(metafor) })
  if (is.character(data) && length(data) == 1) {
    stopifnot(requireNamespace("metadat", quietly = TRUE))
    data <- get(data, envir = asNamespace("metadat"))
  }
  res <- rma(yi = data$yi, vi = data$vi, method = "REML", slab = slab)
  forest_path <- NULL
  if (!is.null(outdir)) {
    dir.create(outdir, recursive = TRUE, showWarnings = FALSE)
    forest_path <- file.path(outdir, "forest.png")
    png(forest_path, width = 1100, height = max(400, 40 * nrow(data) + 200), res = 130)
    forest(res, header = TRUE)
    dev.off()
  }
  list(estimate = as.numeric(coef(res)), ci_lb = res$ci.lb, ci_ub = res$ci.ub,
       pval = res$pval, I2 = res$I2, tau2 = res$tau2, k = res$k,
       summary = capture.output(summary(res)), forest = forest_path)
}

## ---- self-contained HTML report ---------------------------------------------
## PNGs embedded as base64 data URIs — no external files, opens anywhere.
b64_img <- function(path) {
  if (is.null(path) || !file.exists(path)) return("")
  raw <- readBin(path, "raw", file.info(path)$size)
  sprintf('<img src="data:image/png;base64,%s" style="max-width:100%%;height:auto;margin:12px 0;border:1px solid #eee;border-radius:6px"/>',
          jsonlite::base64_enc(raw))
}

build_html_report <- function(desc, charts, meta = NULL, question = "", qid = "", outfile) {
  fmt <- function(x, d = 2) ifelse(is.na(x), "n/a", formatC(x, format = "f", digits = d))
  st <- setNames(desc$status$n, desc$status$status)
  css <- "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem;color:#222;line-height:1.5}
h1{font-size:1.6rem}h2{font-size:1.2rem;border-bottom:2px solid #eee;padding-bottom:4px;margin-top:2rem}
.kpis{display:flex;flex-wrap:wrap;gap:12px;margin:1rem 0}
.kpi{flex:1;min-width:130px;background:#f7f9fb;border:1px solid #e6ebf0;border-radius:8px;padding:12px}
.kpi .v{font-size:1.5rem;font-weight:700}.kpi .l{font-size:.8rem;color:#666}
table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #e6ebf0;padding:6px 10px;text-align:left;font-size:.9rem}
th{background:#f7f9fb}.foot{color:#888;font-size:.8rem;margin-top:2rem}"
  h <- c()
  add <- function(...) h <<- c(h, ...)
  add(sprintf('<!doctype html><html><head><meta charset="utf-8"><title>Council statistics — %s</title><style>%s</style></head><body>',
              qid, css))
  add(sprintf("<h1>Statistical report</h1><p><b>Question:</b> %s</p><p class='foot'>Session <code>%s</code></p>",
              question, qid))
  add("<div class='kpis'>",
      sprintf("<div class='kpi'><div class='v'>%d</div><div class='l'>claims</div></div>", desc$n_claims),
      sprintf("<div class='kpi'><div class='v'>%d</div><div class='l'>validated</div></div>", st[["validated"]] %||% 0),
      sprintf("<div class='kpi'><div class='v'>%s</div><div class='l'>mean consensus</div></div>", fmt(desc$consensus["mean"])),
      sprintf("<div class='kpi'><div class='v'>%s</div><div class='l'>Fleiss &kappa;</div></div>", fmt(desc$kappa, 3)),
      sprintf("<div class='kpi'><div class='v'>%s</div><div class='l'>Krippendorff &alpha;</div></div>", fmt(desc$alpha, 3)),
      sprintf("<div class='kpi'><div class='v'>%d/%d</div><div class='l'>citations resolve</div></div>",
              desc$citations$resolved, desc$citations$total),
      "</div>")

  add("<h2>Descriptive statistics</h2><table><tr><th>metric</th><th>value</th></tr>",
      sprintf("<tr><td>consensus mean (sd)</td><td>%s (%s)</td></tr>", fmt(desc$consensus["mean"]), fmt(desc$consensus["sd"])),
      sprintf("<tr><td>consensus median [min, max]</td><td>%s [%s, %s]</td></tr>",
              fmt(desc$consensus["median"]), fmt(desc$consensus["min"]), fmt(desc$consensus["max"])),
      sprintf("<tr><td>validated share</td><td>%s%%</td></tr>", fmt(desc$pct_validated, 0)),
      sprintf("<tr><td>total verdicts cast</td><td>%d</td></tr>", desc$n_votes),
      sprintf("<tr><td>R checks (pass/fail/inconclusive)</td><td>%d / %d / %d</td></tr>",
              desc$r_checks$passed, desc$r_checks$failed, desc$r_checks$inconclusive),
      sprintf("<tr><td>citations resolved / on-topic / total</td><td>%d / %d / %d</td></tr>",
              desc$citations$resolved, desc$citations$on_topic, desc$citations$total),
      "</table>")

  add("<h2>Figures</h2>",
      b64_img(charts$consensus), b64_img(charts$votes),
      b64_img(charts$calibration), b64_img(charts$citations))

  if (!is.null(meta)) {
    add("<h2>Meta-analysis (random-effects, REML)</h2>",
        sprintf("<table><tr><th>k studies</th><td>%d</td></tr><tr><th>pooled estimate</th><td>%s [%s, %s]</td></tr><tr><th>p-value</th><td>%s</td></tr><tr><th>I&sup2;</th><td>%s%%</td></tr><tr><th>&tau;&sup2;</th><td>%s</td></tr></table>",
                meta$k, fmt(meta$estimate, 3), fmt(meta$ci_lb, 3), fmt(meta$ci_ub, 3),
                format.pval(meta$pval, digits = 3), fmt(meta$I2, 1), fmt(meta$tau2, 4)),
        b64_img(meta$forest))
  }
  add("<p class='foot'>Generated by science-council · R statistics engine (r-stats-methodologist) · ggplot2 + metafor</p></body></html>")
  writeLines(paste(h, collapse = "\n"), outfile)
  outfile
}
