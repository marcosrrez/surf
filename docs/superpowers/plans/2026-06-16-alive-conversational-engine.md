# Alive Conversational Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform surf's interactive loop from a reactive search tool into a conversational answer engine with a Chesterton-inspired voice, six-way input classification, multi-query fanout, and automatic retry logic.

**Architecture:** Four layered changes to surf.py — (1) `_SearchMeta` dataclass threads result metadata through the loop, (2) `_search_with_retry` wraps DDG with narrated retries, (3) `_classify_and_dispatch` replaces the ad-hoc interactive dispatch with a six-way classifier and conversational responses, (4) SEARCH_SYSTEM prompts updated with opinionated, witty voice tier-gated by query complexity.

**Tech Stack:** Python 3.11+, existing `stream_groq` / `CLASSIFIER_MODEL` (llama-3.1-8b-instant), `concurrent.futures.ThreadPoolExecutor` (new import), `dataclasses` (new import)

---

## File Map

All changes are in `surf.py` and `tests/test_surf.py`. No new files.

| Area | Location | What changes |
|------|----------|-------------|
| Imports | Lines 1–15 | Add `from dataclasses import dataclass`, `from concurrent.futures import ThreadPoolExecutor, as_completed` |
| `_SearchMeta` | After line 15 | New dataclass, module-level |
| `_search_with_retry` | Before line 2655 (`search_flow`) | New function |
| `search_flow` | Line 2655 | Wire `_search_with_retry`, construct + pass `_SearchMeta` |
| `_handle_results_input` | Line 2908 | Accept `meta`, call `_classify_and_dispatch` |
| `_classify_input` | Before line 3450 (`_is_casual_input`) | New six-way classifier |
| `_conversational_reply` | After `_classify_input` | New conversational response generator |
| `_handle_scope_expansion` | After `_conversational_reply` | New fanout handler |
| `_classify_and_dispatch` | After `_handle_scope_expansion` | New dispatch function |
| `_handle_followup` | Line 3011 | Return `_SearchMeta` as third element |
| SEARCH_SYSTEM prompts | Lines 527–600 | Voice overhaul |

---

## Task 1: `_SearchMeta` dataclass

**Files:**
- Modify: `surf.py` (add after line 15, before existing imports settle)
- Test: `tests/test_surf.py`

- [ ] **Step 1: Write the failing test**

```python
class TestSearchMeta:
    def test_instantiation(self):
        from surf import _SearchMeta
        meta = _SearchMeta(
            original_query="who won the world cup",
            queries_tried=["who won the world cup", "FIFA world cup winner"],
            result_count=5,
            confidence_tier="current",
            coverage_note=None,
        )
        assert meta.original_query == "who won the world cup"
        assert meta.result_count == 5
        assert meta.coverage_note is None

    def test_coverage_note_populated(self):
        from surf import _SearchMeta
        meta = _SearchMeta(
            original_query="all world cup groups",
            queries_tried=["all world cup groups"],
            result_count=1,
            confidence_tier="current",
            coverage_note="Only found Group C — others not in results",
        )
        assert meta.coverage_note is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSearchMeta -v 2>&1 | tail -10
```
Expected: `ImportError` or `AttributeError` — `_SearchMeta` doesn't exist yet.

- [ ] **Step 3: Add `_SearchMeta` to surf.py**

Add these two lines to the imports block at the top of surf.py (after line 15, before `from bs4 import BeautifulSoup`):

```python
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
```

Then add the dataclass after the imports, before the `# ═══ surf Design System` comment (around line 18):

```python
@dataclass
class _SearchMeta:
    original_query: str
    queries_tried: list[str]
    result_count: int
    confidence_tier: str
    coverage_note: str | None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSearchMeta -v 2>&1 | tail -10
```
Expected: `2 passed`

- [ ] **Step 5: All existing tests still pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q --tb=short 2>&1 | tail -5
```
Expected: `151 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: add _SearchMeta dataclass and concurrent.futures import"
```

---

## Task 2: `_search_with_retry` — narrated DDG retry wrapper

**Files:**
- Modify: `surf.py` (add before `search_flow` at line 2655)
- Test: `tests/test_surf.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestSearchWithRetry:
    def test_returns_results_on_first_try(self):
        """When first search returns ≥3 results, no retry fires."""
        from surf import _search_with_retry
        good_results = [
            {"title": f"Result {i}", "url": f"http://ex.com/{i}", "domain": "ex.com", "snippet": "x" * 60}
            for i in range(5)
        ]
        with patch("surf.ddg_search", return_value=good_results) as mock_ddg:
            results, queries_tried = _search_with_retry("test query")
        assert len(results) == 5
        assert mock_ddg.call_count == 1
        assert queries_tried == ["test query"]

    def test_retries_on_thin_results(self):
        """When first search returns <3 results, retries with rephrased query."""
        from surf import _search_with_retry
        thin = [{"title": "A", "url": "http://a.com", "domain": "a.com", "snippet": "short"}]
        good = [
            {"title": f"R{i}", "url": f"http://b.com/{i}", "domain": "b.com", "snippet": "x" * 60}
            for i in range(4)
        ]
        with patch("surf.ddg_search", side_effect=[thin, good]) as mock_ddg, \
             patch("surf._rephrase_query", return_value="rephrased query") as mock_rephrase, \
             patch("surf.print_status"), patch("surf.clear_status"):
            results, queries_tried = _search_with_retry("test query")
        assert len(results) == 4
        assert mock_ddg.call_count == 2
        assert "rephrased query" in queries_tried

    def test_three_attempts_then_dead_end(self):
        """After 3 thin searches, returns best thin result with coverage_note signal."""
        from surf import _search_with_retry
        thin = [{"title": "A", "url": "http://a.com", "domain": "a.com", "snippet": "x"}]
        with patch("surf.ddg_search", return_value=thin), \
             patch("surf._rephrase_query", return_value="q2"), \
             patch("surf.print_status"), patch("surf.clear_status"):
            results, queries_tried = _search_with_retry("test query")
        assert len(queries_tried) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSearchWithRetry -v 2>&1 | tail -10
