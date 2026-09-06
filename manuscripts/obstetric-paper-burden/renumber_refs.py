#!/usr/bin/env python3
"""Renumber references into BMJ order: sequence of first citation in the text.

Run after any edit that adds, removes or moves a citation. Reports what it
changed, and fails if a listed reference is never cited or a marker has no entry.
"""
import re
import sys
from pathlib import Path

md = Path("manuscript.md")
text = md.read_text()
head, _, reflist = text.partition("# REFERENCES\n")
reflist_body, marker, tail = reflist.partition("\n# FIGURE LEGENDS")
if not marker:
    sys.exit("could not find '# FIGURE LEGENDS' after the reference list; "
             "refusing to rewrite the file")

entries = {}
for line in reflist_body.split("\n"):
    m = re.match(r'^(\d+)\. (.+)$', line.strip())
    if m:
        entries[int(m.group(1))] = m.group(2)

def expand(token):
    out = []
    for part in token.split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-')
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out

order = []
for m in re.finditer(r'\[(\d+(?:\s?[-,]\s?\d+)*)\]', head):
    for n in expand(m.group(1)):
        if n not in order:
            order.append(n)

missing = sorted(set(entries) - set(order))
unknown = sorted(set(order) - set(entries))
if unknown:
    sys.exit(f"citation markers with no reference entry: {unknown}")
if missing:
    sys.exit(f"references never cited in the text: {missing} — add a marker or drop them")

remap = {old: new for new, old in enumerate(order, 1)}

def collapse(nums):
    """1,2,3 -> 1-3 ; BMJ uses hyphens for runs and commas otherwise."""
    nums = sorted(nums)
    runs, start = [], nums[0]
    for a, b in zip(nums, nums[1:] + [None]):
        if b != a + 1:
            runs.append(f"{start}-{a}" if a - start >= 2 else
                        ",".join(str(x) for x in range(start, a + 1)))
            start = b
    return "[" + ",".join(runs) + "]"

head = re.sub(r'\[(\d+(?:\s?[-,]\s?\d+)*)\]',
              lambda m: collapse([remap[n] for n in expand(m.group(1))]), head)
new_list = "\n".join(f"{new}. {entries[old]}" for old, new in
                     sorted(remap.items(), key=lambda kv: kv[1]))
md.write_text(head + "# REFERENCES\n\n" + new_list + "\n" + marker + tail)
changed = {o: n for o, n in remap.items() if o != n}
print("renumbered:", changed if changed else "already in order",
      f"({len(remap)} references)")
