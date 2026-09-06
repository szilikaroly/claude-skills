#!/usr/bin/env python3
"""Word counts under the journal's definition.

Main text excludes the abstract, key messages, tables (caption, rows and the
footnote paragraph that belongs to them), the statement sections, references
and figure legends - the same boundaries sync_tables.py uses for a table block.
"""
import re, sys
from pathlib import Path

def counts(path):
    s = Path(path).read_text()
    abstract = s.split("# ABSTRACT", 1)[1].split("**Keywords**", 1)[0]
    body = s.split("# INTRODUCTION", 1)[1].split("# ACKNOWLEDGEMENTS", 1)[0]
    # drop each table block: caption -> next heading or next caption
    body = re.sub(r"\*\*Table \d+\*\*.*?(?=\n(?:#{1,3} |\*\*Table \d))", "", body, flags=re.S)
    body = re.sub(r"⟦[^⟧]*⟧", "", body)
    w = lambda t: len([x for x in re.sub(r"[*_#|]", " ", t).split() if any(c.isalnum() for c in x)])
    return w(body), w(abstract)

if __name__ == "__main__":
    b, a = counts(sys.argv[1] if len(sys.argv) > 1 else "manuscript.md")
    print(f"main text {b:,}   abstract {a:,}")
