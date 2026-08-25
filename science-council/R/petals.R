## petals.R — the human elements of a manuscript, and the guard that keeps them true.
##
## WHY THIS EXISTS
##
## Scientific prose has been sanded smooth. The passive voice, the absent author,
## the result that "was observed" — none of it reflects how the work actually
## happened. Somebody was surprised. Somebody noticed a rash and ordered a test
## that changed a patient's life. Somebody had been wrong in the previous draft.
## Stripping that out does not make a paper more objective; it makes it less
## informative, because the reader loses the reasoning that produced the finding.
##
## A petal is one small, true, human passage attached to one place in a manuscript.
##
## THE GUARD, AND WHY IT IS STRUCTURAL RATHER THAN ADVISORY
##
## The same request — "make it feel human" — can mean restoring what was stripped
## out, or manufacturing sentiment that never happened. The second is fabrication.
## Inventing a patient's gratitude, a moment of doubt nobody had, or a poignant
## detail that reads well is misconduct, no less so for being emotional rather
## than numerical. A reader cannot tell the two apart from the page.
##
## So a petal is not accepted on the strength of how it reads. Every petal must
## carry an ANCHOR: a specific fact, event or record it derives from, of a kind
## that can be checked. Petals whose anchor does not verify are marked and
## excluded from rendering. This is the same rule the council applies to claims —
## an assertion earns its place by being grounded, not by being persuasive — and
## it is applied here for exactly the same reason.
##
## An anchor of kind `record` cannot be machine-checked: only the clinician knows
## whether a consultation happened as described. Those petals are rendered with an
## explicit author-confirmation flag rather than silently trusted.

suppressPackageStartupMessages({ library(jsonlite) })

## ---------------------------------------------------------------- taxonomy
## Each kind names a specific thing that gets lost in formulaic writing. The
## descriptions are the generation prompt's contract: they say what counts.
PETAL_KINDS <- list(
  motivation = list(
    label = "why this was attempted",
    wants = "The real reason the work started — a clinical problem the authors kept meeting, not a gap in the literature invented after the fact.",
    anchor = "A documented circumstance: a pattern in the caseload, a constraint on prescribing, a repeated failure."),
  surprise = list(
    label = "what the authors did not expect",
    wants = "A finding that contradicted the authors' own expectation, stated as such. Includes being wrong in an earlier analysis.",
    anchor = "A specific result, or a documented change between drafts."),
  clinical_narrative = list(
    label = "a patient whose course changed the understanding",
    wants = "One de-identified course that taught the team something the aggregate could not. Not a heartwarming vignette — a case that carried information.",
    anchor = "A real patient record or documented clinical event."),
  uncertainty = list(
    label = "what still troubles the authors",
    wants = "A doubt the authors genuinely hold about their own result, in plain language rather than boilerplate limitation prose.",
    anchor = "A concrete methodological fact: a violated assumption, a small subgroup, a design gap."),
  patient_impact = list(
    label = "what the result meant for the people in it",
    wants = "The consequence for participants, stated concretely and without inflation.",
    anchor = "An outcome present in the data or the clinical record."),
  craft = list(
    label = "a judgement call and its cost",
    wants = "A decision the authors made that another team would have made differently, and what it cost them.",
    anchor = "A documented analytical or protocol decision."))

## ---------------------------------------------------------------- generation
PETAL_SYSTEM <- "You are helping restore the human reality of a scientific manuscript: the reasoning, surprise and clinical judgement that formulaic writing removes. You are NOT adding colour, warmth or drama. Every sentence you write must be traceable to something that actually happened, and you must name what that is. If the material does not support a given kind of petal, return none of that kind — a missing petal is correct, an invented one is fabrication. Never invent a patient, a quotation, an emotion, or a detail that merely reads well. Write in the authors' collective voice, plainly, without sentiment. Answer with valid JSON only."

