#!/usr/bin/env bash
# install.sh — telepíti a repo skilljeit és a hozzájuk kellő külső függőségeket.
#
#   ./install.sh --check              csak jelentés, semmit nem telepít
#   ./install.sh                      alap telepítés (kicsi, gyors), nagyokra rákérdez
#   ./install.sh --all --yes          minden, kérdés nélkül (nagy letöltésekkel együtt)
#   ./install.sh --skill doc-tools    csak egy skill
#
# Bootstrap (még nincs klónozva):
#   gh repo clone <owner>/<repo> ~/.claude/skills && ~/.claude/skills/install.sh
#
# A skript idempotens: ami már megvan, azt kihagyja.

set -uo pipefail

SKILLS_DIR="${SKILLS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
ALL_SKILLS=(doc-tools memo-index science-council)

MODE=install          # install | check
ASSUME_YES=0
WANT_OPTIONAL=0
PYTHON_OVERRIDE=""
SELECTED=()

# --- kimenet ---------------------------------------------------------------
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
else B=""; G=""; Y=""; R=""; D=""; N=""; fi

hdr()  { printf '\n%s== %s ==%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
bad()  { printf '  %s✗%s %s\n' "$R" "$N" "$*"; }
info() { printf '  %s%s%s\n' "$D" "$*" "$N"; }
have() { command -v "$1" >/dev/null 2>&1; }

# Rákérdez, hacsak nem --yes. Nagy letöltéseknél mindig ezen megy át.
confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -t 0 ] || { warn "nem interaktív futás — kihagyva (add meg: --yes)"; return 1; }
  printf '  %s?%s %s [i/N] ' "$Y" "$N" "$1"
  read -r a </dev/tty || return 1
  case "$a" in [iIyY]*) return 0 ;; *) return 1 ;; esac
}

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check)    MODE=check ;;
    --yes|-y)   ASSUME_YES=1 ;;
    --all)      WANT_OPTIONAL=1 ;;
    --skill)    shift; SELECTED+=("${1:-}") ;;
    --python)   shift; PYTHON_OVERRIDE="${1:-}" ;;
    -h|--help)  usage 0 ;;
    *)          bad "ismeretlen kapcsoló: $1"; usage 2 ;;
  esac
  shift
done
[ ${#SELECTED[@]} -eq 0 ] && SELECTED=("${ALL_SKILLS[@]}")

wants() { for s in "${SELECTED[@]}"; do [ "$s" = "$1" ] && return 0; done; return 1; }

# ===========================================================================
# doc-tools — Python könyvtárak + opcionális külső bináris eszközök
# ===========================================================================
PY_DEPS=(python-docx pymupdf openpyxl pdfplumber python-pptx pylatexenc striprtf xlrd docxtpl docxcompose xlsxwriter pypdf)
# import-név ↔ pip-név, ahol eltér
py_import_of() {
  case "$1" in
    python-docx) echo docx ;; pymupdf) echo fitz ;; python-pptx) echo pptx ;;
    *) echo "${1//-/_}" ;;
  esac
}

# Melyik értelmezővel fussanak a doc-tools szkriptek?
# Sorrend: --python > a jelenlegi shebang (ha él és megvannak a libek) > .venv
pick_python() {
  if [ -n "$PYTHON_OVERRIDE" ]; then echo "$PYTHON_OVERRIDE"; return; fi

  local cur
  cur="$(head -1 "$SKILLS_DIR/doc-tools/bin/pdftotext" 2>/dev/null | sed -n 's|^#!\(.*\)$|\1|p')"
  if [ -n "$cur" ] && [ -x "$cur" ] && "$cur" -c 'import docx, fitz, openpyxl' 2>/dev/null; then
    echo "$cur"; return          # a meglévő setup működik — nem nyúlunk hozzá
  fi
  echo "$SKILLS_DIR/.venv/bin/python3"
}

