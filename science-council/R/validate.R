## validate.R — the deterministic half of the council.
## Model votes are opinions; everything in here is arithmetic. Where the two
## disagree, arithmetic wins (see apply_hard_evidence()).

suppressPackageStartupMessages({ library(jsonlite) })

VERDICTS <- c("support", "refute", "uncertain")

## ---- inter-rater agreement ---------------------------------------------------
## ratings: character matrix [claims x panelists], NA allowed (a panelist may fail).

#' Fleiss' kappa for nominal ratings with a fixed category set.
#' Only rows with >= 2 valid ratings contribute.
fleiss_kappa <- function(ratings, categories = VERDICTS) {
  ratings <- as.matrix(ratings)
  counts <- t(apply(ratings, 1, function(r) table(factor(r[!is.na(r)], levels = categories))))
  if (is.null(dim(counts))) counts <- matrix(counts, nrow = 1)
  n_i <- rowSums(counts)
  keep <- n_i >= 2
  if (sum(keep) < 2) return(NA_real_)
  counts <- counts[keep, , drop = FALSE]; n_i <- n_i[keep]
  ## P_i: observed agreement within claim i (pairwise, chance-corrected below)
  P_i <- (rowSums(counts^2) - n_i) / (n_i * (n_i - 1))
  P_bar <- mean(P_i)
  p_j <- colSums(counts) / sum(n_i)
  Pe <- sum(p_j^2)
  if (abs(1 - Pe) < 1e-12) return(NA_real_)
  (P_bar - Pe) / (1 - Pe)
}

#' Krippendorff's alpha (nominal). Handles missing ratings natively, which is
#' why it is reported alongside kappa: panelists do drop out mid-run.
krippendorff_alpha <- function(ratings, categories = VERDICTS) {
  ratings <- as.matrix(ratings)
  units <- lapply(seq_len(nrow(ratings)), function(i) {
    v <- ratings[i, ]; v[!is.na(v)]
  })
  units <- units[vapply(units, length, integer(1)) >= 2]
  if (length(units) < 2) return(NA_real_)
  ## coincidence matrix
  coin <- matrix(0, length(categories), length(categories),
                 dimnames = list(categories, categories))
  for (u in units) {
    m <- length(u)
    for (a in seq_along(u)) for (b in seq_along(u)) {
      if (a == b) next
      coin[u[a], u[b]] <- coin[u[a], u[b]] + 1 / (m - 1)
    }
  }
  n_total <- sum(coin)
  if (n_total == 0) return(NA_real_)
  Do <- 1 - sum(diag(coin)) / n_total          # observed disagreement (nominal metric)
  nc <- rowSums(coin)
  De <- 1 - sum(nc * (nc - 1)) / (n_total * (n_total - 1))
  if (abs(De) < 1e-12) return(NA_real_)
  1 - Do / De
}

#' Interpretation bands (Landis & Koch) — reported, never used as a gate.
kappa_label <- function(k) {
  if (is.na(k)) return("n/a")
  if (k < 0)    return("poor")
  if (k < 0.21) return("slight")
  if (k < 0.41) return("fair")
  if (k < 0.61) return("moderate")
  if (k < 0.81) return("substantial")
  "almost perfect"
}

## ---- consensus ---------------------------------------------------------------

#' Weighted consensus score in [-1, 1]: +1 = unanimous confident support.
#' Weight of a vote = panelist calibration weight x stated confidence.
#' `uncertain` contributes weight but no direction, so it dilutes rather than
#' cancels — an all-uncertain panel scores ~0 and cannot validate.
consensus_score <- function(verdicts, confidences, weights) {
  keep <- !is.na(verdicts) & verdicts %in% VERDICTS
  if (!any(keep)) return(0)
  v <- verdicts[keep]; cf <- confidences[keep]; w <- weights[keep]
  cf[is.na(cf)] <- 0.5; w[is.na(w)] <- 1
  dir <- ifelse(v == "support", 1, ifelse(v == "refute", -1, 0))
  wt <- w * cf
  if (sum(wt) == 0) return(0)
  sum(dir * wt) / sum(wt)
}

