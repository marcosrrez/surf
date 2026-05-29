# Adaptive Search System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the adaptive search tier system — surf classifies each query, checks snippet quality, goes deep when needed, and shows transparent progress steps.

**Architecture:** Five search tiers (snippet → current → research → contested) selected by heuristics, then validated by a confidence gate that checks snippet coverage/freshness/authority. Deep tiers fetch and read real articles using the existing reader pipeline, then synthesize with tier-specific prompts. All new logic lives in `surf.py`; all new tests in `tests/test_surf.py`.

**Tech Stack:** Python 3.10+, existing surf.py functions (`fetch_page`, `extract_text`, `ddg_search`, `_filter_results`, `_identify_entity_type`, `stream_ai`, `print_status`, `clear_status`)

---

## Files

- **Modify:** `~/termbrowser/surf.py` — add constants, 3 new functions, extend `_identify_entity_type`, modify `search_flow`
- **Modify:** `~/termbrowser/tests/test_surf.py` — add `TestClassifyTier`, `TestConfidenceGate`, `TestDeepResearch`, `TestSearchFlowTiers`

---

## Task 1: Constants — tier signals, source hierarchy, tier prompts

**Files:**
- Modify: `~/termbrowser/surf.py` — insert after `_TEMPORAL_SIGNALS` block (line ~1143)

- [ ] **Step 1: Add SEARCH_TIER_SIGNALS, SOURCE_HIERARCHY, and tier-specific system prompts**

Find the line `_TEMPORAL_SIGNALS = {` in `surf.py` (currently around line 1143) and insert the following block **after** the closing `}` of `_TEMPORAL_SIGNALS`:

```python
SEARCH_TIER_SIGNALS = {
    "current": {
        " will ", "who will", "predict", "prediction", "odds", "chance",
        "favorite", "favourite", " expect", "likely", "latest", "current ",
        " today", "this week", "this month", "upcoming", " next ", " soon",
        "winner", "who wins", "going to win", "going to beat", "forecast",
    },
    "research": {
        "how does", "how do ", "why does", "why do ", "why is ", "why are ",
        "explain ", "what causes", "how to ", "what is the difference",
        "what makes", "how come", "mechanism", "what happens when",
    },
    "contested": {
        " best ", " vs ", " versus ", "compare", "should i ", "worth it",
        "better than", "recommend", "which is better", "which should",
        "pros and cons", "advantages", "disadvantages",
    },
}

SOURCE_HIERARCHY = {
    "sports":   ["espn.com", "bbc.com/sport", "theathletic.com", "skysports.com",
                 "uefa.com", "nfl.com", "nba.com", "mlb.com"],
    "finance":  ["bloomberg.com", "ft.com", "wsj.com", "reuters.com", "cnbc.com",
                 "marketwatch.com"],
    "tech":     ["arstechnica.com", "wired.com", "techcrunch.com", "theverge.com",
                 "zdnet.com", "9to5mac.com"],
    "medical":  ["mayoclinic.org", "pubmed.ncbi.nlm.nih.gov", "webmd.com", "nih.gov",
                 "nejm.org"],
    "science":  ["nature.com", "sciencedaily.com", "nasa.gov", "scientificamerican.com",
                 "newscientist.com"],
    "news":     ["reuters.com", "apnews.com", "bbc.com", "nytimes.com",
                 "theguardian.com"],
    "legal":    ["law.cornell.edu", "oyez.org", "courtlistener.com", "justia.com"],
}

SEARCH_SYSTEM_CURRENT = """You are a precise research assistant synthesizing today's journalism and analysis.

Format rules:
- First line: "▸ TL;DR  " followed by one concrete, specific sentence — include names, numbers, dates
- Blank line
- 2-4 paragraphs using the actual content from the sources provided
- Use **bold** for key names and facts
- Use "•" for bullet points, never dashes

Voice rules:
- Be specific. If the sources have names, scores, odds, dates — use them.
- If an event is imminent, lead with who is involved and when.
- Correct any outdated information from the search snippets.
- No filler phrases. No "Great question"."""

SEARCH_SYSTEM_RESEARCH = """You are a precise research assistant synthesizing explanatory sources.

Format rules:
- First line: "▸ TL;DR  " followed by one clear, direct sentence
- Blank line
- 3-5 paragraphs building from fundamentals to implications
- Use **bold** for key concepts
- Use "•" for bullet points where appropriate

Voice rules:
- Synthesize across sources — don't summarize each separately.
- Note where sources agree and where they meaningfully differ.
- No filler phrases."""

SEARCH_SYSTEM_CONTESTED = """You are a precise research assistant presenting multiple perspectives fairly.

Format rules:
- First line: "▸ TL;DR  " followed by a sentence that names the central tradeoff
- Blank line
- Present each major perspective with its strongest argument
- Use **bold** for key positions and tradeoffs
- End with your honest assessment of which is right for which use case

Voice rules:
- Name the tradeoffs explicitly. Don't pick a winner unless evidence is overwhelming.
- The answer is not which side is right — it is which side is right for what.
- No filler phrases."""
```