install_doc_tools() {
  hdr "doc-tools"
  local py; py="$(pick_python)"

  if [ "$MODE" = check ]; then
    [ -x "$py" ] && ok "értelmező: $py" || warn "értelmező hiányzik: $py (a telepítő létrehozza)"
    if [ -x "$py" ]; then
      local miss=()
      for p in "${PY_DEPS[@]}"; do
        "$py" -c "import $(py_import_of "$p")" 2>/dev/null || miss+=("$p")
      done
      [ ${#miss[@]} -eq 0 ] && ok "python csomagok: mind megvan" || warn "hiányzó csomag: ${miss[*]}"
    fi
  else
    # venv, ha oda mutat a választás
    case "$py" in
      "$SKILLS_DIR/.venv/"*)
        if [ ! -x "$py" ]; then
          local base; base="$(command -v python3)"
          [ -n "$base" ] || { bad "nincs python3 a PATH-on"; return 1; }
          info "virtuális környezet: $SKILLS_DIR/.venv  (alap: $base)"
          "$base" -m venv "$SKILLS_DIR/.venv" || { bad "venv létrehozása sikertelen"; return 1; }
        fi
        ;;
      *) info "meglévő értelmező használata: $py" ;;
    esac

    local miss=()
    for p in "${PY_DEPS[@]}"; do
      "$py" -c "import $(py_import_of "$p")" 2>/dev/null || miss+=("$p")
    done
    if [ ${#miss[@]} -gt 0 ]; then
      info "pip install: ${miss[*]}"
      "$py" -m pip install --quiet --upgrade pip >/dev/null 2>&1
      "$py" -m pip install --quiet "${miss[@]}" || { bad "pip telepítés sikertelen"; return 1; }
    fi
    ok "python csomagok rendben"

    # Shebangek a választott értelmezőre — ettől lesz a klón hordozható.
    local changed=0
    for f in "$SKILLS_DIR"/doc-tools/bin/*; do
      [ -f "$f" ] || continue
      head -1 "$f" | grep -q '^#!.*python' || continue
      if ! head -1 "$f" | grep -qxF "#!$py"; then
        local tmp; tmp="$(mktemp)"
        { printf '#!%s\n' "$py"; tail -n +2 "$f"; } >"$tmp" && mv "$tmp" "$f"
        changed=$((changed+1))
      fi
      chmod +x "$f"
    done
    [ "$changed" -gt 0 ] && ok "shebang átírva $changed fájlban → $py" || ok "shebangek már jók"
  fi

  # Opcionális külső eszközök — mindegyik nagy, mindegyikre külön rákérdezünk.
  have pandoc && ok "pandoc: $(pandoc --version | head -1)" || warn "pandoc hiányzik (fallback konverzió)"
  have tesseract && ok "tesseract: $(tesseract --version 2>&1 | head -1)" || warn "tesseract hiányzik (--ocr nem megy)"
  if [ -d "$HOME/Library/TinyTeX" ] || have pdflatex; then ok "LaTeX toolchain megvan"
  else
    warn "TinyTeX hiányzik (LaTeX fordítás nem megy)"
    if [ "$MODE" = install ] && [ "$WANT_OPTIONAL" = 1 ] && confirm "TinyTeX telepítése? (~200 MB letöltés a tug.org-ról)"; then
      curl -fsSL https://yihui.org/tinytex/install-bin-unix.sh | sh \
        && ok "TinyTeX telepítve — a tlmgr/pdflatex a ~/.local/bin-be került" \
        || bad "TinyTeX telepítés sikertelen"
    fi
  fi
  if [ -d "$HOME/Applications/LibreOffice.app" ] || [ -d "/Applications/LibreOffice.app" ] || have libreoffice; then
    ok "LibreOffice megvan (.doc/.ppt/.odt konverzió)"
  else
    warn "LibreOffice hiányzik — a régi bináris formátumok (.doc/.ppt) nem konvertálhatók"
    info "telepítés: https://www.libreoffice.org/download/  (~350 MB)"
  fi
}

# ===========================================================================
# memo-index — nincs pip függősége; a lokális modell az opcionális rész
# ===========================================================================
install_memo_index() {
  hdr "memo-index"
  ok "python függőség: nincs (csak stdlib)"

  local setup="$SKILLS_DIR/memo-index/scripts/setup_local_model.sh"
  chmod +x "$SKILLS_DIR"/memo-index/scripts/*.sh 2>/dev/null

  if [ -x "$setup" ]; then
    if "$setup" --check >/dev/null 2>&1; then
      ok "lokális modell (Ollama) készen áll"
      "$setup" --check 2>/dev/null | sed 's/^/    /'
      return 0
    fi
    warn "lokális modell nincs kész:"
    "$setup" --check 2>/dev/null | sed 's/^/    /'
    if [ "$MODE" = install ] && [ "$WANT_OPTIONAL" = 1 ] \
       && confirm "Ollama + két modell telepítése? (~500 MB app + ~9,4 GB modell)"; then
      "$setup" --install || bad "Ollama telepítés sikertelen"
    else
      info "enélkül is használható: memo_gen.py --generator=subagent (API tokent fogyaszt)"
    fi
  fi
}

# ===========================================================================
# science-council — R + CRAN csomagok
# ===========================================================================
# Két különböző R vesz részt a futásban, más-más csomagigénnyel:
#   1. a "chair" — a PATH-on lévő R, ez hajtja a protokollt, a DB-t és a HTTP-t
#   2. a "check runner" — a statisztikai ellenőrzéseket futtató R. Ha a Claude for
#      Life Sciences telepítve van, ez az ő r-stats-methodologist env-je (metafor,
#      meta, metadat), amit a skill `check_rscript()`-je magától megtalál.
# Ne telepíts a conda env-be: azt a Claude Science kezeli, CRAN-ról írni bele töri.
R_PKGS_CHAIR=(DBI duckdb cli digest future.apply ggplot2 glue httr2 jsonlite sys)
R_PKGS_CHECK=(metafor)

r_missing() { # $1 = Rscript, $@ = csomagok  -> hiányzók a stdout-on
  local rs="$1"; shift
  for p in "$@"; do
    "$rs" -e "q(status = !requireNamespace('$p', quietly=TRUE))" 2>/dev/null || printf '%s ' "$p"
  done
}

install_science_council() {
  hdr "science-council"

  local rscript=""
  have Rscript && rscript="$(command -v Rscript)"
  if [ -z "$rscript" ]; then
    bad "R nincs telepítve — a science-council enélkül nem fut"
    info "macOS: https://cran.r-project.org/bin/macosx/   Linux: apt/dnf install r-base"
    return 1
  fi
  ok "chair R: $rscript"

  local miss; miss="$(r_missing "$rscript" "${R_PKGS_CHAIR[@]}")"
  if [ -z "$miss" ]; then
    ok "chair R csomagok: mind megvan"
  elif [ "$MODE" = check ]; then
    warn "hiányzó csomag a chair R-ben: $miss"
  else
    info "CRAN telepítés: $miss  (fordítás miatt több perc is lehet)"
    local list; list=$(printf "'%s'," $miss); list="${list%,}"
    "$rscript" -e "install.packages(c($list), repos='https://cloud.r-project.org')" \
      && ok "chair R csomagok telepítve" || bad "néhány csomag nem települt — nézd a fordítási hibát"
  fi

  # A check runner: opcionális, de nélküle a statisztikai ellenőrzés gyengébb.
  local sci_r="${SCICOUNCIL_RSCRIPT:-$HOME/.claude-science/conda/envs/r-stats-methodologist/bin/Rscript}"
  if [ -x "$sci_r" ]; then
    ok "check runner: $sci_r"
    local cmiss; cmiss="$(r_missing "$sci_r" "${R_PKGS_CHECK[@]}")"
    [ -z "$cmiss" ] && ok "  metafor/metadat megvan — valódi meta-analitikus adatokon futhat ellenőrzés" \
                    || warn "  hiányzik: $cmiss (a conda env-et a Claude Science kezeli — ott telepítsd, ne CRAN-ról)"
  else
    warn "check runner: nincs Claude Science env — az ellenőrzések a chair R-ben futnak"
    local cmiss; cmiss="$(r_missing "$rscript" "${R_PKGS_CHECK[@]}")"
    if [ -n "$cmiss" ] && [ "$MODE" = install ]; then
      info "CRAN telepítés a chair R-be: $cmiss"
      "$rscript" -e "install.packages('metafor', repos='https://cloud.r-project.org')" >/dev/null 2>&1 \
        && ok "  metafor telepítve" || warn "  metafor nem települt — meta-analitikus ellenőrzés nélkül fut"
    fi
  fi

  # .env a sablonból — kulcs nélkül, azt a felhasználó tölti ki
  local env="$SKILLS_DIR/science-council/.env"
  if [ -f "$env" ]; then
    ok ".env megvan"
  elif [ "$MODE" = check ]; then
    warn ".env hiányzik (a .env.example-ből kell létrehozni)"
  else
    cp "$SKILLS_DIR/science-council/.env.example" "$env" && chmod 600 "$env"
    ok ".env létrehozva a sablonból — töltsd ki a kulcsokat: $env"
  fi

  chmod +x "$SKILLS_DIR/science-council/bin/council" 2>/dev/null

  # A DuckDB FTS kiterjesztést a duckdb futásidőben tölti le — csak jelezzük.
  if [ -d "$SKILLS_DIR/science-council/db/duckdb_extensions" ]; then
    ok "DuckDB FTS kiterjesztés helyben"
  else
    info "a DuckDB FTS kiterjesztés az első futáskor töltődik le (~5 MB, internet kell)"
  fi

  have claude && ok "claude CLI: bridge mód elérhető" \
               || warn "claude CLI nincs a PATH-on — a Claude-ülés bridge módban nem tud beülni a panelbe"
}

# ===========================================================================
main() {
  printf '%sClaude Code skillek — telepítő%s\n' "$B" "$N"
  info "cél: $SKILLS_DIR"
  [ "$MODE" = check ] && info "MÓD: csak ellenőrzés, semmi nem települ"
  [ "$MODE" = install ] && [ "$WANT_OPTIONAL" = 0 ] && \
    info "alap mód — a nagy letöltésekhez (Ollama, TinyTeX) add meg: --all"

  wants doc-tools      && install_doc_tools
  wants memo-index     && install_memo_index
  wants science-council && install_science_council

  hdr "kész"
  info "a skillek a következő Claude Code indításnál töltődnek be"
  [ "$MODE" = check ] && info "telepítéshez futtasd kapcsoló nélkül: ./install.sh"
}

main