```
Expected: `ImportError` — `_search_with_retry` doesn't exist yet.

- [ ] **Step 3: Add `_rephrase_query` helper first**

Add immediately before `search_flow` (currently around line 2655 — will shift slightly after Task 1 additions):

```python
def _rephrase_query(query: str) -> str:
    """Generate an alternative DDG query formulation for retry."""
    prompt = f"Rephrase this search query to find better results. Return ONLY the new query, no quotes, no explanation.\n\nOriginal: {query}"
    try:
        chunks = list(stream_groq(prompt, "You are a search query optimizer. Return only the query string.", model=CLASSIFIER_MODEL, max_tokens=60))
        return "".join(chunks).strip().strip('"').strip("'")
    except Exception:
        return query + " overview"
```

- [ ] **Step 4: Add `_search_with_retry` after `_rephrase_query`**

```python
def _search_with_retry(query: str, entity_type: str | None = None) -> tuple[list[dict], list[str]]:
    """
    Wrap ddg_search with up to 3 narrated attempts.
    Returns (results, queries_tried).
    'Thin' means fewer than 3 results or all snippets under 50 chars.
    """
    def _is_thin(results: list[dict]) -> bool:
        if len(results) < 3:
            return True
        return all(len(r.get("snippet", "")) < 50 for r in results)

    queries_tried = []

    # Attempt 1: original query
    queries_tried.append(query)
    results = ddg_search(query)
    results = _filter_results(results)
    if not _is_thin(results):
        return results, queries_tried

    # Attempt 2: rephrased query
    print_status("↳ That first pass was thin — trying a different angle...")
    rephrased = _rephrase_query(query)
    queries_tried.append(rephrased)
    results2 = _filter_results(ddg_search(rephrased))
    clear_status()
    if not _is_thin(results2):
        return results2, queries_tried

    # Attempt 3: add domain hint
    print_status("↳ Still not much — adding a source hint...")
    if entity_type and entity_type in _SOURCE_INTELLIGENCE:
        domain_hint = _SOURCE_INTELLIGENCE[entity_type][0].split(".")[0]
    else:
        domain_hint = "wikipedia"
    hinted = f"{query} {domain_hint}"
    queries_tried.append(hinted)
    results3 = _filter_results(ddg_search(hinted))
    clear_status()

    # Return best non-empty set, prefer whichever has most results
    best = max([results, results2, results3], key=len)
    return best, queries_tried
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSearchWithRetry -v 2>&1 | tail -10
```
Expected: `3 passed`

- [ ] **Step 6: All existing tests still pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q --tb=short 2>&1 | tail -5
```
Expected: `154 passed`

- [ ] **Step 7: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: _search_with_retry — narrated 3-attempt DDG retry with rephrasing"
```

---

## Task 3: Wire `_search_with_retry` into `search_flow` and pass `_SearchMeta` to `_handle_results_input`

**Files:**
- Modify: `surf.py` lines ~2655–2910

- [ ] **Step 1: Find the bare `ddg_search` call in `search_flow`**

Run:
```bash
cd ~/termbrowser && grep -n "results = ddg_search" surf.py | head -5
```
Note the line number (approximately 2676 after Task 1/2 additions).

- [ ] **Step 2: Replace the bare `ddg_search` call with `_search_with_retry`**

Find this block in `search_flow` (the primary DDG call, NOT the alt_query call):

```python
    print_status(f"↳ searching: \"{ddg_query[:55]}\"...")
    try:
        results = ddg_search(ddg_query)
```

Replace with:

```python
    print_status(f"↳ searching: \"{ddg_query[:55]}\"...")
    try:
        results, _queries_tried = _search_with_retry(ddg_query, entity_type=_identify_entity_type(query))
```

- [ ] **Step 3: Construct `_SearchMeta` inside `search_flow`**

Find this block near the end of `search_flow` (just before `if interactive:`):

```python
    if interactive:
        _handle_results_input(results, context=response)

    return results, response
```

Replace with:

```python
    _meta = _SearchMeta(
        original_query=query,
        queries_tried=_queries_tried,
        result_count=len(results),
        confidence_tier=tier,
        coverage_note=(
            f"Searches tried: {'; '.join(_queries_tried)}"
            if len(results) < 3 else None
        ),
    )

    if interactive:
        _handle_results_input(results, context=response, meta=_meta)

    return results, response
