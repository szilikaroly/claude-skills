## db.R — indexed, append-only evidence store for validated claims.
## Nothing is ever silently overwritten: every argument, vote, R check and
## citation check is kept so any claim's status can be reconstructed.

suppressPackageStartupMessages({ library(DBI); library(duckdb); library(jsonlite); library(digest) })

db_path <- function() Sys.getenv("SCICOUNCIL_DB",
  file.path(Sys.getenv("SCICOUNCIL_DIR", "."), "db", "council.duckdb"))

db_connect <- function(path = db_path(), read_only = FALSE) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  ## keep the FTS extension on disk instead of re-downloading it every session
  if (!nzchar(Sys.getenv("DUCKDB_EXTENSION_DIRECTORY"))) {
    ed <- file.path(dirname(path), "duckdb_extensions")
    dir.create(ed, recursive = TRUE, showWarnings = FALSE)
    Sys.setenv(DUCKDB_EXTENSION_DIRECTORY = ed)
  }
  con <- dbConnect(duckdb::duckdb(), dbdir = path, read_only = read_only)
  if (!read_only) db_init(con)
  con
}

db_init <- function(con) {
  dbExecute(con, "CREATE SEQUENCE IF NOT EXISTS seq_arg START 1")
  dbExecute(con, "CREATE SEQUENCE IF NOT EXISTS seq_vote START 1")
  dbExecute(con, "CREATE SEQUENCE IF NOT EXISTS seq_check START 1")
  dbExecute(con, "CREATE SEQUENCE IF NOT EXISTS seq_ev START 1")

  dbExecute(con, "CREATE TABLE IF NOT EXISTS questions (
      id VARCHAR PRIMARY KEY, question TEXT NOT NULL, domain VARCHAR,
      context TEXT, status VARCHAR DEFAULT 'open', panel VARCHAR,
      rounds INTEGER, created_at TIMESTAMP, finished_at TIMESTAMP)")

  ## status: validated | contested | refuted | unresolved
  dbExecute(con, "CREATE TABLE IF NOT EXISTS claims (
      id VARCHAR PRIMARY KEY, question_id VARCHAR, claim TEXT NOT NULL,
      claim_norm TEXT, claim_type VARCHAR, proposed_by VARCHAR,
      testable_in_r BOOLEAN DEFAULT FALSE,
      status VARCHAR DEFAULT 'unresolved', status_reason TEXT,
      consensus_score DOUBLE, agreement_kappa DOUBLE, krippendorff_alpha DOUBLE,
      n_support INTEGER, n_refute INTEGER, n_uncertain INTEGER,
      r_check_passed BOOLEAN, citations_verified INTEGER, citations_total INTEGER,
      final_round INTEGER, created_at TIMESTAMP, validated_at TIMESTAMP)")

  ## Every panelist owes BOTH a pro and a contra argument on every claim.
  dbExecute(con, "CREATE TABLE IF NOT EXISTS arguments (
      id INTEGER DEFAULT nextval('seq_arg') PRIMARY KEY,
      claim_id VARCHAR, panelist VARCHAR, model VARCHAR, round INTEGER,
      stance VARCHAR, argument TEXT, evidence TEXT, strength DOUBLE,
      created_at TIMESTAMP)")

  dbExecute(con, "CREATE TABLE IF NOT EXISTS verdicts (
      id INTEGER DEFAULT nextval('seq_vote') PRIMARY KEY,
      claim_id VARCHAR, panelist VARCHAR, model VARCHAR, round INTEGER,
      verdict VARCHAR, confidence DOUBLE, rationale TEXT, revised BOOLEAN,
      created_at TIMESTAMP)")

  dbExecute(con, "CREATE TABLE IF NOT EXISTS r_checks (
      id INTEGER DEFAULT nextval('seq_check') PRIMARY KEY,
      claim_id VARCHAR, author VARCHAR, code TEXT, passed BOOLEAN,
      result TEXT, error TEXT, runtime_sec DOUBLE, created_at TIMESTAMP)")

  ## verified = the identifier resolves to a real paper.
  ## relevant = that paper is actually about this claim. Both are required before
  ## a citation counts as evidence: a real DOI from the wrong field is still junk.
  dbExecute(con, "CREATE TABLE IF NOT EXISTS evidence (
      id INTEGER DEFAULT nextval('seq_ev') PRIMARY KEY,
      claim_id VARCHAR, panelist VARCHAR, source_type VARCHAR,
      citation TEXT, doi VARCHAR, pmid VARCHAR, url TEXT,
      verified BOOLEAN, verify_note TEXT, resolved_title TEXT, year INTEGER,
      relevant BOOLEAN, relevance_note TEXT,
      created_at TIMESTAMP)")

  ## Calibration: how often has each panelist's vote matched the final status?
  dbExecute(con, "CREATE TABLE IF NOT EXISTS calibration (
      panelist VARCHAR PRIMARY KEY, model VARCHAR,
      n_votes INTEGER DEFAULT 0, n_correct INTEGER DEFAULT 0,
      brier_sum DOUBLE DEFAULT 0, weight DOUBLE DEFAULT 1.0,
      updated_at TIMESTAMP)")

  dbExecute(con, "CREATE TABLE IF NOT EXISTS claim_links (
      from_claim VARCHAR, to_claim VARCHAR, relation VARCHAR, note TEXT)")

  invisible(TRUE)
}

