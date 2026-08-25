## ledger.R — every verifiable fact, and what happened when it was verified.
##
## The harness checks a great many things and, until now, reported mostly the
## conclusions. A user reading "16 of 21 claims validated" cannot see which
## citation failed to resolve, which R check never ran, or which number in the
## manuscript was recomputed and which was taken on trust. Those are exactly the
## things a reader needs in order to disagree with the output, and a system that
## makes disagreement difficult is not being transparent, whatever it prints.
##
## The ledger is one table of every fact the harness touched, with three columns
## that matter: how it was checked, what the check returned, and where it came from.
##
## THE PART THAT IS EASY TO GET WRONG
##
## A ledger listing only what verified would be the same curation failure the petal
## analysis exposed: every entry true, the whole misleading. So unverifiable and
## unchecked facts are first-class rows, not omissions. An empty "could not check"
## section is a claim about the material and has to be earned; in practice it is
## almost never empty, and when it is, that is worth looking at twice.

suppressPackageStartupMessages({ library(DBI); library(jsonlite) })

## Both reviewers, working from different mandates, raised the same blocking defect
## in the first version of this table: `unverified` collapsed two different things.
## A check that ran and could not settle the question tells you about the question.
## A check that never ran tells you about the harness. Reporting them under one
## heading let twelve rows from a transport bug sit beside genuinely undecidable
## claims, and a reader had no way to tell them apart. They are separate statuses now.
LEDGER_STATUS <- c(
  refuted      = "checked and failed",
  inconclusive = "checked, and the check could not settle it",
  not_run      = "a method exists but did not run — this is a gap in the harness, not in the evidence",
  no_method    = "nothing here can be checked by machine",
  asserted     = "asserted by a model with no independent support",
  verified     = "checked and held")

## Collect every verifiable fact from one session.
ledger_build <- function(con, qid) {
  rows <- list()
  add <- function(fact, how, status, result, source, detail = NA_character_) {
    rows[[length(rows) + 1]] <<- data.frame(
      fact = substr(fact, 1, 300), how = how, status = status,
      result = substr(result %||% "", 1, 300), source = source,
      detail = substr(detail %||% "", 1, 400), stringsAsFactors = FALSE)
  }

  cl <- tryCatch(dbGetQuery(con, "SELECT * FROM claims WHERE question_id = ?", params = list(qid)),
                 error = function(e) NULL)
  if (!is.null(cl) && nrow(cl)) for (i in seq_len(nrow(cl))) {
    r <- cl[i, ]
    add(r$claim, "panel deliberation (weighted consensus)",
        if (r$status == "validated") "verified" else if (r$status == "refuted") "refuted" else "asserted",
        sprintf("%s (score %+.2f, +%d/-%d/?%d)", toupper(r$status), r$consensus_score,
                r$n_support, r$n_refute, r$n_uncertain),
        "claims", r$status_reason)
  }

  ## citations: two independent gates, reported separately because they fail apart
  ev <- tryCatch(dbGetQuery(con,
    "SELECT * FROM evidence WHERE claim_id IN (SELECT id FROM claims WHERE question_id = ?)",
    params = list(qid)), error = function(e) NULL)
  if (!is.null(ev) && nrow(ev)) for (i in seq_len(nrow(ev))) {
    r <- ev[i, ]
    ## A refuted citation matters differently depending on whether its claim keeps
    ## other support. The reviewer asked for this and was right to: "two DOIs do not
    ## resolve" is not actionable without knowing what it left unsupported.
    left <- ""
    if (!isTRUE(r$verified)) {
      sib <- ev[ev$claim_id == r$claim_id & isTRUE(ev$verified), , drop = FALSE]
      n_ok <- sum(ev$claim_id == r$claim_id & ev$verified %in% TRUE &
                  (is.na(ev$relevant) | ev$relevant %in% TRUE), na.rm = TRUE)
      left <- if (n_ok > 0) sprintf(" Its claim retains %d verified on-topic citation(s).", n_ok)
              else " Its claim is left with NO verified on-topic citation."
    }
    add(sprintf("Citation: %s  [for claim %s]", r$citation, r$claim_id),
        "Crossref / Europe PMC identifier lookup",
        if (isTRUE(r$verified)) "verified" else "refuted",
        paste0(if (isTRUE(r$verified)) sprintf("resolves to: %s", r$resolved_title %||% "(untitled)")
               else "does not resolve — treat as fabricated", left),
        "evidence", r$verify_note)
    if (isTRUE(r$verified))
      add(sprintf("Citation is on-topic for its claim: %s", substr(r$citation, 1, 60)),
          "topical relevance judgement",
          if (isTRUE(r$relevant)) "verified" else if (identical(r$relevant, FALSE)) "refuted" else "not_run",
          if (isTRUE(r$relevant)) "on topic" else if (identical(r$relevant, FALSE)) "resolves but is about something else"
          else "not judged", "evidence", r$relevance_note)
  }

  rc <- tryCatch(dbGetQuery(con,
    "SELECT * FROM r_checks WHERE claim_id IN (SELECT id FROM claims WHERE question_id = ?)",
    params = list(qid)), error = function(e) NULL)
  if (!is.null(rc) && nrow(rc)) for (i in seq_len(nrow(rc))) {
    r <- rc[i, ]
    det <- tryCatch(fromJSON(r$result)$detail, error = function(e) NULL)
    add(sprintf("R check on claim %s", r$claim_id), sprintf("R code executed in a subprocess (author: %s)", r$author),
        if (isTRUE(r$passed)) "verified" else if (identical(r$passed, FALSE)) "refuted"
        else if (grepl("could not run|unparseable|no result|exit", r$error %||% "")) "not_run" else "inconclusive",
        if (is.na(r$passed)) sprintf("%s: %s",
          if (grepl("could not run|unparseable|no result|exit", r$error %||% "")) "DID NOT RUN" else "ran, inconclusive",
          substr(r$error %||% "no result", 1, 120))
        else paste(if (isTRUE(r$passed)) "passed:" else "FAILED:", paste(det, collapse = " ")),
        "r_checks", substr(r$code, 1, 300))
  }

  pt <- tryCatch(dbGetQuery(con, "SELECT * FROM petals WHERE question_id = ?", params = list(qid)),
                 error = function(e) NULL)
  if (!is.null(pt) && nrow(pt)) for (i in seq_len(nrow(pt))) {
    r <- pt[i, ]
    add(sprintf("Petal (%s): %s", r$kind, substr(r$text, 1, 120)), "anchor check",
        if (isTRUE(r$verified)) "verified" else if (identical(r$verified, FALSE)) "refuted" else "not_run",
        r$verify_note %||% "", "petals", r$anchor)
  }

  if (!length(rows)) return(NULL)
  do.call(rbind, rows)
}

