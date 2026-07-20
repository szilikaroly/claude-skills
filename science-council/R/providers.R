## providers.R — unified panelist interface across Claude / OpenAI / Gemini / xAI / DeepSeek / Ollama
## Every provider exposes the same contract:
##   call_provider(id, prompt, system = NULL, json = TRUE) -> list(ok, text, obj, error, latency, model)

suppressPackageStartupMessages({
  library(httr2); library(jsonlite); library(glue)
})

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a) == 1 && is.na(a))) b else a

## Locate the skill root. bin/council always exports SCICOUNCIL_DIR; this fallback
## keeps `source("R/providers.R")` working from an interactive session too.
SKILL_DIR <- local({
  e <- Sys.getenv("SCICOUNCIL_DIR")
  if (nzchar(e)) return(e)
  f <- tryCatch(sys.frame(1)$ofile, error = function(e) NULL)
  normalizePath(file.path(dirname(if (is.null(f)) "." else f), ".."), mustWork = FALSE)
})

load_env <- function(path = file.path(SKILL_DIR, ".env")) {
  if (!file.exists(path)) return(invisible(FALSE))
  for (ln in readLines(path, warn = FALSE)) {
    ln <- trimws(ln)
    if (!nzchar(ln) || startsWith(ln, "#") || !grepl("=", ln, fixed = TRUE)) next
    k <- trimws(sub("=.*$", "", ln)); v <- sub("^[^=]*=", "", ln)
    if (nzchar(v) && !nzchar(Sys.getenv(k))) do.call(Sys.setenv, setNames(list(v), k))
  }
  invisible(TRUE)
}

## Models routinely emit raw newlines and tabs inside JSON strings — which is
## invalid JSON — especially when the string holds R code. Escape control
## characters that appear between quotes, leaving structural whitespace alone.
repair_json_strings <- function(txt) {
  chars <- strsplit(txt, "")[[1]]
  instr <- FALSE; esc <- FALSE; out <- character(length(chars))
  for (i in seq_along(chars)) {
    ch <- chars[i]
    if (esc) { out[i] <- ch; esc <- FALSE; next }
    if (ch == "\\") { out[i] <- ch; esc <- TRUE; next }
    if (ch == '"') { instr <- !instr; out[i] <- ch; next }
    out[i] <- if (instr) switch(ch, "\n" = "\\n", "\r" = "\\r", "\t" = "\\t", ch) else ch
  }
  paste(out, collapse = "")
}

## ---- lenient JSON extraction -------------------------------------------------
## Models wrap JSON in prose or fences. Pull the first balanced {...} or [...] block.
parse_json_lenient <- function(txt) {
  if (is.null(txt) || !nzchar(txt)) return(NULL)
  txt <- gsub("^\\s*```(json)?|```\\s*$", "", txt)
  try_parse <- function(s) tryCatch(fromJSON(s, simplifyVector = FALSE), error = function(e) NULL)
  out <- try_parse(txt)
  if (!is.null(out)) return(out)
  out <- try_parse(repair_json_strings(txt))
  if (!is.null(out)) return(out)
  chars <- strsplit(txt, "")[[1]]
  for (open in c("{", "[")) {
    close <- if (open == "{") "}" else "]"
    start <- which(chars == open)
    if (!length(start)) next
    s <- start[1]; depth <- 0L; instr <- FALSE; esc <- FALSE
    for (i in seq(s, length(chars))) {
      ch <- chars[i]
      if (esc) { esc <- FALSE; next }
      if (ch == "\\") { esc <- TRUE; next }
      if (ch == '"') { instr <- !instr; next }
      if (instr) next
      if (ch == open) depth <- depth + 1L
      if (ch == close) {
        depth <- depth - 1L
        if (depth == 0L) {
          block <- paste(chars[s:i], collapse = "")
          out <- try_parse(block)
          if (!is.null(out)) return(out)
          out <- try_parse(repair_json_strings(block))
          if (!is.null(out)) return(out)
          break
        }
      }
    }
  }
  NULL
}