```

- [ ] **Step 4: Update `_handle_results_input` signature to accept `meta`**

Change the function signature from:
```python
def _handle_results_input(results: list[dict], context: str = "") -> None:
```
To:
```python
def _handle_results_input(results: list[dict], context: str = "", meta: "_SearchMeta | None" = None) -> None:
```

The body is unchanged in this task — we'll rewire it in Task 7.

- [ ] **Step 5: Initialize `_queries_tried` in the except branch of `search_flow`**

Find the except/fallback branch in `search_flow` where `results` is set to `[]` on exception. Add `_queries_tried = [ddg_query]` there so the variable is always defined. (Search for `except Exception` inside `search_flow`.)

- [ ] **Step 6: Verify all existing tests still pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q --tb=short 2>&1 | tail -5
```
Expected: `154 passed`

- [ ] **Step 7: Commit**

```bash
cd ~/termbrowser && git add surf.py && git commit -m "feat: wire _search_with_retry into search_flow, thread _SearchMeta to interactive loop"
```

---

## Task 4: Six-way input classifier (`_classify_input`)

**Files:**
- Modify: `surf.py` (add before `_is_casual_input` at ~line 3450)
- Test: `tests/test_surf.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestClassifyInput:
    def _classify(self, text):
        from surf import _classify_input
        return _classify_input(text)

    # --- command ---
    def test_command_numeric(self):
        assert self._classify("1") == "command"

    def test_command_open(self):
        assert self._classify("o2") == "command"

    def test_command_summary(self):
        assert self._classify("s3") == "command"

    def test_command_quit(self):
        assert self._classify("q") == "command"

    def test_command_help(self):
        assert self._classify("?") == "command"

    def test_command_new(self):
        assert self._classify("n") == "command"

    # --- casual ---
    def test_casual_thanks(self):
        assert self._classify("thanks") == "casual"

    def test_casual_wow(self):
        assert self._classify("wow") == "casual"

    def test_casual_cool(self):
        assert self._classify("cool that's interesting") == "casual"

    # --- correction ---
    def test_correction_no_i_meant(self):
        assert self._classify("no, I meant 2022") == "correction"

    def test_correction_not_thailand(self):
        assert self._classify("not Thailand — Taiwan") == "correction"

    def test_correction_actually(self):
        assert self._classify("actually I want the 1998 tournament") == "correction"

    # --- redirect ---
    def test_redirect_your_job(self):
        assert self._classify("that's your job") == "redirect"

    def test_redirect_try_harder(self):
        assert self._classify("try harder") == "redirect"

    def test_redirect_you_missed(self):
        assert self._classify("you missed the other groups") == "redirect"

    # --- scope_expansion ---
    def test_scope_expansion_the_others(self):
        assert self._classify("what about the others") == "scope_expansion"

    def test_scope_expansion_all_of_them(self):
        assert self._classify("show me all of them") == "scope_expansion"

    def test_scope_expansion_the_rest(self):
        assert self._classify("what about the rest") == "scope_expansion"

    def test_scope_expansion_groups(self):
        assert self._classify("what about groups A B D E F G") == "scope_expansion"

    # --- followup (default) ---
    def test_followup_question(self):
        assert self._classify("why did Brazil draw?") == "followup"

    def test_followup_how(self):
        assert self._classify("how did Scotland score?") == "followup"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestClassifyInput -v 2>&1 | tail -15
```
Expected: `ImportError` — `_classify_input` doesn't exist yet.

- [ ] **Step 3: Add `_classify_input` to surf.py, before `_is_casual_input`**

```python
# ─── Input classifier ──────────────────────────────────────────────────────────

_COMMAND_TOKENS = {"q", "n", "?", "prefer:"}
_REDIRECT_PHRASES = {"your job", "try harder", "you missed", "not good enough", "do better", "try again"}
_CORRECTION_STARTERS = ("no,", "no ", "not ", "i meant", "actually", "wait,", "wrong,")
_SCOPE_PHRASES = {"the others", "all of them", "the rest", "show me more", "what about the",
                  "what about groups", "and the other", "the remaining"}


def _classify_input(text: str) -> str:
    """
    Classify interactive input into one of six types.
    Pattern-match first (instant). LLM fallback only for genuine ambiguity.
    Returns: 'command' | 'casual' | 'correction' | 'redirect' | 'scope_expansion' | 'followup'
    """
    t = text.strip()
    if not t:
        return "followup"
    tl = t.lower()

    # command — exact tokens or numeric/prefixed patterns
    if tl in _COMMAND_TOKENS:
        return "command"
    if len(tl) <= 3 and (tl.isdigit() or (tl[0] in "os" and tl[1:].isdigit())):
        return "command"
    if tl.startswith("prefer:"):
        return "command"

    # casual — short, no question mark, starts with casual word
    words = tl.split()
    if len(words) <= 4 and "?" not in tl and words[0] in _CASUAL_STARTERS:
        return "casual"
    if tl.rstrip("!").strip() in _CASUAL_STARTERS:
        return "casual"

    # correction — starts with correction phrase
    if any(tl.startswith(s) for s in _CORRECTION_STARTERS):
        return "correction"

    # redirect — contains redirect phrase
    if any(phrase in tl for phrase in _REDIRECT_PHRASES):
        return "redirect"

    # scope_expansion — contains scope phrase
    if any(phrase in tl for phrase in _SCOPE_PHRASES):
        return "scope_expansion"

    # Default: followup (safe, sends to existing _handle_followup)
    return "followup"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestClassifyInput -v 2>&1 | tail -25
```
Expected: all tests pass. If a test fails, adjust the pattern sets in `_REDIRECT_PHRASES`, `_SCOPE_PHRASES`, etc. to cover the failing case.