## DuckDB's FTS index is a snapshot: rebuild after inserts.
db_reindex <- function(con) {
  n <- dbGetQuery(con, "SELECT count(*) n FROM claims")$n
  if (n == 0) return(invisible(FALSE))
  tryCatch({
    dbExecute(con, "INSTALL fts"); dbExecute(con, "LOAD fts")
    dbExecute(con, "PRAGMA create_fts_index('claims', 'id', 'claim', overwrite=1)")
    invisible(TRUE)
  }, error = function(e) { message("FTS index skipped: ", conditionMessage(e)); invisible(FALSE) })
}

db_search <- function(con, query, limit = 20, status = NULL) {
  ok <- tryCatch({ dbExecute(con, "LOAD fts"); TRUE }, error = function(e) FALSE)
  if (ok) {
    res <- tryCatch(dbGetQuery(con, sprintf(
      "SELECT id, claim, status, consensus_score, question_id,
              fts_main_claims.match_bm25(id, ?) AS score
       FROM claims WHERE score IS NOT NULL %s
       ORDER BY score DESC LIMIT %d",
      if (is.null(status)) "" else sprintf("AND status = '%s'", status), limit),
      params = list(query)), error = function(e) NULL)
    if (!is.null(res) && nrow(res)) return(res)
  }
  ## fall back to substring match if no FTS index exists yet
  dbGetQuery(con, sprintf(
    "SELECT id, claim, status, consensus_score, question_id, NULL AS score
     FROM claims WHERE lower(claim) LIKE lower(?) %s LIMIT %d",
    if (is.null(status)) "" else sprintf("AND status = '%s'", status), limit),
    params = list(paste0("%", query, "%")))
}

new_id <- function(prefix, ...) paste0(prefix, "_", substr(digest(paste0(..., Sys.time())), 1, 10))

## Normalised form used for cross-panelist claim de-duplication.
norm_claim <- function(x) {
  x <- tolower(trimws(x))
  x <- gsub("[[:punct:]]+", " ", x)
  gsub("\\s+", " ", x)
}

#' Calibration weight from a panelist's track record, shrunk toward 1.0.
#'
#' Raw accuracy x 2 would hand a brand-new panelist double weight after one lucky
#' session. Instead we add PRIOR_N pseudo-votes at 50%, so weight starts at 1.0
#' and only moves as real evidence accumulates: 9/9 correct gives ~1.5, not 2.0,
#' and it takes dozens of votes to approach the cap.
CAL_PRIOR_N <- 10
calibration_weight <- function(n_votes, n_correct) {
  acc <- (n_correct + CAL_PRIOR_N / 2) / (n_votes + CAL_PRIOR_N)
  max(0.25, min(2.0, acc * 2))
}

db_upsert_calibration <- function(con, panelist, model, correct, brier) {
  ex <- dbGetQuery(con, "SELECT * FROM calibration WHERE panelist = ?", params = list(panelist))
  if (nrow(ex) == 0) {
    dbExecute(con, "INSERT INTO calibration VALUES (?,?,?,?,?,?,?)",
      params = list(panelist, model, 1L, as.integer(correct), brier,
                    calibration_weight(1L, as.integer(correct)), format(Sys.time())))
  } else {
    n <- ex$n_votes + 1L; nc <- ex$n_correct + as.integer(correct); bs <- ex$brier_sum + brier
    w <- calibration_weight(n, nc)
    dbExecute(con, "UPDATE calibration SET n_votes=?, n_correct=?, brier_sum=?, weight=?, updated_at=?, model=? WHERE panelist=?",
      params = list(n, nc, bs, w, format(Sys.time()), model, panelist))
  }
}

db_weight <- function(con, panelist) {
  w <- dbGetQuery(con, "SELECT weight FROM calibration WHERE panelist = ?", params = list(panelist))
  if (nrow(w) == 0) 1.0 else w$weight[1]
}

## Export one session to JSON so the stats specialist env (no duckdb) can read it.
export_session_json <- function(con, qid, path) {
  q <- dbGetQuery(con, "SELECT question FROM questions WHERE id = ?", params = list(qid))
  cl <- dbGetQuery(con, "SELECT * FROM claims WHERE question_id = ?", params = list(qid))
  ids <- if (nrow(cl)) cl$id else character()
  sub <- function(tbl) if (!length(ids)) data.frame() else
    dbGetQuery(con, sprintf("SELECT * FROM %s WHERE claim_id IN (%s)", tbl,
               paste(sprintf("'%s'", ids), collapse = ",")))
  jsonlite::write_json(list(
    qid = qid, question = if (nrow(q)) q$question[1] else "",
    claims = cl, verdicts = sub("verdicts"), evidence = sub("evidence"),
    r_checks = sub("r_checks"),
    calibration = dbGetQuery(con, "SELECT * FROM calibration ORDER BY weight")),
    path, auto_unbox = TRUE, null = "null", na = "null")
  path
}
