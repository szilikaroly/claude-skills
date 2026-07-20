## evidence.R — citation grounding against real literature databases.
## A panelist that cites a DOI/PMID which does not resolve has fabricated its
## evidence; that is caught here deterministically, with no model in the loop.
## Both APIs are keyless. Be polite: they are free public infrastructure.

suppressPackageStartupMessages({ library(httr2); library(jsonlite) })

## Crossref and Europe PMC ask for a contact address in the User-Agent so they can
## reach you if a script misbehaves. Set SCICOUNCIL_CONTACT to your own address.
UA <- sprintf("science-council/1.0 (R; mailto:%s)",
              Sys.getenv("SCICOUNCIL_CONTACT", "anonymous@example.org"))

extract_doi <- function(x) {
  m <- regmatches(x, regexpr("10\\.[0-9]{4,9}/[-._;()/:A-Za-z0-9]+", x))
  if (length(m) == 0) return(NA_character_)
  sub("[.,;)\\]]+$", "", m[1])
}

extract_pmid <- function(x) {
  x <- trimws(x)
  ## a citation that is nothing but a 6-8 digit number is a bare PMID
  if (grepl("^[0-9]{6,8}$", x)) return(x)
  m <- regmatches(x, regexpr("(?i)pmid:?\\s*([0-9]{6,8})", x, perl = TRUE))
  if (length(m) == 0) return(NA_character_)
  gsub("\\D", "", m[1])
}

#' Look a DOI up in Crossref. Returns NULL when it does not exist.
crossref_lookup <- function(doi) {
  tryCatch({
    resp <- request(paste0("https://api.crossref.org/works/", utils::URLencode(doi, TRUE))) |>
      req_user_agent(UA) |> req_timeout(20) |>
      req_error(is_error = function(r) FALSE) |> req_perform()
    if (resp_status(resp) != 200) return(NULL)
    m <- resp_body_json(resp)$message
    list(title = (m$title %||% list(""))[[1]],
         year  = tryCatch(m$issued$`date-parts`[[1]][[1]], error = function(e) NA),
         journal = (m$`container-title` %||% list(""))[[1]],
         type = m$type %||% NA, source = "crossref")
  }, error = function(e) NULL)
}

#' Search Europe PMC (covers PubMed + preprints). `query` is EPMC query syntax.
epmc_search <- function(query, limit = 3) {
  tryCatch({
    resp <- request("https://www.ebi.ac.uk/europepmc/webservices/rest/search") |>
      req_url_query(query = query, format = "json", resultType = "core", pageSize = limit) |>
      req_user_agent(UA) |> req_timeout(25) |>
      req_error(is_error = function(r) FALSE) |> req_perform()
    if (resp_status(resp) != 200) return(NULL)
    b <- resp_body_json(resp)
    list(hits = b$hitCount %||% 0, results = b$resultList$result %||% list())
  }, error = function(e) NULL)
}

## ---- relevance ---------------------------------------------------------------
## Resolving an identifier only proves the paper EXISTS. A model can cite a real
## PMID about protein folding as evidence for a blood-pressure claim and sail
## through. So a resolved citation must also be ABOUT the claim.
##
## Judged by a local Ollama model (free, and this is a cheap topical call).
## Falls back to lexical overlap when no local model is reachable.
RELEVANCE_PROMPT <- '
CLAIM: "%s"

CITED PAPER
  Title: %s
  Journal: %s (%s)
  Abstract: %s

Is this paper plausibly about the same subject matter as the claim — could it
serve as evidence for or against it? Judge TOPIC ONLY: you are not deciding
whether the claim is true, only whether this paper is about it at all.

"related"   — same subject; it could bear on the claim either way
"unrelated" — a different field or topic entirely; citing it here is a category error

JSON only: {"verdict":"related","why":"<8 words>"}'

judge_relevance <- function(claim, title, journal = "", year = "", abstract = "") {
  if (is.null(title) || is.na(title) || !nzchar(title))
    return(list(relevant = NA, note = "nothing resolved to judge"))
  p <- sprintf(RELEVANCE_PROMPT, claim, title, journal %||% "", year %||% "",
               substr(abstract %||% "", 1, 1200))
  ok <- exists("call_provider")
  if (ok) {
    seat <- Sys.getenv("SCICOUNCIL_RELEVANCE_SEAT", "ollama/llama3.1:8b")
    r <- tryCatch(call_provider(seat, p, json = TRUE, timeout = 90), error = function(e) NULL)
    v <- tryCatch(tolower(r$obj$verdict), error = function(e) NULL)
    if (!is.null(v) && nzchar(v))
      return(list(relevant = identical(v, "related"),
                  note = paste0("topic judged `", v, "` by ", seat,
                                if (!is.null(r$obj$why)) paste0(": ", r$obj$why) else "")))
  }
  ## lexical fallback: do the claim and the title share meaningful vocabulary?
  stop_w <- c("the","a","an","of","in","on","and","or","to","for","with","by","is","are",
              "be","as","at","from","that","this","it","its","can","may","has","have")
  tok <- function(s) setdiff(unique(strsplit(gsub("[^a-z0-9 ]", " ", tolower(s)), "\\s+")[[1]]), c("", stop_w))
  ct <- tok(claim); tt <- tok(paste(title, abstract))
  if (!length(ct) || !length(tt)) return(list(relevant = NA, note = "insufficient text"))
  ov <- length(intersect(ct, tt)) / length(ct)
  list(relevant = ov >= 0.15,
       note = sprintf("lexical overlap %.0f%% (no local judge available)", 100 * ov))
}