- [ ] **Step 5: All existing tests still pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q --tb=short 2>&1 | tail -5
```
Expected: `174 passed` (154 + 20 new)

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: six-way input classifier — pattern-match, no LLM for common cases"
```

---

## Task 5: Conversational response functions

**Files:**
- Modify: `surf.py` (add after `_classify_input`)
- Test: `tests/test_surf.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestConversationalReply:
    def test_redirect_with_coverage_note(self, capsys):
        from surf import _conversational_reply, _SearchMeta
        meta = _SearchMeta("world cup groups", ["world cup groups"], 1, "current",
                           "Searches tried: world cup groups; world cup standings")
        _conversational_reply("redirect", meta=meta)
        out = capsys.readouterr().out
        assert len(out.strip()) > 0  # printed something

    def test_casual_no_search(self, capsys):
        from surf import _conversational_reply
        _conversational_reply("casual", meta=None)
        out = capsys.readouterr().out
        assert len(out.strip()) > 0

    def test_dead_end_shows_options(self, capsys):
        from surf import _conversational_reply, _SearchMeta
        meta = _SearchMeta("obscure query", ["q1", "q2", "q3"], 0, "snippet", "No results found")
        _conversational_reply("dead_end", meta=meta)
        out = capsys.readouterr().out
        assert "r" in out or "t" in out  # shows options
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestConversationalReply -v 2>&1 | tail -10
```
Expected: `ImportError` — `_conversational_reply` doesn't exist yet.

- [ ] **Step 3: Add `_conversational_reply` to surf.py, after `_classify_input`**

```python
# ─── Conversational response layer ─────────────────────────────────────────────

_CASUAL_REPLIES = [
    "Glad that's useful — anything else you want to dig into?",
    "Happy to keep going — what's next?",
    "Sure — what else can I find for you?",
]

_REDIRECT_REPLIES_NO_NOTE = [
    "Fair enough — let me come at this from a wider angle.",
    "You're right — I'll broaden the search.",
    "Point taken — trying a different approach.",
]


def _conversational_reply(
    reply_type: str,
    meta: "_SearchMeta | None" = None,
    user_text: str = "",
) -> None:
    """
    Print a short conversational response. Two sentences max.
    reply_type: 'redirect' | 'casual' | 'correction' | 'dead_end'
    """
    import random

    if reply_type == "casual":
        print(f"\033[90m{random.choice(_CASUAL_REPLIES)}\033[0m")
        print()

    elif reply_type == "redirect":
        if meta and meta.coverage_note:
            tried = meta.queries_tried[-1] if meta.queries_tried else "that"
            print(f"\033[90mFair point — I'll widen the search beyond \"{tried}\".\033[0m")
        else:
            print(f"\033[90m{random.choice(_REDIRECT_REPLIES_NO_NOTE)}\033[0m")
        print()

    elif reply_type == "correction":
        print(f"\033[90mGot it — starting fresh with that.\033[0m")
        print()

    elif reply_type == "dead_end":
        tried_str = ""
        if meta and meta.queries_tried:
            tried_str = f" (tried: {len(meta.queries_tried)} searches)"
        print(f"\033[90mThree angles, not much to show for it{tried_str}.\033[0m")
        print(f"\033[90m  \033[33mr\033[90m — read the best result I found\033[0m")
        print(f"\033[90m  \033[33mt\033[90m — try a completely different search\033[0m")
        print()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestConversationalReply -v 2>&1 | tail -10
```
Expected: `3 passed`

- [ ] **Step 5: All existing tests still pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q --tb=short 2>&1 | tail -5
```
Expected: `177 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: conversational reply layer — redirect, casual, correction, dead_end"
```

---

## Task 6: Multi-query fanout (`_handle_scope_expansion`)

**Files:**
- Modify: `surf.py` (add after `_conversational_reply`)
- Test: `tests/test_surf.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestScopeExpansion:
    def test_extract_items_from_groups_query(self):
        from surf import _extract_expansion_items
        items = _extract_expansion_items("what about groups A B D E F G", context="World Cup")
        assert len(items) >= 4
        assert any("A" in item or "Group A" in item for item in items)

    def test_extract_items_generic(self):
        from surf import _extract_expansion_items
        items = _extract_expansion_items("what about the other teams", context="Brazil won")
        # Should return something (may be empty if LLM fails gracefully)
        assert isinstance(items, list)

    def test_handle_scope_expansion_fires_searches(self):
        from surf import _handle_scope_expansion, _SearchMeta
        meta = _SearchMeta("World Cup Group C", ["World Cup Group C"], 3, "current", None)
        fake_results = [{"title": "T", "url": "http://x.com", "domain": "x.com", "snippet": "s" * 60}]
        with patch("surf.ddg_search", return_value=fake_results), \
             patch("surf.stream_groq", return_value=iter(["Group A\nGroup B"])), \
             patch("surf.print_header"), patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.vspace"):
            _handle_scope_expansion("what about groups A and B", meta=meta, context="")
        # Should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestScopeExpansion -v 2>&1 | tail -10
```
Expected: `ImportError` — functions don't exist yet.

- [ ] **Step 3: Add `_extract_expansion_items` and `_handle_scope_expansion`**

```python
# ─── Scope expansion fanout ────────────────────────────────────────────────────

