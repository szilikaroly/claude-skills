#!/usr/bin/env bash
# Check for / install the local model runtime used by memo_gen.py.
#
#   setup_local_model.sh --check     report status, exit 0 if ready, 1 if not
#   setup_local_model.sh --install   install Ollama + pull a model (downloads! ask first)
#   setup_local_model.sh --serve     start the Ollama server if it isn't running
#
# Model choice is deliberate, not clever: see references/local-model.md.

set -uo pipefail

# One model per profile: code-tuned models read declarations well but flatten prose.
# MEMO_MODEL overrides both if you'd rather run a single model.
MODEL_CODE="${MEMO_MODEL:-${MEMO_MODEL_CODE:-qwen2.5-coder:7b}}"
MODEL_PROSE="${MEMO_MODEL:-${MEMO_MODEL_PROSE:-llama3.1:8b}}"
OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"

have() { command -v "$1" >/dev/null 2>&1; }

server_up() {
  curl -fsS --max-time 2 "$OLLAMA_HOST/api/tags" >/dev/null 2>&1
}

do_check() {
  local ok=0

  if have ollama; then
    echo "runtime:   ollama $(ollama --version 2>/dev/null | head -1)"
  else
    echo "runtime:   MISSING (ollama not on PATH)"
    ok=1
  fi

  if server_up; then
    echo "server:    up at $OLLAMA_HOST"
  else
    echo "server:    down  (run: setup_local_model.sh --serve)"
    ok=1
  fi

  local tags=""
  server_up && tags="$(curl -fsS "$OLLAMA_HOST/api/tags" 2>/dev/null)"
  for pair in "code:$MODEL_CODE" "prose:$MODEL_PROSE"; do
    local prof="${pair%%:*}" m="${pair#*:}"
    if [ -n "$tags" ] && printf '%s' "$tags" | grep -q "\"$m\""; then
      echo "model($prof): $m present"
    else
      echo "model($prof): $m NOT pulled"
      ok=1
    fi
  done

  if [ "$ok" -eq 0 ]; then
    echo "status:    READY"
  else
    echo "status:    NOT READY — run --install (downloads software; ask the user first)"
    echo "           or fall back to: memo_gen.py --generator=subagent"
  fi
  return $ok
}

do_serve() {
  if server_up; then echo "already up at $OLLAMA_HOST"; return 0; fi
  if ! have ollama; then echo "ollama not installed; run --install" >&2; return 1; fi
  echo "starting ollama serve in background..."
  nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
  for _ in $(seq 1 30); do
    sleep 1
    if server_up; then echo "up at $OLLAMA_HOST"; return 0; fi
  done
  echo "server did not come up; see /tmp/ollama-serve.log" >&2
  return 1
}

do_install() {
  local os; os="$(uname -s)"

  if ! have ollama; then
    case "$os" in
      Darwin)
        if have brew; then
          echo "installing ollama via homebrew..."
          brew install --cask ollama || return 1
        else
          echo "No homebrew found. Downloading Ollama.app (~500MB) from ollama.com ..."
          local tmp; tmp="$(mktemp -d)"
          curl -fL --progress-bar -o "$tmp/Ollama.zip" \
            "https://ollama.com/download/Ollama-darwin.zip" || return 1
          unzip -q "$tmp/Ollama.zip" -d "$tmp" || return 1
          if [ -d "$tmp/Ollama.app" ]; then
            rm -rf "/Applications/Ollama.app"
            mv "$tmp/Ollama.app" /Applications/ || return 1
            echo "installed /Applications/Ollama.app"
            echo "NOTE: open it once so it installs the 'ollama' CLI onto PATH:"
            echo "      open -a Ollama"
          fi
          rm -rf "$tmp"
        fi
        ;;
      Linux)
        echo "installing ollama via official script..."
        curl -fsSL https://ollama.com/install.sh | sh || return 1
        ;;
      *)
        echo "unsupported OS: $os — install ollama manually from https://ollama.com" >&2
        return 1
        ;;
    esac
  fi

  have ollama || { echo "ollama still not on PATH; open the app once, then re-run" >&2; return 1; }
  do_serve || return 1

  for m in "$MODEL_CODE" "$MODEL_PROSE"; do
    if [ -n "$m" ]; then
      echo "pulling $m (a few GB — one time)..."
      ollama pull "$m" || return 1
    fi
  done

  echo
  do_check
}

case "${1:---check}" in
  --check)   do_check ;;
  --install) do_install ;;
  --serve)   do_serve ;;
  *) echo "usage: $0 [--check|--install|--serve]" >&2; exit 2 ;;
esac
