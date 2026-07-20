# Retrieval: keyword -> claim -> content

Read this before tuning scoring. The defaults were set from measurement, not taste.

## The three tiers

| tier | ~cost | what it is | when you pay |
|---|---|---|---|
| keyword | ~2 tok | what a claim is about | never read in bulk — only searched |
| claim | ~20 tok | the atomic fact + anchor | when it matches a query |
| content | ~200 tok | the real source region | only via `--expand`, once a claim earned it |

The whole saving is that tiers 2 and 3 are opt-in. Reading a memo file defeats this
entirely — it pays for every claim in the file to use one. Query; don't `cat`.

## Keyword extraction (`memo_db.extract_keywords`)

Deterministic, no model, so it costs nothing and cannot hallucinate. Weights say how much
a match should count:

| source of term | weight | why |
|---|---|---|
| backticked in the claim | 3.0 | the model explicitly said the claim is about this |
| identifier in the anchored line | 2.5 | grounded in the file, not in prose |
| numbers | 2.0 | what people search papers for, and what models fabricate |
| split identifier parts | 1.5-2.0 | `create_session` should match "session" |
| plain prose words | 1.0 | weak, but how natural-language queries land |

**Stemming is load-bearing.** Measured: without it, "how are chunks split" missed the
`chunk_lines` claim entirely and returned an unrelated claim about refusal warnings
instead — a confident wrong answer, which is worse than an empty result. Both index and
query stem identically; if you change one, change both.

Stopwords are stripped from the FTS query too. Left in, "how are chunks split" scores all
four terms and buries the one claim that matters under everything containing "are".

## Scoring

```
utility   = trust x (0.45*specificity + 0.30*idf + 0.25*feedback)
relevance = 0.6*max(fts, kw) + 0.4*min(fts, kw)
score     = relevance x (0.5 + 0.5*utility)
```

**trust** gates everything (`STATUS_W`): CONFIRMED 1.0, DRIFTED 0.9, NEEDS_AGENT 0.5,
UNSUPPORTED 0.15, REFUTED 0.0. A refuted claim multiplies to zero and is *additionally*
hard-excluded in `memo_query.search` — no `--floor`, no keyword match, and no utility can
bring it back. Trust is not a threshold; that hard exclusion is the hallucination guard,
and an early version leaked refuted claims through `--floor 0` before it was added.

**specificity** separates claims that survive verification while saying nothing
("handles various things") from ones that pin something down. Vague phrasing is penalized
0.35; an anchor, a backticked identifier, and a number each add.

**idf** is computed over the corpus, not guessed. A keyword on every claim ("function",
"returns") carries no information and must not pull matches.

**feedback** starts neutral (0.5) and only moves on evidence. `--feedback C3:helpful`
records it; it survives `--build` because it is the only signal reflecting real
usefulness rather than an index-time guess.

## Tuning

- `UTILITY_FLOOR` (0.15) — prunes vagueness. Raise it if queries return true-but-useless
  claims; lower it if good claims are missing.
- `--include-unproven` — surfaces NEEDS_AGENT claims. They are leads, not answers; use
  when exploring, not when concluding.
- Check corpus health with `memo_db.py --stats`: it prints the utility distribution and
  the noise keywords that are pulling junk matches.