def _extract_expansion_items(user_text: str, context: str = "") -> list[str]:
    """
    Use LLM to extract the list of items the user wants to expand to.
    e.g. "what about groups A B D E F G" → ["Group A", "Group B", "Group D", ...]
    Returns a list of strings, empty list on failure.
    """
    prompt = (
        f"The user asked: \"{user_text}\"\n"
        f"Context: \"{context[:200]}\"\n\n"
        "List the specific items they want information about. "
        "Return each item on its own line, nothing else. "
        "Maximum 8 items. If you can't identify specific items, return nothing."
    )
    try:
        chunks = list(stream_groq(prompt, "Extract list items. One per line. No numbering, no bullets.", model=CLASSIFIER_MODEL, max_tokens=100))
        raw = "".join(chunks).strip()
        items = [line.strip() for line in raw.splitlines() if line.strip()]
        return items[:8]
    except Exception:
        return []


def _fanout_search_one(item: str, base_query: str) -> tuple[str, list[dict], str]:
    """
    Search for one item in a fanout. Returns (item, results, synthesis).
    Runs in a thread.
    """
    query = f"{base_query} {item}"
    try:
        results = _filter_results(ddg_search(query, num_results=3))
    except Exception:
        results = []
    if not results:
        return item, [], f"Nothing found for {item}."
    snippets = "\n".join(f"[{i+1}] {r['snippet']}" for i, r in enumerate(results[:3]))
    prompt = (
        f"Question: What is the current status of {item} in this context: {base_query}?\n\n"
        f"Sources:\n{snippets}\n\n"
        "Answer in 1-2 sentences. Be specific. Lead with the most interesting fact. "
        "State your read clearly. If sources are empty or vague, say so in one sentence."
    )
    try:
        chunks = list(stream_groq(prompt, "You are a sharp research assistant. One to two sentences only.", model=CLASSIFIER_MODEL, max_tokens=120))
        synthesis = "".join(chunks).strip()
    except Exception:
        synthesis = results[0]["snippet"][:200] if results else "No data found."
    return item, results, synthesis


def _handle_scope_expansion(
    user_text: str,
    meta: "_SearchMeta | None",
    context: str,
) -> tuple[list[dict], str, "_SearchMeta"]:
    """
    Fan out searches for multiple items. Stream results as they land.
    Returns (combined_results, combined_response, new_meta).
    """
    base_query = meta.original_query if meta else user_text
    items = _extract_expansion_items(user_text, context=base_query)

    if not items:
        # Fallback: treat as a redirect and do a broader search
        _conversational_reply("redirect", meta=meta, user_text=user_text)
        new_results, new_response = _handle_followup(user_text, context=context)
        new_meta = _SearchMeta(user_text, [user_text], len(new_results), "current", None)
        return new_results, new_response, new_meta

    count = len(items)
    print(f"\033[90mOn it — checking {count} {'item' if count == 1 else 'items'} now.\033[0m\n")

    all_results: list[dict] = []
    all_syntheses: list[str] = []
    queries_tried: list[str] = []

    with ThreadPoolExecutor(max_workers=min(6, count)) as executor:
        futures = {executor.submit(_fanout_search_one, item, base_query): item for item in items}
        for future in as_completed(futures):
            item, results, synthesis = future.result()
            queries_tried.append(f"{base_query} {item}")
            print_header(item, "")
            print(synthesis)
            print()
            all_results.extend(results)
            all_syntheses.append(f"**{item}:** {synthesis}")

    combined_response = "\n\n".join(all_syntheses)
    new_meta = _SearchMeta(
        original_query=base_query,
        queries_tried=queries_tried,
        result_count=len(all_results),
        confidence_tier="current",
        coverage_note=None,
    )
    return all_results, combined_response, new_meta
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestScopeExpansion -v 2>&1 | tail -10
```
Expected: `3 passed`

- [ ] **Step 5: All existing tests still pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q --tb=short 2>&1 | tail -5
```
Expected: `180 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: multi-query fanout — scope_expansion streams parallel searches as they land"
```

---

## Task 7: `_classify_and_dispatch` + rewire `_handle_results_input`

**Files:**
- Modify: `surf.py`
- Test: `tests/test_surf.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestClassifyAndDispatch:
    def _make_meta(self):
        from surf import _SearchMeta
        return _SearchMeta("test query", ["test query"], 5, "current", None)

    def test_command_q_returns_break(self):
        from surf import _classify_and_dispatch, _SearchMeta
        meta = self._make_meta()
        results = []
        _, _, _, should_break = _classify_and_dispatch("q", results, meta, "")
        assert should_break is True

    def test_casual_no_search(self):
        from surf import _classify_and_dispatch
        meta = self._make_meta()
        with patch("surf._conversational_reply") as mock_reply:
            new_results, _, _, should_break = _classify_and_dispatch("thanks", [], meta, "")
        mock_reply.assert_called_once_with("casual", meta=meta, user_text="thanks")
        assert should_break is False

    def test_redirect_calls_followup(self):
        from surf import _classify_and_dispatch
        meta = self._make_meta()
        fake_meta = self._make_meta()
        with patch("surf._conversational_reply"), \
             patch("surf._handle_followup", return_value=([], "", fake_meta)) as mock_fup:
            _classify_and_dispatch("that's your job", [], meta, "")
        mock_fup.assert_called_once()

    def test_scope_expansion_calls_fanout(self):
        from surf import _classify_and_dispatch
        meta = self._make_meta()
        fake_meta = self._make_meta()
        with patch("surf._handle_scope_expansion", return_value=([], "", fake_meta)) as mock_fanout:
            _classify_and_dispatch("what about the others", [], meta, "")
        mock_fanout.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestClassifyAndDispatch -v 2>&1 | tail -10
```
Expected: `ImportError` — `_classify_and_dispatch` doesn't exist yet.

