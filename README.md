# Claude Code skills

Saját [Claude Code](https://claude.com/claude-code) skillek gyűjteménye.
Mindegyik mappa egy önálló skill: a `SKILL.md` a belépési pont, mellette a
futtatható szkriptek és a hivatkozott dokumentáció.

| Skill | Mit csinál |
|---|---|
| [`science-council`](science-council/) | Több modellből (Claude, ChatGPT, Gemini, xAI, DeepSeek, lokális Ollama) álló tudományos tanács, amely adverzariális protokoll szerint vitatja meg a kérdést — minden panelista minden állítás **mellett és ellen** is érvel. A túlélő állításokat determinisztikus R-ellenőrzések és valódi hivatkozáskeresés (Crossref / Europe PMC) validálja, az eredmény indexelt DuckDB tudásbázisba kerül. Az R egyszerre orkesztrátor, statisztikai motor és adatbázisréteg. |
| [`memo-index`](memo-index/) | Csökkenti a kontextus-token fogyasztást nagy anyagoknál: **lokális** modellel készít tömörített, sorhoz kötött memókat (nulla API-token), a kontextusban csak egy apró, kereshető indexet tart, és minden állítást külön validál — amit lehet determinisztikusan, a többit egy-egy izolált subagenttel. Kezel kódot, prózát, táblázatokat és képeket/ábrákat (lokális vision modellel). |
| [`doc-tools`](doc-tools/) | Szövegkinyerés PDF, Word, Excel, PowerPoint és LaTeX fájlokból, valamint `.docx` és `.tex` programozott szerkesztése. A `pdftotext`, `doctotext`, `xlstotext`, `latextotext` parancsokat adja, plusz teljes LaTeX toolchaint (TinyTeX). |
| [`journal-matcher`](journal-matcher/) | Célfolyóirat-keresés és -rangsorolás egy kézirathoz. A scope-illeszkedést **bizonyítékból** számolja (hány cikket adott ki a lap pont ebben a témában, OpenAlex), majd kemény szűrőkkel rangsorol: Scopus-indexeltség, Q1–Q4, **D1** (SJR szerinti felső decilis kategórián belül), IF (valódi, ha van JCR-export — különben jelölt proxy), APC és **EISZ** read&publish fedettség, MEDLINE, a lap *tényleges* cikktípus- és hosszprofilja, PubMed dátumokból **mért** beküldés→elfogadás idő, acceptance/desk reject ráta, szerzői országeloszlás. Ezen kívül novelty- és telítettség-elemzés (lekörözés-ellenőrzés), ragadozó/special issue-farm szűrés, nyitott call for paper és special issue felderítés, és konkrét javaslat arra, mit kell erősíteni egy kategóriával feljebb. |
| ~~`the-collector`~~ | **Átköltözött** a `composer` pluginba (`szk-plugins`), 2026-08-23. Ugyanaz a PubMed-learatás, plusz 5D befogadási kapu (DOI, első szerző, szerzőlista, folyóirat, kötet Crossref + PubMed + Europa PMC ellen), keresésenkénti naplózott könyvtár PRISMA 2020 / PRISMA-S szerint, és PROSPERO protokoll-rekord. Telepítés: `claude plugin install composer@szk-plugins`, vagy a `composer` parancs bármelyik terminálból. |

> A `academic-editor` és a `probast-tripod-ai` skill szintén átköltözött a
> `szk-plugins` marketplace-be — `academic-editor` és `validator` néven. A régi
> másolatok archívuma: `~/.claude/skills-backup-20260823.tar.gz`.

## Telepítés

Egy sor — klónoz és telepít. Előbb `--check`-kel érdemes megnézni, mi hiányzik:

```bash
curl -fsSL https://raw.githubusercontent.com/szilikaroly/claude-skills/main/install.sh | bash -s -- --check
curl -fsSL https://raw.githubusercontent.com/szilikaroly/claude-skills/main/install.sh | bash
```

Alapértelmezett cél a `~/.claude/skills`. Ha az már foglalt, add meg máshova —
a változót a `bash`-re kell állítani, nem a `curl`-re:

```bash
curl -fsSL .../install.sh | SKILLS_DIR=~/claude-skills bash
```

Vagy kézzel, ha jobban szereted látni, mit futtatsz:

```bash
git clone https://github.com/szilikaroly/claude-skills.git ~/.claude/skills
~/.claude/skills/install.sh --check
~/.claude/skills/install.sh
```

A skillek a következő Claude Code indításnál betöltődnek.

### Mit csinál az `install.sh`

Kideríti, mi hiányzik, és **csak azt** tölti le. Idempotens: újrafuttatható.

```bash
./install.sh --check      # csak jelentés, semmit nem telepít  ← ezzel kezdd
./install.sh              # alap: pip csomagok, R csomagok, .env, shebangek
./install.sh --all        # a nagy opcionálisakra is rákérdez (Ollama, TinyTeX)
./install.sh --all --yes  # kérdés nélkül, mindent
./install.sh --skill doc-tools
```

| Mit intéz | Honnan |
|---|---|
| doc-tools Python könyvtárak (`pymupdf`, `python-docx`, `openpyxl`, `pdfplumber`, `python-pptx`, …) | PyPI |
| Az értelmező kiválasztása és a **shebangek átírása** | helyben |
| science-council R csomagok (`duckdb`, `httr2`, `DBI`, `ggplot2`, `metafor`, …) | CRAN |
| `.env` létrehozása a sablonból, `chmod 600` | helyben |
| *opcionális:* Ollama + `qwen2.5-coder:7b`, `llama3.1:8b` (~9,4 GB) | ollama.com |
| *opcionális:* TinyTeX LaTeX toolchain (~200 MB) | tug.org |

A nagy letöltéseket soha nem indítja el magától — vagy `--all` + megerősítés,
vagy `--yes`. A `--check` semmit nem tölt le.

**A shebang-probléma megoldva:** a `doc-tools/bin/*` shebangje ezen a gépen egy
konkrét Anaconda-értelmezőre mutat. A telepítő megnézi, él-e az az útvonal és
megvannak-e benne a könyvtárak; ha nem, csinál egy `.venv`-et a repo mellé, oda
telepít, és átírja a shebangeket. Saját értelmezőt a `--python /path/to/python3`
kapcsolóval erőltethetsz.

## Konfiguráció

A `science-council` API-kulcsokat vár. A `.env` **nincs** verziókövetve; a
telepítő létrehozza a sablonból, a kulcsokat neked kell beleírni:

```bash
$EDITOR ~/.claude/skills/science-council/.env
```

Kulcs nélkül is működik a lokális Ollama panelistával és a Claude-üléssel
(bridge mód, a te előfizetéseden — nem kell API-kulcs).

### Két R kell, más-más szerepben

- **chair R** (a PATH-on lévő): a protokollt, a DuckDB-t és a HTTP-hívásokat futtatja
- **check runner**: a statisztikai ellenőrzéseket futtatja. Ha a Claude for Life
  Sciences telepítve van, ez automatikusan az `r-stats-methodologist` conda env
  lesz — abban van a `metafor`/`metadat`, tehát valódi publikált meta-analitikus
  adatokon futhat ellenőrzés. Felülírható: `SCICOUNCIL_RSCRIPT`.

A telepítő mindkettőt külön nézi. A conda env-be **nem** telepít — azt a Claude
Science kezeli, CRAN-ról írni bele töri.

## Mi nincs a repóban

A `.gitignore` szándékosan kizárja: `.env` (kulcsok), `db/*.duckdb` (a
tudásbázis), `logs/`, `runs/` (korábbi futások kimenete), `__pycache__`.
Ezek gépspecifikusak vagy érzékenyek.
