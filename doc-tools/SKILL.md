---
name: doc-tools
description: Extract text from PDF, Word, Excel, PowerPoint and LaTeX files, and edit/generate .docx and .tex programmatically. Provides the CLI commands pdftotext, doctotext, xlstotext and latextotext plus a compiled LaTeX toolchain (pdflatex/xelatex/latexmk via TinyTeX). Use whenever a task involves reading, converting, extracting from, or producing a document file — .pdf, .docx, .doc, .xlsx, .xls, .pptx, .ppt, .odt, .ods, .rtf, .csv, .tex — including "what does this PDF say", "pull the tables out of this spreadsheet", "turn this into a Word document", "compile this LaTeX", or feeding document text into another pipeline. Hungarian triggers: PDF kiolvasása, Word/Excel fájl feldolgozása, szövegkinyerés, dokumentum konvertálás, LaTeX fordítás.
---

# Document tools

Four extractors on the PATH, plus Python libraries for authoring and editing.
All four share the same interface: `TOOL INPUT [-o OUT] [-f txt|md|json]`.
`-` as INPUT reads stdin. Default output is plain text on stdout.

## Extracting

```bash
pdftotext report.pdf                      # all pages, reading order
pdftotext report.pdf --pages 3-7 --tables # page range + table extraction
pdftotext scan.pdf --ocr --ocr-lang hun   # scanned pages with no text layer
pdftotext report.pdf -f json -o out.json  # per-page records

doctotext memo.docx -f md --tables        # headings as markdown, tables included
doctotext draft.docx --comments           # append reviewer comments
doctotext deck.pptx --notes               # slides + speaker notes
doctotext legacy.doc                      # routed through LibreOffice

xlstotext model.xlsx                      # all sheets, computed values
xlstotext model.xlsx --formulas -s Data   # formulas from one sheet
xlstotext old.xls --csv -o data.csv       # legacy .xls to CSV
xlstotext big.xlsx --max-rows 50          # peek at a large sheet

latextotext paper.tex --bodyonly          # prose only, no preamble
latextotext paper.tex --sections          # outline of the section tree
latextotext paper.tex --pandoc -f md      # via pandoc; better cross-refs
```

Pick `-f json` when the output feeds another program — you get page/sheet/slide
boundaries as structure instead of guessing at separators.

`--formulas` matters when auditing a spreadsheet: without it you see cached
values, and a file never opened by Excel has no cached values at all (formula
cells come back empty). Prefer `--formulas` when the logic is the point.

## Editing and generating

Libraries, not CLIs — write Python against these:

| Need | Library |
|---|---|
| Read/write .docx | `python-docx` (`import docx`) |
| .docx from a template with placeholders | `docxtpl` (Jinja2 syntax inside Word) |
| Merge several .docx into one | `docxcompose` |
| Read/write .xlsx, formulas, charts | `openpyxl`, `xlsxwriter` |
| Read/write .pptx | `python-pptx` |
| PDF manipulation (split/merge/annotate) | `pymupdf` (`import fitz`), `pypdf` |
| PDF tables and layout | `pdfplumber` |
| LaTeX → text, macro expansion | `pylatexenc` |
| LaTeX AST parsing and rewriting | `TexSoup` |
| Any-to-any fallback conversion | `pandoc` (CLI, already installed) |

For .docx work that needs styling beyond python-docx's API (page numbers,
letterheads, tracked changes), see the `anthropic-skills:docx` skill — it goes
deeper on OOXML manipulation. This skill covers extraction and the plumbing.

## Compiling LaTeX

TinyTeX lives in `~/Library/TinyTeX`, binaries symlinked into `~/.local/bin`.

```bash
latexmk -pdf -interaction=nonstopmode paper.tex   # handles reruns, biber, refs
tlmgr install <package>                            # add a missing package
```

`latexmk` exit 0 means a clean build. A PDF can appear even on a failed run —
always check the exit code, then `grep -E "^!" paper.log` for the real error.

Hungarian is set up (`babel-hungarian` + `hyphen-hungarian`, formats rebuilt).
If you add a language, install its `hyphen-*` package and run
`fmtutil-sys --byfmt pdflatex` or hyphenation silently fails.

## Legacy binary formats

`.doc`, `.ppt`, `.odt`, `.odp` are converted by shelling out to LibreOffice,
installed at `~/Applications/LibreOffice.app` (no sudo, not on the PATH — the
tools find it themselves). `.xls` is read directly by `xlrd`, no LibreOffice
needed. Every XML-based format works without it.

## Feeding documents into memo-index

For a large pile of documents, don't extract them one by one — the `memo-index`
skill's `scripts/doc_ingest.py` batches every format through these extractors and
also rasterizes PDF pages for its vision pass. Use that when the goal is
"read all of these and tell me X" rather than reading one file.

## Notes

- The shebangs point at `/Users/szili/anaconda3/bin/python3` on purpose, not at
  `env python3`. A login shell resolves `python3` to the python.org framework
  build, which does not have these libraries; `pip3` installs into Anaconda. If
  the Python setup ever changes, repoint the shebangs and reinstall the libs.
- OCR: Tesseract 5.5.2 lives in the `ocr` conda env, symlinked onto the PATH,
  with 125 languages including `hun`, `eng`, `deu`. Only `pdftotext --ocr` uses
  it. `--ocr` is per-page and lazy: a page that already has a text layer is read
  normally and never rasterized, so passing it on a mixed scanned/digital PDF is
  safe and costs nothing on the digital pages. Default `--ocr-lang` is `eng+hun`.
  Expect OCR to mangle math and to drop accents from capitals (`É` → `E`) — if a
  number matters, verify it against the image rather than trusting the OCR text.
- The implementations are in `bin/` next to this file; `_doccommon.py` holds the
  shared argument parsing, LibreOffice discovery and output handling.
