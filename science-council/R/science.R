## science.R — Claude for Life Sciences integration.
##
## Two things this brings to the council:
##   1. SPECIALISTS — the conda environments under ~/.claude-science ship
##      domain R/Python stacks. The R stats specialist (r-stats-methodologist)
##      carries metafor/meta/ggplot2/rmarkdown, so both the deterministic checks
##      and the statistics-generation layer run there instead of bare system R.
##   2. CONNECTORS — the authorized connectors (BioRender, Google Drive, Gmail,
##      Microsoft 365, ...) are reachable by Claude's bridge seat as real data
##      sources. This module detects and surfaces them; the bridge protocol tells
##      the serving Claude to actually use them for grounding.

suppressPackageStartupMessages({ library(jsonlite) })

science_home <- function() {
  h <- Sys.getenv("CLAUDE_SCIENCE_HOME", path.expand("~/.claude-science"))
  if (dir.exists(h)) h else NA_character_
}

science_available <- function() !is.na(science_home())

## conda envs shipped by the install
science_envs <- function() {
  h <- science_home(); if (is.na(h)) return(character())
  d <- file.path(h, "conda", "envs")
  if (!dir.exists(d)) return(character())
  list.dirs(d, recursive = FALSE, full.names = FALSE)
}

## Does an env carry a usable interpreter?
##
## Existence is not enough. Five of the eleven envs on this install ship a `python`
## or `Rscript` file that is present but not executable — a half-provisioned env.
## `file.exists()` calls those usable and routes work to them, which fails only at
## the point of use with a bare "permission denied". Test the execute bit.
science_env_bin <- function(env, prog) {
  h <- science_home(); if (is.na(h)) return(NA_character_)
  p <- file.path(h, "conda", "envs", env, "bin", prog)
  if (file.exists(p) && file.access(p, 1L) == 0L) p else NA_character_
}

science_env_rscript <- function(env) science_env_bin(env, "Rscript")
science_env_python  <- function(env) science_env_bin(env, "python")

## Role -> specialist env. Surveyed 2026-07-18; only envs that actually carry an
## interpreter are listed. An earlier version of this table mapped `methods` to
## methods-statistician, which turned out to contain neither R nor python — a dead
## route that would have silently fallen back. Verify before adding a row:
##   ./bin/council science   lists every env and marks which are usable.
SCIENCE_ROLE_ENV <- c(          # R roles
  check   = "r-stats-methodologist",
  stats   = "r-stats-methodologist",
  meta    = "r-stats-methodologist",
  default = "r-stats-methodologist")

SCIENCE_PY_ROLE_ENV <- c(       # python roles
  crosscheck = "chirality-data",         # scipy, statsmodels, patsy — independent stats engine
  biolit     = "case-report-assistant",  # biopython (Entrez), python-docx, reportlab, lxml
  figures    = "figs")                   # matplotlib, pandas

science_python <- function(role = "crosscheck") {
  if (!science_available()) return(NA_character_)
  env <- SCIENCE_PY_ROLE_ENV[[role]]
  if (is.null(env)) return(NA_character_)
  science_env_python(env)
}

