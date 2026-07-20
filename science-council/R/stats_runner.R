#!/usr/bin/env Rscript
## stats_runner.R — executed BY the Claude Science stats specialist Rscript.
## Reads a session JSON export + args, produces charts and a self-contained HTML
## statistical report. Kept separate so it can run in an env without duckdb.
##
## Usage: Rscript stats_runner.R <session.json> <outdir> <report.html> [meta.json]

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) stop("usage: stats_runner.R <session.json> <outdir> <report.html> [meta.json]")
session_json <- args[1]; outdir <- args[2]; report <- args[3]
meta_spec <- if (length(args) >= 4 && nzchar(args[4])) args[4] else NULL

self <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))[1])
source(file.path(dirname(self), "stats.R"))

session <- load_session_json(session_json)
desc <- session_descriptives(session)
charts <- save_charts(desc, session$calibration, outdir)

## Optional meta-analysis. meta.json: {"dataset":"dat.bcg","measure":"RR",
## "ai":"tpos",...} for a metadat set, or {"yi":[...],"vi":[...],"slab":[...]}.
meta <- NULL
if (!is.null(meta_spec) && file.exists(meta_spec)) {
  ms <- jsonlite::fromJSON(meta_spec)
  meta <- tryCatch({
    if (!is.null(ms$dataset)) {
      suppressPackageStartupMessages(library(metafor))
      d <- get(ms$dataset, envir = asNamespace("metadat"))
      ## build escalc args from only the column mappings actually supplied
      col_args <- c("ai","bi","ci","di","n1i","n2i","m1i","m2i","sd1i","sd2i","xi","ti","ni")
      ea <- list(measure = ms$measure %||% "RR", data = d)
      for (a in col_args) if (!is.null(ms[[a]]) && ms[[a]] %in% names(d)) ea[[a]] <- d[[ms[[a]]]]
      es <- do.call(escalc, ea)
      slab <- if (!is.null(ms$slab) && ms$slab %in% names(d)) d[[ms$slab]] else NULL
      council_meta(data.frame(yi = es$yi, vi = es$vi), outdir = outdir, slab = slab)
    } else {
      council_meta(data.frame(yi = ms$yi, vi = ms$vi), outdir = outdir, slab = ms$slab)
    }
  }, error = function(e) { message("meta-analysis skipped: ", conditionMessage(e)); NULL })
}

build_html_report(desc, charts, meta, session$question, session$qid, report)
cat("STATS_OK", report, "\n")
