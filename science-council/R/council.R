## council.R — the deliberation protocol.
##
## Design commitments:
##  * Round 1 is BLIND. Panelists never see each other before committing, so the
##    first vote is not an echo of whoever answered first.
##  * Every panelist owes BOTH a pro and a contra argument on EVERY claim —
##    the right and the duty to argue both sides. Refusing to steelman the
##    opposite side invalidates that panelist's vote on that claim.
##  * Deterministic checks (R code, citation resolution) outrank votes.
##  * Only claims that survive all of it are written to the indexed store.

suppressPackageStartupMessages({ library(jsonlite); library(glue); library(cli) })

.here <- function(f) file.path(Sys.getenv("SCICOUNCIL_DIR", "."), "R", f)
source(.here("science.R"))    # Claude Science integration — load first (validate.R uses it)
source(.here("providers.R")); source(.here("db.R"))
source(.here("validate.R"));  source(.here("evidence.R"))

SYS_SCIENTIST <- "You are a panelist on a multi-model scientific council. You are held to the standards of a careful peer reviewer: no rhetoric, no hedging for its own sake, no invented citations. If you do not know, say so and mark low confidence. A citation you cannot vouch for is worse than no citation — every DOI and PMID you give is checked against Crossref and Europe PMC, and a fabricated one is recorded permanently against your name. Always answer with valid JSON only, no prose outside the JSON."