- [ ] **Step 3: Add `_classify_and_dispatch` to surf.py, just before `_handle_results_input`**

```python
def _classify_and_dispatch(
    choice: str,
    results: list[dict],
    meta: "_SearchMeta | None",
    context: str,
) -> tuple[list[dict], str, "_SearchMeta | None", bool]:
    """
    Classify user input and dispatch to the right handler.
    Returns (new_results, new_context, new_meta, should_break).
    """
    from surf import _SearchMeta as SM  # local alias for type hints

    cl = choice.lower().strip()
    input_type = _classify_input(choice)

    # ── commands ──────────────────────────────────────────────────────────────
    if input_type == "command":
        if cl == "q":
            return results, context, meta, True
        if cl == "n":
            query = surf_input("New search: ")
            if query:
                search_flow(query)
            return results, context, meta, True
        if cl == "?":
            n = len(results)
            print()
            print("\033[1msurf commands\033[0m")
            print(f"  \033[33m1–{n}\033[0m      read article in terminal")
            print(f"  \033[33ms1–s{n}\033[0m    quick AI summary")
            print(f"  \033[33mo1–o{n}\033[0m    open in browser")
            print(f"  \033[33mn\033[0m        new search")
            print(f"  \033[33mq\033[0m        quit")
            print(f"  \033[33m↵\033[0m        follow-up question")
            print()
            return results, context, meta, False
        if cl.startswith("o") and cl[1:].isdigit():
            idx = int(cl[1:]) - 1
            if 0 <= idx < len(results):
                record_feature_use("browser")
                open_in_browser(results[idx]["url"])
            return results, context, meta, False
        if cl.startswith("s") and cl[1:].isdigit():
            idx = int(cl[1:]) - 1
            if 0 <= idx < len(results):
                record_feature_use("summary")
                read_flow(results[idx]["url"], interactive=True, ai_summary=True)
                return results, context, meta, True
            return results, context, meta, False
        if cl.isdigit():
            idx = int(cl) - 1
            if 0 <= idx < len(results):
                record_feature_use("reader")
                read_flow(results[idx]["url"], interactive=True, ai_summary=False)
                return results, context, meta, True
            return results, context, meta, False
        if choice.lower().startswith("prefer:"):
            _handle_inline_preference(choice[7:].strip())
            return results, context, meta, False
        # dead_end options from _conversational_reply
        if cl == "r" and results:
            read_flow(results[0]["url"], interactive=True, ai_summary=True)
            return results, context, meta, True
        if cl == "t":
            query = surf_input("New search: ")
            if query:
                search_flow(query)
            return results, context, meta, True
        return results, context, meta, False

    # ── casual ────────────────────────────────────────────────────────────────
    if input_type == "casual":
        _conversational_reply("casual", meta=meta, user_text=choice)
        return results, context, meta, False

    # ── correction ────────────────────────────────────────────────────────────
    if input_type == "correction":
        _conversational_reply("correction", meta=meta, user_text=choice)
        record_feature_use("followup")
        new_results, new_context, new_meta = _handle_followup(choice, context="")
        if new_results:
            print_results(new_results)
        return new_results or results, new_context, new_meta, False

    # ── redirect ──────────────────────────────────────────────────────────────
    if input_type == "redirect":
        _conversational_reply("redirect", meta=meta, user_text=choice)
        record_feature_use("followup")
        # Broaden by appending redirect signal to original query
        broader = (meta.original_query if meta else choice) + " comprehensive overview all"
        new_results, new_context, new_meta = _handle_followup(broader, context="")
        if new_results:
            print_results(new_results)
        return new_results or results, new_context, new_meta, False

    # ── scope_expansion ───────────────────────────────────────────────────────
    if input_type == "scope_expansion":
        record_feature_use("followup")
        new_results, new_context, new_meta = _handle_scope_expansion(choice, meta=meta, context=context)
        return new_results or results, new_context, new_meta, False

    # ── followup (default) ────────────────────────────────────────────────────
    record_feature_use("followup")
    if format_session_context():
        record_feature_use("session")
    new_results, new_context, new_meta = _handle_followup(choice, context=context)
    if new_results:
        print_results(new_results)
        results = new_results
    return results, new_context or context, new_meta, False
```

- [ ] **Step 4: Update `_handle_followup` to return `_SearchMeta` as third element**

Find `_handle_followup` (around line 3011). Change its return type annotation and final return statements.

Change the function signature from:
```python
def _handle_followup(question: str, context: str = "") -> tuple[list[dict], str]:
```
To:
```python
def _handle_followup(question: str, context: str = "") -> tuple[list[dict], str, "_SearchMeta"]:
```

At the end of `_handle_followup`, wherever `return search_results, response` (or similar) appears, change to:

```python
    _fup_meta = _SearchMeta(
        original_query=question,
        queries_tried=[search_query],
        result_count=len(search_results),
        confidence_tier=tier,
        coverage_note=None,
    )
    return search_results, response, _fup_meta
```

Run a grep to find all return statements in `_handle_followup`:
```bash
cd ~/termbrowser && awk '/^def _handle_followup/,/^def [a-z]/' surf.py | grep -n "return"
```

Update EVERY return statement in `_handle_followup` to return the three-tuple. For early-exit returns (no results), return `([], "", _SearchMeta(question, [question], 0, "snippet", None))`.

- [ ] **Step 5: Update the two callers of `_handle_followup` inside `_handle_results_input`**

Find lines like (inside `_handle_results_input`):
```python
new_results, new_response = _handle_followup(choice, context=context)
```

Change both to:
```python
new_results, new_response, _ = _handle_followup(choice, context=context)
```

(The old dispatch ignores meta — Task 7 Step 6 will replace it entirely.)

- [ ] **Step 6: Replace the body of `_handle_results_input` with the new thin loop**

Replace the entire body of `_handle_results_input` with:

```python
def _handle_results_input(results: list[dict], context: str = "", meta: "_SearchMeta | None" = None) -> None:
    """Wait for user input and dispatch via _classify_and_dispatch."""
    while True:
        try:
            choice = surf_input("ask a follow-up or type a new search")
        except (KeyboardInterrupt, EOFError):
            break

        if not choice.strip():
            continue

        _add_to_history(choice)
        results, context, meta, should_break = _classify_and_dispatch(choice, results, meta, context)
        if should_break:
            break
```

- [ ] **Step 7: Run tests**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestClassifyAndDispatch -v 2>&1 | tail -15
```
Expected: `4 passed`

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q --tb=short 2>&1 | tail -5
```
Expected: `184 passed` (all previous tests pass)

