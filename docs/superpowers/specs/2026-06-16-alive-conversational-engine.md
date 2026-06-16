# Surf: Alive Conversational Engine
**Date:** 2026-06-16  
**Status:** Approved for implementation

## Goal

Transform surf from a reactive search tool into a conversational answer engine that feels alive — persistent, warm, witty, and responsive. Users should be able to push back, redirect, expand scope, or express frustration and get a thoughtful response plus immediate action, not a wall.

Inspiration: Perplexity as a floor, G.K. Chesterton as the voice model — the "Prince of Paradox," warm but not soft, intellectually confident, honest about what it doesn't know, and willing to push back when warranted.

---

## What We're NOT Building

- A full agent loop replacing search_flow (that's a future phase)
- Per-domain specialized scrapers for sports/news (the core pipeline fix is the right lever)
- Any change to the command set (q, n, 1–5, s1–s5, o1–o5) — those stay identical

---

## Architecture Overview

Four interconnected changes, all contained within surf.py:

1. **`_SearchMeta` dataclass** — structured result of every search, survives into the interactive loop
2. **`_classify_and_dispatch()`** — replaces the ad-hoc input handling in `_handle_results_input`
3. **Six-way input classifier** — pattern-match first, LLM only for ambiguous cases
4. **Voice overhaul** — SEARCH_SYSTEM and conversation prompts updated to Chesterton voice, tier-gated

---

## Section 1: `_SearchMeta` Dataclass

Every call to `search_flow` and `_handle_followup` produces a `_SearchMeta` object passed back to the interactive loop. This is the foundation that makes redirect, dead-end, and retry logic possible.

```python
@dataclass
class _SearchMeta:
    original_query: str
    queries_tried: list[str]       # all DDG queries actually fired
    result_count: int              # number of results returned
    confidence_tier: str           # snippet / research / current / contested
    coverage_note: str | None      # e.g. "found Group C only, not A/B/D–G"
```

`coverage_note` is populated when:
- The query mentions multiple items (groups, teams, years) and results only covered a subset
- `_confidence_gate` escalated tier but deep reads returned empty
- Result count < 3 after filtering

`_handle_results_input` receives `meta: _SearchMeta` and passes it to `_classify_and_dispatch`.

---

## Section 2: Six-Way Input Classifier

Replaces `_is_casual_input` and the implicit dispatch logic in `_handle_results_input`.

### Types

| Type | Examples | Action |
|------|----------|--------|
| `command` | `o1`, `s2`, `q`, `?`, `n` | Existing command handling (unchanged) |
| `followup` | "why did Brazil draw?" | `_handle_followup` (unchanged) |
| `scope_expansion` | "what about groups A–G?", "show me the others" | Multi-query fanout, streamed |
| `redirect` | "that's your job", "you missed the others", "try harder" | Warm acknowledgment + retry with broader query |
| `correction` | "no, I meant 2022", "not Thailand — Taiwan" | Reset context, fresh search with corrected query |
| `casual` | "wow", "thanks", "interesting", "cool" | Brief warm response, no search |

### Classification logic

**Step 1 — Pattern-match (no LLM, instant):**
- Starts with a known command token → `command`
- 1–3 words, no `?`, no content words → `casual`
- Starts with "no," / "not X" / "I meant" / "actually" → `correction`
- Contains "your job" / "try harder" / "you missed" / "what about the" → `redirect`
- Contains list-like expansion ("A through G", "the others", "all of them", "the rest") → `scope_expansion`

**Step 2 — LLM classifier (only when pattern-match is ambiguous):**
- Fires CLASSIFIER_MODEL (llama-3.1-8b-instant) with a single-shot prompt
- Returns one of the six type tokens
- Fallback on timeout or error: `followup` (safe default)
- Tiebreaker: ambiguous inputs default to `followup`

**Target:** Pattern-match resolves ≥ 80% of inputs. LLM classifier fires rarely.

### `_classify_and_dispatch(choice, results, meta, context) -> (new_results, new_context, new_meta, should_break)`

Extracted from `_handle_results_input`. Each branch is independently testable. Returns a tuple so the caller loop stays simple.

---

## Section 3: Conversational Response Layer

Each non-command type gets a distinct response pattern. Two sentences max before surf is already working. No apology, no over-explanation.

### `redirect`
Acknowledge the pushback, name what was missed (using `meta.coverage_note`), act immediately:

```
Fair point — I stopped at Group C when there are twelve.
Pulling the rest now.
↳ searching: "2026 World Cup Group A standings"...
```

If surf doesn't have a coverage note (can't identify what was missed), it tries a broader rephrasing of the original query:
```
You're right — let me come at this from a wider angle.
↳ searching: "2026 FIFA World Cup all group standings results"...
```

### `scope_expansion`
Name the items, dispatch immediately, stream as they land:

```
On it — checking all six now.

↳ Group A  [streams as it resolves]
↳ Group B  [streams as it resolves]
↳ Group D  [streams as it resolves]
```

Summary table once all threads complete.

### `correction`
Acknowledge and re-run with corrected query, discarding prior context:

```
Got it — switching to 2022.
↳ searching: "2022 FIFA World Cup group standings"...
```

### `casual`
Brief warm response. No search. Uses session context if available:

```
Glad that helped — anything else you want to dig into?
```

### `dead_end` (after 3 failed retries)
Explain what was tried, offer two explicit options:

```
Three angles, not much to show for it. Best I found was [X].
Want me to read that source directly, or try a completely different search?
  r — read [domain]
  t — try a new search
```

---

## Section 4: Multi-Query Fanout with Streaming

Triggered by `scope_expansion`. Implementation:

