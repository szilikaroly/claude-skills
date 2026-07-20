#!/usr/bin/env bash
# memo-index gate — runs from hooks, on every prompt and after every tool call.
#
# Two jobs, both silent unless they have something worth saying. A hook that talks
# every turn gets ignored by turn three, so this one only speaks when it changes
# what should happen next.
#
#   1. Context ceiling. At/over the threshold it writes HANDOFF.md and tells the
#      model to stop taking on bulk work and hand off. It CANNOT clear the context —
#      /clear is the user's action — so it says so plainly instead of pretending.
#   2. Index availability. If the project already has a .memo index, it reminds the
#      model to query it instead of re-reading the sources it was built from.

SKILL="$HOME/.claude/skills/memo-index"
THRESHOLD="${MEMO_CTX_THRESHOLD:-70}"
MEMO_DIR="${MEMO_DIR:-.memo}"

# 1. context ceiling — ctx_watch prints nothing below the threshold
python3 "$SKILL/scripts/ctx_watch.py" --hook \
        --threshold "$THRESHOLD" \
        --handoff "$MEMO_DIR/HANDOFF.md" \
        --memo-dir "$MEMO_DIR" 2>/dev/null

# 2. index availability — only speak if an index actually exists here
if [ -f "$MEMO_DIR/index.db" ]; then
  n=$(python3 - "$MEMO_DIR/index.db" <<'PY' 2>/dev/null
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1])
    print(c.execute("SELECT COUNT(*) FROM claims WHERE status IN"
                    " ('CONFIRMED','DRIFTED')").fetchone()[0])
except Exception:
    print(0)
PY
)
  if [ "${n:-0}" -gt 0 ]; then
    echo "[memo-index] $n verified claims indexed in $MEMO_DIR."
    echo "Query them before reading those sources again:"
    echo "  $SKILL/scripts/memo_query.py --memo-dir $MEMO_DIR '<question>'"
  fi
fi

exit 0
