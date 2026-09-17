# Case 2, Q2 draft — findings that don't hold

*Draft. Yours to argue with — brief warns "an assistant will happily agree with another
assistant."*

Checked all 27 findings against the actual code. Four don't hold as stated.

## 1. Finding 25 (GROUP BY functional dependency) — wrong, not overstated

Review calls `GROUP BY i.investor_id` selecting non-aggregated `i.*` columns a reliance on
"detection." `investor_id` is the PK of `investors` — grouping by a table's own PK makes
every other column of that table functionally dependent by definition. Standard,
documented `ONLY_FULL_GROUP_BY` behavior since MySQL 5.7.5, not a loophole one version
from breaking. Nothing to fix here.

## 2. Finding 8, first half (GROUP_CONCAT alignment) — real gap, severity inflated

Claim: no guarantee element *i* of `prior_matches` lines up with element *i* of
`prior_outcomes`. True per spec, but both aggregates run over the same row stream in the
same single-pass query — no execution path desyncs them today. Cheap insurance
(`ORDER BY match_id` inside both) against a future refactor, not a live mis-attribution
bug. Second half of the same finding — `group_concat_max_len` = 1024B, silent truncation —
is real, keep that part.

## 3. Finding 10, one clause ("undocumented dependency") — factually off

Bug is real: `np.dot(a,b)` isn't cosine similarity unless both vectors are unit length.
But OpenAI documents `text-embedding-3-*` as unit-normalized — not an undocumented
accident. "Breaks on provider switch" consequence still stands; just not for the reason given.

## 4. Finding 18 ("~20 items" degradation claim) — real waste, invented precision

`CANDIDATE_POOL=50`, `top_n=10` → 40/50 generated explanations thrown away every call.
That argument needs no citation. The "~20 items" quality-degradation number is asserted
as if measured against this system; it isn't.

## Kept as-is

Everything else survives, including all seven Critical items (1–7) — #1 (response_format
vs. array prompt), #6 (match history leaking into embeddings), #7 (prompt injection)
confirmed against the code as written.

## Confidence ranking for your revision pass

#1 (finding 25): most confident, simply wrong. #2 and #3: "review's confidence exceeds
evidence" — softer, more arguable. #4: weakest — the underlying advice (shrink the pool)
may still be right on cost grounds alone even if the citation is shaky; you may drop this one.
