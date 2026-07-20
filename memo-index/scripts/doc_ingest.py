#!/usr/bin/env python3
"""Normalize document files into the plain text (and page images) memo_gen.py can read.

memo_gen.py handles text and known image formats; it skips anything else as raw
binary. This stages documents into that world: every input becomes a .txt file,
and PDF pages additionally become .png images so the vision pass can see figures
and charts the text layer is blind to.

    doc_ingest.py paper.pdf report.docx model.xlsx --outdir staging/
    doc_ingest.py sources/*.pdf --outdir staging/ --no-images

Prints a JSON manifest on stdout: one record per input with the artifacts it
produced, so a caller can feed `text` paths to the prose profile and `images` to
the vision profile.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

TEXT_EXT = {".txt", ".md", ".rst", ".org"}
PDF_EXT = {".pdf"}
DOC_EXT = {".docx", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".odp"}
SHEET_EXT = {".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv"}
TEX_EXT = {".tex", ".latex"}

# Which doc-tools CLI owns which extension.
TOOL_FOR = [(PDF_EXT, "pdftotext"), (DOC_EXT, "doctotext"), (SHEET_EXT, "xlstotext"), (TEX_EXT, "latextotext")]

# Below this much non-whitespace text, an "extraction" is really a failure — a scanned
# page yields a handful of stray characters at most.
MIN_TEXT_CHARS = 32

# Extra flags that make the text more useful as memo input.
EXTRA_ARGS = {
    "pdftotext": ["--tables"],
    "doctotext": ["--tables", "-f", "md"],
    "xlstotext": ["-f", "md"],
    "latextotext": ["--bodyonly"],
}


def tool_for(ext):
    for exts, tool in TOOL_FOR:
        if ext in exts:
            return tool
    return None


def staged_stem(path):
    """Build a staged filename that cannot collide with another source.

    Keeping the extension distinguishes report.pdf from report.docx, but basename alone
    still collapses papers/a/report.pdf and papers/b/report.pdf onto one staged file —
    and the second silently overwrites the first, with both reported as succeeded.
    Measured: two inputs went in, one file came out. Since globbing across directories is
    the documented use case, the absolute path is hashed into the name to keep them apart.
    """
    base = os.path.basename(path).replace(".", "_")
    digest = hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:8]
    return f"{base}-{digest}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--outdir", required=True, help="staging directory for the artifacts")
    ap.add_argument("--no-images", action="store_true", help="skip PDF page rasterization")
    ap.add_argument("--dpi", type=int, default=150, help="page image resolution (default 150)")
    ap.add_argument("--max-pages", type=int, default=100, help="cap on rasterized pages per PDF")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    manifest = []
    for path in args.inputs:
        try:
            manifest.append(ingest(path, args))
        except Exception as exc:  # one bad file must not sink the batch
            manifest.append({"source": path, "error": f"{type(exc).__name__}: {exc}"})
            print(f"warning: {path}: {exc}", file=sys.stderr)

    json.dump(manifest, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if any("error" in r for r in manifest) else 0


def ingest(path, args):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path)[1].lower()
    stem = staged_stem(path)
    record = {"source": os.path.abspath(path), "images": []}

    if ext in TEXT_EXT:
        # Already ingestible — copy so the staging dir is self-contained. But copy through
        # a decode check rather than byte-for-byte: memo_gen reads staged text as UTF-8,
        # so a latin-1 source copied verbatim lands in the staging dir looking healthy and
        # only misbehaves one step later, where it is much harder to attribute.
        dest = os.path.join(args.outdir, f"{stem}.txt")
        with open(path, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8")
            record["encoding"] = "utf-8"
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            record["encoding"] = "utf-8 (lossy: source was not valid UTF-8)"
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        record["text"] = dest
        record["tool"] = "copy"
        record["chars"] = len(text)
        record["bytes"] = os.path.getsize(dest)
        if not text.strip():
            record["error"] = "extraction produced no text"
        return record

    tool = tool_for(ext)
    if not tool:
        raise ValueError(f"no extractor for {ext}")
    if not shutil.which(tool):
        raise RuntimeError(f"{tool} not on PATH — is the doc-tools skill installed?")

    dest = os.path.join(args.outdir, f"{stem}.txt")
    subprocess.run([tool, path, *EXTRA_ARGS.get(tool, []), "-o", dest], check=True, capture_output=True, timeout=600)
    record["text"] = dest
    record["tool"] = tool
    # `chars` used to be os.path.getsize — bytes, not characters, which overstates any
    # non-ASCII document. Report both, and measure chars by actually decoding.
    try:
        with open(dest, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        text = ""
    record["chars"] = len(text)
    record["bytes"] = os.path.getsize(dest) if os.path.exists(dest) else 0

    if ext in PDF_EXT and not args.no_images:
        record["images"] = rasterize(path, stem, args)

    # check=True only catches a nonzero exit. pdftotext on a scanned PDF with no text
    # layer exits 0 and writes an empty file: the record went out with "chars": 0 and no
    # error, so the manifest looked clean and the caller indexed nothing at all. An
    # extraction this thin is a failure whether or not the tool admits it.
    if len(text.strip()) < MIN_TEXT_CHARS:
        hint = ("no text layer — this looks like a scanned PDF; the page images are the "
                "only readable content" if ext in PDF_EXT and record["images"]
                else "no text layer — if this is a scanned PDF, re-run WITHOUT --no-images "
                     "so the vision pass can read the pages"
                if ext in PDF_EXT
                else "the extractor produced (almost) nothing")
        record["error"] = (f"{tool} exited 0 but produced only "
                           f"{len(text.strip())} chars of text: {hint}")
        print(f"warning: {path}: {record['error']}", file=sys.stderr)
    return record


def rasterize(path, stem, args):
    """Render PDF pages to PNG so the vision model can read figures and charts.

    Pages whose text layer is already rich are still rendered — a page can carry
    both prose and a chart, and only the image shows the chart.
    """
    try:
        import fitz
    except ImportError:
        # The rest of this skill is stdlib-only on purpose; PyMuPDF is the one exception
        # and only for rasterizing. Say how to fix it rather than surfacing a bare
        # ModuleNotFoundError through the manifest, and note that text still works.
        raise RuntimeError(
            "PyMuPDF is needed to rasterize PDF pages: pip install PyMuPDF. "
            "Text extraction works without it — re-run with --no-images to skip figures."
        ) from None

    doc = fitz.open(path)
    out = []
    n = min(doc.page_count, args.max_pages)
    if n > 12:
        # Each page image costs a vision pass (~1-2 min). A 100-page PDF is hours of
        # local inference, and this skill's whole premise is stating the cost up front.
        print(f"note: {path}: {n} page images -> roughly {n*1.5:.0f} min of vision time "
              f"during generation (--no-images or --max-pages to cut it)", file=sys.stderr)
    for idx in range(n):
        dest = os.path.join(args.outdir, f"{stem}-p{idx + 1:03d}.png")
        doc[idx].get_pixmap(dpi=args.dpi).save(dest)
        out.append(dest)
    if doc.page_count > args.max_pages:
        print(f"warning: {path}: rasterized {args.max_pages} of {doc.page_count} pages", file=sys.stderr)
    doc.close()
    return out


if __name__ == "__main__":
    sys.exit(main())