- [ ] **Step 2: Extend `_identify_entity_type` to detect sports**

Find `_identify_entity_type` in `surf.py`. In the `signals` dict, add a "sports" entry:

```python
        "sports": ["football", "soccer", "basketball", "baseball", "tennis",
                   "nfl", "nba", "mlb", "nhl", "premier league", "champions league",
                   "world cup", "tournament", "championship", "final", "match",
                   "game", "season", "playoff", "standings", "score"],
```

Add it as the first entry in `signals` (before "therapist"). Also add "sports" to `_SOURCE_INTELLIGENCE` in `surf.py` (the existing dict around line 1798) so the existing follow-up logic also benefits:

```python
    "sports": ["espn.com", "bbc.com/sport", "theathletic.com", "skysports.com", "uefa.com"],
```

- [ ] **Step 3: Verify surf still imports cleanly**

```bash
cd ~/termbrowser && .venv/bin/python3 -c "from surf import SEARCH_TIER_SIGNALS, SOURCE_HIERARCHY, SEARCH_SYSTEM_CURRENT, SEARCH_SYSTEM_RESEARCH, SEARCH_SYSTEM_CONTESTED; print('constants OK')"
```

Expected: `constants OK`

- [ ] **Step 4: Run full test suite to verify no regressions**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q
```

Expected: `39 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py && git commit -m "feat: adaptive search constants — tier signals, source hierarchy, tier prompts"
```

---

## Task 2: `_classify_tier` — heuristic query tier classification

**Files:**
- Modify: `~/termbrowser/surf.py` — add `_classify_tier` after `_enrich_ddg_query`
- Modify: `~/termbrowser/tests/test_surf.py` — add `TestClassifyTier`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_surf.py`:

```python
from surf import _classify_tier

class TestClassifyTier:
    def test_current_tier_will(self):
        assert _classify_tier("who will win the UEFA champions league") == "current"

    def test_current_tier_latest(self):
        assert _classify_tier("latest news on AI regulation") == "current"

    def test_current_tier_predict(self):
        assert _classify_tier("predict the stock market tomorrow") == "current"

    def test_research_tier_how_does(self):
        assert _classify_tier("how does a vaccine work") == "research"

    def test_research_tier_explain(self):
        assert _classify_tier("explain quantum entanglement") == "research"

    def test_research_tier_what_causes(self):
        assert _classify_tier("what causes inflation") == "research"

    def test_contested_tier_vs(self):
        assert _classify_tier("React vs Vue for a new project") == "contested"

    def test_contested_tier_best(self):
        assert _classify_tier("best Python web framework 2026") == "contested"

    def test_contested_tier_should_i(self):
        assert _classify_tier("should I use Postgres or MongoDB") == "contested"

    def test_snippet_tier_stable_fact(self):
        assert _classify_tier("who wrote Pride and Prejudice") == "snippet"

    def test_snippet_tier_definition(self):
        assert _classify_tier("what is a black hole") == "snippet"

    def test_current_priority_over_contested(self):
        # "will" signal should beat "best" — current events wins
        assert _classify_tier("who will win the best picture oscar") == "current"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestClassifyTier -v
```

