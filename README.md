# neonVest — Founding Engineer, AI Platform case study

Working repo for the four-case take-home ([`neonVest_FE_Case_Study.docx`](neonVest_FE_Case_Study.docx)).
The actual submission is a single PDF emailed per the brief; this repo is the working
material and the code behind Case 1.

## Case-study attachments (given, not authored here)

- [`data/raw/airtable_investors.csv`](data/raw/airtable_investors.csv) — structured investor attributes
- [`data/raw/mysql_investor_notes.csv`](data/raw/mysql_investor_notes.csv) — free-text notes + contact info
- [`data/raw/match_history.csv`](data/raw/match_history.csv) — past matches with outcomes
- [`matching_service.py`](matching_service.py) — draft matching service (Case 2 subject)
- [`ai_review.md`](ai_review.md) — an AI-generated code review of that draft (Case 2 subject)

## Answers

- **Case 1 — The data layer** → [`answers/case1/report.md`](answers/case1/report.md)
  Schema, merge/dedup strategy, and the merged output. Implementation: [`src/extract.py`](src/extract.py) →
  [`src/transform.py`](src/transform.py) → [`src/load.py`](src/load.py), writing
  [`data/processed/`](data/processed/). Browse the merged vs. raw tables and the review
  queue in [`merge_review_app.html`](merge_review_app.html) (open locally in a browser).
- **Case 2 — Triage the review** → [`answers/case2/q2_findings_that_dont_hold.md`](answers/case2/q2_findings_that_dont_hold.md)
  Draft answer to "which findings in `ai_review.md` are wrong or overstated." (Q1/Q3 in progress.)
- **Case 3 — The evaluation harness** → [`answers/case3/report.md`](answers/case3/report.md)
  Metric, split, baseline, accept/reject rule, plus data-quality findings against `match_history.csv`.
- **Case 4 — The note** → [`answers/case4/note_to_rohit.md`](answers/case4/note_to_rohit.md)
  Read-replica recommendation, written for a non-engineer CEO.

## Running the merge pipeline

```
python3 src/load.py
```

Regenerates `data/processed/merged_investors.csv`, `merged_match_history.csv`, and
`review_queue.csv` from `data/raw/`. Paths are resolved relative to the repo root, so this
works from any working directory.
