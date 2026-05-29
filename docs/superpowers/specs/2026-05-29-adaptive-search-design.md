# surf — Adaptive Search System Design
**Date:** 2026-05-29
**Status:** Approved

---

## Problem

surf currently uses one search strategy for every query: DDG snippets → Claude → answer. This is correct for stable factual queries but wrong for time-sensitive, research-depth, or contested questions. "Who will win the UCL" on the day before the final returns generic evergreen prediction content instead of today's PSG vs Arsenal analysis, pundit odds, and Opta supercomputer — because surf sent the raw query to DDG with no temporal awareness, got SEO farm results, and synthesized from garbage.

The goal: surf should work like a skilled researcher who instinctively knows when to answer from memory, when to quickly check, and when to spend 15 minutes actually reading. The depth of research is determined by what the question needs, not by a user flag. The work is shown through transparent progress steps — no announcements, just actions — like Claude Code.

---

## Design

### 1. Five Search Tiers

Every query is classified into a tier using pure heuristics — no API call. Classification uses the original user query before enrichment.

| Tier | Trigger signals | Time | Depth |
|---|---|---|---|
| **Instant** | math, convert, translate, define — no current-events signal | <1s | LLM only, no DDG |
| **Snippet** | Stable facts, named entities, history | 2–4s | DDG snippets → answer (current behavior) |
| **Current** | `will`, `predict`, `latest`, `winner`, `odds`, `upcoming`, `today`, `this week`, `expect`, `favorite`, `soon` | 8–12s | Snippets + read top 2–3 sources |
| **Research** | `how does`, `why does`, `explain`, `what causes`, `how to`, `what is the difference` | 10–15s | Multi-angle snippets + read 2–3 sources |
| **Contested** | `best`, `vs`, `versus`, `compare`, `should i`, `worth it`, `better than`, `recommend` | 12–18s | Dual-angle snippets + read 2–3 sources |

Tier classification resolves ~80% of queries immediately. The remaining 20% — queries that look like Snippet but aren't — are caught by the adaptive confidence gate.

---

### 2. Adaptive Confidence Gate

After DDG returns snippets but before any answer is generated, a fast quality check runs on the results. No extra API call — pure heuristics on snippet content.

**Three checks:**

**Coverage** — do the snippets contain an answer to this specific query?
Scan snippets for key entities from the query. "Who will win UCL" with snippets mentioning "Manchester City" and "Real Madrid" but never "PSG" or "Arsenal" = low coverage. Measure as proportion of query entities found in snippets.

**Freshness** — for temporal queries, are snippets recent?
Scan snippet text for year signals. If the query implies the current year and snippets reference a prior season, freshness fails. Uses the same `TEMPORAL_SIGNALS` set from `_enrich_ddg_query`.

**Authority** — does the source domain match the query domain?
Sports query hitting `footballbh.net` and `newsanyway.com` = low authority. Sports query hitting `espn.com` or `bbc.com/sport` = high authority. Evaluated against the source hierarchy (Section 4).

**Gate logic:**
- All three pass → stay at Snippet tier, answer fast
- Freshness fails → escalate to Current tier
- Coverage fails → escalate to Research tier
- Authority fails → escalate to the tier appropriate for query domain
- Instant tier bypasses the gate entirely — no DDG

---

### 3. Deep Research Pipeline + Progress UX

When the gate escalates, the pipeline switches from "snippets → answer" to "read sources → synthesize." Progress is shown through the existing `print_status()` status line — each step overwrites the previous. No strategy announcements. The steps are the communication.

**Current tier:**
```
↳ searching: "UEFA Champions League 2026 Final Predictions"...
↳ reading espn.com...
↳ reading theathletic.com...
↳ reading bbc.com...
↳ synthesizing...
```
Fetches full article text from top 2–3 sources. Passes real content — not snippets — to Claude for synthesis.

**Research tier:**
```
↳ searching: "how mRNA vaccines work"...
↳ searching: "mRNA vaccine immune response mechanism"...
↳ reading mayoclinic.org...
↳ reading nature.com...
↳ synthesizing 2 sources...
```
Two DDG searches from different angles (extending the existing `_needs_multi_search` pattern). Reads top 2 sources. Synthesis prompt tuned for explanation.

**Contested tier:**
```
↳ searching: "React vs Vue 2026 developer experience"...
↳ searching: "Vue advantages over React use cases"...
↳ reading dev.to...
↳ reading stackoverflow.com...
↳ synthesizing perspectives...
```
Two searches from opposing angles. Synthesis explicitly surfaces disagreement rather than picking a winner.