Expected: `ImportError: cannot import name '_classify_tier'`

- [ ] **Step 3: Implement `_classify_tier`**

Add after `_enrich_ddg_query` in `surf.py`:

```python
def _classify_tier(query: str) -> str:
    """Classify query into search tier using heuristics. Returns snippet | current | research | contested."""
    q = " " + query.lower() + " "
    # Current takes priority — time-sensitive queries beat everything
    if any(s in q for s in SEARCH_TIER_SIGNALS["current"]):
        return "current"
    if any(s in q for s in SEARCH_TIER_SIGNALS["contested"]):
        return "contested"
    if any(s in q for s in SEARCH_TIER_SIGNALS["research"]):
        return "research"
    return "snippet"
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestClassifyTier -v
```

Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: _classify_tier — heuristic search tier classification"
```

---

## Task 3: `_confidence_gate` — snippet quality check with escalation

**Files:**
- Modify: `~/termbrowser/surf.py` — add `_confidence_gate` after `_classify_tier`
- Modify: `~/termbrowser/tests/test_surf.py` — add `TestConfidenceGate`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_surf.py`:

```python
from surf import _confidence_gate

class TestConfidenceGate:
    def _make_results(self, snippets, domains=None):
        domains = domains or ["example.com"] * len(snippets)
        return [{"snippet": s, "title": "", "domain": d, "url": f"https://{d}"}
                for s, d in zip(snippets, domains)]

    def test_stays_snippet_when_snippets_are_good(self):
        results = self._make_results(
            ["Jane Austen wrote Pride and Prejudice in 1813"],
        )
        assert _confidence_gate("who wrote Pride and Prejudice", results, "snippet") == "snippet"

    def test_escalates_to_current_stale_temporal_query(self):
        # Query is temporal (will), snippets have no current year
        results = self._make_results(
            ["Manchester City predicted to win Champions League 2023-24 season"],
        )
        assert _confidence_gate("who will win the UCL", results, "snippet") == "current"

    def test_escalates_to_research_low_coverage(self):
        # Query words don't appear in snippets at all
        results = self._make_results(["Some completely unrelated content about cookies"])
        result = _confidence_gate("what causes quantum entanglement decoherence", results, "snippet")
        assert result == "research"

    def test_doesnt_downgrade_research_tier(self):
        # Research tier should never be downgraded, even with good snippets
        results = self._make_results(["Great snippet with lots of relevant words about research topics"])
        assert _confidence_gate("how does a vaccine work", results, "research") == "research"

    def test_doesnt_downgrade_contested_tier(self):
        results = self._make_results(["React is better than Vue for large apps"])
        assert _confidence_gate("React vs Vue", results, "contested") == "contested"

    def test_stays_current_with_fresh_snippets(self):
        import time
        year = time.strftime("%Y")
        results = self._make_results(
            [f"PSG vs Arsenal Champions League Final {year}"],
            domains=["espn.com"],
        )
        result = _confidence_gate("who will win the UCL", results, "current")
        assert result == "current"  # already current, stays current

    def test_empty_results_returns_tier_unchanged(self):
        assert _confidence_gate("anything", [], "snippet") == "snippet"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestConfidenceGate -v
```

Expected: `ImportError: cannot import name '_confidence_gate'`

- [ ] **Step 3: Implement `_confidence_gate`**

Add after `_classify_tier` in `surf.py`:

```python
def _confidence_gate(query: str, results: list[dict], tier: str) -> str:
    """
    Check snippet quality. Returns the final tier to use — same as input or escalated one level.
    Never downgrades. Deep tiers (research, contested) pass through unchanged.
    """
    if tier in ("research", "contested") or not results:
        return tier

    year = time.strftime("%Y")
    q_lower = query.lower()
    snippets_text = " ".join(
        r.get("snippet", "") + " " + r.get("title", "") for r in results
    ).lower()

    # Freshness: temporal query but snippets contain no current-year signal
    is_temporal = any(s.strip() in (" " + q_lower + " ") for s in SEARCH_TIER_SIGNALS["current"])
    if is_temporal and year not in snippets_text and str(int(year) - 1) not in snippets_text:
        return "current"

    # Coverage: fewer than 30% of meaningful query words appear in snippets
    query_words = [w for w in q_lower.split() if len(w) > 4]
    if query_words:
        found = sum(1 for w in query_words if w in snippets_text)
        if found / len(query_words) < 0.3:
            return "research"

    # Authority: domain-specific query but zero authoritative sources returned
    entity_type = _identify_entity_type(query)
    if entity_type and entity_type in SOURCE_HIERARCHY:
        result_domains = {r.get("domain", "") for r in results}
        has_authority = any(
            any(auth in d for auth in SOURCE_HIERARCHY[entity_type])
            for d in result_domains
        )
        if not has_authority:
            return "current"

    return tier
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestConfidenceGate -v
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: _confidence_gate — escalates tier when snippets lack freshness, coverage, or authority"
```

---

## Task 4: `_deep_research` — fetch sources with transparent progress

**Files:**
- Modify: `~/termbrowser/surf.py` — add `_deep_research` after `_confidence_gate`
- Modify: `~/termbrowser/tests/test_surf.py` — add `TestDeepResearch`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_surf.py`:

```python
from surf import _deep_research
from unittest.mock import patch, MagicMock
import sys, io

class TestDeepResearch:
    def _make_results(self, domains_and_urls):
        return [{"domain": d, "url": u, "title": "T", "snippet": "S"}
                for d, u in domains_and_urls]

    def test_returns_empty_if_all_fetches_fail(self):
        results = self._make_results([("espn.com", "https://espn.com/article")])
        with patch("surf.fetch_page", side_effect=Exception("timeout")):
            content, sources = _deep_research("who will win", "current", results)
        assert content == ""
        assert sources == []

    def test_returns_content_from_successful_fetch(self):
        results = self._make_results([("espn.com", "https://espn.com/article")])
        fake_html = "<html><body><p>" + "PSG vs Arsenal analysis. " * 50 + "</p></body></html>"
        with patch("surf.fetch_page", return_value=fake_html), \
             patch("surf._is_spa_shell", return_value=False):
            content, sources = _deep_research("who will win the UCL", "current", results)
        assert len(content) > 100
        assert len(sources) == 1
        assert sources[0]["domain"] == "espn.com"

    def test_caps_at_three_sources(self):
        results = self._make_results([
            ("espn.com", "https://espn.com/1"),
            ("bbc.com", "https://bbc.com/2"),
            ("skysports.com", "https://skysports.com/3"),
            ("theathletic.com", "https://theathletic.com/4"),  # 4th — should be skipped
        ])
        fake_html = "<html><body><p>" + "article content " * 60 + "</p></body></html>"
        with patch("surf.fetch_page", return_value=fake_html), \
             patch("surf._is_spa_shell", return_value=False):
            content, sources = _deep_research("sports query", "current", results)
        assert len(sources) <= 3

    def test_skips_short_content(self):
        results = self._make_results([("bad.com", "https://bad.com/article")])
        # Returns HTML with very little content (<100 words)
        fake_html = "<html><body><p>Short.</p></body></html>"
        with patch("surf.fetch_page", return_value=fake_html), \
             patch("surf._is_spa_shell", return_value=False):
            content, sources = _deep_research("query", "current", results)
        assert sources == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDeepResearch -v
