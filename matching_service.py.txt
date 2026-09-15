"""
neonVest — investor matching service (draft)

Generated with an AI coding assistant against the architecture note.
Reviewed by nobody yet. This is the file the candidate is asked to review.

DO NOT SHIP.
"""

import os
import json
import numpy as np
import mysql.connector
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
CANDIDATE_POOL = 50


# ----------------------------------------------------------------- data

def load_investors():
    """Pull every investor with their attributes and match history."""
    cnx = mysql.connector.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        database="neonvest",
    )
    cur = cnx.cursor(dictionary=True)
    cur.execute(
        """
        SELECT i.investor_id,
               i.full_name,
               i.firm,
               i.description,
               i.stage_focus,
               i.min_cheque,
               i.max_cheque,
               i.geography,
               GROUP_CONCAT(m.company_name SEPARATOR ', ') AS prior_matches,
               GROUP_CONCAT(m.outcome      SEPARATOR ', ') AS prior_outcomes
        FROM investors i
        LEFT JOIN matches m ON m.investor_id = i.investor_id
        GROUP BY i.investor_id
        """
    )
    rows = cur.fetchall()
    cnx.close()
    return rows


def investor_text(inv):
    """The text we embed for each investor."""
    parts = [
        inv["firm"] or "",
        inv["description"] or "",
        f"Focus: {inv['stage_focus']}",
        f"Geography: {inv['geography']}",
    ]
    if inv.get("prior_matches"):
        parts.append(f"Previously matched with: {inv['prior_matches']}")
        parts.append(f"Outcomes: {inv['prior_outcomes']}")
    return "\n".join(parts)


# ----------------------------------------------------------------- embeddings

def embed(text):
    resp = client.embeddings.create(model=EMBED_MODEL, input=text)
    return np.array(resp.data[0].embedding)


def build_index(investors):
    """Embed every investor description."""
    index = []
    for inv in investors:
        vec = embed(investor_text(inv))
        index.append((inv, vec))
    return index


def cosine_similarity(a, b):
    return float(np.dot(a, b))


# ----------------------------------------------------------------- matching

def rank_with_llm(company_brief, candidates):
    """Ask the model to rank the shortlist and explain each pick."""
    payload = [
        {
            "investor_id": inv["investor_id"],
            "name": inv["full_name"],
            "firm": inv["firm"],
            "description": inv["description"],
        }
        for inv, _ in candidates
    ]

    prompt = f"""You are an expert venture analyst.

Company:
{company_brief}

Investors:
{json.dumps(payload, indent=2)}

Rank these investors by how well they fit the company. For each, give a
one-paragraph explanation of why they are a good match.

Return JSON: [{{"investor_id": int, "rank": int, "explanation": str}}]
"""
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def passes_hard_filters(inv, company):
    if company["stage"] not in (inv["stage_focus"] or ""):
        return False
    if company["cheque_target"] < inv["min_cheque"]:
        return False
    if company["cheque_target"] > inv["max_cheque"]:
        return False
    return True


def match(company_brief, company, top_n=10):
    """Return the top_n investors for a company, with explanations."""
    investors = load_investors()
    index = build_index(investors)

    q = embed(company_brief)
    scored = [(inv, cosine_similarity(q, vec)) for inv, vec in index]
    scored.sort(key=lambda x: x[1], reverse=True)
    candidates = scored[:CANDIDATE_POOL]

    ranked = rank_with_llm(company_brief, candidates)

    by_id = {inv["investor_id"]: inv for inv, _ in candidates}
    results = []
    for r in sorted(ranked, key=lambda x: x["rank"]):
        inv = by_id[r["investor_id"]]
        if not passes_hard_filters(inv, company):
            continue
        results.append(
            {
                "investor_id": inv["investor_id"],
                "name": inv["full_name"],
                "firm": inv["firm"],
                "explanation": r["explanation"],
            }
        )
    return results[:top_n]


# ----------------------------------------------------------------- tests

def test_match_returns_ranked(monkeypatch):
    monkeypatch.setattr("__main__.embed", lambda t: np.ones(1536))
    monkeypatch.setattr(
        "__main__.rank_with_llm",
        lambda b, c: [
            {"investor_id": 1, "rank": 1, "explanation": "Strong fit."},
            {"investor_id": 2, "rank": 2, "explanation": "Adjacent fit."},
        ],
    )
    out = match("a fintech company", {"stage": "Seed", "cheque_target": 500000})
    assert out[0]["investor_id"] == 1
    assert out[1]["investor_id"] == 2
    assert len(out) == 2
