# Case 2, Q2 draft — findings in ai_review.md that are wrong, overstated, or would make things worse

*First-pass draft. This is the AI-assisted read; the brief is explicit that this is the
question it reads most closely and that "an assistant will happily agree with another
assistant" — so treat every claim below as something to argue with, not sign off on.*

Went back to `matching_service.py` line by line for each of the 27 findings rather than
taking the review's framing at face value. Four don't hold up as stated.

## 1. Finding 25 ("relying on MySQL's functional-dependency detection") — mischaracterized as fragile; it's actually correct, standard SQL

The review calls out `GROUP BY i.investor_id` selecting non-aggregated `i.*` columns as
something that only works because MySQL *detects* a loophole:

> `GROUP BY i.investor_id` selects non-aggregated columns, relying on MySQL's functional-dependency detection.

`investor_id` is the primary key of `investors` (it's the join key in the `LEFT JOIN` and
the only thing in the `GROUP BY`). When you group by a table's primary key, every other
column from that same table is *functionally dependent* on it by definition — each group
has exactly one row from `investors`, so `i.full_name`, `i.firm`, etc. are unambiguous.
This isn't MySQL cleverly working around a gap; it's the documented, intentional behavior
of `ONLY_FULL_GROUP_BY` since 5.7.5 (the default mode in every MySQL version this code
would plausibly run on). Calling it "relying on detection" makes a correct, standard query
pattern sound like it's one MySQL version away from breaking. It isn't — it would only
break if `investor_id` stopped being the primary key, which is a schema question, not a
query-fragility one.

**Downgrade, don't fix**: nothing to change here.

## 2. Finding 8, first half (GROUP_CONCAT alignment) — real hygiene gap, but the severity is overstated

> Two independent `GROUP_CONCAT`s are assumed to be positionally aligned... MySQL gives no
> guarantee element *i* of one corresponds to element *i* of the other, so the text can
> attribute the wrong outcome to the wrong company.

The "no guarantee" part is technically true — the SQL standard and MySQL's docs don't
contractually promise it. But both `GROUP_CONCAT` calls are aggregating the *same* row
stream, in the *same* single-pass execution, for the *same* `GROUP BY`. There is no
mechanism in MySQL by which `prior_matches` and `prior_outcomes` would be computed over
two *different* row orderings within one query execution — they're the same rows, visited
once. This isn't a live bug that will silently mis-attribute outcomes; it's an
implicit-ordering assumption that happens to always hold given how MySQL actually executes
a single query. Worth an explicit `ORDER BY m.match_id` inside both `GROUP_CONCAT`s as
cheap insurance against a future refactor (e.g., someone splits this into two queries), but
it's not the active data-integrity risk the review implies.

The **second half of the same finding** — `group_concat_max_len` defaulting to 1024 bytes
and silently truncating long histories — is correct and real, and shouldn't get lost
because the first half is overstated.

## 3. Finding 10, one clause ("an undocumented dependency") — factually off

> It agrees with cosine only because OpenAI returns unit-norm vectors — an undocumented
> dependency that breaks the moment anyone passes `dimensions=`, switches provider, or
> averages two embeddings.

The bug itself is real: `np.dot(a, b)` is not cosine similarity unless both vectors are
unit length, and this code never normalizes. But OpenAI's embeddings docs do state that
`text-embedding-3-*` vectors are normalized to length 1 — it's documented, not an
undocumented accident this code happens to be riding on. The "breaks the moment you switch
provider" consequence is still correct and is the real reason to fix it (nothing enforces
that a different embedding provider, or even a different OpenAI model, normalizes the same
way) — just not because the current behavior is undocumented.

## 4. Finding 18, the specific claim ("degrades badly past ~20 items") — real phenomenon, invented precision

> Long-list LLM ranking degrades badly past ~20 items, and 40 of 50 generated paragraphs
> are discarded.

The waste argument (`CANDIDATE_POOL = 50`, `top_n = 10`, so 40 of 50 generated
explanations are thrown away) stands on its own and doesn't need the first clause — it's a
straightforward cost problem regardless of ranking quality. The "~20 items" threshold is
stated as if it were measured against this system; it isn't — it's a general instinct about
long-context ranking that may or may not hold for this exact prompt, this exact model, and
this candidate pool. Citing it with that precision overstates how settled the number is.

## What I'd keep from this list, explicitly

Everything else survives the re-check, including all seven "Critical" items (1–7). In
particular #1 (`response_format` vs. array prompt), #6 (match-history leaking into
embeddings), and #7 (prompt injection) are not overstated — the code confirms all three as
written.

---

**Where I want your judgment, not mine**: #1 above (finding 25) is the one I'm most
confident is simply wrong, not just overstated — check that reasoning before it goes in the
submission. #2 and #3 are "the review's confidence exceeds the evidence," which is a softer
claim and more arguable. #4 is the weakest of the four — you may decide it doesn't belong
in "wrong or overstated" at all, since the underlying advice (shrink the pool) is still
probably right for cost reasons alone even if the quality-degradation citation is shaky.