```

Expected: `ImportError: cannot import name '_deep_research'`

- [ ] **Step 3: Implement `_deep_research`**

Add after `_confidence_gate` in `surf.py`:

```python
def _deep_research(
    query: str,
    tier: str,
    results: list[dict],
    enriched_query: str = "",
) -> tuple[str, list[dict]]:
    """
    Fetch real article content for deep-tier searches.
    Shows '↳ reading domain.com...' status per source.
    Returns (combined_content, sources_read). Falls back to ("", []) if all reads fail.
    """
    sources_to_read = list(results[:3])

    # For current/contested: if no authoritative sources in results, add a targeted search
    entity_type = _identify_entity_type(query)
    if tier in ("current", "contested") and entity_type in SOURCE_HIERARCHY:
        result_domains = {r.get("domain", "") for r in results}
        has_authority = any(
            any(auth in d for auth in SOURCE_HIERARCHY[entity_type])
            for d in result_domains
        )
        if not has_authority and enriched_query:
            try:
                auth_domains = SOURCE_HIERARCHY[entity_type]
                site_query = enriched_query + " " + " ".join(
                    f"site:{d}" for d in auth_domains[:3]
                )
                targeted = _filter_results(ddg_search(site_query, num_results=3))
                if targeted:
                    sources_to_read = targeted[:2] + sources_to_read[:1]
            except Exception:
                pass

    combined: list[str] = []
    sources_read: list[dict] = []

    for r in sources_to_read[:3]:
        url = r.get("url", "")
        domain = r.get("domain", "").removeprefix("www.")
        if not url or not url.startswith("http"):
            continue

        sys.stdout.write(f"\r\033[90m↳ reading {domain}...\033[0m" + " " * 20)
        sys.stdout.flush()

        try:
            html = fetch_page(url)
            if _is_spa_shell(html):
                content = _fetch_with_jina(url)
            else:
                _, content = extract_text(html, max_words=1500, return_title=True)
            if content and len(content.split()) > 100:
                combined.append(f"[Source: {domain}]\n{content[:2000]}")
                sources_read.append(r)
        except Exception:
            continue

    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()

    return "\n\n---\n\n".join(combined), sources_read
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDeepResearch -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: _deep_research — fetch real article content with transparent per-source progress"
```

---

## Task 5: Wire into `search_flow` — integrate tier routing, gate, and deep path

**Files:**
- Modify: `~/termbrowser/surf.py` — update `search_flow`
- Modify: `~/termbrowser/tests/test_surf.py` — add `TestSearchFlowTiers`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_surf.py`:

```python
from surf import search_flow

class TestSearchFlowTiers:
    def _fake_results(self):
        return [{"title": "T", "url": "https://espn.com/1", "domain": "espn.com",
                 "snippet": "PSG vs Arsenal Champions League 2026 final prediction"}]

    def test_deep_research_called_for_current_tier(self):
        with patch("surf.ddg_search", return_value=self._fake_results()), \
             patch("surf._classify_tier", return_value="current"), \
             patch("surf._confidence_gate", return_value="current"), \
             patch("surf._deep_research", return_value=("deep content", self._fake_results())) as mock_deep, \
             patch("surf.stream_ai", return_value=iter(["▸ TL;DR  PSG win."])), \
             patch("surf.stream_to_terminal", return_value="▸ TL;DR  PSG win."), \
             patch("surf.print_header"), patch("surf.print_status"), \
             patch("surf.clear_status"), patch("surf._print_linked_sources"), \
             patch("surf.print_results"), patch("surf.save_session_entry"), \
             patch("surf.format_session_context", return_value=""):
            search_flow("who will win the UCL", interactive=False)
        mock_deep.assert_called_once()

    def test_deep_research_not_called_for_snippet_tier(self):
        with patch("surf.ddg_search", return_value=self._fake_results()), \
             patch("surf._classify_tier", return_value="snippet"), \
             patch("surf._confidence_gate", return_value="snippet"), \
             patch("surf._deep_research") as mock_deep, \
             patch("surf.stream_ai", return_value=iter(["▸ TL;DR  Jane Austen."])), \
             patch("surf.stream_to_terminal", return_value="▸ TL;DR  Jane Austen."), \
             patch("surf.print_header"), patch("surf.print_status"), \
             patch("surf.clear_status"), patch("surf._print_linked_sources"), \
             patch("surf.print_results"), patch("surf.save_session_entry"), \
             patch("surf.format_session_context", return_value=""):
            search_flow("who wrote Pride and Prejudice", interactive=False)
        mock_deep.assert_not_called()

    def test_tier_specific_system_prompt_used(self):
        from surf import SEARCH_SYSTEM_CURRENT, SEARCH_SYSTEM
        captured_system = []

        def capture_stream(prompt, system, max_tokens=2048):
            captured_system.append(system)
            return iter(["▸ TL;DR  answer."])

        with patch("surf.ddg_search", return_value=self._fake_results()), \
             patch("surf._classify_tier", return_value="current"), \
             patch("surf._confidence_gate", return_value="current"), \
             patch("surf._deep_research", return_value=("deep content", self._fake_results())), \
             patch("surf.stream_ai", side_effect=capture_stream), \
             patch("surf.stream_to_terminal", return_value="▸ TL;DR  answer."), \
             patch("surf.print_header"), patch("surf.print_status"), \
             patch("surf.clear_status"), patch("surf._print_linked_sources"), \
             patch("surf.print_results"), patch("surf.save_session_entry"), \
             patch("surf.format_session_context", return_value=""):
            search_flow("who will win the UCL", interactive=False)

        assert captured_system[0] == SEARCH_SYSTEM_CURRENT
        assert captured_system[0] != SEARCH_SYSTEM
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSearchFlowTiers -v
```

