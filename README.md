# Claude Code skills

Saját [Claude Code](https://claude.com/claude-code) skillek gyűjteménye.
Mindegyik mappa egy önálló skill: a `SKILL.md` a belépési pont, mellette a
futtatható szkriptek és a hivatkozott dokumentáció.

| Skill | Mit csinál |
|---|---|
| [`science-council`](science-council/) | Több modellből (Claude, ChatGPT, Gemini, xAI, DeepSeek, lokális Ollama) álló tudományos tanács, amely adverzariális protokoll szerint vitatja meg a kérdést — minden panelista minden állítás **mellett és ellen** is érvel. A túlélő állításokat determinisztikus R-ellenőrzések és valódi hivatkozáskeresés (Crossref / Europe PMC) validálja, az eredmény indexelt DuckDB tudásbázisba kerül. Az R egyszerre orkesztrátor, statisztikai motor és adatbázisréteg. |
| [`memo-index`](memo-index/) | Csökkenti a kontextus-token fogyasztást nagy anyagoknál: **lokális** modellel készít tömörített, sorhoz kötött memókat (nulla API-token), a kontextusban csak egy apró, kereshető indexet tart, és minden állítást külön validál — amit lehet determinisztikusan, a többit egy-egy izolált subagenttel. Kezel kódot, prózát, táblázatokat és képeket/ábrákat (lokális vision modellel). |
| [`doc-tools`](doc-tools/) | Szövegkinyerés PDF, Word, Excel, PowerPoint és LaTeX fájlokból, valamint `.docx` és `.tex` programozott szerkesztése. A `pdftotext`, `doctotext`, `xlstotext`, `latextotext` parancsokat adja, plusz teljes LaTeX toolchaint (TinyTeX). |

## Telepítés

```bash
git clone <repo-url> ~/.claude/skills
```

Vagy ha már van `~/.claude/skills` mappád, egyesével:

```bash
git clone <repo-url> /tmp/claude-skills
cp -r /tmp/claude-skills/science-council ~/.claude/skills/
```

A skillek a következő Claude Code indításnál automatikusan betöltődnek.

## Konfiguráció

A `science-council` API-kulcsokat vár. A `.env` **nincs** verziókövetve; a
sablonból indulj:

```bash
cd ~/.claude/skills/science-council
cp .env.example .env
chmod 600 .env
# töltsd ki a kulcsokat
```

A `memo-index` és a `doc-tools` külső függőségeiről a saját `SKILL.md`-jük ír.

> **Ha más gépre klónozod:** a `doc-tools/bin/*` szkriptek shebangje egy konkrét
> Anaconda-értelmezőre van rögzítve (`#!/Users/szili/anaconda3/bin/python3`),
> mert a bejelentkezési shell `python3`-ja más környezet. Klónozás után írd át a
> saját értelmeződre, pl. `#!/usr/bin/env python3`.

## Mi nincs a repóban

A `.gitignore` szándékosan kizárja: `.env` (kulcsok), `db/*.duckdb` (a
tudásbázis), `logs/`, `runs/` (korábbi futások kimenete), `__pycache__`.
Ezek gépspecifikusak vagy érzékenyek.
