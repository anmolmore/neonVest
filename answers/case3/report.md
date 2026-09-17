# Case 3 — The evaluation harness

## Data shape, checked first

`match_history.csv` — 360 rows. Outcomes: meeting_taken 151, no_response 53, converted
53, intro_only 52, declined 51. Two facts drive the design:

- **305/332 companies (92%) have exactly one attempted match.** Not a labeled-ranking
  dataset — a log of single decisions, single outcomes. No ground truth anywhere for
  "what if a different investor had been picked."
- **No `cheque_target`, no free-text brief logged** — only `company_sector`/`company_stage`.
  `match()` takes both a `company_brief` string and a `company` dict with `cheque_target`;
  neither survives into this file. Replay can only exercise the sector/stage part, not the
  cheque hard filter or the semantic ranking, because the original input was never logged.

Action item, not just caveat: **log the exact request payload against every outcome going
forward.** Without it every future harness run evaluates a reconstruction, not the input.

## The harness

**Metric — two numbers.** No full candidate ranking exists, so NDCG/MRR don't apply.
Label GOOD = {converted, meeting_taken}, BAD = {declined, no_response}. `intro_only`
excluded — ambiguous (see Q1).

- **Recall_good@10** — of GOOD rows, % where that `investor_pk` lands in the new
  matcher's top 10 for that company (input reconstructed from sector/stage). Higher better.
- **Contamination_bad@10** — of BAD rows, % still in top 10. Lower better.

Two numbers, not one blended score — otherwise a matcher can cheat by just returning more
investors per company. Can't trade "sometimes right" against "often wastes the founder's
time" for a paying-founder-facing product.

**Split** — chronological, not random: earliest ~75% train/tune, latest ~25% held out.
Random split leaks near-in-time signal (sector fads, temporary investor appetite).
Quarantine the 29 future-dated rows first (Q1) — can't be legitimate trailing ground truth
for a chronological cutoff.

**Baseline** — the filtered SQL query being replaced: stage/geo hard filter, no embedding,
no LLM, current production ordering. Same two metrics, same held-out set. Question is "better
than what a founder gets today," not "good in the abstract."

**Accept/reject** — ship only if, on held-out data:

1. `Recall_good@10(new) ≥ Recall_good@10(baseline)`
2. `Contamination_bad@10(new) ≤ Contamination_bad@10(baseline)`
3. the gain on (1) survives a bootstrap CI — test set is ~60–70 rows after quarantine; if
   the interval crosses zero, that's "no evidence," not a win.

(2) blocks shipping regardless of (1) — a false positive spends a founder's limited
investor relationships on someone who already said no or went quiet, which costs more than
a missed good match.

## Q1 — what's wrong with the ground truth

**`investor_feedback` is statistically independent of `outcome`.** Every canned feedback
string appears across all five outcomes in roughly equal proportion.
`"Converted, led the round."` attaches to `outcome=declined` 14 times, `no_response` 7
times, `converted` only 8 times. Not a few miskeyed rows — every string, evenly spread.
**Consequence**: can't use feedback as a feature or to disambiguate/sanity-check outcome —
it carries no information about outcome in this file. Never touched in the harness above.

**29/360 rows (8%) dated after the evaluation's own as-of point**, up to 2026-12-25.
Can't have a recorded outcome for a meeting that hasn't happened yet, chronologically.
**Consequence**: exclude from both train and test explicitly rather than let the sort
place them — shrinks an already-small set further, hence the CI requirement above.

**92% single-outcome-per-company is a ground-truth ceiling, not just a metric-design
issue.** We only observe the outcome of whichever investor the old system picked. If the
old system had a systematic blind spot, every historical row inherits it — no harness
recovers the missing counterfactual. Reason to pair a historical-replay "pass" with a
small live/shadow test before trusting it fully.

## Q2 — what ships the old query instead

Ship the baseline, not the new matcher, if either:

- **Contamination_bad@10(new) > baseline**, CI excluding zero — real evidence the new
  matcher surfaces more decline/ghost investors than a plain filter. A ranked list with a
  written explanation reads as more credible to a founder than a bare filter result —
  confidently wrong beats plainly average, in the wrong direction.
- **Recall_good@10(new) statistically indistinguishable from baseline** — new matcher
  costs materially more per request (rebuilds the embedding index from scratch, one call
  per investor, every request) and is less explainable than a transparent SQL filter.
  Paying that cost for a tie is a net loss.

Either result: ship the old one, no caveat. Failure mode that matters here is false
confidence, not missed opportunity — a known-mediocre baseline beats an unproven ranker.