#' Verify one citation string. Strategy, in order:
#'   1. DOI  -> Crossref, then Europe PMC
#'   2. PMID -> Europe PMC
#'   3. free text -> Europe PMC title search (weak: "plausible" not "verified")
#' Returns verified = TRUE only for an identifier that actually resolves.
verify_citation <- function(citation) {
  out <- list(citation = citation, verified = FALSE, note = NA_character_,
              title = NA_character_, year = NA_integer_,
              doi = NA_character_, pmid = NA_character_, method = NA_character_,
              journal = NA_character_, abstract = NA_character_)
  if (is.null(citation) || !nzchar(trimws(citation))) {
    out$note <- "empty citation"; return(out)
  }
  doi <- extract_doi(citation); pmid <- extract_pmid(citation)
  out$doi <- doi; out$pmid <- pmid

  if (!is.na(doi)) {
    out$method <- "doi"
    cr <- crossref_lookup(doi)
    if (!is.null(cr)) {
      out$verified <- TRUE; out$title <- cr$title; out$year <- cr$year
      out$journal <- cr$journal; out$note <- paste0("Crossref: ", cr$journal)
      ## Crossref has no abstract; pull one from Europe PMC for the topic check
      ep <- epmc_search(sprintf('DOI:"%s"', doi), 1)
      if (!is.null(ep) && ep$hits > 0) out$abstract <- ep$results[[1]]$abstractText %||% NA
      return(out)
    }
    ep <- epmc_search(sprintf('DOI:"%s"', doi), 1)
    if (!is.null(ep) && ep$hits > 0) {
      r <- ep$results[[1]]
      out$verified <- TRUE; out$title <- r$title %||% NA
      out$year <- suppressWarnings(as.integer(r$pubYear %||% NA))
      out$pmid <- r$pmid %||% NA; out$journal <- r$journalInfo$journal$title %||% NA
      out$abstract <- r$abstractText %||% NA; out$note <- "Europe PMC"; return(out)
    }
    out$note <- "DOI does not resolve in Crossref or Europe PMC — likely fabricated"
    return(out)
  }

  if (!is.na(pmid)) {
    out$method <- "pmid"
    ep <- epmc_search(sprintf('EXT_ID:%s AND SRC:MED', pmid), 1)
    if (!is.null(ep) && ep$hits > 0) {
      r <- ep$results[[1]]
      out$verified <- TRUE; out$title <- r$title %||% NA
      out$year <- suppressWarnings(as.integer(r$pubYear %||% NA))
      out$doi <- r$doi %||% NA; out$journal <- r$journalInfo$journal$title %||% NA
      out$abstract <- r$abstractText %||% NA; out$note <- "Europe PMC (PMID)"; return(out)
    }
    out$note <- "PMID not found in Europe PMC — likely fabricated"; return(out)
  }

  ## No identifier: try to find the work by its text. Never counts as verified.
  out$method <- "text"
  q <- gsub('"', " ", substr(citation, 1, 220))
  ep <- epmc_search(sprintf('TITLE:"%s"', q), 1)
  if (!is.null(ep) && ep$hits > 0) {
    r <- ep$results[[1]]
    out$title <- r$title %||% NA
    out$year <- suppressWarnings(as.integer(r$pubYear %||% NA))
    out$doi <- r$doi %||% NA; out$pmid <- r$pmid %||% NA
    out$note <- "title match in Europe PMC, but no identifier given — unconfirmed"
  } else {
    out$note <- "no identifier and no title match — unverifiable as stated"
  }
  out
}

#' Verify a list of citation strings; returns a data.frame, one row each.
verify_citations <- function(citations) {
  citations <- unique(citations[nzchar(trimws(citations %||% character()))])
  if (!length(citations)) return(NULL)
  do.call(rbind, lapply(citations, function(c1) as.data.frame(verify_citation(c1),
                                                              stringsAsFactors = FALSE)))
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a) == 1 && is.na(a))) b else a
