# matching_service.py — architecture

Request flow through `match()`, annotated with the `ai_review.md` finding numbers that
sit at each stage. Renders natively on GitHub.

```mermaid
flowchart TD
    Req["match(company_brief, company, top_n)"]

    Req --> Load["load_investors()"]
    Load -->|"MySQL: investors LEFT JOIN matches\nGROUP BY investor_id\n#25 fine · #8 GROUP_CONCAT order"| Rows["investor rows\n+ prior_matches / prior_outcomes"]

    Rows --> Build["build_index()"]
    Build -->|"#9 one embed() call per investor,\nevery request, no cache"| EmbInv["embed() -> OpenAI\ntext-embedding-3-small"]
    EmbInv --> Idx["index: (investor, vector) list\n#6 prior match/outcome text\nembedded -> leaves the building\n#16 NULL -> literal 'None'"]

    Req --> EmbQ["embed(company_brief)"]
    EmbQ --> QVec["query vector"]

    Idx --> Sim["cosine_similarity()\n#10 plain np.dot, no norm\n-only correct bc OpenAI vectors are unit-length"]
    QVec --> Sim
    Sim --> Pool["sort desc, take CANDIDATE_POOL=50\n#18 40/50 explanations thrown away"]

    Pool --> Rank["rank_with_llm()"]
    Rank -->|"prompt asks for JSON array,\nresponse_format=json_object\n#1 CRITICAL - shape mismatch crashes sort()\n#7 investor description + brief\ninterpolated raw -> prompt injection\n#12 prompt presupposes every pick is good"| LLM["OpenAI chat.completions\ngpt-4o-mini"]
    LLM --> Ranked["ranked list, sorted by rank\n#17 no dedup/completeness check"]

    Ranked --> ById["by_id[investor_id] lookup\n#5 hallucinated/typed ID -> KeyError"]
    ById --> Filter["passes_hard_filters(inv, company)\n#2 CRITICAL - runs after LLM ranking,\nnot before retrieval\n#3 stage: substring match, not set membership\n#4 cheque bounds: no NULL guard -> TypeError"]
    Filter --> Out["results[:top_n]\n#19 company_brief vs company dict\ncan disagree, undetected"]
```

## What the diagram doesn't show

No error handling / timeout / retry on any network call (#13), the DB connection leak on
exception (#14), the client constructed at import time from a required env var (#15), or
the fact none of this is logged (#27) — these aren't stages in the flow, they're the
absence of guardrails around every stage above.

## Reading order for triage

`#1` and `#2` sit directly on the path every request takes — `#1` crashes it outright,
`#2` means the 50-candidate pool is already wrong before the LLM sees it. `#6` and `#7`
are the two where the failure mode isn't a crash but the system quietly doing the wrong
thing at scale (leaking match data outward, taking instructions from investor-controlled
text). That ordering is the basis for the Case 2 Q1 answer (which three ship first).