#' Render the ledger. Failures and gaps come first: a reader scanning the top of a
#' table should meet what did not hold, not what did.
ledger_render <- function(led, qid = "", title = "Verifiable facts") {
  if (is.null(led) || !nrow(led)) return("*(nothing to report)*")
  ord <- names(LEDGER_STATUS)
  led$status <- factor(led$status, levels = ord)
  led <- led[order(led$status), ]
  n <- table(led$status)

  L <- c(sprintf("# %s", title), "",
         if (nzchar(qid)) sprintf("Session `%s`.", qid) else "",
         "Every fact below was produced or touched by the harness. The middle column",
         "says how it was checked and the third what the check returned — including",
         "the checks that failed and the ones no method could settle. A ledger showing",
         "only what held would be as misleading as any other curated set.", "",
         "| | count | meaning |", "|---|---:|---|")
  for (s in ord) if ((n[[s]] %||% 0) > 0)
    L <- c(L, sprintf("| **%s** | %d | %s |", s, n[[s]], LEDGER_STATUS[[s]]))
  L <- c(L, "")

  for (s in ord) {
    sub <- led[led$status == s, , drop = FALSE]
    if (!nrow(sub)) next
    L <- c(L, sprintf("## %s — %s (%d)", s, LEDGER_STATUS[[s]], nrow(sub)), "")
    for (i in seq_len(nrow(sub))) {
      r <- sub[i, ]
      L <- c(L, sprintf("**%s**", r$fact), "",
             sprintf("- *Checked by:* %s", r$how),
             sprintf("- *Result:* %s", r$result),
             sprintf("- *From:* `%s`", r$source))
      if (!is.na(r$detail) && nzchar(r$detail)) L <- c(L, sprintf("- *Detail:* %s", r$detail))
      L <- c(L, "")
    }
  }
  paste(L, collapse = "\n")
}

#' One-line summary for the console.
ledger_summary <- function(led) {
  if (is.null(led) || !nrow(led)) return("no facts recorded")
  n <- table(factor(led$status, levels = names(LEDGER_STATUS)))
  paste(sprintf("%d %s", as.integer(n), names(n))[as.integer(n) > 0], collapse = ", ")
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a) == 1 && is.na(a))) b else a
