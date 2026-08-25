## specialists.R — the Claude for Life Sciences specialist environments, wired to
## actual capabilities rather than to names.
##
## science.R resolves which env to use; this file is what those envs are FOR.
## Each function degrades to list(ok = FALSE, note = ...) when its env is absent,
## so nothing here is load-bearing for a plain install.
##
## Deliberately not wired, and why:
##   compute-provider-modal  — remote compute, needs a Modal account and token. It
##                             would be a route that fails at the point of use for
##                             almost everyone, which is the mistake this file exists
##                             to avoid. Detected and reported, never routed to.
##   claude-science-mcp      — the daemon's own MCP server, not a council specialist.
##   methods-statistician, docconv, langcheck, python, r
##                           — half-provisioned on a stock install: the interpreter
##                             file exists but is not executable. See science.R.

suppressPackageStartupMessages({ library(jsonlite) })

## Run a python snippet in a specialist env, parse its last stdout line as JSON.
## Never throws: a missing env or a broken script becomes ok = FALSE.
specialist_py <- function(role, code, timeout = 120) {
  py <- science_python(role)
  if (is.na(py)) return(list(ok = FALSE, note = sprintf("specialist env for '%s' unavailable", role)))
  f <- tempfile(fileext = ".py"); on.exit(unlink(f), add = TRUE)
  writeLines(code, f)
  out <- suppressWarnings(system2(py, f, stdout = TRUE, stderr = TRUE, timeout = timeout))
  j <- NULL
  for (ln in rev(out)) { j <- tryCatch(fromJSON(ln, simplifyVector = TRUE), error = function(e) NULL)
                         if (!is.null(j)) break }
  if (is.null(j)) return(list(ok = FALSE,
    note = paste0("no parseable result: ", substr(paste(tail(out, 4), collapse = " "), 1, 200))))
  c(list(ok = TRUE), j)
}

## ---------------------------------------------------------------- biolit
## PubMed via biopython/Entrez. This is not a duplicate of the Crossref and
## Europe PMC lookups in evidence.R: those answer "does this citation exist and is
## it on topic". Entrez additionally returns MeSH terms and publication types,
## which answer a question the council could not otherwise ask — *what kind of
## evidence is this*. A claim carried by a randomised trial and the same claim
## carried by a case report are not equally supported, and until now the harness
## treated them identically.

ENTREZ_HEADER <- '
from Bio import Entrez, Medline
import json, os
Entrez.email = os.environ.get("SCICOUNCIL_CONTACT", "anonymous@example.org")
'

## Evidence hierarchy, strongest first. Matched against PubMed publication types.
EVIDENCE_LEVELS <- list(
  list(level = 1L, label = "systematic review / meta-analysis",
       pt = c("Meta-Analysis", "Systematic Review")),
  list(level = 2L, label = "randomised controlled trial",
       pt = c("Randomized Controlled Trial", "Equivalence Trial")),
  list(level = 3L, label = "non-randomised or early-phase trial",
       pt = c("Clinical Trial", "Clinical Trial, Phase I", "Clinical Trial, Phase II",
              "Clinical Trial, Phase III", "Clinical Trial, Phase IV", "Controlled Clinical Trial")),
  list(level = 4L, label = "observational study",
       pt = c("Observational Study", "Comparative Study", "Multicenter Study")),
  list(level = 5L, label = "case report / series",
       pt = c("Case Reports")),
  list(level = 6L, label = "review or narrative",
       pt = c("Review", "Editorial", "Comment", "Letter")))

classify_evidence <- function(pubtypes) {
  if (is.null(pubtypes) || !length(pubtypes)) return(list(level = NA_integer_, label = "unclassified"))
  for (e in EVIDENCE_LEVELS) if (any(pubtypes %in% e$pt)) return(list(level = e$level, label = e$label))
  list(level = NA_integer_, label = "unclassified")
}

