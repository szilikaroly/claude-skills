## assembly.R — one claim, the whole panel, every petal lens.
##
## The ordinary `council run` spreads a question across many claims and asks each
## panelist for a pro, a contra and a vote. An assembly does the opposite: it takes
## a SINGLE claim and goes deep, requiring each panelist to state its position
## through all six petal lenses, then to answer the others by name.
##
## Why the lenses. A panelist asked only for "the argument against" produces the
## argument it finds easiest — usually a methodological one. Forcing the same
## position through `patient_impact` and through `craft` surfaces objections that
## live in different places: what this costs the people in the study, what judgement
## the authors had to make. Panelists that agree on the verdict often disagree
## sharply once the lens changes, and that disagreement is the useful part.
##
## The anchor rule from petals.R still applies. A lens is a way of seeing the
## material, not a licence to invent it.

suppressPackageStartupMessages({ library(jsonlite); library(glue) })

## THE STANDING RULE OF THE ASSEMBLY
##
## No panelist may lie or invent data. Every panelist may foreground one fact over
## the others and read it differently.
##
## The two halves are not in tension, and the second is not a loophole. Honest
## scientific disagreement almost never turns on the facts being different; it
## turns on which fact is treated as decisive. Two readers of this cohort can both
## be truthful and still disagree about whether 93.2 per cent completion or four
## unexplained discontinuations is the number that settles the question. Forbidding
## that would not produce more rigour, only unanimity — panelists reciting the same
## safe reading of the same material.
##
## What the rule does need is a companion, because emphasis without disclosure is
## how cherry-picking works: cite the supporting facts, omit the ones that cut the
## other way, invent nothing, mislead completely. So each panelist must declare
## what it foregrounded AND what it set aside to do so. The setting-aside is the
## part that gets scrutinised, and the assembly record prints it beside the
## argument rather than at the end.
ASSEMBLY_RULE <- "THE RULE OF THIS ASSEMBLY. You may not lie, and you may not invent a fact, a number, a patient or a source. Everything you assert must be in the material. Within that limit you are free — and expected — to foreground the fact you consider decisive and to read it your own way. Two honest panelists disagree about which fact settles a question, not about what the facts are; that disagreement is what the assembly is for. One condition attaches: whatever you foreground, you must also name what you are setting aside in order to foreground it, and why that is defensible. Emphasis you declare is argument. Emphasis you conceal is cherry-picking, and the assembly will print your declaration next to your reasoning so a reader can judge which you did."

ASSEMBLY_SYSTEM <- paste("You are a panelist at a scientific assembly examining one claim. You owe the assembly a genuine position, argued from the material and nothing else. You will be asked to state that position through several different lenses; each lens must draw on something specific in the material, and you must name what. If a lens has nothing real behind it for this claim, say so in that lens rather than inventing content — an empty lens is an acceptable answer and a fabricated one is not. Write plainly, in your own voice, without flattery towards the authors or the other panelists.", ASSEMBLY_RULE, "Answer with valid JSON only.")

