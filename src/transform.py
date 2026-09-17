"""
Transform stage: clean, dedupe and join the raw Airtable + MySQL + match_history
rows into one keyed investor dataset, flagging anything that can't be merged
automatically for manual review.

Strategy (see answers/case1/report.md for the full writeup):
  1. Normalize name/firm strings on both sides.
  2. Collapse exact-duplicate rows WITHIN each source first (same normalized
     name+firm). Keep the row with the more recent/non-null timestamp as
     primary; union non-conflicting fields in; flag genuine field conflicts
     (e.g. two different emails for the same person) for review instead of
     guessing.
  3. Cross-source join:
       Tier 1 - exact match on normalized (name, firm) -> auto-merge.
       Tier 2 - fuzzy match (name similarity + firm token overlap) on the
                remainder -> auto-merge only if there's a single high-confidence
                candidate with no competing match; otherwise -> review queue.
  4. Field-level merge: Airtable wins for structured fields (sectors, stages,
     geographies, investor_type); MySQL wins for contact info extracted from
     notes; notes_raw is always carried through unparsed.
  5. Rows with no recoverable identity (blank name, or the "Legacy Contact N /
     rokk3r" placeholder batch) are excluded from the investor table but kept
     as an UNKNOWN_LEGACY sentinel in match_history so outcome data isn't lost.
  6. match_history is re-keyed from investor_pk -> investor_id.
"""
import re
import uuid
import difflib
from collections import defaultdict

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\+?\d[\d\s\-\(\)]{7,}\d")
LEGACY_NAME_RE = re.compile(r"legacy contact", re.I)
HONORIFIC_RE = re.compile(r"^(dr|mr|ms|mrs|prof)\.?\s+", re.I)

NAME_THRESHOLD = 0.90
FIRM_THRESHOLD = 0.70


def norm(s):
    s = (s or "").strip().lower()
    s = HONORIFIC_RE.sub("", s)
    s = re.sub(r"\s+", " ", s)
    return s