petal_prompt <- function(question, material, kinds = names(PETAL_KINDS), max_petals = 6) {
  kd <- paste(vapply(kinds, function(k) {
    p <- PETAL_KINDS[[k]]
    sprintf('  "%s" — %s\n      wants: %s\n      anchor must be: %s', k, p$label, p$wants, p$anchor)
  }, character(1)), collapse = "\n")
  glue::glue('
MANUSCRIPT / STUDY:
<<question>>

MATERIAL YOU MAY DRAW ON (this is the only permitted source of fact):
<<material>>

Propose at most <<max_petals>> petals. A petal is one short passage (2-4 sentences)
that restores something true and human to the manuscript.

KINDS:
<<kd>>

For each petal give:
  - kind: one of the keys above
  - section: where it belongs — "introduction" | "methods" | "results" | "discussion" | "conclusion"
  - text: the passage itself, in the authors\' voice, plain and unsentimental
  - anchor: the specific fact, event or record it derives from, quoted or cited precisely
  - anchor_kind: "data" (a value in the dataset) | "session" (something in the council
    record) | "history" (a documented change between drafts) | "record" (a clinical
    event only the authors can confirm)
  - confidence: 0.0-1.0 that the anchor genuinely supports the text

Rules that override everything else:
  - If you cannot name a precise anchor, do not write the petal.
  - Do not embellish an anchor. If the record says a rash developed, do not write
    that the patient was frightened.
  - Prefer three well-anchored petals to six decorated ones.

JSON only:
{"petals":[{"kind":"surprise","section":"discussion","text":"...","anchor":"...","anchor_kind":"session","confidence":0.9}]}
', .open = "<<", .close = ">>")
}

## ---------------------------------------------------------------- verification
## A petal is accepted only if its anchor checks out. What "checks out" means
## depends on the anchor kind — and one kind deliberately cannot be settled here.
petal_verify <- function(petal, con = NULL, qid = NULL, data_file = NULL) {
  ak <- petal$anchor_kind %||% "record"
  a  <- petal$anchor %||% ""
  txt <- petal$text %||% ""

  if (!nzchar(trimws(a)))
    return(list(verified = FALSE, needs_author = FALSE, note = "no anchor given — rejected"))
  if (nchar(trimws(txt)) < 40)
    return(list(verified = FALSE, needs_author = FALSE, note = "text too short to carry meaning"))

  ## Numbers in the passage must appear in the anchor. This catches the most common
  ## drift: a petal that inflates a real finding into a rounder, better-reading one.
  nums_txt <- unique(regmatches(txt, gregexpr("[0-9]+(\\.[0-9]+)?", txt))[[1]])
  nums_a   <- unique(regmatches(a,   gregexpr("[0-9]+(\\.[0-9]+)?", a))[[1]])
  stray <- setdiff(nums_txt, nums_a)
  ## allow small ordinals and years, which are usually structural rather than claims
  stray <- stray[!(stray %in% c("1","2","3","4","5","6","7","8","9","10")) &
                 !grepl("^(19|20)[0-9]{2}$", stray)]

  if (ak == "session" && !is.null(con) && !is.null(qid)) {
    cl <- tryCatch(DBI::dbGetQuery(con,
      "SELECT claim, status, status_reason FROM claims WHERE question_id = ?",
      params = list(qid)), error = function(e) NULL)
    hit <- FALSE
    if (!is.null(cl) && nrow(cl)) {
      key <- tolower(gsub("[^a-z0-9 ]", " ", a))
      toks <- setdiff(strsplit(key, "\\s+")[[1]], c("", "the","a","an","of","in","and","was","were","that"))
      hit <- any(vapply(seq_len(nrow(cl)), function(i) {
        hay <- tolower(paste(cl$claim[i], cl$status[i], cl$status_reason[i]))
        sum(vapply(toks, function(t) grepl(t, hay, fixed = TRUE), logical(1))) >= max(2, length(toks) %/% 3)
      }, logical(1)))
    }
    if (!hit) return(list(verified = FALSE, needs_author = FALSE,
                          note = "anchor claims to come from the council record but does not match any claim in it"))
  }

  if (length(stray))
    return(list(verified = FALSE, needs_author = FALSE,
                note = sprintf("text states figures absent from its anchor (%s) — the anchor does not support the passage",
                               paste(head(stray, 4), collapse = ", "))))

  if (ak == "record")
    return(list(verified = NA, needs_author = TRUE,
                note = "clinical record: only the authors can confirm this happened as described"))

  list(verified = TRUE, needs_author = FALSE, note = sprintf("anchor kind '%s' consistent with the passage", ak))
}

## ---------------------------------------------------------------- storage
petals_init <- function(con) {
  DBI::dbExecute(con, "CREATE SEQUENCE IF NOT EXISTS seq_petal START 1")
  DBI::dbExecute(con, "CREATE TABLE IF NOT EXISTS petals (
      id INTEGER DEFAULT nextval('seq_petal') PRIMARY KEY,
      question_id VARCHAR, kind VARCHAR, section VARCHAR,
      text TEXT, anchor TEXT, anchor_kind VARCHAR, confidence DOUBLE,
      proposed_by VARCHAR, verified BOOLEAN, needs_author BOOLEAN,
      verify_note TEXT, created_at TIMESTAMP)")
  invisible(TRUE)
}

petals_save <- function(con, qid, petals) {
  petals_init(con)
  now <- format(Sys.time())
  for (p in petals) {
    DBI::dbExecute(con, "INSERT INTO petals (question_id,kind,section,text,anchor,anchor_kind,confidence,proposed_by,verified,needs_author,verify_note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
      params = list(qid, p$kind %||% NA, p$section %||% NA, p$text %||% NA,
                    p$anchor %||% NA, p$anchor_kind %||% NA,
                    as.numeric(p$confidence %||% NA), p$proposed_by %||% NA,
                    p$verified, isTRUE(p$needs_author), p$verify_note %||% NA, now))
  }
  invisible(length(petals))
}

## ---------------------------------------------------------------- rendering
## Markdown for pasting into a manuscript. Rejected petals are shown too, with the
## reason — a struck petal tells the author what the material would not support,
## which is more useful than silently dropping it.
petals_render <- function(petals, include_rejected = TRUE) {
  ord <- c("introduction","methods","results","discussion","conclusion")
  ok  <- Filter(function(p) !identical(p$verified, FALSE), petals)
  bad <- Filter(function(p)  identical(p$verified, FALSE), petals)
  L <- c("# Human elements (petals)", "",
         "Each passage below is anchored to something specific. Read the anchor before",
         "using the passage: if the anchor is not something you can stand behind in",
         "review, delete the petal rather than softening it.", "")
  for (s in ord) {
    ps <- Filter(function(p) identical(tolower(p$section %||% ""), s), ok)
    if (!length(ps)) next
    L <- c(L, sprintf("## %s", tools::toTitleCase(s)), "")
    for (p in ps) {
      flag <- if (isTRUE(p$needs_author)) "  **[needs author confirmation]**" else ""
      L <- c(L, sprintf("**%s** — %s%s", p$kind, PETAL_KINDS[[p$kind]]$label %||% "", flag), "",
                sprintf("> %s", p$text), "",
                sprintf("*Anchor (%s):* %s", p$anchor_kind %||% "?", p$anchor), "")
    }
  }
  if (include_rejected && length(bad)) {
    L <- c(L, "## Not used", "",
           "These were proposed but their anchor did not hold. They are listed so the",
           "same idea is not reintroduced without evidence.", "")
    for (p in bad) L <- c(L, sprintf("- ~~%s~~ (%s) — %s", substr(p$text, 1, 110), p$kind, p$verify_note), "")
  }
  paste(L, collapse = "\n")
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a) == 1 && is.na(a))) b else a