## ---------------------------------------------------------------- round 1: blind
assembly_prompt_open <- function(claim, material, lenses) {
  ld <- paste(vapply(lenses, function(k) {
    p <- PETAL_KINDS[[k]]
    sprintf('  "%s" — %s\n      through this lens, say: %s', k, p$label, p$wants)
  }, character(1)), collapse = "\n")
  glue('
THE CLAIM BEFORE THE ASSEMBLY:
<<claim>>

MATERIAL (the only permitted source of fact):
<<material>>

<<ASSEMBLY_RULE>>

State your position. You owe four things.

1. VERDICT — "support", "refute" or "uncertain", with a confidence from 0.0 to 1.0.

2. PRO and CONTRA — the strongest honest case for the claim, and the strongest
   honest case against it. Both are required whichever way you vote. Steelman the
   side you reject; a one-sided answer is a failed answer.

3. EMPHASIS — name the single fact in the material you treat as decisive, name what
   you are setting aside in order to treat it that way, and give your reading of it.
   Another panelist may foreground a different fact and be equally honest; what is
   not permitted is to foreground quietly.

4. LENSES — the same position seen six ways. For each lens give a short passage
   (2-4 sentences) and the specific fact in the material it rests on.

<<ld>>

If a lens genuinely has nothing behind it for this claim, return it with an empty
text and say why in the anchor field. That is a real answer.

JSON only:
{"verdict":"refute","confidence":0.8,
 "pro":"...","contra":"...",
 "emphasis":{"foregrounds":"<the fact you treat as decisive, quoted from the material>",
             "sets_aside":"<the fact you are consequently giving less weight, quoted>",
             "reading":"<why that ordering is defensible>"},
 "lenses":[{"kind":"motivation","text":"...","anchor":"..."}]}
', .open = "<<", .close = ">>")
}

## ---------------------------------------------------------------- round 2: reply
assembly_prompt_reply <- function(claim, material, record, me) {
  glue('
THE CLAIM:
<<claim>>

MATERIAL:
<<material>>

WHAT THE ASSEMBLY SAID (you are <<me>>; your own entry is included):
<<record>>

Now answer the others directly. For each panelist other than yourself:
  - name them
  - say whether you agree or disagree with their position
  - give the reason, pointing at their argument rather than restating your own
  - say plainly if they found something you had missed
  - address their EMPHASIS, not only their conclusion: they were required to name the
    fact they treated as decisive and the fact they set aside to do it. Say whether
    that ordering is defensible. A panelist may honestly foreground a different fact
    than you; what you should challenge is a weighting whose stated reason does not
    hold, or one where the thing set aside is the thing that settles the question.

Then state whether your own verdict has moved, and why or why not. Changing your
mind because the evidence warrants it is the point of the exercise; changing it
because you are outnumbered is not, and neither is digging in to save face.

JSON only:
{"replies":[{"to":"<panelist>","agree":true,"reason":"..."}],
 "verdict":"support","confidence":0.7,"moved":false,"moved_why":"..."}
', .open = "<<", .close = ">>")
}

## ---------------------------------------------------------------- record
assembly_format_record <- function(entries) {
  paste(vapply(entries, function(e) {
    ls <- paste(vapply(e$lenses %||% list(), function(l)
      sprintf("    [%s] %s", l$kind %||% "?", substr(l$text %||% "(empty)", 1, 300)),
      character(1)), collapse = "\n")
    ## The emphasis declaration MUST travel to the other panelists. Without it they
    ## can attack a conclusion but not its weighting, and weighting is where honest
    ## disagreement lives — which is the whole reason the rule requires declaring it.
    ## The first run of this protocol omitted it and a panelist had to spend its
    ## cross-examination reporting the omission instead of arguing.
    em <- e$emphasis %||% list()
    emtxt <- if (nzchar(trimws(em$foregrounds %||% "")))
      sprintf("\n  EMPHASIS:\n    foregrounds: %s\n    sets aside:  %s\n    reading:     %s",
              em$foregrounds, em$sets_aside %||% "(not declared)", substr(em$reading %||% "", 1, 400))
    else "\n  EMPHASIS: none declared — this panelist's weighting is unexamined."
    sprintf("%s — %s (confidence %.2f)\n  PRO: %s\n  CONTRA: %s%s\n  LENSES:\n%s",
            e$seat, toupper(e$verdict %||% "?"), as.numeric(e$confidence %||% 0),
            substr(e$pro %||% "", 1, 400), substr(e$contra %||% "", 1, 400), emtxt, ls)
  }, character(1)), collapse = "\n\n")
}

## ---------------------------------------------------------------- emphasis map
## The rule permits divergent emphasis; this makes the divergence legible.
##
## Three patterns are worth naming, and only the third is a fault:
##   SHARED     — panelists foreground the same fact. Agreement about what matters,
##                even where the verdicts differ.
##   DIVIDED    — panelists foreground different facts. This is the disagreement in
##                its clearest form, and usually more informative than the votes.
##   UNCLAIMED  — a fact every panelist set aside and none foregrounded. Nobody has
##                lied and nobody has argued for it either, so it leaves the assembly
##                unexamined. This is the blind spot the emphasis rule exists to expose.
assembly_emphasis_map <- function(entries) {
  em <- lapply(entries, function(e) {
    x <- e$emphasis %||% list()
    list(seat = e$seat, verdict = e$verdict %||% "?",
         fore = trimws(x$foregrounds %||% ""), aside = trimws(x$sets_aside %||% ""),
         reading = trimws(x$reading %||% ""))
  })
  declared <- Filter(function(x) nzchar(x$fore), em)
  if (!length(declared)) return(list(entries = em, shared = NULL, unclaimed = NULL,
                                     n_declared = 0L, note = "no panelist declared an emphasis"))

  ## do two panelists foreground the same thing? token overlap on the quoted fact
  tok <- function(s) {
    stop_w <- c("the","a","an","of","in","and","to","for","with","by","is","are","was","were",
                "that","this","it","no","not","all","one","four","from","on","as","at","which")
    setdiff(unique(strsplit(gsub("[^a-z0-9 ]", " ", tolower(s)), "\\s+")[[1]]), c("", stop_w))
  }
  shared <- list()
  if (length(declared) >= 2) for (i in 1:(length(declared) - 1)) for (j in (i + 1):length(declared)) {
    a <- tok(declared[[i]]$fore); b <- tok(declared[[j]]$fore)
    ov <- if (length(union(a, b))) length(intersect(a, b)) / length(union(a, b)) else 0
    shared[[length(shared) + 1]] <- data.frame(
      a = declared[[i]]$seat, b = declared[[j]]$seat, overlap = round(ov, 2),
      relation = if (ov >= 0.35) "same fact foregrounded" else "different facts foregrounded",
      stringsAsFactors = FALSE)
  }
  shared <- if (length(shared)) do.call(rbind, shared) else NULL

  ## a fact set aside by everyone and foregrounded by no one
  unclaimed <- character()
  for (x in em) {
    if (!nzchar(x$aside)) next
    ta <- tok(x$aside)
    claimed <- any(vapply(declared, function(d) {
      tf <- tok(d$fore)
      length(union(ta, tf)) && length(intersect(ta, tf)) / length(union(ta, tf)) >= 0.35
    }, logical(1)))
    if (!claimed) unclaimed <- c(unclaimed, sprintf("%s set aside: %s", x$seat, substr(x$aside, 1, 150)))
  }
  list(entries = em, shared = shared, unclaimed = unique(unclaimed),
       n_declared = length(declared))
}

## ---------------------------------------------------------------- markdown
assembly_markdown <- function(claim, material, entries, replies, lenses, interaction = NULL) {
  L <- c("# Assembly record", "",
         sprintf("**Claim under examination:** %s", claim), "",
         sprintf("Panel: %s", paste(vapply(entries, function(e) sprintf("`%s`", e$seat), character(1)), collapse = ", ")),
         sprintf("Lenses required of each panelist: %s", paste(sprintf("`%s`", lenses), collapse = ", ")), "",
         "Round 1 was blind: no panelist saw another before committing. Round 2 is the",
         "cross-examination, in which each answers the others by name.", "",
         "---", "", "## Round 1 — independent positions", "")
  for (e in entries) {
    L <- c(L, sprintf("### %s", e$seat), "",
           sprintf("**Verdict:** %s (confidence %.2f)", toupper(e$verdict %||% "?"), as.numeric(e$confidence %||% 0)), "",
           "**The case for**", "", sprintf("> %s", e$pro %||% "*(none given)*"), "",
           "**The case against**", "", sprintf("> %s", e$contra %||% "*(none given)*"), "")
    em <- e$emphasis %||% list()
    if (nzchar(trimws(em$foregrounds %||% ""))) {
      L <- c(L, "**Declared emphasis**", "",
             sprintf("- *Foregrounds:* %s", em$foregrounds),
             sprintf("- *Sets aside:* %s", em$sets_aside %||% "*(not declared — the rule requires this)*"),
             sprintf("- *Reading:* %s", em$reading %||% ""), "")
    } else {
      L <- c(L, "**Declared emphasis** — *none declared. Under the rule of this assembly a",
             "panelist may foreground any fact but may not do so silently; this position's",
             "weighting is therefore unexamined.*", "")
    }
    L <- c(L, "**Through the lenses**", "")
    for (l in (e$lenses %||% list())) {
      lab <- PETAL_KINDS[[l$kind %||% ""]]$label %||% ""
      txt <- trimws(l$text %||% "")
      if (!nzchar(txt)) {
        L <- c(L, sprintf("*%s — %s:* **empty.** %s", l$kind %||% "?", lab, l$anchor %||% "no reason given"), "")
      } else {
        v <- petal_verify(list(kind = l$kind, text = txt, anchor = l$anchor, anchor_kind = l$anchor_kind %||% "data"))
        mark <- if (isTRUE(v$verified)) "" else if (isTRUE(v$needs_author)) " *[needs author confirmation]*" else sprintf(" *[anchor failed: %s]*", v$note)
        L <- c(L, sprintf("**%s** — %s%s", l$kind %||% "?", lab, mark), "",
               sprintf("> %s", txt), "", sprintf("*Anchor:* %s", l$anchor %||% "*(none)*"), "")
      }
    }
    L <- c(L, "")
  }
  if (length(replies)) {
    L <- c(L, "---", "", "## Round 2 — cross-examination", "")
    for (r in replies) {
      L <- c(L, sprintf("### %s replies", r$seat), "")
      for (rep in (r$replies %||% list()))
        L <- c(L, sprintf("- **to %s** — %s. %s", rep$to %||% "?",
                          if (isTRUE(rep$agree)) "agrees" else "disagrees", rep$reason %||% ""))
      L <- c(L, "",
             sprintf("**Verdict after hearing the assembly:** %s (confidence %.2f) — %s",
                     toupper(r$verdict %||% "?"), as.numeric(r$confidence %||% 0),
                     if (isTRUE(r$moved)) paste("moved:", r$moved_why %||% "") else paste("unmoved:", r$moved_why %||% "")), "")
    }
  }
  if (!is.null(attr(entries, "emphasis_map"))) {
    emap <- attr(entries, "emphasis_map")
    L <- c(L, "---", "", "## Where the emphasis fell", "",
           "The rule of this assembly permits any panelist to foreground one fact over the",
           "others and read it differently. It does not permit doing so silently. This is what",
           "each declared, and what nobody claimed.", "")
    if (!is.null(emap$shared) && nrow(emap$shared)) {
      L <- c(L, "| panelist | panelist | overlap | |", "|---|---|---:|---|")
      for (i in seq_len(nrow(emap$shared)))
        L <- c(L, sprintf("| `%s` | `%s` | %.2f | %s |", emap$shared$a[i], emap$shared$b[i],
                          emap$shared$overlap[i], emap$shared$relation[i]))
      L <- c(L, "")
    }
    if (length(emap$unclaimed)) {
      L <- c(L, "**Set aside by someone and foregrounded by no one.** Nobody lied about these",
             "and nobody argued from them either, so they leave the assembly unexamined:", "")
      L <- c(L, paste0("- ", emap$unclaimed), "")
    } else if (emap$n_declared > 0) {
      L <- c(L, "*Every fact that one panelist set aside was foregrounded by another — the",
             "assembly left no declared fact unexamined.*", "")
    }
  }
  if (!is.null(interaction)) {
    cm <- interaction$composition
    L <- c(L, "---", "", "## How the lenses sit together", "",
           sprintf("Across the whole assembly, %d lens passages carried content: %d cost the authors, %d credit them.",
                   cm$n, cm$n_costs, cm$n_credits), "")
    if (length(cm$flags)) L <- c(L, paste0("- ", cm$flags), "")
    else L <- c(L, "- composition balanced", "")
  }
  L <- c(L, "---", "",
         "*Generated by science-council `assembly`. Lens passages are checked against their",
         "anchors by `petal_verify()`; a failed anchor is marked in place rather than removed,",
         "so the reader can see what a panelist asserted without support.*")
  paste(L, collapse = "\n")
}

## ---------------------------------------------------------------- orchestration
assembly_run <- function(claim, material, panel = NULL, lenses = names(PETAL_KINDS),
                         rounds = 2, outfile = NULL) {
  panel <- panel %||% available_panelists()
  if (length(panel) < 2) stop("an assembly needs at least 2 seats; got: ",
                              paste(panel, collapse = ", "), call. = FALSE)
  cli::cli_h1("Assembly")
  cli::cli_alert_info("Claim: {substr(claim, 1, 90)}")
  cli::cli_alert_info("Seats: {paste(panel, collapse=' | ')}")

  ## round 1 — blind
  cli::cli_alert_info("Round 1/2 — independent positions ({length(lenses)} lenses each)")
  p1 <- assembly_prompt_open(claim, material, lenses)
  res <- call_panel(panel, function(id) p1, system = ASSEMBLY_SYSTEM, json = TRUE)
  entries <- list()
  for (r in res) {
    if (!isTRUE(r$ok)) { cli::cli_alert_warning("{r$provider}: {substr(r$error, 1, 90)}"); next }
    o <- r$obj
    n_full <- sum(vapply(o$lenses %||% list(), function(l) nzchar(trimws(l$text %||% "")), logical(1)))
    cli::cli_alert_success("{r$provider}: {toupper(o$verdict %||% '?')} — {n_full}/{length(lenses)} lenses filled ({round(r$latency)}s)")
    entries[[length(entries) + 1]] <- list(seat = r$provider, verdict = o$verdict,
      confidence = o$confidence, pro = o$pro, contra = o$contra,
      emphasis = o$emphasis, lenses = o$lenses)
  }
  if (length(entries) < 2) stop("fewer than 2 seats answered — no assembly", call. = FALSE)

  ## round 2 — cross-examination
  replies <- list()
  if (rounds >= 2) {
    cli::cli_alert_info("Round 2/2 — cross-examination")
    rec <- assembly_format_record(entries)
    seats <- vapply(entries, function(e) e$seat, character(1))
    res2 <- call_panel(seats, function(id) assembly_prompt_reply(claim, material, rec, id),
                       system = ASSEMBLY_SYSTEM, json = TRUE)
    for (r in res2) {
      if (!isTRUE(r$ok)) { cli::cli_alert_warning("{r$provider} did not reply: {substr(r$error, 1, 80)}"); next }
      o <- r$obj
      cli::cli_alert_success("{r$provider}: {toupper(o$verdict %||% '?')}{if (isTRUE(o$moved)) ' (moved)' else ''}")
      replies[[length(replies) + 1]] <- list(seat = r$provider, replies = o$replies,
        verdict = o$verdict, confidence = o$confidence, moved = o$moved, moved_why = o$moved_why)
    }
  }

  ## lens composition across the whole assembly
  allp <- list()
  for (e in entries) for (l in (e$lenses %||% list())) {
    if (!nzchar(trimws(l$text %||% ""))) next
    allp[[length(allp) + 1]] <- list(id = paste0(substr(e$seat, 1, 6), ":", l$kind),
      kind = l$kind, section = "discussion", text = l$text,
      anchor = l$anchor, anchor_kind = l$anchor_kind %||% "data")
  }
  ia <- if (length(allp) >= 2) petals_interact(allp, quiet = TRUE) else NULL

  emap <- assembly_emphasis_map(entries)
  n_undeclared <- sum(vapply(emap$entries, function(x) !nzchar(x$fore), logical(1)))
  if (n_undeclared > 0)
    cli::cli_alert_warning("{n_undeclared} panelist(s) argued without declaring an emphasis — their weighting is unexamined")
  if (length(emap$unclaimed))
    cli::cli_alert_warning("{length(emap$unclaimed)} fact(s) set aside by someone and foregrounded by no one")
  attr(entries, "emphasis_map") <- emap

  md <- assembly_markdown(claim, material, entries, replies, lenses, ia)
  if (!is.null(outfile)) { writeLines(md, outfile); cli::cli_alert_success("Record: {outfile}") }
  invisible(list(entries = entries, replies = replies, interaction = ia,
                 emphasis = emap, markdown = md))
}
