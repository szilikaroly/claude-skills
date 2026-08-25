## review.R — two independent checks, with different mandates.
##
## The panel argues. The reviewer and the statistician do not: they audit what the
## panel produced, separately and without seeing each other, because the two failure
## modes they catch are different and a single reader tends to catch only one.
##
##   REVIEWER      reads as a journal peer reviewer. Does the conclusion follow from
##                 what is actually shown? Is anything claimed more strongly than the
##                 evidence carries? What would a hostile referee ask first?
##
##   STATISTICIAN  reads only the numbers. Is the test appropriate to the design?
##                 Were its assumptions checked and do they hold? Is the result
##                 reported correctly, and — the commonest failure — is it
##                 interpreted correctly? A non-significant result is not evidence
##                 of no effect, and a statistician's job is to say so every time.
##
## Both are frontier-only. A small local model asked to referee produces a list of
## generic concerns that fits any manuscript, which is worse than no review because
## it looks like work. The same rule bars local models from authoring R checks.

suppressPackageStartupMessages({ library(jsonlite); library(glue) })

REVIEWER_SYSTEM <- "You are an experienced peer reviewer for a scientific journal. You are not the author's colleague and you are not their adversary: you are the reader who will not let an unsupported sentence pass. Judge only what is in front of you. Do not invent objections to seem rigorous, and do not withhold one to seem generous. Every finding you raise must point at a specific sentence, number or claim — a concern that could be written about any paper is not a finding. Answer with valid JSON only."

STATISTICIAN_SYSTEM <- "You are a statistician reviewing the quantitative content of a study. You care about three things and nothing else: whether the analysis suits the design, whether its assumptions were tested and hold, and whether the result is reported and interpreted correctly. You are unmoved by the substantive interest of the findings. Watch particularly for a null result described as evidence of no difference, a p-value treated as an effect size, an assumption stated as checked when only reported, and a subgroup too small to support the sentence built on it. Every finding must name the specific quantity. Answer with valid JSON only."