## ---------------------------------------------------------------- phase 1: propose
## NOTE: prompts are full of JSON braces, so glue uses << >> as its delimiters here.
PROMPT_PROPOSE <- function(question, context) glue('
QUESTION UNDER EXAMINATION:
<<question>>
<<if (nzchar(context)) paste0("\nCONTEXT PROVIDED BY THE ASKER:\n", context) else "">>

Decompose your answer into ATOMIC, FALSIFIABLE claims. An atomic claim states
exactly one thing that could in principle be shown false. Do not bundle two
assertions with "and". Avoid claims that are pure definition or tautology.

For each claim state:
  - claim: one sentence, standalone (understandable without the question)
  - type: "empirical" | "statistical" | "causal" | "mechanistic" | "definitional" | "normative"
  - confidence: 0.0-1.0, your honest credence
  - testable_in_r: true ONLY if it can be settled by computation on data or by
    recomputing a statistic — not merely "a study could test this"
  - r_check_sketch: if testable_in_r, one sentence on what R would compute
  - citations: array of the strongest real sources. Prefer DOI or PMID. Give [] if you have none.

Return 3-8 claims covering the substance of the question. JSON only:
{"claims":[{"claim":"...","type":"empirical","confidence":0.8,"testable_in_r":false,"r_check_sketch":"","citations":["10.1038/..."]}]}
', .open = "<<", .close = ">>")

propose_claims <- function(panel, question, context) {
  cli_alert_info("Phase 1/5 — blind proposal ({length(panel)} panelists)")
  p <- PROMPT_PROPOSE(question, context)
  res <- call_panel(panel, function(id) p, system = SYS_SCIENTIST, json = TRUE)
  out <- list()
  for (r in res) {
    if (!isTRUE(r$ok)) { cli_alert_warning("{r$provider}: {substr(r$error,1,90)}"); next }
    cl <- r$obj$claims %||% list()
    cli_alert_success("{r$provider} ({r$model}): {length(cl)} claims in {round(r$latency)}s")
    for (c1 in cl) {
      if (is.null(c1$claim) || !nzchar(c1$claim)) next
      out[[length(out) + 1]] <- list(
        claim = c1$claim, type = c1$type %||% "empirical",
        confidence = as.numeric(c1$confidence %||% 0.5),
        testable_in_r = isTRUE(c1$testable_in_r),
        r_check_sketch = c1$r_check_sketch %||% "",
        citations = unlist(c1$citations %||% list()),
        proposed_by = r$provider)
    }
  }
  out
}

## Merge near-duplicate claims across panelists (token Jaccard, no embeddings
## needed). Co-proposal is recorded but grants no vote — panelists still have to
## argue the merged claim on its merits.
merge_claims <- function(claims, threshold = 0.6) {
  if (!length(claims)) return(claims)
  toks <- lapply(claims, function(c1) unique(strsplit(norm_claim(c1$claim), " ")[[1]]))
  merged <- list(); used <- rep(FALSE, length(claims))
  for (i in seq_along(claims)) {
    if (used[i]) next
    grp <- i; used[i] <- TRUE
    for (j in seq_along(claims)) {
      if (used[j] || j <= i) next
      inter <- length(intersect(toks[[i]], toks[[j]]))
      uni   <- length(union(toks[[i]], toks[[j]]))
      if (uni > 0 && inter / uni >= threshold) { grp <- c(grp, j); used[j] <- TRUE }
    }
    base <- claims[[grp[1]]]
    base$proposed_by <- paste(unique(vapply(claims[grp], function(x) x$proposed_by, character(1))), collapse = ",")
    base$citations <- unique(unlist(lapply(claims[grp], function(x) x$citations)))
    base$testable_in_r <- any(vapply(claims[grp], function(x) isTRUE(x$testable_in_r), logical(1)))
    base$confidence <- mean(vapply(claims[grp], function(x) x$confidence, numeric(1)))
    base$n_proposers <- length(unique(strsplit(base$proposed_by, ",")[[1]]))
    merged[[length(merged) + 1]] <- base
  }
  cli_alert_info("Merged {length(claims)} proposals into {length(merged)} distinct claims")
  merged
}

## ------------------------------------------- phase 2: mandatory pro AND contra
PROMPT_ADVERSARIAL <- function(question, claims, round, matrix_txt = "") {
  lst <- paste(vapply(seq_along(claims), function(i)
    sprintf('  {"cid": "%s", "claim": %s}', claims[[i]]$id, toJSON(claims[[i]]$claim, auto_unbox = TRUE)),
    character(1)), collapse = ",\n")
  base <- glue('
QUESTION: <<question>>

CLAIMS BEFORE THE COUNCIL:
[
<<lst>>
]
', .open = "<<", .close = ">>")
  ## matrix_txt is model-written text and may contain braces — use << >> here too
  delib <- if (nzchar(matrix_txt)) glue('
THIS IS ROUND <<round>> — DELIBERATION. You now see what the whole council said,
including the deterministic R checks and citation checks. Those checks are facts,
not opinions: if a check contradicts your earlier vote, change your vote.
Do not change a vote merely because you are outnumbered — say so and hold, if the
evidence is on your side. Dissent that survives is recorded, not punished.

COUNCIL RECORD SO FAR:
<<matrix_txt>>
', .open = "<<", .close = ">>") else ""
  paste0(base, delib, '
For EVERY claim you must discharge BOTH duties. This is not optional; a claim
where you supply only one side has your vote discarded.

  1. pro     — the strongest honest case FOR the claim. Steelman it even if you
               intend to vote refute.
  2. contra  — the strongest honest case AGAINST it. Steelman it even if you
               intend to vote support. Name the specific evidence, confound,
               boundary condition or failure mode that would break it.
  3. verdict — "support" | "refute" | "uncertain", after weighing both sides.
  4. confidence — 0.0-1.0.
  5. citations — real DOIs/PMIDs backing either side. [] if none. Checked automatically.

Empty, generic or non-responsive pro/contra text ("there may be counterarguments")
counts as not discharged. JSON only:
{"reviews":[{"cid":"...","pro":"...","contra":"...","verdict":"support","confidence":0.7,"rationale":"...","citations":[]}]}
')
}

adversarial_round <- function(panel, question, claims, round, matrix_txt = "") {
  cli_alert_info("Phase 2/5 — adversarial review, round {round} (pro+contra mandatory)")
  p <- PROMPT_ADVERSARIAL(question, claims, round, matrix_txt)
  res <- call_panel(panel, function(id) p, system = SYS_SCIENTIST, json = TRUE)
  rows <- list()
  for (r in res) {
    if (!isTRUE(r$ok)) { cli_alert_warning("{r$provider}: {substr(r$error,1,90)}"); next }
    revs <- r$obj$reviews %||% list()
    ok_n <- 0
    for (rv in revs) {
      cid <- rv$cid %||% next
      pro <- trimws(rv$pro %||% ""); contra <- trimws(rv$contra %||% "")
      ## enforce the duty to argue both sides
      discharged <- nchar(pro) >= 40 && nchar(contra) >= 40
      rows[[length(rows) + 1]] <- list(
        cid = cid, panelist = r$provider, model = r$model, round = round,
        pro = pro, contra = contra,
        verdict = if (discharged) (rv$verdict %||% "uncertain") else NA_character_,
        confidence = as.numeric(rv$confidence %||% 0.5),
        rationale = rv$rationale %||% "",
        citations = unlist(rv$citations %||% list()),
        discharged = discharged)
      if (discharged) ok_n <- ok_n + 1
    }
    cli_alert_success("{r$provider}: {ok_n}/{length(revs)} reviews with both sides argued ({round(r$latency)}s)")
  }
  rows
}

## Keep each panelist's most recent review per claim across rounds.
merge_rounds <- function(old, new) {
  key <- function(r) paste(r$panelist, r$cid, sep = "|")
  keep <- list()
  for (r in c(old, new)) {
    k <- key(r)
    if (is.null(keep[[k]]) || r$round >= keep[[k]]$round) keep[[k]] <- r
  }
  unname(keep)
}

## ------------------------------------------------- phase 3: deterministic checks
RCODE_PROMPT <- function(claim, sketch) glue('
Write R code that empirically checks this claim:
  "<<claim>>"
Intended check: <<sketch>>

ENVIRONMENT:
  <<check_env_blurb()>>

Rules:
  - No installs, no network, no file writes, no system calls.
  - Be honest: the code must be able to FAIL. Code that always passes is worthless.
  - End by assigning: result <- list(passed = TRUE/FALSE/NA, detail = "what was computed and found")

CRITICAL — when is a check meaningful?
  * Mathematical, probabilistic or algebraic claims: compute or simulate. R settles these.
  * Claims about what a statistical method can show: compute power, coverage, bias.
  * Empirical claims about the real world (effect sizes in populations, clinical
    magnitudes): you almost certainly have NO data. Simulating data with the claimed
    effect baked in and then "finding" that effect is CIRCULAR and worthless.
    In that case set passed = NA and say why. NA is a respected answer here;
    a fake TRUE is a serious failure.

Return ONLY an R code block, nothing else:
```r
# your code here
result <- list(passed = TRUE, detail = "...")
```
', .open = "<<", .close = ">>")

## Pull R code out of a fenced block. Asking for code inside JSON is fragile —
## every quote in the code has to survive JSON escaping, and models routinely
## fail that. A fenced block has no escaping to get wrong.
extract_code_block <- function(txt) {
  if (is.null(txt) || !nzchar(txt)) return(NULL)
  m <- regmatches(txt, regexpr("(?s)```[Rr]?\\s*\n(.*?)```", txt, perl = TRUE))
  if (length(m)) {
    code <- sub("(?s)^```[Rr]?\\s*\n", "", m[1], perl = TRUE)
    return(sub("```\\s*$", "", code))
  }
  ## no fence: accept raw text if it looks like R that assigns `result`
  if (grepl("result\\s*<-", txt)) return(txt)
  NULL
}

## Who may author a check?
##
## A failing check REFUTES a claim outright, overriding the whole panel. That
## authority is only safe if the code is competent. It is not a hypothetical
## worry: llama3.1:8b, asked to check "the probability of winning by staying is
## 1/3", produced code with no relationship to the Monty Hall problem at all and
## returned FALSE — a false refutation of a provably true claim, with veto power
## over the council.
##
## So checks may only be authored by a frontier seat. If none is on the panel, we
## run NO checks and say so: an unchecked claim is honest, a wrongly refuted one
## is not.
CHECK_AUTHOR_DEFAULT <- "claude,openai,gemini,deepseek,xai"

## `live` (optional) restricts the choice to seats that actually answered this
## session. A quota-exhausted seat is on the panel but mute — picking it means
## every check errors out, and claims that could have been settled go unchecked.
pick_check_author <- function(panel, live = NULL) {
  allowed <- trimws(strsplit(Sys.getenv("SCICOUNCIL_CHECK_AUTHORS", CHECK_AUTHOR_DEFAULT), ",")[[1]])
  candidates <- if (is.null(live)) panel else panel[panel %in% live]
  for (a in allowed) {
    hit <- candidates[vapply(candidates, function(s) identical(split_id(s)$provider, a), logical(1))]
    if (length(hit)) return(hit[1])
  }
  NULL
}

run_checks <- function(panel, claims, live = NULL) {
  testable <- Filter(function(c1) isTRUE(c1$testable_in_r), claims)
  if (!length(testable)) { cli_alert_info("Phase 3/5 — no R-testable claims"); return(list()) }
  author <- pick_check_author(panel, live)
  ## a frontier seat exists but went silent (quota/outage) — say so explicitly
  if (is.null(author) && !is.null(live) && !is.null(pick_check_author(panel))) {
    cli_alert_warning("Phase 3/5 — the frontier seat(s) on this panel did not respond this session; skipping R checks rather than letting a weak model author them.")
    return(list())
  }
  if (is.null(author)) {
    cli_alert_warning("Phase 3/5 — {length(testable)} claim(s) are R-testable, but no frontier seat is on the panel to author the checks.")
    cli_alert_warning("  Skipping R checks entirely: a weak author writes code that refutes true claims. Set SCICOUNCIL_CHECK_AUTHORS to override.")
    return(list())
  }
  cli_alert_info("Phase 3/5 — deterministic R checks on {length(testable)} claim(s), authored by {author}")
  out <- list()
  for (c1 in testable) {
    r <- call_provider(author, RCODE_PROMPT(c1$claim, c1$r_check_sketch %||% ""),
                       system = "You are a careful statistician. Return only an R code block.",
                       json = FALSE)
    code <- if (isTRUE(r$ok)) extract_code_block(r$text) else NULL
    if (is.null(code)) {
      cli_alert_warning("check author produced no usable code for {c1$id}: {substr(r$error %||% 'no code block',1,70)}")
      next
    }
    chk <- run_r_check(code)
    sym <- if (isTRUE(chk$passed)) "PASS" else if (isFALSE(chk$passed)) "FAIL" else "N/A "
    cli_alert("  [{sym}] {substr(c1$claim, 1, 62)}...")
    out[[c1$id]] <- list(cid = c1$id, author = author, code = code,
                         passed = chk$passed, result = chk$result,
                         error = chk$error, runtime = chk$runtime)
  }
  out
}

## A resolved identifier only proves the paper exists. Check that each claim's
## citations are actually ABOUT that claim — a real DOI from another field is a
## category error, and models make it constantly.
judge_citations <- function(claims, cit) {
  if (is.null(cit)) return(NULL)
  pairs <- list()
  for (c1 in claims) {
    for (cc in (c1$citations %||% character())) {
      row <- cit[cit$citation == cc, ][1, ]
      if (is.na(row$citation) || !isTRUE(row$verified)) next
      pairs[[length(pairs) + 1]] <- list(cid = c1$id, claim = c1$claim, citation = cc, row = row)
    }
  }
  if (!length(pairs)) return(NULL)
  cli_alert_info("  judging topical relevance of {length(pairs)} resolved citation(s)")
  out <- do.call(rbind, lapply(pairs, function(p) {
    j <- judge_relevance(p$claim, p$row$title, p$row$journal, p$row$year, p$row$abstract)
    if (isFALSE(j$relevant))
      cli_alert_danger("  off-topic: {substr(p$citation,1,44)} -> \"{substr(p$row$title,1,46)}\"")
    data.frame(cid = p$cid, citation = p$citation,
               relevant = j$relevant, relevance_note = j$note, stringsAsFactors = FALSE)
  }))
  n_off <- sum(isFALSE(out$relevant) | (!is.na(out$relevant) & !out$relevant))
  if (n_off > 0) cli_alert_warning("  {n_off} citation(s) resolve but are off-topic — they do not count as evidence")
  out
}

check_all_citations <- function(claims, reviews) {
  cits <- unique(c(unlist(lapply(claims, function(c1) c1$citations)),
                   unlist(lapply(reviews, function(r) r$citations))))
  cits <- cits[nzchar(trimws(cits %||% character()))]
  if (!length(cits)) { cli_alert_info("Phase 4/5 — no citations offered"); return(NULL) }
  cli_alert_info("Phase 4/5 — verifying {length(cits)} citation(s) against Crossref / Europe PMC")
  v <- verify_citations(cits)
  if (!is.null(v)) {
    nver <- sum(v$verified)
    cli_alert(if (nver == nrow(v)) "  all {nrow(v)} citations resolve"
              else "  {nver}/{nrow(v)} resolve — {nrow(v)-nver} unverified/fabricated")
    for (i in which(!v$verified)) cli_alert_danger("  unresolved: {substr(v$citation[i],1,70)} — {v$note[i]}")
  }
  v
}

## ------------------------------------------------------- record for deliberation
format_matrix <- function(claims, reviews, checks, cit) {
  lines <- character()
  for (c1 in claims) {
    rs <- Filter(function(r) r$cid == c1$id, reviews)
    lines <- c(lines, sprintf('\n[%s] %s', c1$id, c1$claim))
    for (r in rs) {
      lines <- c(lines, sprintf('  %s -> %s (conf %.2f)\n     PRO: %s\n     CONTRA: %s',
        r$panelist, r$verdict %||% "DISCARDED (did not argue both sides)",
        r$confidence, substr(r$pro, 1, 260), substr(r$contra, 1, 260)))
    }
    ch <- checks[[c1$id]]
    if (!is.null(ch)) {
      det <- tryCatch(ch$result$detail, error = function(e) NULL) %||% ch$error %||% ""
      lines <- c(lines, sprintf('  R CHECK: %s — %s',
        if (isTRUE(ch$passed)) "PASSED" else if (isFALSE(ch$passed)) "FAILED" else "INCONCLUSIVE",
        substr(paste(det, collapse = " "), 1, 240)))
    }
  }
  if (!is.null(cit) && any(!cit$verified)) {
    lines <- c(lines, "\nCITATIONS THAT DO NOT RESOLVE (treat as fabricated):")
    for (i in which(!cit$verified)) lines <- c(lines, sprintf("  - %s (%s)", substr(cit$citation[i],1,90), cit$note[i]))
  }
  paste(lines, collapse = "\n")
}

## ------------------------------------------------------------ phase 5: scoring
score_claims <- function(con, claims, reviews, checks, cit, threshold, rel = NULL) {
  cli_alert_info("Phase 5/5 — weighted consensus + agreement statistics")
  panelists <- unique(vapply(reviews, function(r) r$panelist, character(1)))
  rating_mat <- matrix(NA_character_, nrow = length(claims), ncol = length(panelists),
                       dimnames = list(vapply(claims, function(c) c$id, character(1)), panelists))
  scored <- list()
  for (i in seq_along(claims)) {
    c1 <- claims[[i]]
    rs <- Filter(function(r) r$cid == c1$id && isTRUE(r$discharged), reviews)
    v  <- vapply(rs, function(r) r$verdict %||% NA_character_, character(1))
    cf <- vapply(rs, function(r) as.numeric(r$confidence %||% 0.5), numeric(1))
    pl <- vapply(rs, function(r) r$panelist, character(1))
    w  <- vapply(pl, function(p) db_weight(con, p), numeric(1))
    if (length(v)) rating_mat[i, pl] <- v

    score <- consensus_score(v, cf, w)
    ch <- checks[[c1$id]]
    r_passed <- if (is.null(ch)) NA else ch$passed
    ## a citation counts as evidence only if it resolves AND is on-topic
    own_cits <- c1$citations %||% character()
    ct <- length(own_cits)
    cv <- NA
    if (!is.null(cit) && ct > 0) {
      resolved <- cit$citation[cit$verified & cit$citation %in% own_cits]
      cv <- if (is.null(rel)) length(resolved) else {
        ok <- rel$citation[rel$cid == c1$id & !is.na(rel$relevant) & rel$relevant]
        length(intersect(resolved, ok))
      }
    }

    cls <- classify_claim(score, threshold, r_passed,
                          n_refute = sum(v == "refute", na.rm = TRUE),
                          n_valid = length(v), citations_verified = cv, citations_total = ct)
    scored[[i]] <- modifyList(c1, list(
      status = cls$status, status_reason = cls$reason, consensus_score = score,
      n_support = sum(v == "support", na.rm = TRUE),
      n_refute = sum(v == "refute", na.rm = TRUE),
      n_uncertain = sum(v == "uncertain", na.rm = TRUE),
      n_valid = length(v), r_check_passed = r_passed,
      citations_verified = cv, citations_total = ct))
  }
  kap <- fleiss_kappa(rating_mat); alp <- krippendorff_alpha(rating_mat)
  if (is.na(kap)) {
    ## kappa is undefined without variance — say why rather than printing a bare n/a
    votes <- unique(as.vector(rating_mat)); votes <- votes[!is.na(votes)]
    reason <- if (length(votes) <= 1) "panel was unanimous — chance-corrected agreement is undefined"
              else "too few claims with 2+ valid votes"
    cli_alert("  Fleiss kappa = n/a ({reason}); Krippendorff alpha = {ifelse(is.na(alp),'n/a',sprintf('%.3f',alp))}")
  } else {
    cli_alert("  Fleiss kappa = {sprintf('%.3f',kap)} ({kappa_label(kap)}), Krippendorff alpha = {ifelse(is.na(alp),'n/a',sprintf('%.3f',alp))}")
  }
  for (i in seq_along(scored)) { scored[[i]]$agreement_kappa <- kap; scored[[i]]$krippendorff_alpha <- alp }
  list(claims = scored, kappa = kap, alpha = alp, ratings = rating_mat)
}

## ---------------------------------------------------------------- persistence
persist <- function(con, qid, question, domain, context, panel, rounds, scored, reviews, checks, cit, rel = NULL) {
  now <- format(Sys.time())
  dbExecute(con, "INSERT OR REPLACE INTO questions VALUES (?,?,?,?,?,?,?,?,?)",
    params = list(qid, question, domain %||% "", context %||% "", "closed",
                  paste(panel, collapse = ","), rounds, now, now))
  n_val <- 0
  for (c1 in scored) {
    dbExecute(con, "INSERT OR REPLACE INTO claims
      (id, question_id, claim, claim_norm, claim_type, proposed_by, testable_in_r,
       status, status_reason, consensus_score, agreement_kappa, krippendorff_alpha,
       n_support, n_refute, n_uncertain, r_check_passed,
       citations_verified, citations_total, final_round, created_at, validated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      params = list(c1$id, qid, c1$claim, norm_claim(c1$claim), c1$type, c1$proposed_by,
                    isTRUE(c1$testable_in_r), c1$status, c1$status_reason %||% NA,
                    c1$consensus_score,
                    c1$agreement_kappa, c1$krippendorff_alpha,
                    c1$n_support, c1$n_refute, c1$n_uncertain,
                    c1$r_check_passed, c1$citations_verified %||% NA, c1$citations_total,
                    rounds, now, if (c1$status == "validated") now else NA))
    if (c1$status == "validated") n_val <- n_val + 1
    ## record every argument and vote, including the discarded ones
    for (r in Filter(function(r) r$cid == c1$id, reviews)) {
      for (st in c("pro", "contra")) {
        dbExecute(con, "INSERT INTO arguments (claim_id,panelist,model,round,stance,argument,evidence,strength,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
          params = list(c1$id, r$panelist, r$model, r$round, st, r[[st]],
                        paste(r$citations, collapse = "; "), nchar(r[[st]]) / 1000, now))
      }
      dbExecute(con, "INSERT INTO verdicts (claim_id,panelist,model,round,verdict,confidence,rationale,revised,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        params = list(c1$id, r$panelist, r$model, r$round,
                      r$verdict %||% "discarded", r$confidence, r$rationale, r$round > 1, now))
      ## calibration: did this panelist's vote match where the claim landed?
      if (c1$status %in% c("validated", "refuted") && !is.na(r$verdict %||% NA)) {
        truth <- if (c1$status == "validated") "support" else "refute"
        db_upsert_calibration(con, r$panelist, r$model, r$verdict == truth,
                              brier_of_vote(r$verdict, r$confidence, c1$status) %||% 0.25)
      }
    }
    ch <- checks[[c1$id]]
    if (!is.null(ch)) {
      dbExecute(con, "INSERT INTO r_checks (claim_id,author,code,passed,result,error,runtime_sec,created_at) VALUES (?,?,?,?,?,?,?,?)",
        params = list(c1$id, ch$author, ch$code, ch$passed,
                      toJSON(ch$result %||% list(), auto_unbox = TRUE),
                      ch$error %||% NA, ch$runtime, now))
    }
    if (!is.null(cit) && length(c1$citations %||% character())) {
      for (cc in c1$citations) {
        row <- cit[cit$citation == cc, ][1, ]
        if (is.na(row$citation)) next
        rr <- if (!is.null(rel)) rel[rel$cid == c1$id & rel$citation == cc, ][1, ] else NULL
        dbExecute(con, "INSERT INTO evidence (claim_id,panelist,source_type,citation,doi,pmid,url,verified,verify_note,resolved_title,year,relevant,relevance_note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          params = list(c1$id, c1$proposed_by, row$method, cc, row$doi, row$pmid,
                        if (!is.na(row$doi)) paste0("https://doi.org/", row$doi) else NA,
                        row$verified, row$note, row$title, row$year,
                        if (is.null(rr) || is.na(rr$cid)) NA else rr$relevant,
                        if (is.null(rr) || is.na(rr$cid)) NA else rr$relevance_note,
                        now))
      }
    }
  }
  db_reindex(con)
  n_val
}

## ---------------------------------------------------- R statistics generation
## Runs the stats specialist Rscript (r-stats-methodologist) on a JSON export of
## the session to produce ggplot2 charts + a self-contained HTML statistical
## report. `meta_spec` optionally points at a JSON meta-analysis spec.
generate_stats_report <- function(qid, outfile = NULL, meta_spec = NULL, quiet = FALSE) {
  if (!file.exists(db_path()))
    stop("No claim store yet at ", db_path(), " — run a session first.", call. = FALSE)
  con <- db_connect(read_only = TRUE); on.exit(dbDisconnect(con, shutdown = TRUE), add = TRUE)
  n <- dbGetQuery(con, "SELECT count(*) n FROM claims WHERE question_id = ?", params = list(qid))$n
  if (n == 0) stop("No such session or no claims: ", qid, call. = FALSE)

  runs <- file.path(Sys.getenv("SCICOUNCIL_DIR", "."), "runs", qid)
  dir.create(runs, recursive = TRUE, showWarnings = FALSE)
  sj <- file.path(runs, "session.json")
  export_session_json(con, qid, sj)
  outfile <- outfile %||% file.path(runs, "stats_report.html")

  rscript <- science_rscript("stats")
  runner <- file.path(Sys.getenv("SCICOUNCIL_DIR", "."), "R", "stats_runner.R")
  args <- c("--vanilla", runner, sj, runs, outfile, meta_spec %||% "")
  if (!quiet) cli::cli_alert_info("Generating R statistics via {basename(dirname(dirname(rscript)))} ...")
  out <- suppressWarnings(system2(rscript, args, stdout = TRUE, stderr = TRUE, timeout = 300))
  ok <- any(grepl("STATS_OK", out))
  if (!ok) stop("stats generation failed:\n", paste(tail(out, 15), collapse = "\n"), call. = FALSE)
  if (!quiet) {
    imgs <- list.files(runs, pattern = "\\.png$", full.names = FALSE)
    cli::cli_alert_success("Statistical report: {outfile}")
    cli::cli_alert_info("Charts ({length(imgs)}): {paste(imgs, collapse=', ')}")
  }
  invisible(outfile)
}

## ------------------------------------------------------------------- main entry
council_run <- function(question, context = "", domain = "", rounds = NULL, panel = NULL,
                        threshold = NULL, quiet = FALSE, stats = FALSE) {
  load_env()
  rounds    <- rounds    %||% as.integer(Sys.getenv("SCICOUNCIL_MAX_ROUNDS", "2"))
  threshold <- threshold %||% as.numeric(Sys.getenv("SCICOUNCIL_CONSENSUS_THRESHOLD", "0.70"))
  panel     <- panel     %||% available_panelists()
  if (length(panel) < 2) stop("Need at least 2 reachable panelists; got: ",
                              paste(panel, collapse = ", "), call. = FALSE)

  cli_h1("Science Council")
  cli_alert_info("Question: {question}")
  cli_alert_info("Panel: {paste(sprintf('%s (%s)', panel, vapply(panel, panelist_model, character(1))), collapse=' | ')}")

  con <- db_connect(); on.exit(dbDisconnect(con, shutdown = TRUE), add = TRUE)
  qid <- new_id("q", question)

  raw <- propose_claims(panel, question, context)
  if (!length(raw)) stop("No panelist produced a usable claim.", call. = FALSE)
  claims <- merge_claims(raw)
  for (i in seq_along(claims)) claims[[i]]$id <- new_id("c", claims[[i]]$claim, i)

  reviews <- adversarial_round(panel, question, claims, 1)
  ## Seats that actually spoke this session — a quota-dead seat must not be
  ## handed the check-authoring job (observed live: gemini 429'd, then authored
  ## five checks that all errored out).
  live <- unique(c(vapply(raw, function(c1) c1$proposed_by, character(1)),
                   vapply(reviews, function(r) r$panelist, character(1))))
  live <- unique(unlist(strsplit(live, ",")))
  checks  <- run_checks(panel, claims, live = live)
  cit     <- check_all_citations(claims, reviews)

  final_round <- 1
  if (rounds > 1) {
    for (rd in 2:rounds) {
      mtx <- format_matrix(claims, reviews, checks, cit)
      more <- adversarial_round(panel, question, claims, rd, mtx)
      if (length(more)) {
        ## A panelist's latest word wins, but a panelist that dropped out this
        ## round keeps its earlier vote — otherwise one timeout silently erases
        ## a whole seat and the agreement statistics collapse.
        reviews <- merge_rounds(reviews, more)
        final_round <- rd
      }
      cit2 <- check_all_citations(claims, more)
      if (!is.null(cit2)) cit <- unique(rbind(cit, cit2))
    }
  }

  rel <- judge_citations(claims, cit)
  sc <- score_claims(con, claims, reviews, checks, cit, threshold, rel)
  n_val <- persist(con, qid, question, domain, context, panel, final_round,
                   sc$claims, reviews, checks, cit, rel)

  cli_h2("Result")
  for (c1 in sc$claims) {
    tag <- switch(c1$status, validated = "VALIDATED", refuted = "REFUTED",
                  contested = "CONTESTED", "UNRESOLVED")
    cli_text("[{tag}] score={sprintf('%+.2f', c1$consensus_score)} (+{c1$n_support}/-{c1$n_refute}/?{c1$n_uncertain}) {c1$claim}")
    cli_text("           why: {c1$status_reason}")
  }
  cli_alert_success("{n_val}/{length(sc$claims)} claims validated and indexed. Question id: {qid}")

  stats_file <- NULL
  if (isTRUE(stats)) {
    stats_file <- tryCatch(generate_stats_report(qid, quiet = quiet),
                           error = function(e) { cli_alert_warning("stats report skipped: {conditionMessage(e)}"); NULL })
  }
  invisible(list(qid = qid, claims = sc$claims, kappa = sc$kappa, alpha = sc$alpha,
                 reviews = reviews, checks = checks, citations = cit, panel = panel,
                 stats_report = stats_file))
}