## ---------------------------------------------------------------- orchestration
#' Propose petals for a manuscript, verify each, store and render.
#'
#' `material` is the only permitted source of fact for the generator. Pass real
#' things: verified statistics, the council record, documented clinical events.
#' Whatever is not in here cannot legitimately appear in a petal.
petals_run <- function(question, material, qid = NULL, seat = NULL,
                       kinds = names(PETAL_KINDS), max_petals = 6, outfile = NULL) {
  seat <- seat %||% {
    av <- available_panelists()
    frontier <- av[vapply(av, function(s) split_id(s)$provider %in%
                          c("claude","openai","gemini","deepseek","xai"), logical(1))]
    if (length(frontier)) frontier[1] else if (length(av)) av[1] else NULL
  }
  if (is.null(seat)) stop("no reachable seat to propose petals", call. = FALSE)
  cli::cli_alert_info("Proposing petals via {seat}")

  r <- call_provider(seat, petal_prompt(question, material, kinds, max_petals),
                     system = PETAL_SYSTEM, json = TRUE)
  if (!isTRUE(r$ok)) stop("petal proposal failed: ", substr(r$error, 1, 160), call. = FALSE)
  raw <- r$obj$petals %||% list()
  if (!length(raw)) { cli::cli_alert_warning("no petals proposed — the material may not support any"); return(invisible(NULL)) }

  con <- NULL
  if (!is.null(qid)) { con <- db_connect(); on.exit(DBI::dbDisconnect(con, shutdown = TRUE), add = TRUE) }

  petals <- lapply(raw, function(p) {
    v <- petal_verify(p, con = con, qid = qid)
    modifyList(p, list(verified = v$verified, needs_author = v$needs_author,
                       verify_note = v$note, proposed_by = seat))
  })

  n_ok  <- sum(vapply(petals, function(p) isTRUE(p$verified), logical(1)))
  n_aut <- sum(vapply(petals, function(p) isTRUE(p$needs_author), logical(1)))
  n_bad <- sum(vapply(petals, function(p) identical(p$verified, FALSE), logical(1)))
  cli::cli_alert_success("{length(petals)} proposed: {n_ok} anchored, {n_aut} need author confirmation, {n_bad} rejected")
  for (p in Filter(function(x) identical(x$verified, FALSE), petals))
    cli::cli_alert_danger("  rejected ({p$kind}): {substr(p$verify_note, 1, 100)}")

  ## A petal set is not the sum of its petals: see petals_interact().
  kept <- Filter(function(x) !identical(x$verified, FALSE), petals)
  ia <- if (length(kept) >= 2) petals_interact(kept) else NULL

  if (!is.null(con) && !is.null(qid)) petals_save(con, qid, petals)
  md <- petals_render(petals)
  if (!is.null(ia)) {
    md <- paste0(md, "\n\n## How these sit together\n\n",
      sprintf("%d petals kept: %d cost the authors, %d credit them.\n\n",
              ia$composition$n, ia$composition$n_costs, ia$composition$n_credits),
      if (length(ia$composition$flags))
        paste0(paste0("- ", ia$composition$flags, collapse = "\n"), "\n")
      else "- composition balanced\n")
  }
  if (!is.null(outfile)) { writeLines(md, outfile); cli::cli_alert_success("Written: {outfile}") }
  invisible(list(petals = petals, markdown = md, interaction = ia))
}

