# neonVest Case Study — Case 1: The Data Layer

Merging Airtable structured attributes with MySQL free-text notes and match history into one clean, keyed investor dataset.

## What's actually in the files

**data/raw/airtable_investors.csv**: 228 rows, keyed by `record_id` (unique). Structured columns are mostly empty in practice: `check_size` is blank in 202/228 rows (88.6%), and the 26 values that are filled are free text (“$80K – $1M”, “50~100k”, “Varies”, “N/A”) — not a parseable field today. `investor_type` is blank in 51.8% of rows, `sectors` in 42.5%. `title` sometimes contains a full sentence (“I'm currently a Founder and CEO”) instead of a job title — form-field bleed from a free-response question.

**data/raw/mysql_investor_notes.csv**: 223 rows, keyed by `investor_pk` (unique). `notes` is unstructured free text; 88 rows have an embedded email, 47 a phone number, 21 are empty. 14 rows are placeholder junk: `name_raw` = “Legacy Contact 0”–“13”, `firm_raw` = “rokk3r” / “Rokk3r Inc.” / “Unknown” / blank, `source` = “import_2023”, no `created` date — a synthetic/scrubbed legacy batch, not real investors, but 16 rows in match_history reference these pks, so they're wired into outcome data.

**data/raw/match_history.csv**: 360 rows, foreign-keyed to `mysql_investor_notes.investor_pk`, not to Airtable. There is no shared key across the two source systems at all — joining Airtable to MySQL (and therefore to match history) has to happen on `(name, firm)` text matching, which is exactly the unreliable operation you'd want a key for.

## Schema for the merged dataset

```
investor (
  investor_id          UUID          -- new surrogate key, minted at merge time
  airtable_record_id   text NULL     -- FK back to source, 1:1 or NULL
  mysql_investor_pk    int  NULL     -- FK back to source, 1:1 or NULL
  full_name            text
  firm_name            text
  title                text
  investor_type        text[]
  check_size_raw       text NULL     -- kept verbatim, unparsed
  check_size_min_usd   numeric NULL  -- parsed, nullable, confidence flag
  check_size_max_usd   numeric NULL
  sectors              text[]
  geographies          text[]
  stages               text[]
  takes_meetings       boolean NULL
  contact_email        text NULL     -- extracted from notes
  contact_phone        text NULL     -- extracted from notes
  notes_raw            text NULL
  source_confidence    enum(clean, review, unresolved)
  merge_method         enum(exact_key, fuzzy_match, single_source, excluded_sentinel)
  last_updated_at      timestamp     -- max(submitted_at, created)
)

match_history (
  match_id          int PK (from source)
  investor_id       UUID FK -> investor.investor_id   -- resolved, not the raw mysql pk
  company           text
  company_sector    text
  company_stage     text
  matched_on        date
  outcome           enum
  investor_feedback text
)
```

**Uniqueness**: a record is unique on `investor_id`, a synthetic key minted at merge time — because neither `record_id` nor `investor_pk` is a real-world identity, and no real-world identity (email, LinkedIn URL) is reliably present in either source. `(full_name, firm_name)` is the *matching* key, not a uniqueness guarantee — it collides (see below) and fails silently on renames.

## Merge & deduplication strategy

1. **Normalize** both sides first: lowercase, strip whitespace/punctuation, strip honorifics (“Dr”, “A.”), and normalize the sector vocabulary itself (e.g. “AR  / VR” has a literal double space baked into the source vocabulary).
2. **Tier 1 — exact match** on normalized `(full_name, firm_name)`. Auto-merge; keep both source IDs.
3. **Tier 2 — fuzzy match** the remainder on name similarity (≥ 0.90) and firm token overlap (≥ 0.70, after stripping generic suffixes like Inc./Ventures/Capital/Partners). Auto-merge only if there is exactly one candidate above threshold; two or more competing candidates go to manual review instead of picking the closest one.
4. **Field-level merge** when both sides match: Airtable wins for structured fields (sectors, stages, geographies, investor_type) since that's its purpose-built schema; MySQL wins for contact info (parsed out of notes via email/phone regex) and is the only source for the match-history join. `notes_raw` is always carried through unparsed.
5. **Within-source duplicate collapse**, done before the cross-source join: exact (name, firm) duplicates within Airtable (6 pairs, e.g. Pieter Kovac / Saltmarsh Holdings appears twice, one copy missing `submitted_at`) and within MySQL (5 pairs). Rule: keep the row with the more recent/non-null timestamp as primary; union in any non-conflicting fields from the other; if two non-null values for the *same* field disagree (Marco Baker's notes list two different emails, `@gmail.com` vs `@proton.me`, across his two MySQL rows), that's a conflict, not a dedup — route to review rather than guess. Every constituent source ID is aliased to the surviving row's ID so downstream joins (match_history) don't silently orphan rows that pointed at the non-primary duplicate.
6. Merge is **append-only on the audit side**: every source row keeps a pointer to the `investor_id` it was folded into, so a bad merge is reversible.

## Q1 — Which records get refused, and what happens to them?

Auto-merge only when a match is an exact normalized-key match, or a fuzzy match with a single high-confidence candidate and no conflicting non-null field. Everything else goes to a review queue, specifically:

- **Field-level conflicts on an otherwise-confirmed match** — the Marco Baker two-email case. Same person, same firm, two different contact emails from two different `investor_pk` rows. Auto-merging and guessing wrong means a warm intro bounces or goes to a stale address, so this is a hard stop.
- **Fuzzy matches without a clear winner** — two or more MySQL candidates equally close to one Airtable name/firm. Ambiguous identity merges are the ones most likely to silently corrupt match history (an outcome credited to the wrong person).
- **The 14 “Legacy Contact N / rokk3r” rows** — not a merge-ambiguity problem, a data-integrity problem: no real name, near-empty firm, no created date, but 16 match_history rows depend on them. These are excluded from the investor table as structurally unresolvable, and their match_history rows are kept but pointed at a synthetic `UNKNOWN_LEGACY` sentinel so the outcome data isn't lost, just clearly marked as not attributable to a contactable investor.
- **Blank-identity rows** — 10 Airtable rows with empty `full_name` (e.g. two “Fathom Group” rows with different titles/sectors and no name at all). Can't dedupe or merge these against anything; they go to review as “possibly two different unnamed people at the same firm,” not auto-collapsed into one.

**Who reviews, against what, how large can the queue get**: this is a short, cheap manual pass for whoever on the ops/BD side owns investor relationships, checked against the CRM outreach actually runs through, or a direct email when contact info conflicts. Against the real numbers here — running the merge script against the current CSVs produces a review queue of **17 rows** (7 genuine field conflicts + 10 blank-identity rows) out of 451 combined source rows — that's a one-sitting task, not a workflow. The line where it becomes a problem is if the queue's size scales with ingest volume rather than staying roughly constant: if every new CSV import adds another dozen unresolved rows, the fix belongs upstream (the Airtable form, the MySQL import script), not in a permanently growing review backlog.

## Q2 — Are self-reported sectors usable for matching?

**How they were actually filled in**: 97 of 228 records (42.5%) have zero sectors. Of the rest, the distribution is bimodal in a telling way — a pile of records with 1–4 tags, then spikes at 27 tags (13 records) and **41 tags** (6 records) — 41 being *every single sector tag in the vocabulary*. Nobody has a genuine, differentiated thesis across all 41 categories; that's satisficing behavior on a long multi-select form (click “select all” and move on), not signal.

**Against usability**: cross-referencing declared sectors against actual behavior in match_history is damning. Of the 151 `meeting_taken` outcomes where the investor had any declared sectors, the company's sector was inside the investor's declared list in **39 cases and outside it in 40** — statistically a coin flip. Concretely, Marco Baker's MySQL note says “Deep tech and hardware only. Please no marketplaces” — his Airtable sectors field lists ten categories including **Marketplace**, and omits Hardware/DeepTech entirely. And 46 `meeting_taken` rows carry the feedback “Took the meeting as a favour, not a fit” — investors are visibly taking meetings outside whatever they checked on a form.

**For usability**: it's still the only structured, queryable signal that exists at scale — free-text notes require NLP to extract anything comparable, and 88/223 of those notes are about contact details, not focus. Used as a soft filter (deprioritize, not exclude) or blended with match_history as a stronger prior than the form itself, it has some value — an investor who's never met with a FinTech company despite years of activity is informative regardless of what their sectors field says.

**My read**: treat sectors as a weak prior at best, never a hard filter — the 50/50 hit rate against actual meetings means a hard filter would reject roughly as many good matches as it prevents bad ones. Match history (`company_sector` + `outcome`) is the more trustworthy signal for the same purpose and should be weighted above the self-reported field, not alongside it.

## Q3 — What would you delete, and what would you keep anyway?

**Delete**: the 14 “Legacy Contact” / rokk3r placeholder rows as *identity* records (no real name/firm recoverable) — but keep their match_history rows, reattributed to a sentinel, not deleted. Delete exact duplicate rows within a source once the surviving row has absorbed any non-conflicting fields (the `submitted_at`-less copies of Pieter Kovac / Marco Baker, once merged). Don't delete the free-text title overflow (“I'm currently a Founder and CEO”) — just stop treating it as a structured `title` field; keep it as notes.

**Keep even though it's useless today**: `notes_raw` in full, unparsed beyond email/phone extraction — the 21% with no extractable structure (“See my website,” “Currently not deploying,” “Warm intros only”) is exactly the kind of thing that becomes valuable the day someone builds a better extraction pass or an LLM sector classifier; deleting free text because today's regex can't use it is a one-way door. Same for `match_history.investor_feedback` — right now it's a handful of repeated boilerplate strings, but it's the closest thing this dataset has to ground truth on what actually drives a good match, and it's exactly what the 50/50 sector analysis above was built from. And keep both source IDs (`airtable_record_id`, `mysql_investor_pk`) on every merged row permanently — not because they're useful for matching, but because they're the only way to trace a bad merge back to its source and undo it.

## Appendix: merge run output

```
Airtable source rows:        228
MySQL source rows:           223
  - excluded legacy pks:      14
  - blank-identity airtable:  10
Merged investors:            221
  - matched both sources:     195
  - airtable only:             18
  - mysql only:                 7
  - + UNKNOWN_LEGACY sentinel:  1  -> 222 rows in data/processed/merged_investors.csv
Match history rows:          360  (+1 header) -> 361 rows in data/processed/merged_match_history.csv
  - unresolved investor_pk:     0
Review queue rows:              17
```

Produced by the `src/` pipeline (`extract.py` → `transform.py` → `load.py`) against `data/raw/airtable_investors.csv`, `data/raw/mysql_investor_notes.csv` and `data/raw/match_history.csv`. Outputs: `data/processed/merged_investors.csv`, `data/processed/merged_match_history.csv`, `data/processed/review_queue.csv`.