**Mechanics:**
- Each `↳ reading domain.com...` is a real article fetch using the existing `fetch_page` + `extract_text` pipeline — the same reader mode, called silently mid-search
- Sources that time out (>8s), 403, or return <200 words are skipped; next source tried
- If all reads fail, falls back gracefully to snippet answer — no error shown
- Total source reads capped at 3
- Elapsed time line still appears: `↳ 8.3s · claude $0.12/$1.00`

---

### 4. Source Hierarchy

For deep tiers, surf checks whether DDG results include authoritative sources for the query domain. If not, a targeted search is added.

```python
SOURCE_HIERARCHY = {
    "sports":   ["espn.com", "bbc.com/sport", "theathletic.com", "skysports.com", "uefa.com"],
    "finance":  ["bloomberg.com", "ft.com", "wsj.com", "reuters.com", "cnbc.com"],
    "tech":     ["arstechnica.com", "wired.com", "techcrunch.com", "theverge.com"],
    "medical":  ["mayoclinic.org", "pubmed.ncbi.nlm.nih.gov", "webmd.com", "nejm.org"],
    "science":  ["nature.com", "sciencedaily.com", "nasa.gov", "scientificamerican.com"],
    "news":     ["reuters.com", "apnews.com", "bbc.com", "nytimes.com"],
    "legal":    ["law.cornell.edu", "oyez.org", "courtlistener.com"],
}
```

Domain detection uses the existing `_identify_entity_type()` function. If zero results match the hierarchy for the detected domain, surf adds a targeted DDG search (`site:espn.com OR site:bbc.com/sport {enriched_query}`) before the reading step.

---

### 5. Synthesis Prompts Per Tier

Each tier uses a different synthesis instruction passed as the system prompt, replacing the current one-size-fits-all `SEARCH_SYSTEM`:

**Snippet** — current `SEARCH_SYSTEM`, unchanged.

**Current** — *Lead with specific, concrete facts from today's sources. Names, dates, scores, odds. Do not generalize. If an event is happening soon, say who is involved and when. Correct any outdated information from the snippets.*

**Research** — *Synthesize across sources. Identify where they agree and where they differ. Build from fundamentals to implications. Do not summarize each source separately — extract the shared understanding and highlight meaningful disagreements.*

**Contested** — *Present both sides honestly before reaching a conclusion. Name the tradeoffs explicitly. The answer is not which side is right — it is which side is right for what use case.*

The `▸ TL;DR` format is preserved across all tiers. What changes is what goes in the body and what the model prioritizes.

---

## Architecture — What Changes in surf.py

**New components:**
- `SEARCH_TIERS` — dict of tier name → trigger signals
- `SOURCE_HIERARCHY` — dict of domain → authoritative sources
- `SEARCH_SYSTEM_CURRENT`, `SEARCH_SYSTEM_RESEARCH`, `SEARCH_SYSTEM_CONTESTED` — tier-specific prompts
- `_classify_tier(query)` → `str` — heuristic tier classification
- `_confidence_gate(query, results, tier)` → `str` — check snippets, returns the final tier to use (may be same as input or escalated one level)
- `_deep_research(query, tier, results)` → `(str, list[dict])` — fetch sources, show progress, return (content, sources)

**Modified:**
- `search_flow()` — inserts tier classification and confidence gate before the current synthesis step; routes to `_deep_research` for non-Snippet tiers
- `_needs_multi_search()` — absorbed into Research/Contested tier logic
- `SEARCH_SYSTEM` — becomes the Snippet-tier prompt; other tiers get their own

**Unchanged:**
- `classify_intent()` and its routing (instant/transactional/navigation)
- `_enrich_ddg_query()` — still runs before DDG regardless of tier
- Session context injection
- Follow-up question system
- Reader mode, article fetching

---

## What Good Looks Like

**Before:** `surf who will win the UEFA champions league` → generic prediction article from 2023, no mention of PSG or Arsenal, hedged non-answer.

**After:** `surf who will win the UEFA champions league`
```
↳ searching: "UEFA Champions League 2026 Final Predictions"...
↳ reading espn.com...
↳ reading theathletic.com...
↳ synthesizing...

▸ TL;DR  PSG are slight favorites over Arsenal in tomorrow's final at
  Puskás Aréna, with most analysts giving them a 54–46 edge on current form.

ESPN's analysis puts PSG ahead based on Mbappé's recent form — 6 goals in
the last 4 Champions League knockout games — and their defensive record
(3 goals conceded in 8 knockout games). The Athletic's preview notes
Arsenal's counter-attacking threat through Saka and Martinelli as the
main equalizer...
```

---

## Out of Scope

- User-facing tier selection ("--deep" flag) — adaptive behavior is the feature
- Caching of deep research results
- Parallel source fetching (sequential is simpler, good enough)
- More than 3 source reads per query