## ---------------------------------------------------------------- interaction
## Petals verified one by one can still misbehave as a set.
##
## Three failure modes, none visible when a petal is inspected alone:
##
##   REDUNDANCY  — several petals drawn from the same event. One n = 1 case ends up
##                 carrying a third of the manuscript's human weight, which is more
##                 than a single patient can bear.
##   CROWDING    — petals stacked into one section, so the Discussion turns into a
##                 sequence of set pieces and the argument disappears between them.
##   COMPOSED    — the one that matters. Every petal is true, and the portrait they
##     OVERCLAIM   compose is not. Six passages in the register of "we noticed, we
##                 were careful, we admitted our error" add up to a claim about the
##                 authors that no single petal makes and no anchor supports.
##
## The third has no per-petal fix, because nothing is wrong with any petal. It is a
## property of the set, and the only honest response is to show the balance and let
## the authors decide what to cut.

## Does a petal cost the authors something, or credit them? Assigned by kind, then
## adjusted by what the text actually does — a `surprise` that admits error costs,
## one that reports a lucky finding does not.
petal_valence <- function(p) {
  base <- switch(p$kind %||% "",
    surprise = -1, uncertainty = -1, craft = -1,
    clinical_narrative = 1, patient_impact = 1,
    motivation = 0, 0)
  t <- tolower(p$text %||% "")
  costs   <- c("we were wrong", "the error", "our error", "we had first", "does not hold",
               "still trouble", "is not enough", "it cost us", "we cannot", "we failed",
               "mistake", "withdrew", "we did not")
  credits <- c("was noticed", "we noticed", "because she was seen", "prompted", "we caught",
               "the right treatment", "made possible", "we kept", "grew out of")
  adj <- sum(vapply(costs,   function(k) grepl(k, t, fixed = TRUE), logical(1))) * -1 +
         sum(vapply(credits, function(k) grepl(k, t, fixed = TRUE), logical(1)))
  v <- base + sign(adj) * min(abs(adj), 1)
  max(-1, min(1, v))
}