#' Look up PMIDs and return study design, MeSH terms and year for each.
#' @return list(ok, records = data.frame(pmid, year, title, evidence_level, evidence_label, mesh))
specialist_evidence_level <- function(pmids) {
  pmids <- unique(pmids[!is.na(pmids) & nzchar(pmids)])
  if (!length(pmids)) return(list(ok = FALSE, note = "no PMIDs given"))
  code <- paste0(ENTREZ_HEADER, sprintf('
ids = %s
recs = []
try:
    h = Entrez.efetch(db="pubmed", id=",".join(ids), rettype="medline", retmode="text")
    for r in Medline.parse(h):
        if not r.get("PMID"): continue
        recs.append({"pmid": r.get("PMID"), "year": (r.get("DP") or "")[:4],
                     "title": (r.get("TI") or "")[:300],
                     "pt": r.get("PT") or [], "mesh": (r.get("MH") or [])[:8]})
except Exception as e:
    print(json.dumps({"error": f"{type(e).__name__}: {e}"})); raise SystemExit
print(json.dumps({"records": recs}))
', toJSON(pmids)))
  r <- specialist_py("biolit", code)
  if (!isTRUE(r$ok)) return(r)
  if (!is.null(r$error)) return(list(ok = FALSE, note = r$error))
  recs <- r$records
  if (is.null(recs) || !length(recs)) return(list(ok = TRUE, records = NULL, note = "no records returned"))
  rows <- lapply(seq_len(nrow(recs)), function(i) {
    pt <- recs$pt[[i]]; ev <- classify_evidence(pt)
    data.frame(pmid = recs$pmid[i], year = recs$year[i], title = recs$title[i],
               evidence_level = ev$level, evidence_label = ev$label,
               pubtypes = paste(pt, collapse = "; "),
               mesh = paste(recs$mesh[[i]], collapse = "; "), stringsAsFactors = FALSE)
  })
  list(ok = TRUE, records = do.call(rbind, rows))
}

#' Search PubMed. `pubtype` optionally restricts to a design, e.g.
#' "Randomized Controlled Trial" — the filter Crossref cannot express.
specialist_pubmed_search <- function(query, limit = 10, pubtype = NULL) {
  term <- if (is.null(pubtype)) query else sprintf('(%s) AND "%s"[pt]', query, pubtype)
  code <- paste0(ENTREZ_HEADER, sprintf('
try:
    ids = Entrez.read(Entrez.esearch(db="pubmed", term=%s, retmax=%d, sort="relevance"))["IdList"]
except Exception as e:
    print(json.dumps({"error": f"{type(e).__name__}: {e}"})); raise SystemExit
print(json.dumps({"pmids": ids}))
', toJSON(term, auto_unbox = TRUE), as.integer(limit)))
  r <- specialist_py("biolit", code)
  if (!isTRUE(r$ok) || !is.null(r$error)) return(list(ok = FALSE, note = r$note %||% r$error))
  list(ok = TRUE, pmids = unlist(r$pmids))
}

## ---------------------------------------------------------------- crosscheck
## Independent re-estimation in statsmodels. The checks and the statistics layer
## both run in R, so a mis-specified model reproduces itself rather than showing
## up; a second implementation catches that class of error. Verified 2026-07-18:
## reproduces this project's ANCOVA p-values to four decimals.
## Implementation lives in science.R as science_crosscheck_ancova().

## ---------------------------------------------------------------- figures
## matplotlib fallback. ggplot2 in r-stats-methodologist is the better renderer and
## is what stats.R uses; this exists only for installs with python but no R, so a
## session can still produce a consensus chart instead of none.
specialist_figure_consensus <- function(session_json, outfile) {
  code <- sprintf('
import json, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
s = json.load(open(%s))
cl = s.get("claims") or []
if not cl:
    print(json.dumps({"error": "no claims in session"})); raise SystemExit
cl = sorted(cl, key=lambda c: c.get("consensus_score") or 0)
lab = [(c.get("claim") or "")[:58] + ("..." if len(c.get("claim") or "") > 58 else "") for c in cl]
val = [c.get("consensus_score") or 0 for c in cl]
col = {"validated": "#1a9850", "contested": "#f0a202", "refuted": "#d73027"}
bar = [col.get(c.get("status"), "#9e9e9e") for c in cl]
fig, ax = plt.subplots(figsize=(9, max(2.5, 0.42 * len(cl) + 1.2)))
ax.barh(range(len(cl)), val, color=bar)
ax.set_yticks(range(len(cl))); ax.set_yticklabels(lab, fontsize=8)
ax.axvline(0, color="grey", lw=0.8); ax.set_xlim(-1, 1)
ax.set_xlabel("consensus score"); ax.set_title("Weighted consensus per claim")
fig.tight_layout(); fig.savefig(%s, dpi=130)
print(json.dumps({"figure": %s, "n": len(cl)}))
', toJSON(session_json, auto_unbox = TRUE), toJSON(outfile, auto_unbox = TRUE),
   toJSON(outfile, auto_unbox = TRUE))
  specialist_py("figures", code)
}

## ---------------------------------------------------------------- status
#' What each specialist env can actually do on this machine, tested rather than assumed.
specialist_status <- function(probe = FALSE) {
  roles <- c(SCIENCE_ROLE_ENV, SCIENCE_PY_ROLE_ENV)
  rows <- lapply(names(roles), function(role) {
    env <- roles[[role]]
    is_r <- role %in% names(SCIENCE_ROLE_ENV)
    bin <- if (is_r) science_env_rscript(env) else science_env_python(env)
    data.frame(role = role, env = env, kind = if (is_r) "R" else "python",
               available = !is.na(bin), stringsAsFactors = FALSE)
  })
  df <- do.call(rbind, rows)
  df <- df[!duplicated(paste(df$role, df$env)), ]
  if (probe) {
    df$probe <- vapply(seq_len(nrow(df)), function(i) {
      if (!df$available[i]) return("unavailable")
      switch(df$role[i],
        biolit  = { r <- specialist_pubmed_search("obesity", 1)
                    if (isTRUE(r$ok) && length(r$pmids)) "PubMed reachable" else "Entrez unreachable" },
        figures = { r <- specialist_py("figures", 'import json, matplotlib; print(json.dumps({"v": matplotlib.__version__}))')
                    if (isTRUE(r$ok)) paste("matplotlib", r$v) else "import failed" },
        crosscheck = { r <- specialist_py("crosscheck", 'import json, statsmodels; print(json.dumps({"v": statsmodels.__version__}))')
                       if (isTRUE(r$ok)) paste("statsmodels", r$v) else "import failed" },
        "ok")
    }, character(1))
  }
  df
}