1. **Extract items** from the query — LLM call extracts the list (e.g., `["Group A", "Group B", "Group D", "Group E", "Group F", "Group G"]`) from "what about groups A, B, D, E, F, G?"

2. **Dispatch threads** — one thread per item, each fires `ddg_search` + lightweight synthesis (snippet tier, 150 tokens max per item)

3. **Stream to terminal** — each thread prints its mini-section as it completes, in completion order (not dispatch order), using existing `print_header` / `print_section_break` conventions

4. **Summary** — once all threads resolve, print a 2-3 sentence synthesis across all items

**Threading:** `concurrent.futures.ThreadPoolExecutor(max_workers=6)` — matches the existing pattern used in `_fetch_sub_pages`. Cap at 6 concurrent DDG searches to avoid rate limits.

**Coordination with existing `alt_query`:** The `alt_query` path in `search_flow` fires for research/contested tier as a second pass within a single query. Fanout is a separate path triggered from the interactive loop for multi-item scope expansions. They are orthogonal — no coordination needed.

---

## Section 5: Persistence & Retry Logic

Automatic retry sequence, fully narrated:

Retry logic lives in a new `_search_with_retry(query, entity_type) -> (results, queries_tried)` function that wraps `ddg_search`. Called from `search_flow` in place of the bare `ddg_search` call. `search_flow`'s routing, synthesis, and tier logic are otherwise unchanged.

**Attempt 1:** Standard DDG search
- If result_count ≥ 3 and tier is not escalated: proceed normally

**Attempt 2:** (automatic, if attempt 1 is thin)
Print: *"That first pass was thin — trying a different angle."*
Rephrase query: LLM generates alternative formulation (e.g., "World Cup 2026 Group C results" → "FIFA 2026 group stage standings Group C")

**Attempt 3:** (automatic, if attempt 2 still thin)
Print: *"Still not much — adding a source hint."*
Append authoritative domain to query (from `_SOURCE_INTELLIGENCE` if entity type detected, else `site:wikipedia.org`)

**After 3 thin attempts:** Dead-end response (see Section 3).

**What counts as "thin":** result_count < 3 OR (result_count ≥ 3 AND all snippets are < 50 words AND no deep read succeeded).

**Max depth:** 3 attempts per interactive input. Never loops silently.

---

## Section 6: Voice Overhaul

### SEARCH_SYSTEM prompt changes

Replace:
```
You are a precise research assistant answering questions using search result snippets.
```

With:
```
You are a sharp, well-read research assistant with genuine opinions. You find topics 
interesting and it shows. You lead with the most surprising or counterintuitive finding, 
not the most obvious one. You state your read clearly — not "sources suggest" but what 
you actually think the evidence shows. You are honest about what you don't know.
```

**Voice rules additions** (appended to existing rules):
- Lead with the finding that would make someone say "huh, interesting" — not the one they could have guessed
- State a clear interpretation: "Brazil looked ordinary" not "Brazil's performance was mixed"
- When data is partial, say so with wit: "I've got Group C nailed down — the other eleven are still keeping their secrets"
- Use contractions. Write like a person.
- Never pad. If you have one good point, make it and stop.

**Tier-gating — critical:** The opinionated/Chesterton voice applies to `research`, `current`, and `contested` tiers only. For `snippet` tier (short factual queries: a name, a date, a score), respond plainly and directly. The gate is `_classify_tier()` — if it returns `"snippet"`, use the existing voice rules unchanged.

**Pushback gate:** Only push back on a vague query when BOTH conditions are true:
1. The query contains multiple possible referents with no disambiguation
2. The search returned thin results (result_count < 3)

Do not push back preemptively on clear questions.

### Conversational response voice
All redirect/casual/dead-end responses use the same Chesterton register:
- Warm but not sycophantic
- One clear sentence of acknowledgment, then action
- Short. The professor nods and gets to work — doesn't give a speech.

---

## What Changes, What Doesn't

**Changes:**
- `_handle_results_input` (line 2908) — extracts dispatch logic into `_classify_and_dispatch`
- `_is_casual_input` (line 3450) — replaced by pattern-match layer in new classifier
- `search_flow` (line 2256) — returns `_SearchMeta` alongside existing return values
- `_handle_followup` (line 3011) — returns `_SearchMeta` alongside existing return values
- `SEARCH_SYSTEM` (line 527) — voice overhaul
- `SEARCH_SYSTEM_CURRENT` / `SEARCH_SYSTEM_RESEARCH` / `SEARCH_SYSTEM_CONTESTED` — voice overhaul

**Unchanged:**
- All command handling (q, n, ?, 1–5, s1–s5, o1–o5)
- `_handle_followup` internal logic
- `search_flow` routing, synthesis, and tier logic (DDG call replaced by `_search_with_retry`)
- All existing tests must continue to pass
- Weather handler, routing infrastructure

---

## Testing

New test classes:
- `TestSearchMeta` — coverage_note population, dataclass behavior
- `TestInputClassifier` — all six types via pattern-match; LLM path mocked
- `TestClassifyAndDispatch` — each branch returns correct tuple
- `TestSearchWithRetry` — 3-attempt sequence with mocked thin results, narration printed
- `TestFanout` — scope_expansion extracts items, fires correct number of threads

All existing 151 tests must pass unchanged.

---

## Success Criteria

1. "That's your job" → warm acknowledgment + immediate broader search (no wall)
2. "What about groups A–G?" → six parallel searches, streamed as they land
3. "No, I meant 2022" → corrected search, prior context discarded
4. World Cup question with thin results → 3 attempts narrated, dead-end with options if all fail
5. "Who won the 1966 World Cup?" → plain direct answer, no wit, no paradox
6. "Which team has impressed most?" → opinionated synthesis leading with the surprising finding
7. All 151 existing tests pass