.tok <- function(s) {
  stop_w <- c("the","a","an","of","in","on","and","or","to","for","with","by","is","are","was",
              "were","be","as","at","from","that","this","it","its","one","patient","week","weeks")
  setdiff(unique(strsplit(gsub("[^a-z0-9 ]", " ", tolower(s %||% "")), "\\s+")[[1]]), c("", stop_w))
}

#' Pairwise relations between petals. Deterministic: anchor overlap and placement.
petal_pairs <- function(petals) {
  n <- length(petals); if (n < 2) return(NULL)
  rows <- list()
  for (i in 1:(n - 1)) for (j in (i + 1):n) {
    a <- petals[[i]]; b <- petals[[j]]
    ta <- .tok(a$anchor); tb <- .tok(b$anchor)
    jac <- if (length(union(ta, tb))) length(intersect(ta, tb)) / length(union(ta, tb)) else 0
    rel <- NA_character_; note <- NA_character_
    if (jac >= 0.45) {
      rel <- "same_event"
      note <- "both drawn from the same event — using both over-weights a single case"
    } else if (jac >= 0.22) {
      rel <- "shared_source"
      note <- "anchors overlap; check they are not two readings of one fact"
    }
    if (identical(a$section, b$section) && identical(a$kind, b$kind)) {
      rel <- if (is.na(rel)) "crowds" else rel
      note <- paste(na.omit(c(note, "same kind in the same section — they will read as repetition")), collapse = "; ")
    }
    if (!is.na(rel)) rows[[length(rows) + 1]] <-
      data.frame(a = a$id %||% i, b = b$id %||% j, relation = rel,
                 overlap = round(jac, 2), note = note, stringsAsFactors = FALSE)
  }
  ## NB: this returns a plain data.frame (or NULL). petal_tension() below returns a
  ## LIST because it must report what it could not judge; do not unify the two.
  if (!length(rows)) return(NULL)
  do.call(rbind, rows)
}