## How can we reach Claude? Prefer a real API key, then a logged-in CLI, else the
## bridge (only viable when a Claude Code session is driving this run).
claude_mode <- function() {
  load_env()
  forced <- Sys.getenv("SCICOUNCIL_CLAUDE_MODE", "")
  if (nzchar(forced)) return(forced)
  if (nzchar(Sys.getenv("ANTHROPIC_API_KEY"))) return("api")
  if (nzchar(Sys.which("claude")) && claude_cli_logged_in()) return("cli")
  "bridge"
}

claude_cli_logged_in <- function() {
  out <- suppressWarnings(tryCatch(
    system2("claude", c("-p", "--output-format", "json", "--model", "haiku"),
            stdin = textConnection_file("ping"), stdout = TRUE, stderr = TRUE, timeout = 30),
    error = function(e) ""))
  raw <- paste(out, collapse = " ")
  nzchar(raw) && !grepl("Not logged in|Please run /login|Invalid API key", raw)
}

textConnection_file <- function(txt) {
  f <- tempfile(); writeLines(txt, f); f
}

## Anthropic native Messages API (only when ANTHROPIC_API_KEY is present).
call_openai_compat_anthropic <- function(model, prompt, system, json, timeout) {
  body <- list(model = model, max_tokens = 4096,
               messages = list(list(role = "user", content = prompt)))
  if (!is.null(system)) body$system <- system
  resp <- request(paste0(Sys.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"), "/v1/messages")) |>
    req_headers(`x-api-key` = Sys.getenv("ANTHROPIC_API_KEY"),
                `anthropic-version` = "2023-06-01", `Content-Type` = "application/json") |>
    req_body_json(body) |> req_timeout(timeout) |> req_retry(max_tries = 3) |>
    req_error(is_error = function(r) FALSE) |> req_perform()
  if (resp_status(resp) >= 400)
    return(list(ok = FALSE, error = paste0("HTTP ", resp_status(resp), ": ", substr(resp_body_string(resp), 1, 300))))
  b <- resp_body_json(resp)
  txt <- paste(vapply(b$content, function(p) p$text %||% "", character(1)), collapse = "")
  list(ok = nzchar(txt), text = txt, error = if (nzchar(txt)) NULL else "empty completion", usage = b$usage)
}

## ---- which panelists are actually usable ------------------------------------
available_panelists <- function() {
  load_env()
  want <- trimws(strsplit(Sys.getenv("SCICOUNCIL_PANEL", "claude,openai,gemini,ollama"), ",")[[1]])
  ok <- character()
  for (seat in want) {
    p <- split_id(seat)$provider
    usable <- switch(p,
      ## bridge mode only works when a Claude Code session is driving the run
      claude   = claude_mode() != "bridge" || nzchar(Sys.getenv("SCICOUNCIL_BRIDGE_ACTIVE")),
      openai   = nzchar(Sys.getenv("OPENAI_API_KEY")),
      gemini   = nzchar(Sys.getenv("GEMINI_API_KEY")),
      xai      = nzchar(Sys.getenv("XAI_API_KEY")),
      deepseek = nzchar(Sys.getenv("DEEPSEEK_API_KEY")),
      ollama   = tryCatch({
                   req_perform(req_timeout(request(paste0(Sys.getenv("OLLAMA_HOST", "http://localhost:11434"), "/api/tags")), 5))
                   TRUE }, error = function(e) FALSE),
      FALSE)
    if (isTRUE(usable)) ok <- c(ok, seat)
  }
  ok
}

## A panelist id is either "provider" or "provider/model-override", e.g.
## "ollama/llama3.1:8b". Overrides let one provider seat several distinct minds.
split_id <- function(id) {
  i <- regexpr("/", id, fixed = TRUE)
  if (i == -1) list(provider = id, model = NULL)
  else list(provider = substr(id, 1, i - 1), model = substr(id, i + 1, nchar(id)))
}

panelist_model <- function(id) {
  load_env()
  s <- split_id(id)
  if (!is.null(s$model)) return(s$model)
  id <- s$provider
  switch(id,
    claude   = Sys.getenv("CLAUDE_MODEL", "opus"),
    openai   = Sys.getenv("OPENAI_MODEL", "gpt-5.4"),
    gemini   = Sys.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview"),
    xai      = Sys.getenv("XAI_MODEL", "grok-4"),
    deepseek = Sys.getenv("DEEPSEEK_MODEL", "deepseek-reasoner"),
    ollama   = Sys.getenv("OLLAMA_MODEL", "gemma4:12b"),
    "unknown")
}

## ---- OpenAI-compatible chat endpoint (OpenAI, xAI, DeepSeek) -----------------
call_openai_compat <- function(base_url, key, model, prompt, system, json, timeout) {
  msgs <- list()
  if (!is.null(system)) msgs <- append(msgs, list(list(role = "system", content = system)))
  msgs <- append(msgs, list(list(role = "user", content = prompt)))
  body <- list(model = model, messages = msgs)
  if (isTRUE(json)) body$response_format <- list(type = "json_object")
  ## gpt-5.x rejects non-default temperature; leave it unset everywhere for comparability.
  resp <- request(paste0(base_url, "/chat/completions")) |>
    req_headers(Authorization = paste("Bearer", key), `Content-Type` = "application/json") |>
    req_body_json(body) |> req_timeout(timeout) |> req_retry(max_tries = 3) |>
    req_error(is_error = function(r) FALSE) |> req_perform()
  if (resp_status(resp) >= 400)
    return(list(ok = FALSE, error = paste0("HTTP ", resp_status(resp), ": ", substr(resp_body_string(resp), 1, 300))))
  b <- resp_body_json(resp)
  txt <- b$choices[[1]]$message$content %||% ""
  list(ok = nzchar(txt), text = txt,
       error = if (nzchar(txt)) NULL else "empty completion",
       usage = b$usage)
}

## ---- Gemini ------------------------------------------------------------------
call_gemini <- function(key, model, prompt, system, json, timeout) {
  body <- list(contents = list(list(role = "user", parts = list(list(text = prompt)))))
  if (!is.null(system)) body$systemInstruction <- list(parts = list(list(text = system)))
  if (isTRUE(json)) body$generationConfig <- list(responseMimeType = "application/json")
  url <- glue("https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent")
  resp <- request(url) |>
    req_url_query(key = key) |> req_headers(`Content-Type` = "application/json") |>
    req_body_json(body) |> req_timeout(timeout) |> req_retry(max_tries = 3) |>
    req_error(is_error = function(r) FALSE) |> req_perform()
  if (resp_status(resp) >= 400)
    return(list(ok = FALSE, error = paste0("HTTP ", resp_status(resp), ": ", substr(resp_body_string(resp), 1, 300))))
  b <- resp_body_json(resp)
  parts <- b$candidates[[1]]$content$parts %||% list()
  txt <- paste(vapply(parts, function(p) p$text %||% "", character(1)), collapse = "")
  list(ok = nzchar(txt), text = txt,
       error = if (nzchar(txt)) NULL else paste0("empty (finish=", b$candidates[[1]]$finishReason %||% "?", ")"),
       usage = b$usageMetadata)
}

## ---- Ollama (local) ----------------------------------------------------------
call_ollama <- function(host, model, prompt, system, json, timeout) {
  msgs <- list()
  if (!is.null(system)) msgs <- append(msgs, list(list(role = "system", content = system)))
  msgs <- append(msgs, list(list(role = "user", content = prompt)))
  ## Ollama defaults to a 2048-token context, which silently TRUNCATES a council
  ## prompt plus its JSON answer — the model looks like it emitted broken JSON
  ## when it simply ran out of room. Give it space explicitly.
  body <- list(model = model, messages = msgs, stream = FALSE,
               options = list(
                 num_ctx = as.integer(Sys.getenv("OLLAMA_NUM_CTX", "8192")),
                 num_predict = as.integer(Sys.getenv("OLLAMA_NUM_PREDICT", "4096"))))
  if (isTRUE(json)) body$format <- "json"
  resp <- request(paste0(host, "/api/chat")) |>
    req_body_json(body) |> req_timeout(timeout) |>
    req_error(is_error = function(r) FALSE) |> req_perform()
  if (resp_status(resp) >= 400)
    return(list(ok = FALSE, error = paste0("HTTP ", resp_status(resp), ": ", substr(resp_body_string(resp), 1, 300))))
  b <- resp_body_json(resp)
  txt <- b$message$content %||% ""
  list(ok = nzchar(txt), text = txt, error = if (nzchar(txt)) NULL else "empty completion")
}

## ---- Claude via bridge (the driving Claude Code session answers as panelist) --
## Used when no ANTHROPIC_API_KEY and the `claude` CLI is not logged in. R drops a
## request file and blocks; the orchestrating Claude session writes the reply file.
## This is also the panelist that carries web search / Life Sciences connectors.
bridge_dir <- function() {
  d <- Sys.getenv("SCICOUNCIL_BRIDGE", file.path(SKILL_DIR, "runs", "bridge"))
  dir.create(d, recursive = TRUE, showWarnings = FALSE); d
}

call_claude_bridge <- function(model, prompt, system, json, timeout) {
  d <- bridge_dir()
  id <- paste0(format(Sys.time(), "%H%M%S"), "-", substr(digest::digest(prompt), 1, 8))
  req <- file.path(d, paste0("req_", id, ".json"))
  resp <- file.path(d, paste0("resp_", id, ".json"))
  ## Tell the serving Claude which real data sources it may ground answers with.
  sys_full <- system %||% ""
  if (exists("science_connector_briefing", mode = "function"))
    sys_full <- paste0(sys_full, science_connector_briefing())
  connectors <- if (exists("science_connectors", mode = "function")) {
    cx <- science_connectors(TRUE); if (is.null(cx)) character() else cx$name
  } else character()
  write_json(list(id = id, model = model, system = sys_full, prompt = prompt,
                  want_json = json, connectors = connectors,
                  created = format(Sys.time(), "%F %T")),
             req, auto_unbox = TRUE, pretty = TRUE)
  wait <- as.numeric(Sys.getenv("SCICOUNCIL_BRIDGE_TIMEOUT", "1200"))
  t0 <- Sys.time()
  repeat {
    if (file.exists(resp)) {
      Sys.sleep(0.2)  # let the writer finish
      body <- tryCatch(fromJSON(resp, simplifyVector = FALSE), error = function(e) NULL)
      unlink(c(req, resp))
      txt <- if (is.null(body)) "" else (body$text %||% body$result %||% "")
      if (is.list(txt)) txt <- toJSON(txt, auto_unbox = TRUE)
      return(list(ok = nzchar(txt), text = txt,
                  error = if (nzchar(txt)) NULL else "empty bridge reply"))
    }
    if (as.numeric(difftime(Sys.time(), t0, units = "secs")) > wait) {
      unlink(req)
      return(list(ok = FALSE, error = paste0("bridge timeout after ", wait, "s (no reply to ", basename(req), ")")))
    }
    Sys.sleep(1)
  }
}

## ---- Claude via local CLI (uses the user's subscription; no API key needed) ---
call_claude_cli <- function(model, prompt, system, json, timeout) {
  full <- if (is.null(system)) prompt else paste0(system, "\n\n---\n\n", prompt)
  pf <- tempfile(fileext = ".txt"); on.exit(unlink(pf), add = TRUE)
  writeLines(full, pf)
  args <- c("-p", "--output-format", "json", "--model", model)
  out <- suppressWarnings(system2("claude", args, stdin = pf, stdout = TRUE, stderr = TRUE, timeout = timeout))
  status <- attr(out, "status") %||% 0
  raw <- paste(out, collapse = "\n")
  if (!identical(as.integer(status), 0L))
    return(list(ok = FALSE, error = paste0("claude CLI exit ", status, ": ", substr(raw, 1, 300))))
  env <- tryCatch(fromJSON(raw, simplifyVector = FALSE), error = function(e) NULL)
  txt <- env$result %||% raw
  list(ok = nzchar(txt), text = txt, error = if (nzchar(txt)) NULL else "empty completion",
       usage = env$usage)
}

## ---- unified entry point -----------------------------------------------------
call_provider <- function(id, prompt, system = NULL, json = TRUE, timeout = NULL) {
  load_env()
  timeout <- timeout %||% as.numeric(Sys.getenv("SCICOUNCIL_TIMEOUT", "240"))
  model <- panelist_model(id)
  seat  <- id                    # keep the full seat name for the record
  id    <- split_id(id)$provider
  t0 <- Sys.time()
  res <- tryCatch(switch(id,
    claude   = switch(claude_mode(),
                 api    = call_openai_compat_anthropic(model, prompt, system, json, timeout),
                 cli    = call_claude_cli(model, prompt, system, json, timeout),
                 bridge = call_claude_bridge(model, prompt, system, json, timeout)),
    openai   = call_openai_compat("https://api.openai.com/v1", Sys.getenv("OPENAI_API_KEY"), model, prompt, system, json, timeout),
    xai      = call_openai_compat("https://api.x.ai/v1", Sys.getenv("XAI_API_KEY"), model, prompt, system, json, timeout),
    deepseek = call_openai_compat("https://api.deepseek.com", Sys.getenv("DEEPSEEK_API_KEY"), model, prompt, system, json, timeout),
    gemini   = call_gemini(Sys.getenv("GEMINI_API_KEY"), model, prompt, system, json, timeout),
    ollama   = call_ollama(Sys.getenv("OLLAMA_HOST", "http://localhost:11434"), model, prompt, system, json, timeout),
    list(ok = FALSE, error = paste("unknown provider:", id))
  ), error = function(e) list(ok = FALSE, error = conditionMessage(e)))

  res$latency <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  res$provider <- seat; res$model <- model
  res$obj <- if (isTRUE(res$ok) && isTRUE(json)) parse_json_lenient(res$text) else NULL
  if (isTRUE(json) && isTRUE(res$ok) && is.null(res$obj)) {
    res$ok <- FALSE
    res$error <- paste0("unparseable JSON: ", substr(res$text, 1, 200))
  }
  res
}

## Fan out one prompt to the whole panel, in parallel. Failures degrade to error rows.
call_panel <- function(ids, prompt_fn, system = NULL, json = TRUE) {
  suppressPackageStartupMessages(library(future.apply))
  plan(multisession, workers = min(length(ids), 6))
  on.exit(plan(sequential), add = TRUE)
  envvars <- c("OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY", "DEEPSEEK_API_KEY",
               "OLLAMA_HOST", "OLLAMA_MODEL", "CLAUDE_MODEL", "OPENAI_MODEL",
               "GEMINI_MODEL", "XAI_MODEL", "DEEPSEEK_MODEL", "SCICOUNCIL_TIMEOUT", "SCICOUNCIL_DIR")
  snapshot <- setNames(lapply(envvars, Sys.getenv), envvars)
  future_lapply(ids, function(id) {
    do.call(Sys.setenv, snapshot[nzchar(unlist(snapshot))])
    call_provider(id, prompt_fn(id), system = system, json = json)
  }, future.seed = TRUE)
}
