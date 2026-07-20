# Memo format

Read this if you're debugging script output or writing a memo by hand.

## File layout

Memos live in `.memo/`, one per source, named `<path-with-__-separators>.memo.md`.
`.memo/INDEX.md` is generated and should not be edited.

```markdown
---
source: src/auth/session.ts
sha256: a1b2c3d4...
lines: 412
profile: code
generator: ollama/qwen2.5-coder:7b
raw_tokens_est: 4180
verified: 9 confirmed, 1 drifted, 1 refuted, 2 needs_agent
---

# src/auth/session.ts

## Topics
- L1-33: imports and module-level session store
- L34-87: session creation and TTL handling
- L88-140: token refresh and rotation

## Claims

- [C1] [CONFIRMED] `createSession(userId, ttl)` returns a Session with expiresAt set from ttl @L34 `export function createSession(userId: string, ttl: number): Session {`
- [C2] [DRIFTED] the store is an in-memory Map, not Redis @L14 `const store = new Map<string, Session>()`  <!-- anchor moved L12->L14 -->
- [C3] [REFUTED] sessions are persisted to Redis on write @L60 `await redis.set(key, val)`  <!-- snippet not found in source -->
- [C4] [NEEDS_AGENT] this module is the single owner of session lifetime @semantic L34-140
- [C5] [UNSUPPORTED] sessions default to a 24h TTL @semantic L34-87  <!-- verified: L34: no default is defined; ttl is always a required parameter -->
```

## Frontmatter

| field | meaning |
|---|---|
| `source` | path relative to the root passed to `memo_gen.py` |
| `sha256` | hash of the source **at generation time** — this is the staleness check |
| `lines` | source line count |
| `profile` | `code`, `prose`, or `generic`; drives chunking and prompt |
| `generator` | which model wrote the claims (`ollama/...` or `subagent`) |
| `raw_tokens_est` | ~tokens to read the source directly; feeds the ledger |
| `verified` | tally, rewritten by `verify_anchors.py` and `apply_verdicts.py` |

`sha256` is load-bearing. If you hand-edit a memo, the hash still says "current" and
every later run will trust your edit as if the pipeline had verified it. Regenerate
instead: `memo_gen.py --force`.

## Claim grammar

```
- [C<n>] [<STATUS>] <claim text> <anchor>  <!-- optional note -->
```

Anchors come in two kinds:

- **`@L<line> \`<snippet>\``** — the snippet must appear at that line in the source.
  Checked by string match (whitespace-collapsed), so it costs nothing. This is the
  preferred kind: a claim with a textual anchor never needs a subagent.
- **`@semantic L<lo>-<hi>`** — the claim is about meaning, not text ("this module owns
  retry policy"). No string match can settle it, so it escalates to step 4.

The local model is told to copy snippets character-for-character. That instruction is
doing real work: a model that paraphrases the snippet produces a claim that fails string
match and gets thrown out, which is the correct outcome for a model that wasn't actually
looking at the line.

## Statuses

| status | set by | meaning | trust it? |
|---|---|---|---|
| `PENDING` | `memo_gen.py` | freshly generated, not yet checked | no |
| `CONFIRMED` | `verify_anchors.py` or step 4 | snippet found at the cited line, or a subagent confirmed from source | yes |
| `DRIFTED` | `verify_anchors.py` | snippet found, but elsewhere; anchor auto-corrected | yes — the fact holds, the line moved |
| `REFUTED` | either | snippet nowhere in the source, or a subagent found a contradiction | no — actively wrong |
| `NEEDS_AGENT` | `verify_anchors.py` | semantic; awaiting step 4 | not yet |
| `UNSUPPORTED` | step 4 only | source doesn't settle it either way | treat as a lead, not a fact |
| `STALE` | `verify_anchors.py` | source changed since generation | regenerate |

`REFUTED` and `UNSUPPORTED` claims stay in the file on purpose. They're a record of what
the local model got wrong on this source, which is exactly the thing that would otherwise
get regenerated and believed next time.

## Why claims and not prose

A prose summary can't be checked. "This module handles authentication and session
management, using an in-memory store with configurable TTLs" is one sentence containing
four claims, one of which (configurable TTLs) may be invented — and there's no way to
verify a quarter of a sentence. Atomic claims are individually true or false, individually
anchored, and individually verifiable in parallel. The format is unlovely to read and
that's fine; the index is what humans read.