#' Set-level properties. This is where composed overclaim shows up.
petal_composition <- function(petals) {
  v <- vapply(petals, petal_valence, numeric(1))
  secs <- table(vapply(petals, function(p) p$section %||% "?", character(1)))
  kinds <- table(vapply(petals, function(p) p$kind %||% "?", character(1)))
  aks <- table(vapply(petals, function(p) p$anchor_kind %||% "?", character(1)))
  n <- length(petals)

  ## how concentrated are the anchors? cluster petals whose anchors overlap heavily
  clusters <- seq_len(n)
  for (i in seq_len(n)) for (j in seq_len(n)) if (i < j) {
    if (length(union(.tok(petals[[i]]$anchor), .tok(petals[[j]]$anchor))) &&
        length(intersect(.tok(petals[[i]]$anchor), .tok(petals[[j]]$anchor))) /
        length(union(.tok(petals[[i]]$anchor), .tok(petals[[j]]$anchor))) >= 0.45)
      clusters[j] <- clusters[i]
  }
  biggest <- max(table(clusters))

  flags <- character()
  if (sum(v > 0) > 0 && sum(v < 0) == 0)
    flags <- c(flags, "every petal credits the authors and none costs them — a set this flattering reads as curated, whatever each anchor says")
  else if (n >= 4 && sum(v > 0) >= 3 * max(1, sum(v < 0)))
    flags <- c(flags, sprintf("%d petals credit the authors against %d that cost them — the composition leans self-serving", sum(v > 0), sum(v < 0)))
  if (max(secs) > n / 2)
    flags <- c(flags, sprintf("%d of %d petals sit in '%s' — that section will read as a sequence of set pieces", max(secs), n, names(secs)[which.max(secs)]))
  if (biggest >= 2 && biggest >= n / 3)
    flags <- c(flags, sprintf("%d petals derive from one event — a single case is carrying too much of the manuscript's human weight", biggest))
  if ((aks[["record"]] %||% 0) > n / 2)
    flags <- c(flags, sprintf("%d of %d anchors are clinical records, which no reader can check — mix in anchors tied to the data", aks[["record"]] %||% 0, n))

  ## Petals resting on a single patient. Different patients do not collide pairwise,
  ## so this concentration is invisible above: three petals about three different
  ## people still means three-sixths of the human content is n = 1 anecdote, and in
  ## this design the individuals with the most quotable courses are usually the ones
  ## who left the study.
  anec <- vapply(petals, function(p) {
    a <- tolower(p$anchor %||% "")
    grepl("^one patient|\\bone patient\\b|a single patient", a) && !grepl("patients", a)
  }, logical(1))
  if (sum(anec) >= 2 && sum(anec) >= n / 3)
    flags <- c(flags, sprintf("%d of %d petals rest on a single patient each — the human content is carried by anecdote rather than by the cohort", sum(anec), n))

  list(n = n, valence = v, n_anecdote = sum(anec),
       n_costs = sum(v < 0), n_credits = sum(v > 0), n_neutral = sum(v == 0),
       sections = secs, kinds = kinds, anchor_kinds = aks,
       largest_anchor_cluster = biggest, flags = flags)
}

#' Full interaction report.
petals_interact <- function(petals, quiet = FALSE) {
  pr <- petal_pairs(petals)
  cm <- petal_composition(petals)
  if (!quiet) {
    cli::cli_h2("Pairwise")
    if (is.null(pr)) cli::cli_alert_success("no redundancy or crowding between petals")
    else for (i in seq_len(nrow(pr)))
      cli::cli_alert_warning("{pr$a[i]} <-> {pr$b[i]} [{pr$relation[i]}, overlap {pr$overlap[i]}] {pr$note[i]}")
    cli::cli_h2("Composition")
    cli::cli_text("  {cm$n} petals: {cm$n_costs} cost the authors, {cm$n_credits} credit them, {cm$n_neutral} neutral")
    cli::cli_text("  sections: {paste(names(cm$sections), cm$sections, sep='=', collapse='  ')}")
    cli::cli_text("  anchor kinds: {paste(names(cm$anchor_kinds), cm$anchor_kinds, sep='=', collapse='  ')}")
    if (!length(cm$flags)) cli::cli_alert_success("composition balanced")
    else for (f in cm$flags) cli::cli_alert_danger(f)
  }
  invisible(list(pairs = pr, composition = cm))
}

## ---------------------------------------------------------------- semantic tension
## Two petals can each be true and still undercut one another: "we verified every
## record" beside "we found late that four were duplicated". No string comparison
## settles that, so it is the one part of this analysis that needs a model. Kept
## separate and optional — the deterministic pass above stands on its own.
TENSION_SYSTEM <- "You judge whether two passages from the same scientific manuscript sit badly together. You are not checking whether either is true; assume both are. Answer with valid JSON only."

