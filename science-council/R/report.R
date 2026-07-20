## report.R — human-readable account of a council session.
## The report exists so a reader can disagree with the council: every dissent,
## every failed check and every unresolved citation is on the page, not just
## the verdict.

suppressPackageStartupMessages({ library(DBI); library(jsonlite) })

md_escape <- function(x) gsub("\\|", "\\\\|", x %||% "")

report_question <- function(con, qid, out_file = NULL) {
  q <- dbGetQuery(con, "SELECT * FROM questions WHERE id = ?", params = list(qid))
  if (!nrow(q)) stop("No such question: ", qid, call. = FALSE)
  cl <- dbGetQuery(con, "SELECT * FROM claims WHERE question_id = ? ORDER BY consensus_score DESC",
                   params = list(qid))
  L <- c()
  add <- function(...) L <<- c(L, ...)

  add(sprintf("# Science Council — session report"), "",
      sprintf("**Question:** %s", q$question[1]), "",
      sprintf("- Panel: `%s`", q$panel[1]),
      sprintf("- Rounds: %d", q$rounds[1]),
      sprintf("- Session: `%s` (%s)", qid, q$created_at[1]), "")

  if (nrow(cl)) {
    k <- cl$agreement_kappa[1]; a <- cl$krippendorff_alpha[1]
    add("## Agreement", "",
        sprintf("- Fleiss' kappa: %s", ifelse(is.na(k), "n/a", sprintf("%.3f", k))),
        sprintf("- Krippendorff's alpha: %s", ifelse(is.na(a), "n/a", sprintf("%.3f", a))),
        "", "Low agreement is not a failure — it means the question is genuinely open,",
        "and the claims below marked CONTESTED are where the disagreement lives.", "")
  }

  add("## Claims", "",
      "| Status | Score | +/-/? | R check | Citations | Claim |",
      "|---|---:|---|---|---|---|")
  for (i in seq_len(nrow(cl))) {
    r <- cl[i, ]
    rc <- if (is.na(r$r_check_passed)) "—" else if (r$r_check_passed) "pass" else "**FAIL**"
    ci <- if (r$citations_total == 0) "—" else sprintf("%s/%d", ifelse(is.na(r$citations_verified), "?", r$citations_verified), r$citations_total)
    add(sprintf("| **%s** | %+.2f | +%d/-%d/?%d | %s | %s | %s |",
                toupper(r$status), r$consensus_score, r$n_support, r$n_refute,
                r$n_uncertain, rc, ci, md_escape(r$claim)))
  }
  add("")

  ## the point of the exercise: show the argument, not just the outcome
  for (i in seq_len(nrow(cl))) {
    r <- cl[i, ]
    add(sprintf("### [%s] %s", toupper(r$status), md_escape(r$claim)), "",
        sprintf("Consensus %+.2f · type `%s` · proposed by `%s`", r$consensus_score, r$claim_type, r$proposed_by), "",
        if (!is.na(r$status_reason)) sprintf("**Why %s:** %s", r$status, md_escape(r$status_reason)) else "", "")
    vd <- dbGetQuery(con, "SELECT * FROM verdicts WHERE claim_id = ? ORDER BY round DESC, panelist",
                     params = list(r$id))
    if (nrow(vd)) {
      add("| Panelist | Round | Verdict | Conf. | Rationale |", "|---|---|---|---:|---|")
      for (j in seq_len(nrow(vd)))
        add(sprintf("| %s | %d | %s | %.2f | %s |", vd$panelist[j], vd$round[j], vd$verdict[j],
                    vd$confidence[j], md_escape(substr(vd$rationale[j], 1, 160))))
      add("")
    }
    ag <- dbGetQuery(con, "SELECT * FROM arguments WHERE claim_id = ? AND round = (SELECT max(round) FROM arguments WHERE claim_id = ?) ORDER BY panelist, stance",
                     params = list(r$id, r$id))
    if (nrow(ag)) {
      add("<details><summary>Arguments for and against</summary>", "")
      for (j in seq_len(nrow(ag)))
        add(sprintf("**%s — %s:** %s", ag$panelist[j], toupper(ag$stance[j]), md_escape(ag$argument[j])), "")
      add("</details>", "")
    }
    ch <- dbGetQuery(con, "SELECT * FROM r_checks WHERE claim_id = ?", params = list(r$id))
    if (nrow(ch)) {
      det <- tryCatch(fromJSON(ch$result[1])$detail, error = function(e) NULL)
      add(sprintf("**R check** (by `%s`): %s", ch$author[1],
                  if (is.na(ch$passed[1])) "inconclusive" else if (ch$passed[1]) "passed" else "**failed**"), "",
          if (!is.null(det)) paste0("> ", paste(det, collapse = " ")) else "",
          "", "<details><summary>Check code</summary>", "", "```r", ch$code[1], "```", "</details>", "")
    }
    ev <- dbGetQuery(con, "SELECT * FROM evidence WHERE claim_id = ?", params = list(r$id))
    if (nrow(ev)) {
      add("**Evidence** — a citation is evidence only if it resolves *and* is on-topic", "")
      for (j in seq_len(nrow(ev))) {
        tag <- if (!ev$verified[j]) "DOES NOT RESOLVE"
               else if (isFALSE(ev$relevant[j])) "resolves but OFF-TOPIC"
               else if (is.na(ev$relevant[j])) "resolves, relevance unjudged"
               else "verified + on-topic"
        add(sprintf("- **%s** `%s` — %s%s%s", tag, md_escape(substr(ev$citation[j], 1, 70)),
                    md_escape(ev$verify_note[j]),
                    if (!is.na(ev$resolved_title[j])) paste0(" → *", md_escape(substr(ev$resolved_title[j], 1, 90)), "*") else "",
                    if (!is.na(ev$relevance_note[j])) paste0(" [", md_escape(ev$relevance_note[j]), "]") else ""))
      }
      add("")
    }
  }

  cal <- dbGetQuery(con, "SELECT * FROM calibration ORDER BY weight DESC")
  if (nrow(cal)) {
    add("## Panelist calibration (cumulative, all sessions)", "",
        "| Panelist | Model | Votes | Matched final | Weight |", "|---|---|---:|---:|---:|")
    for (i in seq_len(nrow(cal)))
      add(sprintf("| %s | %s | %d | %.0f%% | %.2f |", cal$panelist[i], cal$model[i],
                  cal$n_votes[i], 100 * cal$n_correct[i] / max(cal$n_votes[i], 1), cal$weight[i]))
    add("")
  }

  txt <- paste(L, collapse = "\n")
  if (!is.null(out_file)) { writeLines(txt, out_file); invisible(out_file) } else txt
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a
