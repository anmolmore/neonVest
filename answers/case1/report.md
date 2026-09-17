# Case 1 — The data layer

Merge Airtable (structured) + MySQL (free-text) + match_history into one keyed investor table.

## Source files, real state

**airtable_investors.csv** — 228 rows, key `record_id`. Mostly empty structured fields:
`check_size` blank 202/228 (88.6%), filled values free text ("$80K–$1M", "50~100k",
"Varies") — not parseable as-is. `investor_type` blank 51.8%, `sectors` blank 42.5%.
`title` sometimes a full sentence, not a job title — free-response bleed.

**mysql_investor_notes.csv** — 223 rows, key `investor_pk`. `notes` free text: 88 rows
embed an email, 47 a phone, 21 empty. 14 rows are junk — "Legacy Contact 0"–"13",
firm "rokk3r"/"Unknown"/blank, no `created` date. 16 match_history rows point at these pks
— dead identities still wired into outcome data.

**match_history.csv** — 360 rows, FK to `mysql_investor_notes.investor_pk` only, no link
to Airtable. No shared key across sources at all — join has to run on `(name, firm)` text,
the exact thing you'd want a key to avoid.

## Merged schema

```
investor (
  investor_id          UUID          -- surrogate key, minted at merge
  airtable_record_id   text NULL     -- FK to source, 1:1 or NULL
  mysql_investor_pk    int  NULL     -- FK to source, 1:1 or NULL
  full_name            text
  firm_name            text
  title                text
  investor_type        text[]
  check_size_raw       text NULL     -- verbatim, unparsed
  check_size_min_usd   numeric NULL  -- parsed, nullable
  check_size_max_usd   numeric NULL
  sectors              text[]
  geographies          text[]
  stages               text[]
  takes_meetings       boolean NULL
  contact_email        text NULL     -- from notes
  contact_phone        text NULL     -- from notes
  notes_raw            text NULL
  source_confidence    enum(clean, review, unresolved)
  merge_method         enum(exact_key, fuzzy_match, single_source, excluded_sentinel)
  last_updated_at      timestamp     -- max(submitted_at, created)
)

match_history (
  match_id          int PK (source)
  investor_id       UUID FK -> investor.investor_id   -- resolved, not raw mysql pk
  company           text
  company_sector    text
  company_stage     text
  matched_on        date
  outcome           enum
  investor_feedback text
)
```

**Unique on** `investor_id` — synthetic, minted at merge. Neither `record_id` nor
`investor_pk` is a real identity; no reliable real identity (email, LinkedIn) exists in
either source. `(full_name, firm_name)` is the matching key, not a uniqueness guarantee —
collides, breaks silently on rename.

## Merge / dedup strategy

1. Normalize both sides: lowercase, strip punctuation, strip honorifics, normalize sector
   vocabulary (source has literal double-spaces baked in, e.g. "AR  / VR").
2. **Tier 1**: exact match on normalized `(full_name, firm_name)`. Auto-merge, keep both IDs.
3. **Tier 2**: fuzzy match remainder — name similarity ≥0.90, firm token overlap ≥0.70
   (after stripping Inc./Ventures/Capital/etc.). Auto-merge only if exactly one candidate
   clears threshold. 2+ candidates → review, never pick the closest.
4. Field-level merge on matched pairs: Airtable wins structured fields (sectors, stages,
   geo, investor_type). MySQL wins contact info (regex-extracted from notes). `notes_raw`
   always carried unparsed.
5. Within-source dedup before cross-source join: 6 exact-dup pairs in Airtable, 5 in
   MySQL. Keep most-recent-timestamp row as primary, union non-conflicting fields in. Two
   non-null values for the same field disagreeing (e.g. two different emails for one
   person) = conflict, not dedup → review, don't guess. Alias every constituent source ID
   to the surviving row so match_history joins don't orphan.
6. Merge is append-only on the audit side: every source row keeps a pointer to the
   `investor_id` it folded into — bad merges are reversible.

## Q1 — refused records: who reviews, against what, how big

Auto-merge = exact key match, or single unambiguous fuzzy match with no conflicting
field. Everything else → review:

- **Field conflict on a confirmed match** — same person/firm, two different emails across
  two MySQL rows. Wrong guess = bounced intro or stale contact. Hard stop.
- **Ambiguous fuzzy match** — 2+ MySQL candidates equally close to one Airtable row.
  Highest-risk case for silently crediting an outcome to the wrong person.
- **14 "Legacy Contact"/rokk3r rows** — not ambiguity, integrity: no name, no date.
  Excluded from investor table; their match_history rows kept, repointed at an
  `UNKNOWN_LEGACY` sentinel so outcome counts don't silently drop.
- **Blank-identity rows** — 10 Airtable rows with no `full_name`. Nothing to dedupe
  against — flagged as "possibly distinct people," never auto-collapsed.

**Reviewer**: whoever on ops/BD owns investor relationships, checked against CRM or a
direct email on contact conflicts. **Queue size, real numbers**: 17 rows (7 field
conflicts + 10 blank-identity) out of 451 source rows — one sitting, not a workflow.
**Becomes a problem when**: queue size scales with ingest volume instead of staying flat —
if every import adds another dozen, fix the Airtable form / MySQL import upstream, don't
grow the backlog.

## Q2 — are self-reported sectors usable?

**How filled in**: 97/228 (42.5%) have zero sectors. Rest is bimodal — small clusters at
1–4 tags, then spikes at 27 tags (13 records) and **41 tags** (6 records, i.e. the entire
vocabulary). "Select all, move on" on a long multi-select, not a thesis.

**Against**: cross-checked vs. match_history outcomes — of 151 `meeting_taken` rows with
declared sectors, company sector fell inside the list in 39, outside in 40. Coin flip. One
investor's notes say "deep tech and hardware only, no marketplaces"; their sectors field
lists ten categories including Marketplace, omits Hardware/DeepTech. 46 `meeting_taken`
rows carry feedback "took the meeting as a favour, not a fit."

**For**: still the only structured signal at scale — free text needs NLP, and 88/223 notes
are just contact info, not focus. As a soft deprioritize filter, or blended with
match_history as a stronger prior, has marginal value.

**Verdict**: weak prior, never a hard filter. 50/50 hit rate means a hard filter rejects
about as many good matches as it blocks bad ones. `match_history` (sector + outcome) is
the more trustworthy signal for the same job — weight it above the self-reported field,
not alongside it.

## Q3 — delete vs. keep-anyway

**Delete**: the 14 legacy/rokk3r rows as identity records — keep their match_history rows
reattributed to the sentinel. Delete exact within-source dupes once absorbed. Don't delete
free-text `title` overflow — stop treating it as structured, keep as notes.

**Keep though useless today**: `notes_raw` in full — the ~21% unstructured remainder
("see my website," "warm intros only") is exactly what a future extraction pass or LLM
classifier needs; deleting it now is a one-way door. `match_history.investor_feedback` —
mostly boilerplate today, but the only ground truth this dataset has on match quality
(it's what the Q2 sector analysis was built from). Both source IDs on every merged row,
permanently — not for matching, but to trace and undo a bad merge.

## Appendix — merge run output

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

`src/extract.py` → `transform.py` → `load.py`, against `data/raw/*.csv`, writing
`data/processed/{merged_investors,merged_match_history,review_queue}.csv`.