## Returns list(pairs, judged, failed, seat). `failed` is the count of pairs the
## model could not judge — never folded into "nothing found".
##
## Two lessons are encoded here, both learned the hard way in this project:
##
##   A model that cannot be reached must not look like a clean result. On the first
##   run of this function Gemini was returning 503 and every pair came back "none";
##   the output was indistinguishable from a manuscript with no tension in it.
##
##   A small local model cannot do this judgement. llama3.1:8b flagged all three
##   test pairs at severity 1.0, including two with no relationship — the same
##   failure mode that bars local models from authoring R checks. Frontier seats
##   only, unless SCICOUNCIL_TENSION_ANY_SEAT is set.
petal_tension <- function(petals, seat = NULL, max_pairs = 12) {
  n <- length(petals)
  if (n < 2) return(list(pairs = NULL, judged = 0L, failed = 0L, seat = NA_character_))
  frontier <- c("claude","openai","gemini","deepseek","xai")
  if (is.null(seat)) {
    av <- available_panelists()
    fr <- av[vapply(av, function(s) split_id(s)$provider %in% frontier, logical(1))]
    seat <- if (length(fr)) fr[1] else NULL
  }
  if (is.null(seat))
    return(list(pairs = NULL, judged = 0L, failed = 0L, seat = NA_character_,
                note = "no frontier seat reachable — tension not judged. A local model over-flags this task and is not accepted."))
  if (!(split_id(seat)$provider %in% frontier) &&
      !nzchar(Sys.getenv("SCICOUNCIL_TENSION_ANY_SEAT")))
    return(list(pairs = NULL, judged = 0L, failed = 0L, seat = seat,
                note = sprintf("%s is not a frontier seat; it would flag every pair. Set SCICOUNCIL_TENSION_ANY_SEAT=1 to override.", seat)))
  pairs <- list()
  for (i in 1:(n - 1)) for (j in (i + 1):n) pairs[[length(pairs) + 1]] <- c(i, j)
  if (length(pairs) > max_pairs) pairs <- pairs[seq_len(max_pairs)]

  rows <- list(); judged <- 0L; failed <- 0L
  for (ij in pairs) {
    a <- petals[[ij[1]]]; b <- petals[[ij[2]]]
    p <- sprintf('
PASSAGE A (%s, %s section):
%s

PASSAGE B (%s, %s section):
%s

Do these two sit badly together in one manuscript? Consider only:
  - contradiction: one implies something the other denies
  - undercutting: one weakens the credibility of the other
  - repetition: they make the same point in different words

Most pairs are fine. Answer "none" unless there is a real problem a reviewer would notice.

JSON only: {"relation":"none|contradiction|undercuts|repetition","severity":0.0,"why":"<12 words>"}',
      a$kind %||% "?", a$section %||% "?", a$text %||% "",
      b$kind %||% "?", b$section %||% "?", b$text %||% "")
    r <- tryCatch(call_provider(seat, p, system = TENSION_SYSTEM, json = TRUE, timeout = 90),
                  error = function(e) NULL)
    if (is.null(r) || !isTRUE(r$ok)) { failed <- failed + 1L; next }
    judged <- judged + 1L
    rel <- tryCatch(tolower(as.character(r$obj$relation)[1]), error = function(e) NULL)
    ## NA is not FALSE: tolower(NULL) gives character(0) and nzchar(NA) gives NA,
    ## either of which makes the `if` below error rather than skip. Normalise first.
    if (is.null(rel) || !length(rel) || is.na(rel)) rel <- "none"
    if (rel != "none")
      rows[[length(rows) + 1]] <- data.frame(
        a = a$id %||% ij[1], b = b$id %||% ij[2], relation = rel,
        severity = suppressWarnings(as.numeric(r$obj$severity %||% NA))[1],
        why = r$obj$why %||% "", judged_by = seat, stringsAsFactors = FALSE)
  }
  list(pairs = if (length(rows)) do.call(rbind, rows) else NULL,
       judged = judged, failed = failed, seat = seat)
}