#' Final status for one claim. Deliberately conservative: a claim is only
#' `validated` when the panel agrees, nobody's refutation survived, and any
#' hard check (R code / citations) that ran did not fail.
#' Returns list(status, reason). The reason matters: a claim can be `contested`
#' with a unanimous vote (because its evidence evaporated), and printing that
#' verdict without saying why is actively misleading.
classify_claim <- function(score, threshold, r_passed = NA, n_refute = 0, n_valid = 0,
                           citations_verified = NA, citations_total = 0) {
  out <- function(s, r) list(status = s, reason = r)
  if (n_valid < 2)
    return(out("unresolved", sprintf("only %d valid vote(s) — panel too small to conclude", n_valid)))
  if (isFALSE(r_passed))
    return(out("refuted", "the deterministic R check failed — computation overrides the vote"))
  if (score <= -threshold)
    return(out("refuted", sprintf("panel consensus against (%.2f)", score)))
  ## A passing R check is proof, and proof outranks paperwork: a claim settled by
  ## computation stays validated even if a panelist stapled a junk citation to it
  ## (the fabrication is still recorded in `evidence`).
  if (isTRUE(r_passed) && score >= threshold)
    return(out("validated", sprintf("R check passed — settled by computation; consensus %.2f%s", score,
                                    if (!is.na(citations_verified) && citations_total > 0 && citations_verified == 0)
                                      " (note: its citations do not resolve, but the check does not need them)" else "")))
  ## Otherwise, unanimous support means nothing if the evidence for it does not exist.
  if (!is.na(citations_verified) && citations_total > 0 && citations_verified == 0)
    return(out("contested", sprintf("consensus was %.2f, but none of its %d citation(s) resolve to a real, on-topic paper — the supporting evidence is unverified",
                                    score, citations_total)))
  if (score >= threshold)
    return(out("validated", sprintf("consensus %.2f >= %.2f", score, threshold)))
  if (abs(score) < threshold && n_refute > 0)
    return(out("contested", sprintf("panel split — consensus %.2f with %d refutation(s) standing", score, n_refute)))
  out("unresolved", sprintf("consensus %.2f below the %.2f threshold, panel collectively uncertain", score, threshold))
}

#' Hard evidence overrides the vote. Returns the claim row, mutated.
apply_hard_evidence <- function(claim, r_passed) {
  if (isFALSE(r_passed)) { claim$status <- "refuted"; claim$override <- "R check failed" }
  if (isTRUE(r_passed) && claim$status == "unresolved") {
    claim$status <- "validated"; claim$override <- "R check passed"
  }
  claim
}

## Brier score for one categorical vote against the settled outcome.
brier_of_vote <- function(verdict, confidence, final_status) {
  if (is.na(verdict) || !final_status %in% c("validated", "refuted")) return(NA_real_)
  truth <- if (final_status == "validated") "support" else "refute"
  p <- if (is.na(confidence)) 0.5 else confidence
  if (verdict == "uncertain") return(0.25)
  if (verdict == truth) (1 - p)^2 else p^2
}

## ---- sandboxed R execution ---------------------------------------------------
## LLM-authored code is executed in a separate process with a timeout and a
## static deny-list. This is a guardrail against accidents, NOT a security
## sandbox — never point this at untrusted input. Disable with
## SCICOUNCIL_ALLOW_RCODE=false.

RCODE_DENY <- c("system\\s*\\(", "system2\\s*\\(", "shell\\s*\\(", "unlink\\s*\\(",
                "file\\.remove", "file\\.rename", "file\\.create", "writeLines\\s*\\(",
                "write\\.csv", "write\\.table", "saveRDS", "save\\s*\\(",
                "download\\.file", "url\\s*\\(", "socketConnection", "curl",
                "install\\.packages", "Sys\\.setenv", "setwd\\s*\\(", "q\\s*\\(",
                "quit\\s*\\(", "\\.Internal", "eval\\s*\\(\\s*parse", "source\\s*\\(",
                "reticulate", "processx", "sys::")

rcode_screen <- function(code) {
  hits <- RCODE_DENY[vapply(RCODE_DENY, function(p) grepl(p, code), logical(1))]
  if (length(hits)) list(ok = FALSE, reason = paste("blocked pattern(s):", paste(hits, collapse = ", ")))
  else list(ok = TRUE)
}

#' Which Rscript runs the checks? Delegates to the Claude Science integration
#' (R/science.R) which routes to the r-stats-methodologist specialist env —
#' metafor/meta/metadat, i.e. real data instead of a simulation of the answer.
#' Falls back to system R when Claude Science is absent.
check_rscript <- function() {
  if (exists("science_rscript", mode = "function")) return(science_rscript("check"))
  forced <- Sys.getenv("SCICOUNCIL_RSCRIPT", "")
  if (nzchar(forced)) return(forced)
  sci <- path.expand("~/.claude-science/conda/envs/r-stats-methodologist/bin/Rscript")
  if (file.exists(sci)) return(sci)
  "Rscript"
}

