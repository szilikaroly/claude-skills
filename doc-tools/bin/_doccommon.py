"""Shared helpers for the doc-tools extractors (pdftotext/doctotext/xlstotext/latextotext)."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

SOFFICE_CANDIDATES = [
    "soffice",
    "libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    os.path.expanduser("~/Applications/LibreOffice.app/Contents/MacOS/soffice"),
]


def find_soffice():
    """Locate a LibreOffice CLI, or None. Needed only for legacy binary formats."""
    for cand in SOFFICE_CANDIDATES:
        path = shutil.which(cand) if os.sep not in cand else (cand if os.path.exists(cand) else None)
        if path:
            return path
    return None


def require_soffice(fmt):
    soffice = find_soffice()
    if not soffice:
        die(
            f"{fmt} is a legacy binary format and needs LibreOffice.\n"
            "Install it from https://www.libreoffice.org/download/ (or `brew install --cask libreoffice`),\n"
            "then re-run. Modern formats (.docx/.xlsx/.pptx) work without it."
        )
    return soffice


def soffice_convert(path, to, outdir=None):
    """Convert `path` via LibreOffice headless; returns the converted file's path.

    The caller owns the returned file's temp directory only when outdir is None,
    in which case it is a tempfile.mkdtemp() that is intentionally left behind
    for the caller to read before process exit.
    """
    soffice = require_soffice(os.path.splitext(path)[1])
    outdir = outdir or tempfile.mkdtemp(prefix="doctools-")
    subprocess.run(
        [soffice, "--headless", "--convert-to", to, "--outdir", outdir, path],
        check=True,
        capture_output=True,
        timeout=180,
    )
    stem = os.path.splitext(os.path.basename(path))[0]
    target = os.path.join(outdir, f"{stem}.{to.split(':')[0]}")
    if not os.path.exists(target):
        die(f"LibreOffice produced no output for {path}")
    return target


def pandoc(path, to, extra=None):
    """Run pandoc and return stdout, or None if pandoc is unavailable/fails."""
    if not shutil.which("pandoc"):
        return None
    cmd = ["pandoc", path, "-t", to] + (extra or [])
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def parse_pages(spec, total):
    """Parse a 1-based page spec like '1-5,8,11-' into a sorted list of 0-based indices."""
    if not spec:
        return list(range(total))
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            lo = int(lo) if lo.strip() else 1
            hi = int(hi) if hi.strip() else total
        else:
            lo = hi = int(part)
        for p in range(lo, hi + 1):
            if 1 <= p <= total:
                out.add(p - 1)
    return sorted(out)


def base_parser(description):
    p = argparse.ArgumentParser(description=description)
    p.add_argument("input", help="input file ('-' reads stdin into a temp file)")
    p.add_argument("-o", "--output", help="write here instead of stdout")
    p.add_argument(
        "-f",
        "--format",
        default="txt",
        choices=["txt", "md", "json"],
        help="txt = plain text (default), md = markdown-ish structure, json = structured records",
    )
    return p


def read_stdin_to_temp(suffix):
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="doctools-stdin-")
    with os.fdopen(fd, "wb") as fh:
        fh.write(sys.stdin.buffer.read())
    return path


def emit(text, output):
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False, indent=2)
    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {output} ({len(text)} chars)", file=sys.stderr)
    else:
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")


def resolve_input(args, suffix=".bin"):
    if args.input == "-":
        return read_stdin_to_temp(suffix)
    if not os.path.exists(args.input):
        die(f"no such file: {args.input}")
    return args.input
