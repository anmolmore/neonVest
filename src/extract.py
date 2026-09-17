"""
Extract stage: read the raw source CSVs as-is, no cleaning or joining.
"""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AIRTABLE_PATH = os.path.join(ROOT, "data/raw/airtable_investors.csv")
MYSQL_PATH = os.path.join(ROOT, "data/raw/mysql_investor_notes.csv")
MATCH_PATH = os.path.join(ROOT, "data/raw/match_history.csv")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def extract():
    """Read airtable_investors, mysql_investor_notes and match_history from data/raw/."""
    return read_csv(AIRTABLE_PATH), read_csv(MYSQL_PATH), read_csv(MATCH_PATH)