#' What the check author is allowed to assume is installed. Probed once, cached —
#' the answer changes what a check can honestly do, so the prompt must reflect it.
.check_pkgs_cache <- NULL
check_packages <- function() {
  if (!is.null(.check_pkgs_cache)) return(.check_pkgs_cache)
  want <- c("metafor", "meta", "metadat", "lme4", "survival", "boot", "MASS")
  out <- suppressWarnings(tryCatch(
    system2(check_rscript(), c("--vanilla", "-e",
      shQuote(sprintf('cat(paste(intersect(c(%s), rownames(installed.packages())), collapse=","))',
                      paste(sprintf('"%s"', want), collapse = ",")))),
      stdout = TRUE, stderr = FALSE, timeout = 60), error = function(e) ""))
  ## a conda R invoked from inside system R warns about the inherited R_HOME on
  ## stdout — strip it, or it ends up glued to the first package name
  out <- out[!grepl("^WARNING: ignoring environment value of R_HOME", out)]
  out <- sub("^WARNING: ignoring environment value of R_HOME", "", out)
  pkgs <- trimws(paste(out, collapse = ""))
  .check_pkgs_cache <<- if (nzchar(pkgs)) strsplit(pkgs, ",")[[1]] else character()
  .check_pkgs_cache
}

#' Prompt fragment describing the check environment.
check_env_blurb <- function() {
  p <- check_packages()
  if (!length(p)) return("Base R and stats only — no extra packages are available.")
  s <- paste0("Base R, plus these packages are installed and may be used: ",
              paste(p, collapse = ", "), ".")
  if ("metadat" %in% p)
    s <- paste0(s, "\n  IMPORTANT: `metadat` ships REAL published meta-analytic datasets (see data(package='metadat')), ",
                "and `metafor` fits random-effects models via rma(). If a real dataset genuinely matches the claim, ",
                "USE IT — that is a real check. If none matches, do NOT substitute simulated data and claim a result; set passed = NA.")
  s
}

#' Run a claim's check. The code must end by assigning a list to `result`
#' containing at least `passed` (logical) and `detail` (character).
## Defensive unwrap. Observed live: a bridge-authored check arrived here still
## JSON-encoded ({"code": "...", "explanation": "..."} with literal \n and \"),
## so every check died with "unexpected ','" before running. Rather than trust
## every upstream path to hand us clean R, detect that shape and unwrap it.
unwrap_code <- function(code) {
  if (!is.character(code) || length(code) != 1) return(code)
  s <- trimws(code)
  if (!startsWith(s, "{")) return(code)
  o <- tryCatch(fromJSON(s, simplifyVector = TRUE), error = function(e) NULL)
  if (is.list(o) && is.character(o$code) && length(o$code) == 1) return(o$code)
  code
}

run_r_check <- function(code, data_files = NULL, timeout = 60) {
  if (!identical(tolower(Sys.getenv("SCICOUNCIL_ALLOW_RCODE", "true")), "true"))
    return(list(passed = NA, error = "R checks disabled (SCICOUNCIL_ALLOW_RCODE=false)", runtime = 0))
  code <- unwrap_code(code)
  scr <- rcode_screen(code)
  if (!scr$ok) return(list(passed = NA, error = scr$reason, runtime = 0))

  f <- tempfile(fileext = ".R"); out <- tempfile(fileext = ".json")
  on.exit(unlink(c(f, out)), add = TRUE)
  wrapper <- sprintf('
result <- NULL
.sc_out <- "%s"
%s
if (is.null(result)) result <- list(passed = NA, detail = "code did not assign `result`")
if (is.null(result$passed)) result$passed <- NA
jsonlite::write_json(result, .sc_out, auto_unbox = TRUE, force = TRUE, null = "null")
', out, code)
  writeLines(wrapper, f)
  t0 <- Sys.time()
  err <- suppressWarnings(system2(check_rscript(), c("--vanilla", f),
                                  stdout = TRUE, stderr = TRUE, timeout = timeout))
  rt <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  status <- attr(err, "status") %||% 0
  if (!file.exists(out))
    return(list(passed = NA, error = paste0("no result (exit ", status, "): ",
                                            substr(paste(err, collapse = " "), 1, 300)), runtime = rt))
  res <- tryCatch(fromJSON(out, simplifyVector = TRUE), error = function(e) NULL)
  if (is.null(res)) return(list(passed = NA, error = "unparseable result", runtime = rt))
  list(passed = if (is.null(res$passed) || is.na(res$passed)) NA else as.logical(res$passed)[1],
       result = res, error = NULL, runtime = rt)
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a
