# Case 3 — The evaluation harness

## What the data actually looks like (checked before designing anything)

`data/raw/match_history.csv`: 360 rows, `outcome` ∈ {meeting_taken 151, no_response 53,
converted 53, intro_only 52, declined 51}, spanning 2023–2026. Two structural facts drive
every choice below:

- **305 of 332 distinct companies (92%) have exactly one attempted match.** Only 27 have
  2–3. This is not a labeled-ranking dataset (many candidates per query, one correct
  answer) — it's a log of single decisions with a single realized outcome each. There is
  no ground truth anywhere in this file for "what would have happened with a different
  investor."
- **The file logs `company_sector` and `company_stage` only** — no `cheque_target`, no
  free-text company brief. `matching_service.match()` takes both a `company_brief` string
  and a structured `company` dict (with `cheque_target`); neither is retrievable from this
  file. Any replay of history through the real matcher can only exercise the
  sector/stage-driven part of it, not the cheque-size hard filter or the free-text
  semantic ranking, because the exact input that produced each historical row was never
  logged.

That second point is itself an action item, not just a caveat: **starting now, log the
exact `company_brief` and `company` payload alongside every outcome.** Without it, every
future harness run is evaluating a reconstruction of the input, not the input.

## The harness

**Metric — two numbers, not one.** Classic ranking metrics (NDCG, MRR) assume you know
the full relevance ordering over the candidate set; we don't — we have one realized
outcome per company, 92% of the time. So the metric has to be built around "did the
matcher's list contain the one thing we have a label for":

- Label outcomes as GOOD (`converted`, `meeting_taken`) or BAD (`declined`, `no_response`).
  `intro_only` is excluded from both — see Q1, it's ambiguous.
- **Recall_good@10** — of the GOOD-labeled historical rows, what fraction have that same
  `investor_pk` inside the new matcher's top 10 for that company? (Reconstructing the
  company input from `company_sector`/`company_stage`, per the limitation above.) Higher
  is better — it asks "would the new matcher have surfaced the investor who actually
  converted or took the meeting?"
- **Contamination_bad@10** — of the BAD-labeled rows, what fraction still land in the top
  10? Lower is better — it asks "does the new matcher keep surfacing investors who
  actually declined or ghosted?"

Two numbers because a matcher can cheat a single blended score by just returning more
investors per company; a founder-facing product can't trade "sometimes finds the right
one" against "often wastes the founder's time with the wrong one" — both have to hold.

**Split — chronological, not random.** Sort by `matched_on`, train/tune on the earliest
~75%, hold out the most recent ~25% as the test set the matcher never sees during
development. Random splitting would leak information about outcomes near in time to each
other (sector fads, an investor's temporary appetite) into training. **Quarantine the
29 rows dated after the evaluation's "as-of" date first** (see Q1) — they can't be
legitimate trailing ground truth for a chronological cutoff, and including them would
silently shrink or corrupt whichever side of the split they land on.

**Baseline — the filtered SQL query the matcher replaces.** Reproduce it literally: apply
`passes_hard_filters`-equivalent stage/geography matching, no embedding, no LLM, ordered by
whatever the current production query orders by (e.g., most recently active investor).
Compute the same two metrics for it on the same held-out set. The point of Case 3 isn't
"is the new matcher good in the abstract," it's "is it better than what a founder gets
today" — and today's baseline literally is a filtered database query, per the case brief.

**Accept/reject rule.** Ship the new version only if, on the held-out set:

1. `Recall_good@10(new) ≥ Recall_good@10(baseline)` — it must find at least as many of
   the investors who actually worked out, and
2. `Contamination_bad@10(new) ≤ Contamination_bad@10(baseline)` — it must not surface
   more investors who actually declined or ghosted, and
3. the improvement on (1) is outside sampling noise given the test set is only on the
   order of 60–70 rows after quarantining future dates — bootstrap a confidence interval
   on the metric difference; if it crosses zero, treat the result as "no evidence of
   improvement," not as a win.

Any regression on (2) blocks shipping regardless of gains on (1) — a false positive here
means a founder's limited investor relationships get spent on someone who's already said
no or gone quiet, which the case brief is explicit costs more than a missed good match.

## Q1 — What's wrong with the ground truth, and what it does to the harness

**`investor_feedback` is statistically independent of `outcome`.** Every canned feedback
string in the file appears across all five outcome categories in roughly the same
proportions. `"Converted, led the round."` — which reads unambiguously as a win — is
attached to `outcome = declined` 14 times, `no_response` 7 times, and to `outcome =
converted` only 8 times. `"Too early."` appears under `converted` 7 times and under
`meeting_taken` 22 times. This isn't a handful of miskeyed rows; it's every feedback
string, spread evenly, which looks like `outcome` and `investor_feedback` were generated
independently rather than as one coherent record.

**Consequence for the harness:** `investor_feedback` cannot be used as a feature, as a way
to disambiguate an ambiguous outcome, or as a sanity check on `outcome` itself — it carries
no information about outcome in this file. The harness above deliberately never touches
it. (It may still be worth keeping per Case 1's answer — for a future dataset where it's
actually coupled to outcome correctly — but not this one.)

**29 of 360 rows (8%) are dated after the evaluation's own "as of" point**, as late as
2026-12-25. You cannot have a recorded outcome for a meeting that, chronologically, hasn't
happened yet. **Consequence:** any chronological split must explicitly exclude these rows
from both train and test rather than let them land wherever the sort puts them — and doing
so shrinks an already-small dataset (360 rows total) further, which is exactly why the
accept/reject rule above insists on a confidence interval rather than a bare point
estimate.

**92% single-outcome-per-company, discussed above, is itself a ground-truth limitation,
not just a metric-design constraint.** We only ever observe the outcome of the investor
the *old* system chose. If the old system was systematically bad at reaching a whole class
of good-fit investors, every historical row inherits that blind spot, and no amount of
harness design recovers the missing counterfactual. This is a ceiling on what any offline
harness against this file can tell you — it's a reason to pair the metric above with a
small live/shadow test before fully trusting a "pass" on historical replay.

## Q2 — What result ships the old query instead

Ship the old filtered-query baseline, not the new matcher, if either:

- **`Contamination_bad@10` for the new matcher is higher than the baseline's**, with a
  confidence interval that does not include zero — i.e., there's real evidence the new
  matcher surfaces more investors who actually declined or went quiet than a plain SQL
  filter does. A ranked list with a written explanation reads as more credible to a
  founder than a bare filter result; being more confidently wrong is worse than being
  plainly average.
- **`Recall_good@10` is statistically indistinguishable from the baseline** (confidence
  interval crosses zero) — because the new matcher costs materially more to run (per Case
  2's finding on rebuilding the embedding index from scratch on every request) and is
  less predictable/explainable than a transparent SQL filter, paying that cost for a tie
  is a net loss, not a wash.

Either result is "ship the old one," full stop — not "ship the new one with a caveat."
The brief's framing (a founder paying for this) means the failure mode that matters is
false confidence, not missed opportunity, and a baseline SQL filter that's honestly not
great is a known, bounded quantity in a way an unproven ranker is not.