Expected: the tests fail (no tier routing in search_flow yet).

- [ ] **Step 3: Integrate tier routing into `search_flow`**

In `search_flow`, make the following changes:

**A. Add tier classification before the DDG search** — right after the `print_status` line:

```python
def search_flow(query: str, interactive: bool = True, json_output: bool = False) -> tuple[list[dict], str]:
    ddg_query = _enrich_ddg_query(query)
    tier = _classify_tier(query)                   # ← ADD THIS LINE
    print_status(f"↳ searching: \"{ddg_query[:55]}\"...")
```

**B. Replace the existing `_needs_multi_search` block** with tier-aware multi-search. Find this block:

```python
        if _needs_multi_search(query) and results:
            # Second search with a different angle for complex queries
            alt_query = f"{ddg_query} analysis expert opinion"
```

Replace with:

```python
        if tier in ("research", "contested") and results:
            alt_query = (
                f"{ddg_query} analysis expert opinion"
                if tier == "research"
                else f"{ddg_query} alternative perspective drawbacks"
            )
```

**C. After `results = _filter_results(results)`, run the confidence gate and branch**:

Find this block after `clear_status()`:

```python
    results = _filter_results(results)

    if not results:
        print("\033[90mNo results found.\033[0m")
        return [], ""

    domains = " · ".join(r["domain"].removeprefix("www.") for r in results[:3])
    print_header(query.capitalize(), f"{domains}  ({len(results)} results)")
    ...
    print_status("↳ thinking...")
    prompt = build_search_prompt(query, results)
    # Prepend session context so the model can use what it already learned this session
    session_ctx = format_session_context()
    if session_ctx:
        prompt = f"{session_ctx}\n\n{prompt}"
    _t0 = time.time()
    stream = stream_ai(prompt, SEARCH_SYSTEM)
    clear_status()

    response = stream_to_terminal(stream)
    _elapsed = time.time() - _t0
```

Replace the block from `print_status("↳ thinking...")` through `response = stream_to_terminal(stream)` and `_elapsed` assignment with:

```python
    # Adaptive confidence gate — may escalate tier based on snippet quality
    tier = _confidence_gate(query, results, tier)

    # Build base prompt (used by all tiers)
    base_prompt = build_search_prompt(query, results)
    session_ctx = format_session_context()
    if session_ctx:
        base_prompt = f"{session_ctx}\n\n{base_prompt}"

    _t0 = time.time()

    if tier in ("current", "research", "contested"):
        # Deep path: fetch real article content
        print_status("↳ thinking...")
        deep_content, deep_sources = _deep_research(query, tier, results, ddg_query)

        if deep_content:
            source_count = len(deep_sources)
            print_status(f"↳ synthesizing {source_count} source{'s' if source_count != 1 else ''}...")
            prompt = base_prompt + f"\n\nFull article content from {source_count} source(s):\n{deep_content}"
            system = {
                "current":   SEARCH_SYSTEM_CURRENT,
                "research":  SEARCH_SYSTEM_RESEARCH,
                "contested": SEARCH_SYSTEM_CONTESTED,
            }[tier]
        else:
            # All reads failed — fall back to snippet path gracefully
            prompt = base_prompt
            system = SEARCH_SYSTEM
            deep_sources = []

        clear_status()
        stream = stream_ai(prompt, system)
        response = stream_to_terminal(stream)

        # Use deep_sources for the linked sources line if available
        if deep_sources:
            results = deep_sources + [r for r in results if r not in deep_sources][:2]
    else:
        # Snippet path (fast — existing behavior)
        print_status("↳ thinking...")
        clear_status()
        stream = stream_ai(base_prompt, SEARCH_SYSTEM)
        response = stream_to_terminal(stream)

    _elapsed = time.time() - _t0
```

- [ ] **Step 4: Run all tests — full suite should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -v
```

Expected: `42 passed` (39 existing + 3 new integration tests)

- [ ] **Step 5: Quick smoke test — run surf on the exact query that failed**

```bash
surf who will win the UEFA champions league
```

Expected: you see `↳ searching: "UEFA Champions League 2026 Final Predictions"...` followed by `↳ reading espn.com...` (or similar authoritative sports domain) and an answer that mentions PSG and Arsenal specifically.

- [ ] **Step 6: Smoke test snippet path (fast, no deep reads)**

```bash
surf who wrote Pride and Prejudice
```

Expected: answer in ~3s with no `↳ reading` lines — just `↳ thinking...` then instant answer.

- [ ] **Step 7: Commit and push**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: adaptive search — tier routing, confidence gate, deep reading pipeline

Search now adapts depth to query type:
- snippet tier: DDG snippets → answer (fast, existing behavior)
- current tier: escalated for temporal/prediction queries — reads top 2-3 sources
- research tier: multi-angle search + source reading for explanatory queries
- contested tier: dual-angle search + source reading for comparison queries

_confidence_gate escalates snippet tier when freshness, coverage, or source
authority checks fail. _deep_research shows transparent per-source progress
(↳ reading espn.com...) and falls back gracefully if reads fail.

_needs_multi_search replaced by tier-in-(research,contested) check." && git push
```

---

## Self-Review

**Spec coverage check:**
- ✅ Five tiers defined with signals (Task 1)
- ✅ `_classify_tier` — heuristic classification (Task 2)
- ✅ `_confidence_gate` — coverage/freshness/authority checks (Task 3)
- ✅ `_deep_research` — fetch sources, transparent progress, 3-source cap (Task 4)
- ✅ `SOURCE_HIERARCHY` with sports added to `_identify_entity_type` (Task 1)
- ✅ Tier-specific synthesis prompts (Task 1)
- ✅ `search_flow` integration — tier routing, gate, deep path, prompt selection (Task 5)
- ✅ `_needs_multi_search` replaced by tier logic (Task 5)
- ✅ Fallback to snippet path when all reads fail (Task 5, Step 3C)
- ✅ deep_sources used for linked sources line (Task 5, Step 3C)
- ✅ Progress UX: `↳ reading domain.com...` per source (Task 4)

**Type consistency check:** `_deep_research` returns `tuple[str, list[dict]]` — matches usage in Task 5 where `deep_content, deep_sources = _deep_research(...)`. `_confidence_gate` returns `str` — matches `tier = _confidence_gate(...)`.

**No placeholders:** All code blocks are complete. No TBD/TODO.