def norm_firm(s):
    s = norm(s)
    s = re.sub(r"\b(inc\.?|llc|ltd\.?|holdings?|group|fund|capital|partners?|ventures?|advisors?)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_contact(notes):
    email = EMAIL_RE.search(notes or "")
    phone = PHONE_RE.search(notes or "")
    return (email.group(0) if email else ""), (phone.group(0) if phone else "")


def collapse_within_source(rows, name_field, firm_field, ts_field, id_field):
    """Group exact-duplicate rows within one source by normalized (name, firm).
    Returns (kept_rows, conflicts, alias_map) where kept_rows have
    non-conflicting fields unioned in, conflicts lists (key, field, values)
    for anything that disagreed and could not be auto-resolved, and
    alias_map maps EVERY constituent row's id_field value -> the surviving
    row's id_field value (so downstream joins like match_history, keyed on
    the original id, don't silently lose rows that were folded into a
    duplicate)."""
    groups = defaultdict(list)
    for r in rows:
        key = (norm(r[name_field]), norm_firm(r[firm_field]))
        groups[key].append(r)

    kept = []
    conflicts = []
    alias_map = {}
    for key, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            alias_map[group[0][id_field]] = group[0][id_field]
            continue
        # pick primary = row with a non-empty timestamp, else first
        primary = next((r for r in group if r.get(ts_field, "").strip()), group[0])
        merged = dict(primary)
        for other in group:
            alias_map[other[id_field]] = primary[id_field]
            if other is primary:
                continue
            for field, val in other.items():
                if field in (ts_field, id_field):
                    continue
                pval = merged.get(field, "").strip()
                oval = (val or "").strip()
                if not oval:
                    continue
                if not pval:
                    merged[field] = oval
                elif pval.lower() != oval.lower():
                    conflicts.append({
                        "key": key, "field": field,
                        "value_a": pval, "value_b": oval,
                        "row_a": primary, "row_b": other,
                    })
        kept.append(merged)
    return kept, conflicts, alias_map


def fuzzy_score(a_name, a_firm, b_name, b_firm):
    name_sim = difflib.SequenceMatcher(None, a_name, b_name).ratio()
    firm_sim = difflib.SequenceMatcher(None, a_firm, b_firm).ratio() if a_firm and b_firm else 0.0
    return name_sim, firm_sim


def transform(air_raw, mysql_raw, match_raw):
    """Clean, dedupe and join the raw rows. Returns (investors, merged_matches,
    review_rows, stats)."""

    # --- split out structurally unresolvable rows (legacy placeholders, blank identity) ---
    legacy_pks = set()
    mysql_clean = []
    for r in mysql_raw:
        if LEGACY_NAME_RE.search(r["name_raw"] or ""):
            legacy_pks.add(r["investor_pk"])
            continue
        mysql_clean.append(r)

    air_clean = []
    air_blank_identity = []
    for r in air_raw:
        if not r["full_name"].strip():
            air_blank_identity.append(r)
            continue
        air_clean.append(r)

    # --- Step 2: collapse within-source duplicates ---
    air_kept, air_conflicts, air_alias = collapse_within_source(
        air_clean, "full_name", "company_name", "submitted_at", "record_id"
    )
    mysql_kept, mysql_conflicts, mysql_alias = collapse_within_source(
        mysql_clean, "name_raw", "firm_raw", "created", "investor_pk"
    )

    review_rows = []
    for c in air_conflicts:
        review_rows.append({
            "reason": "within_source_field_conflict",
            "source": "airtable",
            "name": c["row_a"]["full_name"], "firm": c["row_a"]["company_name"],
            "field": c["field"], "value_a": c["value_a"], "value_b": c["value_b"],
        })
    for c in mysql_conflicts:
        review_rows.append({
            "reason": "within_source_field_conflict",
            "source": "mysql",
            "name": c["row_a"]["name_raw"], "firm": c["row_a"]["firm_raw"],
            "field": c["field"], "value_a": c["value_a"], "value_b": c["value_b"],
        })

    for r in air_blank_identity:
        review_rows.append({
            "reason": "blank_identity",
            "source": "airtable",
            "name": "", "firm": r["company_name"],
            "field": "full_name", "value_a": "", "value_b": "",
        })

    # --- Step 3: cross-source join ---
    air_by_key = {(norm(r["full_name"]), norm_firm(r["company_name"])): r for r in air_kept}
    mysql_by_key = {(norm(r["name_raw"]), norm_firm(r["firm_raw"])): r for r in mysql_kept}

    matched_air_keys = set()
    matched_mysql_keys = set()
    pairs = []  # (air_row_or_None, mysql_row_or_None)

    # Tier 1: exact key match
    for key in set(air_by_key) & set(mysql_by_key):
        pairs.append((air_by_key[key], mysql_by_key[key]))
        matched_air_keys.add(key)
        matched_mysql_keys.add(key)

    # Tier 2: fuzzy match remainder
    remaining_air = [k for k in air_by_key if k not in matched_air_keys]
    remaining_mysql = [k for k in mysql_by_key if k not in matched_mysql_keys]

    for a_key in remaining_air:
        a_name, a_firm = a_key
        candidates = []
        for m_key in remaining_mysql:
            if m_key in matched_mysql_keys:
                continue
            m_name, m_firm = m_key
            name_sim, firm_sim = fuzzy_score(a_name, a_firm, m_name, m_firm)
            if name_sim >= NAME_THRESHOLD and firm_sim >= FIRM_THRESHOLD:
                candidates.append((m_key, name_sim, firm_sim))
        if len(candidates) == 1:
            m_key = candidates[0][0]
            pairs.append((air_by_key[a_key], mysql_by_key[m_key]))
            matched_air_keys.add(a_key)
            matched_mysql_keys.add(m_key)
        elif len(candidates) > 1:
            review_rows.append({
                "reason": "ambiguous_fuzzy_match_multiple_candidates",
                "source": "airtable", "name": air_by_key[a_key]["full_name"],
                "firm": air_by_key[a_key]["company_name"],
                "field": "", "value_a": str(len(candidates)) + " candidates", "value_b": "",
            })
        # zero candidates -> falls through, becomes an airtable-only row below

    # anything still unmatched on the airtable side with no candidate at all
    # (0 candidates) is NOT a review case by itself - it just merges as an
    # airtable-only investor. Only ambiguity (2+ candidates) needs a human.

    for key, air_row in air_by_key.items():
        if key not in matched_air_keys:
            pairs.append((air_row, None))
    for key, mysql_row in mysql_by_key.items():
        if key not in matched_mysql_keys:
            pairs.append((None, mysql_row))

    # --- Step 4: build merged investor table ---
    investors = []
    mysql_pk_to_investor_id = {}

    for air_row, mysql_row in pairs:
        investor_id = str(uuid.uuid4())
        full_name = (air_row["full_name"] if air_row else mysql_row["name_raw"]).strip()
        firm_name = (air_row["company_name"] if air_row else mysql_row["firm_raw"]).strip()
        notes = mysql_row["notes"] if mysql_row else ""
        email, phone = extract_contact(notes)

        submitted = air_row["submitted_at"] if air_row else ""
        created = mysql_row["created"] if mysql_row else ""
        last_updated = max(filter(None, [submitted, created]), default="")

        if air_row and mysql_row:
            merge_method = "exact_key" if (norm(air_row["full_name"]), norm_firm(air_row["company_name"])) == \
                (norm(mysql_row["name_raw"]), norm_firm(mysql_row["firm_raw"])) else "fuzzy_match"
            confidence = "clean"
        else:
            merge_method = "single_source"
            confidence = "review" if False else "clean"  # single-source rows aren't conflicts, just incomplete

        investors.append({
            "investor_id": investor_id,
            "airtable_record_id": air_row["record_id"] if air_row else "",
            "mysql_investor_pk": mysql_row["investor_pk"] if mysql_row else "",
            "full_name": full_name,
            "firm_name": firm_name,
            "title": air_row["title"] if air_row else "",
            "investor_type": air_row["investor_type"] if air_row else "",
            "check_size_raw": air_row["check_size"] if air_row else "",
            "sectors": air_row["sectors"] if air_row else "",
            "geographies": air_row["geographies"] if air_row else "",
            "stages": air_row["stages"] if air_row else "",
            "takes_meetings": air_row["takes_meetings"] if air_row else "",
            "contact_email": email,
            "contact_phone": phone,
            "notes_raw": notes,
            "source_confidence": confidence,
            "merge_method": merge_method,
            "last_updated_at": last_updated,
        })
        if mysql_row:
            primary_pk = mysql_row["investor_pk"]
            for alias_pk, target_pk in mysql_alias.items():
                if target_pk == primary_pk:
                    mysql_pk_to_investor_id[alias_pk] = investor_id

    # legacy placeholder pks map to a single sentinel investor
    if legacy_pks:
        sentinel_id = "UNKNOWN_LEGACY"
        for pk in legacy_pks:
            mysql_pk_to_investor_id[pk] = sentinel_id
        investors.append({
            "investor_id": sentinel_id,
            "airtable_record_id": "",
            "mysql_investor_pk": "|".join(sorted(legacy_pks)),
            "full_name": "UNKNOWN (legacy placeholder batch)",
            "firm_name": "",
            "title": "", "investor_type": "", "check_size_raw": "",
            "sectors": "", "geographies": "", "stages": "", "takes_meetings": "",
            "contact_email": "", "contact_phone": "",
            "notes_raw": "14 unresolvable 'Legacy Contact N' / rokk3r rows from import_2023; no recoverable identity.",
            "source_confidence": "unresolved",
            "merge_method": "excluded_sentinel",
            "last_updated_at": "",
        })

    # --- Step 5: re-key match_history ---
    merged_matches = []
    unresolved_match_pks = set()
    for r in match_raw:
        pk = r["investor_pk"]
        investor_id = mysql_pk_to_investor_id.get(pk)
        if investor_id is None:
            investor_id = "UNRESOLVED"
            unresolved_match_pks.add(pk)
        merged_matches.append({
            "match_id": r["match_id"],
            "investor_id": investor_id,
            "company": r["company"],
            "company_sector": r["company_sector"],
            "company_stage": r["company_stage"],
            "matched_on": r["matched_on"],
            "outcome": r["outcome"],
            "investor_feedback": r["investor_feedback"],
        })

    for pk in unresolved_match_pks:
        review_rows.append({
            "reason": "match_history_investor_pk_not_found_in_mysql",
            "source": "match_history", "name": "", "firm": "",
            "field": "investor_pk", "value_a": pk, "value_b": "",
        })

    stats = {
        "airtable_source_rows": len(air_raw),
        "mysql_source_rows": len(mysql_raw),
        "excluded_legacy_pks": len(legacy_pks),
        "blank_identity_airtable": len(air_blank_identity),
        "merged_investors": len(investors),
        "matched_both_sources": sum(1 for a, m in pairs if a and m),
        "airtable_only": sum(1 for a, m in pairs if a and not m),
        "mysql_only": sum(1 for a, m in pairs if m and not a),
        "match_history_rows": len(merged_matches),
        "unresolved_match_pks": len(unresolved_match_pks),
        "review_queue_rows": len(review_rows),
    }

    return investors, merged_matches, review_rows, stats