## ---------------------------------------------------------------- prompts
review_prompt <- function(subject, ledger_md, material, role = c("reviewer", "statistician")) {
  role <- match.arg(role)
  mandate <- if (role == "reviewer") '
Your mandate is the argument, not the arithmetic.
  - Does the conclusion follow from what is shown?
  - Is anything asserted more strongly than the evidence carries?
  - What has been left out that a referee would ask for first?
  - Is any limitation stated in a way that neutralises it rather than admitting it?
Leave the numbers to the statistician; if a number looks wrong, say so briefly and move on.' else '
Your mandate is the quantitative content, not the argument.
  - Is each analysis appropriate to the design that produced the data?
  - Were the assumptions tested? Reporting an assumption is not testing it.
  - Are estimates, intervals and p-values reported correctly?
  - Is each result INTERPRETED correctly? A non-significant difference is not
    equivalence; a wide interval is not a null; a subgroup of eight is not a finding.
  - Is any quantity in the text absent from, or inconsistent with, the ledger?
Leave the framing and the prose to the reviewer.'

  glue('
SUBJECT OF REVIEW:
<<subject>>

LEDGER OF VERIFIABLE FACTS (what was checked, how, and what the check returned):
<<ledger_md>>

SUPPORTING MATERIAL:
<<material>>

<<mandate>>

For each finding give:
  - severity: "blocking" (must be fixed before this can be relied on)
              "major" (a reviewer would require it addressed)
              "minor" (worth fixing, not decisive)
  - location: the specific sentence, number, claim id or table it concerns
  - finding: what is wrong, in one or two sentences
  - remedy: what would fix it — a concrete change, not "consider revising"
  - certain: true if you are sure, false if this needs the authors to confirm something

Report only what you actually find. An empty list is a legitimate answer and is
better than a padded one. Do not repeat a problem the ledger has already recorded
as failed unless the response to it was inadequate.

JSON only:
{"findings":[{"severity":"major","location":"...","finding":"...","remedy":"...","certain":true}],
 "overall":"<one sentence: can this be relied on as it stands>"}
', .open = "<<", .close = ">>")
}

## ---------------------------------------------------------------- run one role
review_run_role <- function(subject, ledger_md, material, role, seat = NULL) {
  frontier <- c("claude", "openai", "gemini", "deepseek", "xai")
  if (is.null(seat)) {
    av <- available_panelists()
    fr <- av[vapply(av, function(s) split_id(s)$provider %in% frontier, logical(1))]
    seat <- if (length(fr)) fr[1] else NULL
  }
  if (is.null(seat))
    return(list(ok = FALSE, role = role,
                note = "no frontier seat reachable — review not performed. A local model returns generic concerns that fit any manuscript, which reads like a review and is not one."))
  if (!(split_id(seat)$provider %in% frontier) && !nzchar(Sys.getenv("SCICOUNCIL_REVIEW_ANY_SEAT")))
    return(list(ok = FALSE, role = role, seat = seat,
                note = sprintf("%s is not a frontier seat. Set SCICOUNCIL_REVIEW_ANY_SEAT=1 to override.", seat)))

  sys <- if (role == "reviewer") REVIEWER_SYSTEM else STATISTICIAN_SYSTEM
  r <- call_provider(seat, review_prompt(subject, ledger_md, material, role),
                     system = sys, json = TRUE, timeout = 240)
  if (!isTRUE(r$ok))
    return(list(ok = FALSE, role = role, seat = seat,
                note = sprintf("%s could not be reached: %s", seat, substr(r$error, 1, 140))))
  list(ok = TRUE, role = role, seat = seat,
       findings = r$obj$findings %||% list(), overall = r$obj$overall %||% "")
}

## ---------------------------------------------------------------- render
review_render <- function(reviews, subject) {
  sev_order <- c("blocking", "major", "minor")
  L <- c("# Independent review", "",
         sprintf("**Subject:** %s", subject), "",
         "The reviewer and the statistician worked from the same ledger, separately,",
         "and did not see each other's findings. They have different mandates: the",
         "reviewer judges whether the argument holds, the statistician whether the",
         "numbers support it. A finding raised by both is worth more than one raised twice.", "")
  for (rv in reviews) {
    L <- c(L, sprintf("## %s", tools::toTitleCase(rv$role)), "")
    if (!isTRUE(rv$ok)) {
      L <- c(L, sprintf("**Not performed.** %s", rv$note), "",
             "*This is a gap, not a pass. Nothing below was checked from this angle.*", "")
      next
    }
    L <- c(L, sprintf("*Performed by `%s`.*", rv$seat), "",
           sprintf("**Overall:** %s", rv$overall), "")
    f <- rv$findings
    if (!length(f)) { L <- c(L, "No findings raised.", ""); next }
    for (s in sev_order) {
      sub <- Filter(function(x) identical(tolower(x$severity %||% ""), s), f)
      if (!length(sub)) next
      L <- c(L, sprintf("### %s (%d)", tools::toTitleCase(s), length(sub)), "")
      for (x in sub) {
        L <- c(L, sprintf("**%s**%s", x$location %||% "(no location given)",
                          if (isFALSE(x$certain)) "  *[needs author confirmation]*" else ""), "",
               sprintf("> %s", x$finding %||% ""), "",
               sprintf("*Remedy:* %s", x$remedy %||% "*(none proposed)*"), "")
      }
    }
  }
  ## Two reviews from one model are not two reviews.
  ##
  ## The value of splitting reviewer from statistician is that two different readers
  ## miss different things. Run both through the same seat and that is gone: the
  ## mandates differ but the blind spots are shared, and agreement between them means
  ## only that one model is consistent with itself. The record has to say so, because
  ## a page headed "reviewer" and "statistician" otherwise implies an independence it
  ## does not have.
  ok <- Filter(function(r) isTRUE(r$ok), reviews)
  if (length(ok) == 2 && identical(ok[[1]]$seat, ok[[2]]$seat)) {
    L <- c(L, "---", "", "## A caution about independence", "",
           sprintf("Both passes were performed by the same seat (`%s`). They are two mandates, not", ok[[1]]$seat),
           "two readers: whatever this model does not see, neither pass will catch, and",
           "agreement between them shows consistency rather than corroboration. Seat a",
           "second frontier model and re-run before treating a clean review as clean.", "")
  }
  if (length(ok) == 2) {
    locs <- lapply(ok, function(r) tolower(vapply(r$findings, function(x) substr(x$location %||% "", 1, 40), character(1))))
    both <- intersect(locs[[1]], locs[[2]])
    both <- both[nzchar(both)]
    L <- c(L, "---", "", "## Where the two agree", "")
    if (length(both)) L <- c(L, "Both reviewers raised findings at:", "", paste0("- `", both, "`"), "",
                             "*A location flagged from two different mandates is the first thing to fix.*", "")
    else L <- c(L, "*The reviewer and the statistician found no location in common — they are*",
                "*looking at genuinely different aspects, which is what the split is for.*", "")
  }
  paste(L, collapse = "\n")
}

## ---------------------------------------------------------------- orchestration
review_run <- function(subject, con = NULL, qid = NULL, material = "",
                       ledger_md = NULL, seats = list(), outdir = NULL) {
  led <- NULL
  if (is.null(ledger_md) && !is.null(con) && !is.null(qid)) {
    led <- ledger_build(con, qid)
    ledger_md <- ledger_render(led, qid)
    cli::cli_alert_info("Ledger: {ledger_summary(led)}")
  }
  ledger_md <- ledger_md %||% "*(no ledger supplied)*"

  out <- list()
  for (role in c("reviewer", "statistician")) {
    cli::cli_alert_info("Running {role} ...")
    r <- review_run_role(subject, ledger_md, material, role, seat = seats[[role]])
    if (isTRUE(r$ok)) {
      nf <- length(r$findings)
      nb <- sum(vapply(r$findings, function(x) identical(tolower(x$severity %||% ""), "blocking"), logical(1)))
      cli::cli_alert_success("{role} ({r$seat}): {nf} finding(s), {nb} blocking")
    } else cli::cli_alert_danger("{role}: {r$note}")
    out[[role]] <- r
  }

  md <- review_render(out, subject)
  if (!is.null(outdir)) {
    dir.create(outdir, recursive = TRUE, showWarnings = FALSE)
    writeLines(md, file.path(outdir, "review.md"))
    if (!is.null(led)) writeLines(ledger_md, file.path(outdir, "ledger.md"))
    cli::cli_alert_success("Written to {outdir}/")
  }
  invisible(list(reviews = out, ledger = led, ledger_md = ledger_md, markdown = md))
}
