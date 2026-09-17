"""
Load stage: write the transformed tables to data/processed/, and run the
full extract -> transform -> load pipeline when executed directly.

Outputs:
  merged_investors.csv       - the merged, deduped investor table
  merged_match_history.csv   - match_history re-keyed to investor_id
  review_queue.csv           - everything that needs a human before it merges
"""
import csv
import os

from extract import extract
from transform import transform

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OUT_INVESTORS = os.path.join(ROOT, "data/processed/merged_investors.csv")
OUT_MATCHES = os.path.join(ROOT, "data/processed/merged_match_history.csv")
OUT_REVIEW = os.path.join(ROOT, "data/processed/review_queue.csv")


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def load(investors, merged_matches, review_rows):
    write_csv(OUT_INVESTORS, investors, list(investors[0].keys()))
    write_csv(OUT_MATCHES, merged_matches, list(merged_matches[0].keys()))
    write_csv(OUT_REVIEW, review_rows, ["reason", "source", "name", "firm", "field", "value_a", "value_b"])


def print_summary(stats):
    print(f"Airtable source rows:        {stats['airtable_source_rows']}")
    print(f"MySQL source rows:           {stats['mysql_source_rows']}")
    print(f"  - excluded legacy pks:     {stats['excluded_legacy_pks']}")
    print(f"  - blank-identity airtable: {stats['blank_identity_airtable']}")
    print(f"Merged investors:            {stats['merged_investors']}")
    print(f"  - matched both sources:    {stats['matched_both_sources']}")
    print(f"  - airtable only:           {stats['airtable_only']}")
    print(f"  - mysql only:              {stats['mysql_only']}")
    print(f"Match history rows:          {stats['match_history_rows']}")
    print(f"  - unresolved investor_pk:  {stats['unresolved_match_pks']}")
    print(f"Review queue rows:           {stats['review_queue_rows']}")
    print()
    print(f"Wrote {OUT_INVESTORS}, {OUT_MATCHES}, {OUT_REVIEW}")


def main():
    air_raw, mysql_raw, match_raw = extract()
    investors, merged_matches, review_rows, stats = transform(air_raw, mysql_raw, match_raw)
    load(investors, merged_matches, review_rows)
    print_summary(stats)


if __name__ == "__main__":
    main()