## Independent re-estimation of an ANCOVA in statsmodels.
##
## The R checks and the statistics layer both run in one language, so a mistake in
## the model specification would reproduce itself rather than show up. Re-fitting the
## same model in a different implementation catches that class of error. Verified
## 2026-07-18: statsmodels reproduces the R group-effect p-values for this project to
## four decimals (HbA1c 0.5137, weight 0.2008).
##
## Returns list(ok, p_value, engine, note). Never throws — a missing env yields ok=FALSE.
science_crosscheck_ancova <- function(xlsx, sheet = 1, dep_cols, cov_col, group_col,
                                      drop_labels = c("Min", "Max", "Átlag")) {
  py <- science_python("crosscheck")
  if (is.na(py)) return(list(ok = FALSE, note = "crosscheck env unavailable"))
  script <- tempfile(fileext = ".py"); on.exit(unlink(script), add = TRUE)
  writeLines(sprintf('
import json, pandas as pd, statsmodels.formula.api as smf, statsmodels.api as sm
d = pd.read_excel(%s, sheet_name=%d)
d = d[~d.iloc[:,0].isin(%s)].copy()
d["grp"] = d.iloc[:,%d]
d["dep"] = d.iloc[:,%d].values - d.iloc[:,%d].values
d["cov"] = d.iloc[:,%d].values
m = smf.ols("dep ~ cov + C(grp)", data=d).fit()
a = sm.stats.anova_lm(m, typ=3)
print(json.dumps({"p": float(a.loc["C(grp)","PR(>F)"]), "n": int(len(d))}))
', shQuote(xlsx), sheet - 1, jsonlite::toJSON(drop_labels), group_col - 1,
   dep_cols[1] - 1, dep_cols[2] - 1, cov_col - 1), script)
  out <- suppressWarnings(system2(py, script, stdout = TRUE, stderr = FALSE, timeout = 120))
  j <- tryCatch(jsonlite::fromJSON(tail(out, 1)), error = function(e) NULL)
  if (is.null(j)) return(list(ok = FALSE, note = "statsmodels produced no result"))
  list(ok = TRUE, p_value = j$p, n = j$n, engine = "statsmodels (chirality-data)")
}

science_rscript <- function(role = "default") {
  forced <- Sys.getenv("SCICOUNCIL_RSCRIPT", "")
  if (nzchar(forced)) return(forced)
  if (science_available()) {
    env <- SCIENCE_ROLE_ENV[[role]] %||% SCIENCE_ROLE_ENV[["default"]]
    rs <- science_env_rscript(env)
    if (!is.na(rs)) return(rs)
    rs <- science_env_rscript(SCIENCE_ROLE_ENV[["default"]])
    if (!is.na(rs)) return(rs)
  }
  "Rscript"
}

## Authorized connectors, from the install's directory cache.
science_connectors <- function(only_authorized = TRUE) {
  h <- science_home(); if (is.na(h)) return(NULL)
  f <- file.path(h, "mcp", "directory-cache.json")
  if (!file.exists(f)) return(NULL)
  d <- tryCatch(fromJSON(f, simplifyVector = FALSE), error = function(e) NULL)
  if (is.null(d) || is.null(d$connectors)) return(NULL)
  rows <- lapply(d$connectors, function(c1) data.frame(
    name = c1$name %||% NA, auth = c1$authState %||% NA,
    url = c1$backendUrl %||% NA, stringsAsFactors = FALSE))
  df <- do.call(rbind, rows)
  if (only_authorized) df <- df[!is.na(df$auth) & df$auth == "authorized", , drop = FALSE]
  df
}

## Is the Claude Science daemon up? (best-effort; never throws)
science_daemon <- function() {
  h <- science_home(); if (is.na(h)) return(list(running = FALSE))
  bin <- file.path(h, "bin", "claude-science")
  if (!file.exists(bin)) return(list(running = FALSE))
  out <- suppressWarnings(tryCatch(
    system2(bin, "status", stdout = TRUE, stderr = FALSE, timeout = 15),
    error = function(e) ""))
  j <- tryCatch(fromJSON(paste(out, collapse = "\n")), error = function(e) NULL)
  if (is.null(j)) list(running = FALSE) else j
}

## A short briefing appended to Claude's bridge system prompt so the serving
## session knows which real data sources it may use to ground its answers.
science_connector_briefing <- function() {
  cx <- science_connectors(TRUE)
  if (is.null(cx) || !nrow(cx)) return("")
  names <- paste(cx$name, collapse = ", ")
  paste0(
    "\n\nYou are running with Claude for Life Sciences connectors AUTHORIZED: ", names, ".\n",
    "You alone among the panelists can reach real data. Use them to ground your claims:\n",
    "  - Google Drive / Microsoft 365: the user's own datasets, spreadsheets and papers — ",
    "search for data that directly bears on the question rather than reasoning from memory.\n",
    "  - BioRender: figures and pathway diagrams when a mechanistic claim needs one.\n",
    "  - Gmail / Calendar: only if the question explicitly concerns the user's correspondence or schedule.\n",
    "Prefer a real document you can cite over an unsourced assertion. Never fabricate a source; ",
    "every citation you give is machine-verified against Crossref and Europe PMC.")
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a) == 1 && is.na(a))) b else a