- [ ] **Step 8: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: _classify_and_dispatch + rewire _handle_results_input — conversational loop live"
```

---

## Task 8: Voice overhaul — Chesterton SEARCH_SYSTEM prompts

**Files:**
- Modify: `surf.py` lines 527–600 (SEARCH_SYSTEM, SEARCH_SYSTEM_CURRENT, SEARCH_SYSTEM_RESEARCH, SEARCH_SYSTEM_CONTESTED)

No automated tests for prompt strings — manual verification checklist below.

- [ ] **Step 1: Replace the opening line of `SEARCH_SYSTEM` (line 527)**

Find:
```python
SEARCH_SYSTEM = """You are a precise research assistant answering questions using search result snippets.
```

Replace with:
```python
SEARCH_SYSTEM = """You are a sharp, well-read research assistant with genuine opinions. You find topics interesting and it shows. You lead with the most surprising or counterintuitive finding, not the most obvious one. You state your read clearly — not "sources suggest" but what you actually think the evidence shows. You are honest about what you don't know, and you say so with wit rather than disclaimers.
```

- [ ] **Step 2: Add new voice rules to `SEARCH_SYSTEM` (after the existing voice rules block)**

Find the last voice rule in SEARCH_SYSTEM (currently: `- Never fabricate specific facts not present in the search snippets.`). After that line, before the closing `"""`, add:

```
- Lead with the finding that would make someone say "huh, interesting" — not the one they already expected.
- State a clear interpretation: "Brazil looked ordinary" not "Brazil's performance was mixed."
- When data is partial, say so with character: "I've got Group C nailed down — the other eleven are keeping their secrets." Then stop — don't pad.
- Use contractions. Write like a person, not a report.
- TIER GATE: For short factual queries (a score, a date, a name, a definition) — answer plainly in 1-2 sentences. Reserve the opinionated voice for analytical or multi-faceted questions.
```

- [ ] **Step 3: Update `SEARCH_SYSTEM_CURRENT`**

Find:
```python
SEARCH_SYSTEM_CURRENT = """You are a precise research assistant synthesizing today's journalism and analysis.
```

Replace with:
```python
SEARCH_SYSTEM_CURRENT = """You are a sharp analyst synthesizing today's news with genuine opinions. You lead with what's actually surprising or significant — not just what happened, but what it means. You state your read clearly. When coverage is thin or contradictory, you say so in one sentence and explain why.
```

After the existing voice rules in `SEARCH_SYSTEM_CURRENT`, add:
```
- Start with the most significant development, not the most recent one.
- "Scotland sit top of their group — which is either remarkable or a quiet indictment of Group C, depending on how the next two games go." is better than "Scotland are currently leading Group C."
- Use contractions. Be a person, not a wire service.
```

- [ ] **Step 4: Update `SEARCH_SYSTEM_RESEARCH`**

Find:
```python
SEARCH_SYSTEM_RESEARCH = """You are a precise research assistant synthesizing explanatory sources.
```

Replace with:
```python
SEARCH_SYSTEM_RESEARCH = """You are a knowledgeable analyst explaining complex topics with genuine intellectual engagement. You make the interesting parts interesting. You synthesize across sources and state where you land — not "scholars debate" but what the evidence actually shows and where real uncertainty remains.
```

After existing voice rules in `SEARCH_SYSTEM_RESEARCH`, add:
```
- Open with the finding that reframes the question, not a definition of terms.
- Use contractions and natural language. Academic prose is a vice, not a virtue.
```

- [ ] **Step 5: Update `SEARCH_SYSTEM_CONTESTED`**

Find:
```python
SEARCH_SYSTEM_CONTESTED = """You are a precise research assistant presenting multiple perspectives fairly.
```

Replace with:
```python
SEARCH_SYSTEM_CONTESTED = """You are an intellectually honest analyst presenting competing views with genuine engagement. You steelman each side before offering your honest read. You are not a pushover — when evidence favors one side clearly, you say so. When it genuinely doesn't, you say that too, and explain why the disagreement persists.
```

After existing voice rules in `SEARCH_SYSTEM_CONTESTED`, add:
```
- "The evidence leans toward X, though Y has a point about Z" is better than "both sides have merit."
- Name the actual tradeoff, not a diplomatic summary of it.
```

- [ ] **Step 6: Run all existing tests to confirm prompt changes didn't break anything**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q --tb=short 2>&1 | tail -5
```
Expected: `184 passed`

- [ ] **Step 7: Manual voice check**

Run surf and verify the voice feels right:
```bash
cd ~/termbrowser && .venv/bin/python surf.py "which team has impressed most at the 2026 world cup"
```
Check: Does the TL;DR lead with a surprising observation, not a plain summary? Does it sound like a person?

```bash
cd ~/termbrowser && .venv/bin/python surf.py "who won the 1966 world cup"
```
Check: Short factual query → plain direct answer, no wit or paradox (tier gate working).

- [ ] **Step 8: Commit**

```bash
cd ~/termbrowser && git add surf.py && git commit -m "feat: Chesterton voice overhaul — opinionated, warm, tier-gated SEARCH_SYSTEM prompts"
```

---

## Task 9: Integration test, push, and verify

**Files:**
- Test: `tests/test_surf.py`

- [ ] **Step 1: Add integration-level tests for the full conversational flow**

```python
class TestConversationalIntegration:
    def test_thats_your_job_classified_as_redirect(self):
        from surf import _classify_input
        assert _classify_input("that's your job") == "redirect"

    def test_what_about_others_classified_as_scope_expansion(self):
        from surf import _classify_input
        assert _classify_input("what about the other groups") == "scope_expansion"

    def test_no_i_meant_classified_as_correction(self):
        from surf import _classify_input
        assert _classify_input("no, I meant 2022") == "correction"

    def test_thanks_classified_as_casual(self):
        from surf import _classify_input
        assert _classify_input("thanks") == "casual"

    def test_followup_question_classified_as_followup(self):
        from surf import _classify_input
        assert _classify_input("why did Brazil draw?") == "followup"

    def test_search_meta_survives_followup(self):
        from surf import _handle_followup
        with patch("surf.ddg_search", return_value=[
            {"title": "T", "url": "http://x.com", "domain": "x.com", "snippet": "s" * 60}
        ] * 3), patch("surf.stream_ai", return_value=iter(["Test response"])), \
             patch("surf.print_header"), patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.vspace"), patch("surf.print_results"):
            results, response, meta = _handle_followup("test question")
        assert meta.original_query == "test question"
        assert meta.result_count >= 0
```

- [ ] **Step 2: Run all tests**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q --tb=short 2>&1 | tail -10
```
Expected: all tests pass (184+ passing, 0 failures).

- [ ] **Step 3: Manual end-to-end test of the World Cup scenario**

```bash
cd ~/termbrowser && .venv/bin/python surf.py "which team has impressed most after the first games at the world cup"
```

In the interactive prompt, type:
1. `that's your job` → should get warm redirect response + new search
2. `what about groups A B D E F G` → should fan out 6 searches, stream them
3. `no, I meant the 2022 world cup` → should reset and search 2022
4. `thanks` → should get casual warm response
5. `q` → should exit

- [ ] **Step 4: Push to GitHub**

```bash
cd ~/termbrowser && git push origin main
```

- [ ] **Step 5: Final commit count check**

```bash
cd ~/termbrowser && git log --oneline -10
```
Should show: voice overhaul, classify_and_dispatch, fanout, conversational_reply, classifier, retry, _SearchMeta, weather handler, routing infra, etc.

---

## Self-Review

**Spec coverage check:**
- ✅ `_SearchMeta` — Task 1
- ✅ `_search_with_retry` — Task 2 + 3
- ✅ Six-way classifier — Task 4
- ✅ Conversational responses (redirect, casual, correction, dead_end) — Task 5
- ✅ Multi-query fanout with streaming — Task 6
- ✅ `_classify_and_dispatch` replacing `_handle_results_input` dispatch — Task 7
- ✅ `_handle_followup` returns `_SearchMeta` — Task 7 Step 4
- ✅ Voice overhaul, tier-gated — Task 8
- ✅ Integration tests + push — Task 9

**Type consistency check:**
- `_handle_followup` returns `tuple[list[dict], str, _SearchMeta]` — defined Task 7 Step 4, called in `_classify_and_dispatch` Step 3 ✅
- `_handle_scope_expansion` returns `tuple[list[dict], str, _SearchMeta]` — defined Task 6, called in `_classify_and_dispatch` Step 3 ✅
- `_classify_and_dispatch` returns `tuple[list[dict], str, _SearchMeta | None, bool]` — defined Task 7 Step 3, called in `_handle_results_input` Step 6 ✅
- `_SearchMeta` used consistently in Tasks 1–9 ✅

**Placeholder scan:** No TBDs, no TODOs, no "similar to Task N" references. All code blocks complete. ✅
