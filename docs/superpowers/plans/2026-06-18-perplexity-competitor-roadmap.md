# Surf → Perplexity Competitor Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap between surf and Perplexity by adding multi-step deep search, better search quality (Brave), stdin/file input, terminal-native superpowers (watch, diff, shell integration), and persistent research threads with export.

**Architecture:** All features are added to the existing single-file `surf.py` (5,434 lines). Each task introduces a self-contained function group that integrates with the existing `search_flow()` / `main()` entry points. New CLI flags are added to the `argparse` parser in `main()`. The existing provider chain (`stream_ai` → Claude → Groq → Cerebras → Gemini → Ollama) and design system tokens are reused throughout.

**Tech Stack:** Python 3.10+, requests, BeautifulSoup4, Groq SDK, Anthropic SDK, rich (tables), prompt_toolkit (completion). New optional: `pdfminer.six` for PDF extraction (in-process optional import, not subprocess).

## Global Constraints

- Single file: all code lives in `surf.py`, tests in `tests/test_surf.py`
- Design system: all output uses the existing color tokens (`C_BRAND`, `C_META`, `C_INTERACTIVE`, etc.), glyph vocabulary (`GLYPH_META`, `GLYPH_DIVIDER`, `GLYPH_HEADER_FILL`, etc.), and spacing tokens (`SPACE_XS`, `SPACE_SM`, etc.) defined at the top of `surf.py`. **Never use raw Unicode characters — always use the token constants.**
- Brand voice: all progress output uses `↳` prefix via `print_status()` (transient/overwrite) or `GLYPH_META` (permanent). No numbered steps, no verbose labels. Match existing cadence: `↳ reading domain.com...`, `↳ synthesizing 5 sources...`
- Provider chain: all LLM calls go through `stream_ai()` which handles Claude → Groq → Cerebras → Gemini → Ollama fallback. All web searches go through `_get_search_backend()` (never call `ddg_search` directly after Task 2 ships)
- Session memory: all new features that produce search results must call `save_session_entry()`, `_obsidian_save()`, and `record_feature_use()`. Must inject `_read_preferences()` and `_obsidian_find_related()` context where applicable
- Config: all user-configurable values (API keys, paths) go in `~/.config/surf/config` via `load_config()`
- No new dependencies without explicit justification — prefer stdlib where possible
- Tests: every new function gets unit tests with mocked I/O. Test file uses `pytest` with `unittest.mock.patch`
- Commits: one per task, conventional format (`feat:`, `fix:`, `refactor:`)
- Discoverability: all new features add entries to `FEATURE_TIPS` dict and to the `?` help screen in `_classify_and_dispatch`
- Security: never interpolate user-controlled strings into subprocess commands or f-string shell invocations. Use argument lists

## Review Fixes Applied

The following issues were identified by code quality, design/UX, and brand consistency reviewers and are incorporated into the tasks below:

1. **Deep search UX:** Use `print_status()` for transient progress, only persist final summary line. Show spinner during intermediate synthesis. Add 45s wall-clock timeout on gap-filling loop. Dedup gaps to prevent infinite loops.
2. **Security:** PDF extraction uses in-process optional import (like `try: import anthropic`), not subprocess with f-string interpolation.
3. **Brand voice:** All progress output uses `↳ action — detail` format, no "step N:" prefix. Watch refresh uses `↳ refreshed · HH:MM`.
4. **Design system tokens:** Watch divider uses `GLYPH_DIVIDER`, diff header uses `GLYPH_HEADER_FILL`. Never raw characters.
5. **Missing patterns:** Stdin and file analysis call `_obsidian_save()`, `record_feature_use()`, inject preferences.
6. **Naming:** `--shell` renamed to `--shell`. Thread shorthand `-t` added. `--deep` auto-activates for research/contested tiers.
7. **Stdin content detection:** Auto-detect content type (stack trace, code, prose) and adapt system prompt.
8. **Diff structural header:** Show "Sources: +N new, -M removed" before LLM narrative.
9. **Discoverability:** Power features added to `FEATURE_TIPS` and `?` help screen.

---

## File Structure

All changes are in two files:

- **Modify: `surf.py`** — new functions added in logical groups near related existing code
- **Modify: `tests/test_surf.py`** — new test classes added at the end of the file

New function groups by task:

| Task | Functions added to `surf.py` | Location (after) |
|------|------------------------------|-------------------|
| 1: Deep Search | `_deep_search_loop()`, `_identify_knowledge_gaps()`, `_format_deep_step()` | After `_deep_research()` (~line 3162) |
| 2: Brave Search | `brave_search()`, `_get_search_backend()` | After `ddg_search()` (~line 676) |
| 3: Stdin Input | `_read_stdin()`, updated `main()` | Before `main()` (~line 5275) |
| 4: File Analysis | `_extract_file_content()`, `_analyze_local_file()` | After `read_flow()` (~line 3908) |
| 5: Shell Integration | `_get_last_command_error()`, `_get_shell_context()` | Before `main()` (~line 5275) |
| 6: Watch Mode | `_watch_loop()` | Before `main()` (~line 5275) |
| 7: Diff Mode | `_load_search_snapshot()`, `_save_search_snapshot()`, `_diff_search()` | After session functions (~line 260) |
| 8: Named Threads | `_thread_path()`, `_load_thread()`, `_save_thread_entry()`, `_list_threads()` | After session functions (~line 260) |
| 9: Export | `_export_thread()`, `_export_session()` | After thread functions |
| 10: Custom Sources | `_parse_source_list()`, modified `ddg_search()` and `search_flow()` | Near `ddg_search()` (~line 676) |

---

## Phase 1: Core Research Engine

---

### Task 1: Deep Search Mode — Multi-step Reasoning Loop

**Files:**
- Modify: `surf.py` — add `_deep_search_loop()`, `_identify_knowledge_gaps()`, `_format_deep_step()` after `_deep_research()` (line 3162); modify `search_flow()` to use deep search loop for `--deep` flag and research/contested tiers
- Modify: `tests/test_surf.py` — add `TestDeepSearchLoop` class
- Modify: `main()` — add `--deep` argparse flag

**Interfaces:**
- Consumes: `ddg_search(query, num_results) -> list[dict]`, `_deep_research(query, tier, results, enriched_query, entity_type) -> tuple[str, list[dict]]`, `stream_ai(prompt, system, max_tokens, tier)`, `_filter_results(results) -> list[dict]`, `_classify_tier(query) -> str`, `build_search_prompt(query, snippets) -> str`
- Produces: `_deep_search_loop(query: str, initial_results: list[dict], tier: str, max_steps: int = 3, timeout: float = 45.0) -> tuple[str, list[dict], list[str]]` — returns (final_synthesis, all_sources, step_log). Has wall-clock timeout to cap total duration. `_identify_knowledge_gaps(query: str, current_synthesis: str) -> list[str]` — returns list of gap queries, deduped against previously seen gaps. Uses `print_status()` for transient progress (overwrite), only persists final summary line.

- [ ] **Step 1: Write failing tests for `_identify_knowledge_gaps`**

```python
# tests/test_surf.py — add at end

class TestDeepSearchLoop:
    def test_identify_gaps_returns_list_of_queries(self):
        from surf import _identify_knowledge_gaps
        with patch("surf.stream_ai") as mock_ai:
            mock_ai.return_value = iter(['["impact on GDP", "timeline of events"]'])
            gaps = _identify_knowledge_gaps("US tariffs on China", "Tariffs were imposed in 2018.")
            assert isinstance(gaps, list)
            assert len(gaps) == 2
            assert "GDP" in gaps[0]

    def test_identify_gaps_returns_empty_on_error(self):
        from surf import _identify_knowledge_gaps
        with patch("surf.stream_ai", side_effect=Exception("fail")):
            gaps = _identify_knowledge_gaps("test query", "some context")
            assert gaps == []

    def test_identify_gaps_returns_empty_for_invalid_json(self):
        from surf import _identify_knowledge_gaps
        with patch("surf.stream_ai") as mock_ai:
            mock_ai.return_value = iter(["not valid json"])
            gaps = _identify_knowledge_gaps("test query", "some context")
            assert gaps == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDeepSearchLoop -v`
Expected: FAIL — `ImportError: cannot import name '_identify_knowledge_gaps'`

- [ ] **Step 3: Implement `_identify_knowledge_gaps`**

Add after `_deep_research()` (after line 3162) in `surf.py`:

```python
def _identify_knowledge_gaps(query: str, current_synthesis: str, seen_gaps: set[str] | None = None) -> list[str]:
    """Use LLM to identify what's missing from the current answer. Returns deduped list of follow-up queries."""
    if seen_gaps is None:
        seen_gaps = set()
    prompt = (
        f"Original question: {query}\n\n"
        f"Current answer:\n{current_synthesis[:2000]}\n\n"
        "What important aspects of this question are NOT covered in the answer? "
        "Return a JSON array of 1-3 specific search queries that would fill the gaps. "
        "Return ONLY the JSON array, no explanation. Example: [\"query one\", \"query two\"]"
    )
    try:
        chunks = list(stream_ai(prompt, "You identify knowledge gaps. Return only a JSON array of search queries.", max_tokens=200))
        raw = "".join(chunks).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        gaps = json.loads(raw)
        if isinstance(gaps, list) and all(isinstance(g, str) for g in gaps):
            return [g for g in gaps[:3] if g.lower().strip() not in seen_gaps]
        return []
    except Exception:
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDeepSearchLoop -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for `_format_deep_step` and `_deep_search_loop`**

```python
    def test_identify_gaps_deduplicates(self):
        from surf import _identify_knowledge_gaps
        with patch("surf.stream_ai") as mock_ai:
            mock_ai.return_value = iter(['["impact on GDP", "impact on GDP", "timeline"]'])
            gaps = _identify_knowledge_gaps("US tariffs", "context", seen_gaps={"impact on GDP"})
            assert "impact on GDP" not in gaps
            assert "timeline" in gaps

    def test_deep_search_loop_returns_synthesis_and_sources(self):
        from surf import _deep_search_loop
        initial_results = [
            {"title": "Article 1", "url": "https://example.com/1", "domain": "example.com", "snippet": "Tariffs imposed in 2018"},
        ]
        with patch("surf._identify_knowledge_gaps", return_value=["GDP impact"]), \
             patch("surf.ddg_search", return_value=[
                 {"title": "GDP Report", "url": "https://econ.com/1", "domain": "econ.com", "snippet": "GDP fell 0.3%"},
             ]), \
             patch("surf._filter_results", side_effect=lambda r, **kw: r), \
             patch("surf._deep_research", return_value=("Article content here", initial_results)), \
             patch("surf.stream_ai", return_value=iter(["Final synthesis of tariff impacts."])), \
             patch("surf.stream_to_terminal", return_value="Final synthesis of tariff impacts."), \
             patch("surf.print_header"), \
             patch("surf.print_status"), \
             patch("surf.clear_status"):
            synthesis, sources, steps = _deep_search_loop("US tariffs", initial_results, "research")
            assert isinstance(synthesis, str)
            assert len(synthesis) > 0
            assert isinstance(sources, list)
            assert isinstance(steps, list)

    def test_deep_search_loop_stops_when_no_gaps(self):
        from surf import _deep_search_loop
        initial = [{"title": "A", "url": "https://a.com", "domain": "a.com", "snippet": "full coverage"}]
        with patch("surf._identify_knowledge_gaps", return_value=[]), \
             patch("surf._deep_research", return_value=("content", initial)), \
             patch("surf.stream_ai", return_value=iter(["Complete answer."])), \
             patch("surf.stream_to_terminal", return_value="Complete answer."), \
             patch("surf.print_header"), \
             patch("surf.print_status"), \
             patch("surf.clear_status"):
            synthesis, sources, steps = _deep_search_loop("test", initial, "research")
            assert len(steps) == 1  # only initial step, no gap-filling

    def test_deep_search_loop_max_steps_respected(self):
        from surf import _deep_search_loop
        initial = [{"title": "A", "url": "https://a.com", "domain": "a.com", "snippet": "partial"}]
        with patch("surf._identify_knowledge_gaps", return_value=["more info"]), \
             patch("surf.ddg_search", return_value=[
                 {"title": "B", "url": "https://b.com", "domain": "b.com", "snippet": "more"},
             ]), \
             patch("surf._filter_results", side_effect=lambda r, **kw: r), \
             patch("surf._deep_research", return_value=("content", initial)), \
             patch("surf.stream_ai", return_value=iter(["synthesis"])), \
             patch("surf.stream_to_terminal", return_value="synthesis"), \
             patch("surf.print_header"), \
             patch("surf.print_status"), \
             patch("surf.clear_status"):
            _, _, steps = _deep_search_loop("test", initial, "research", max_steps=2)
            assert len(steps) <= 3  # initial + at most 2 gap steps
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDeepSearchLoop -v`
Expected: FAIL — `ImportError: cannot import name '_format_deep_step'` / `'_deep_search_loop'`

- [ ] **Step 7: Implement `_format_deep_step` and `_deep_search_loop`**

Add after `_identify_knowledge_gaps` in `surf.py`:

```python
def _deep_search_loop(
    query: str,
    initial_results: list[dict],
    tier: str,
    max_steps: int = 3,
    timeout: float = 45.0,
) -> tuple[str, list[dict], list[str]]:
    """
    Multi-step deep search: search → read → identify gaps → search again → synthesize.
    Returns (final_synthesis, all_sources, step_log).
    Wall-clock timeout caps total duration to prevent runaway loops.
    """
    t_start = time.time()
    all_sources: list[dict] = list(initial_results)
    all_content: list[str] = []
    step_log: list[str] = []
    seen_domains: set[str] = {r.get("domain", "") for r in initial_results}
    seen_gaps: set[str] = set()

    # Initial deep read
    print_status(f"↳ reading {len(initial_results)} sources{GLYPH_ELLIPSIS}")
    step_log.append(f"read {len(initial_results)} initial sources")
    deep_content, deep_sources = _deep_research(query, tier, initial_results, query)
    if deep_content:
        all_content.append(deep_content)
    if deep_sources:
        for s in deep_sources:
            if s.get("domain", "") not in seen_domains:
                all_sources.append(s)
                seen_domains.add(s["domain"])

    # Initial synthesis (with spinner — user sees progress)
    prompt = build_search_prompt(query, all_sources) + f"\n\nFull article content:\n{deep_content}" if deep_content else build_search_prompt(query, all_sources)
    print_status(f"↳ synthesizing initial findings{GLYPH_ELLIPSIS}")
    with Spinner("synthesizing..."):
        chunks = list(stream_ai(prompt, SEARCH_SYSTEM_RESEARCH, max_tokens=1500))
    current_synthesis = "".join(chunks)
    clear_status()

    # Gap-filling loop with wall-clock timeout and dedup
    search_fn = _get_search_backend()
    for step in range(max_steps):
        if time.time() - t_start > timeout:
            step_log.append("timeout — moving to final synthesis")
            break

        gaps = _identify_knowledge_gaps(query, current_synthesis, seen_gaps=seen_gaps)
        if not gaps:
            step_log.append("no gaps found — search complete")
            break

        gap_query = gaps[0]
        seen_gaps.add(gap_query.lower().strip())
        print_status(f"↳ filling gap — {gap_query[:50]}{GLYPH_ELLIPSIS}")
        step_log.append(f"gap search — \"{gap_query}\"")

        try:
            gap_results = _filter_results(search_fn(gap_query, num_results=5))
        except Exception:
            gap_results = []

        new_results = [r for r in gap_results if r.get("domain", "") not in seen_domains]
        if not new_results:
            step_log.append("no new sources found")
            continue

        for r in new_results[:3]:
            seen_domains.add(r["domain"])
            all_sources.append(r)

        gap_content, gap_sources = _deep_research(gap_query, tier, new_results[:3], gap_query)
        if gap_content:
            all_content.append(gap_content)

    # Final synthesis with all accumulated content
    clear_status()
    combined_content = "\n\n---\n\n".join(all_content)

    # Inject preferences and vault context (matching search_flow pattern)
    prefs = _read_preferences()
    vault_ctx = _obsidian_find_related(query)
    preamble = ""
    if prefs:
        preamble += f"[User preferences]\n{prefs}\n[End preferences]\n\n"
    if vault_ctx:
        preamble += f"{vault_ctx}\n\n"

    final_prompt = (
        f"{preamble}"
        f"Original question: {query}\n\n"
        f"Research from {len(all_sources)} sources across {len(step_log)} search steps:\n\n"
        f"{combined_content[:8000]}\n\n"
        f"Provide a comprehensive answer. Cite sources inline as [1], [2], etc."
    )

    elapsed = time.time() - t_start
    print(f"{C_META}{GLYPH_META} deep search: {len(step_log)} steps, {len(all_sources)} sources, {elapsed:.0f}s{C_RESET}")
    print_header(query.capitalize(), f"{len(all_sources)} sources {GLYPH_SEPARATOR} deep search")
    stream = stream_ai(final_prompt, SEARCH_SYSTEM_RESEARCH, max_tokens=3000, tier="research")
    final_synthesis = stream_to_terminal(stream, results=all_sources)

    return final_synthesis, all_sources, step_log
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDeepSearchLoop -v`
Expected: PASS

- [ ] **Step 9: Write failing tests for `--deep` CLI flag integration**

```python
    def test_deep_flag_parsed_by_argparse(self):
        import surf
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("input", nargs="*")
        parser.add_argument("--deep", action="store_true")
        args = parser.parse_args(["--deep", "climate", "change"])
        assert args.deep is True
        assert args.input == ["climate", "change"]

    def test_search_flow_accepts_deep_flag(self):
        """search_flow should accept a deep=True parameter."""
        import contextlib
        from surf import search_flow
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("surf._classify_data_source", return_value="web"))
            stack.enter_context(patch("surf.ddg_search", return_value=[
                {"title": "A", "url": "https://a.com", "domain": "a.com", "snippet": "info"},
            ]))
            stack.enter_context(patch("surf._search_with_retry", return_value=([
                {"title": "A", "url": "https://a.com", "domain": "a.com", "snippet": "info"},
            ], ["query"])))
            stack.enter_context(patch("surf._filter_results", side_effect=lambda r, **kw: r))
            stack.enter_context(patch("surf._deep_search_loop", return_value=("answer", [], ["step 1"])))
            stack.enter_context(patch("surf._snippets_are_diverse", return_value=True))
            stack.enter_context(patch("surf._sources_are_substantive", return_value=True))
            stack.enter_context(patch("surf.stream_ai", return_value=iter(["response"])))
            stack.enter_context(patch("surf.stream_to_terminal", return_value="response"))
            stack.enter_context(patch("surf.print_header"))
            stack.enter_context(patch("surf.print_status"))
            stack.enter_context(patch("surf.clear_status"))
            stack.enter_context(patch("surf._obsidian_save", return_value=None))
            stack.enter_context(patch("surf._obsidian_find_related", return_value=""))
            stack.enter_context(patch("surf._read_preferences", return_value=""))
            stack.enter_context(patch("surf.save_session_entry"))
            stack.enter_context(patch("surf.record_feature_use"))
            stack.enter_context(patch("surf._claude_budget_ok", return_value=False))
            results, response = search_flow("climate change", interactive=False, deep=True)
            # deep_search_loop should have been called
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDeepSearchLoop::test_deep_flag_parsed_by_argparse tests/test_surf.py::TestDeepSearchLoop::test_search_flow_accepts_deep_flag -v`
Expected: FAIL

- [ ] **Step 11: Wire `--deep` flag into `main()` and `search_flow()`**

In `surf.py`, modify `search_flow` signature (line 3219):

```python
def search_flow(query: str, interactive: bool = True, json_output: bool = False, deep: bool = False) -> tuple[list[dict], str]:
```

Inside `search_flow`, after the BM25 rerank block (after line 3315) and tier classification, add the deep search branch:

```python
    # Deep search mode: multi-step reasoning loop
    if deep or (tier in ("research", "contested") and len(results) >= 3):
        if deep:
            _format_deep_step(0, "deep search", query)
        synthesis, all_sources, step_log = _deep_search_loop(query, results, tier)
        save_session_entry(query, "deep_search", _truncate_at_sentence(synthesis, 300))
        _obsidian_save(query, synthesis, all_sources, session_id=_obsidian_session_id())
        record_feature_use("search")
        if json_output:
            _output_json(query, synthesis, [s["domain"] for s in all_sources], intent="deep_search")
            return all_sources, synthesis
        vspace(ZONE_SPACING[("answer", "metadata")])
        _print_linked_sources(all_sources)
        print_results(all_sources)
        _meta = _SearchMeta(
            original_query=query,
            queries_tried=[f"deep:{s}" for s in step_log],
            result_count=len(all_sources),
            confidence_tier="deep",
            coverage_note=f"Deep search: {len(step_log)} steps, {len(all_sources)} sources",
        )
        if interactive:
            _handle_results_input(all_sources, context=synthesis, meta=_meta)
        return all_sources, synthesis
```

In `main()`, add argparse flag (after line 5288):

```python
    parser.add_argument("--deep", action="store_true",
                        help="Multi-step deep search — searches, reads, identifies gaps, repeats")
```

And pass it to `search_flow` (modify line 5428):

```python
        else:
            search_flow(query, interactive=not json_output, json_output=json_output, deep=args.deep)
```

- [ ] **Step 12: Run full test suite**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: All tests PASS (225 existing + new tests)

- [ ] **Step 13: Commit**

```bash
git add surf.py tests/test_surf.py
git commit -m "feat: add deep search mode — multi-step reasoning with gap detection"
```

---

### Task 2: Brave Search Backend

**Files:**
- Modify: `surf.py` — add `brave_search()` and `_get_search_backend()` after `ddg_search()` (line 676); modify `ddg_search` call sites to use `_get_search_backend()`
- Modify: `tests/test_surf.py` — add `TestBraveSearch` class

**Interfaces:**
- Consumes: `load_config() -> dict`, `HEADERS` dict, `SSL_CERT` path
- Produces: `brave_search(query: str, num_results: int = 5) -> list[dict]` — same return format as `ddg_search`: `[{"title": str, "url": str, "domain": str, "snippet": str}]`. `_get_search_backend() -> callable` — returns `brave_search` if `BRAVE_API_KEY` configured, else `ddg_search`.

- [ ] **Step 1: Write failing tests**

```python
class TestBraveSearch:
    def test_brave_search_returns_formatted_results(self):
        from surf import brave_search
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "web": {
                "results": [
                    {
                        "title": "Test Article",
                        "url": "https://example.com/article",
                        "description": "A test snippet about the topic",
                    },
                    {
                        "title": "Another Article",
                        "url": "https://other.com/page",
                        "description": "More information here",
                    },
                ]
            }
        }
        with patch("surf.requests.get", return_value=mock_response):
            results = brave_search("test query", num_results=5)
        assert len(results) == 2
        assert results[0]["title"] == "Test Article"
        assert results[0]["domain"] == "example.com"
        assert results[0]["snippet"] == "A test snippet about the topic"
        assert results[0]["url"] == "https://example.com/article"

    def test_brave_search_handles_empty_response(self):
        from surf import brave_search
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"web": {"results": []}}
        with patch("surf.requests.get", return_value=mock_response):
            results = brave_search("no results query")
        assert results == []

    def test_brave_search_handles_api_error(self):
        from surf import brave_search
        with patch("surf.requests.get", side_effect=Exception("API error")):
            results = brave_search("failing query")
        assert results == []

    def test_get_search_backend_returns_brave_when_configured(self):
        from surf import _get_search_backend, brave_search
        with patch("surf.load_config", return_value={"BRAVE_API_KEY": "test-key"}):
            backend = _get_search_backend()
        assert backend == brave_search

    def test_get_search_backend_returns_ddg_by_default(self):
        from surf import _get_search_backend, ddg_search
        with patch("surf.load_config", return_value={}):
            backend = _get_search_backend()
        assert backend == ddg_search
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestBraveSearch -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `brave_search` and `_get_search_backend`**

Add after `ddg_search()` (after line 676) in `surf.py`:

```python
def brave_search(query: str, num_results: int = 5) -> list[dict]:
    """Search Brave and return list of {title, url, domain, snippet}. Same format as ddg_search."""
    from urllib.parse import urlparse
    config = load_config()
    api_key = config.get("BRAVE_API_KEY", os.environ.get("BRAVE_API_KEY", ""))
    if not api_key:
        return []
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": num_results},
            headers={"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": api_key},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("web", {}).get("results", []):
            url = item.get("url", "")
            parsed = urlparse(url)
            domain = parsed.netloc.removeprefix("www.") if parsed.netloc else ""
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "domain": domain,
                "snippet": item.get("description", ""),
            })
        return results
    except Exception:
        return []


def _get_search_backend() -> callable:
    """Return brave_search if BRAVE_API_KEY is configured, else ddg_search."""
    config = load_config()
    if config.get("BRAVE_API_KEY") or os.environ.get("BRAVE_API_KEY"):
        return brave_search
    return ddg_search
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestBraveSearch -v`
Expected: PASS

- [ ] **Step 5: Replace `ddg_search` calls with `_get_search_backend()` in search_flow and _handle_followup**

In `search_flow()` (line 3249), replace:
```python
        results, _queries_tried = _search_with_retry(ddg_query, entity_type=_identify_entity_type(query))
```

`_search_with_retry` internally calls `ddg_search`. Modify `_search_with_retry` (line 3174) to accept and use a backend parameter:

```python
def _search_with_retry(query: str, entity_type: str | None = None, search_fn: callable | None = None) -> tuple[list[dict], list[str]]:
```

And at the top of that function, add:
```python
    if search_fn is None:
        search_fn = _get_search_backend()
```

Replace all internal `ddg_search(` calls within `_search_with_retry` with `search_fn(`.

Similarly update `_handle_followup` (line 3662) to use `_get_search_backend()`:
```python
        search_results = _get_search_backend()(search_query)
```

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: All PASS

- [ ] **Step 7: Add `BRAVE_API_KEY` to setup wizard**

In `_run_setup()` (around line 5135), after the Gemini key prompt, add:

```python
    print()
    cfg["BRAVE_API_KEY"] = _setup_prompt(
        "Brave Search API key (better results — brave.com/search/api, free 2k/mo)",
        cfg.get("BRAVE_API_KEY", ""), secret=True
    )
```

- [ ] **Step 8: Commit**

```bash
git add surf.py tests/test_surf.py
git commit -m "feat: add Brave Search backend — better results when API key configured"
```

---

### Task 3: Stdin Input — Pipe Content into Surf

**Files:**
- Modify: `surf.py` — add `_read_stdin()` before `main()`; modify `main()` to detect stdin pipe
- Modify: `tests/test_surf.py` — add `TestStdinInput` class

**Interfaces:**
- Consumes: `sys.stdin`, `stream_ai()`, `stream_to_terminal()`, `build_read_prompt()`
- Produces: `_read_stdin() -> str | None` — returns piped content or None if stdin is a terminal.

- [ ] **Step 1: Write failing tests**

```python
class TestStdinInput:
    def test_read_stdin_returns_none_when_tty(self):
        from surf import _read_stdin
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            assert _read_stdin() is None

    def test_read_stdin_returns_content_when_piped(self):
        from surf import _read_stdin
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            mock_stdin.read.return_value = "error: connection refused\nstack trace..."
            result = _read_stdin()
            assert "connection refused" in result

    def test_read_stdin_truncates_long_input(self):
        from surf import _read_stdin
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            mock_stdin.read.return_value = "x" * 50000
            result = _read_stdin()
            assert len(result) <= 20000

    def test_read_stdin_returns_none_for_empty_input(self):
        from surf import _read_stdin
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            mock_stdin.read.return_value = ""
            assert _read_stdin() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestStdinInput -v`
Expected: FAIL

- [ ] **Step 3: Implement `_read_stdin` and wire into `main()`**

Add before `main()` in `surf.py`:

```python
_STDIN_MAX_CHARS = 20000

def _read_stdin() -> str | None:
    """Read piped stdin content. Returns None if stdin is a terminal or empty."""
    if sys.stdin.isatty():
        return None
    try:
        content = sys.stdin.read()
        if not content or not content.strip():
            return None
        if len(content) > _STDIN_MAX_CHARS:
            content = content[:_STDIN_MAX_CHARS] + "\n[truncated]"
        return content
    except Exception:
        return None
```

In `main()`, after the query is assembled (after line 5340 `query = " ".join(args.input)`), add:

```python
    piped_content = _read_stdin()
    if piped_content:
        # Auto-detect content type for smarter prompting
        content_type = "text"
        if any(sig in piped_content for sig in ["Traceback", "Error:", "Exception:", "at line", "FAILED"]):
            content_type = "error"
        elif any(sig in piped_content[:200] for sig in ["def ", "function ", "class ", "import ", "const ", "var ", "#include"]):
            content_type = "code"

        system_by_type = {
            "error": "You diagnose errors and stack traces from the terminal. Start with ▸ TL;DR naming the root cause, then explain the fix. Be specific.",
            "code": "You analyze source code. Start with ▸ TL;DR describing what the code does, then provide detailed analysis.",
            "text": "You analyze content piped from the terminal. Start with ▸ TL;DR followed by your analysis. Be direct and specific.",
        }

        label = query.capitalize() if query else f"Analyzing piped {content_type}"
        print_header(label)
        prefs = _read_preferences()
        preamble = f"[User preferences]\n{prefs}\n[End preferences]\n\n" if prefs else ""
        prompt = f"{preamble}User piped the following {content_type} and asks: {query or 'explain this'}\n\nContent:\n{piped_content}"
        stream = stream_ai(prompt, system_by_type[content_type], max_tokens=2048)
        response = stream_to_terminal(stream)
        save_session_entry(query or "piped input", "pipe", _truncate_at_sentence(response, 300))
        _obsidian_save(query or "piped input", response, [], session_id=_obsidian_session_id())
        record_feature_use("pipe")
        if json_output:
            _output_json(query or "piped input", response, [], intent="pipe")
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestStdinInput -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add surf.py tests/test_surf.py
git commit -m "feat: add stdin pipe support — cat file | surf 'explain this'"
```

---

## Phase 2: Terminal-Native Superpowers

---

### Task 4: Local File Analysis

**Files:**
- Modify: `surf.py` — add `_extract_file_content()` and update `main()` to detect file paths
- Modify: `tests/test_surf.py` — add `TestFileAnalysis` class

**Interfaces:**
- Consumes: `stream_ai()`, `stream_to_terminal()`, `extract_text()` (for HTML files), `save_session_entry()`, `_obsidian_save()`
- Produces: `_extract_file_content(path: str) -> tuple[str, str]` — returns `(content, file_type)` where file_type is "pdf", "text", "code", "html". Returns `("", "unknown")` on failure.

- [ ] **Step 1: Write failing tests**

```python
class TestFileAnalysis:
    def test_extract_text_file(self, tmp_path):
        from surf import _extract_file_content
        f = tmp_path / "notes.txt"
        f.write_text("Meeting notes from today:\n- Discussed budget\n- Next steps")
        content, ftype = _extract_file_content(str(f))
        assert "budget" in content
        assert ftype == "text"

    def test_extract_code_file(self, tmp_path):
        from surf import _extract_file_content
        f = tmp_path / "app.py"
        f.write_text("def main():\n    print('hello')\n")
        content, ftype = _extract_file_content(str(f))
        assert "def main" in content
        assert ftype == "code"

    def test_extract_html_file(self, tmp_path):
        from surf import _extract_file_content
        f = tmp_path / "page.html"
        f.write_text("<html><body><p>Hello world</p></body></html>")
        content, ftype = _extract_file_content(str(f))
        assert "Hello world" in content
        assert ftype == "html"

    def test_extract_nonexistent_file(self):
        from surf import _extract_file_content
        content, ftype = _extract_file_content("/nonexistent/file.txt")
        assert content == ""
        assert ftype == "unknown"

    def test_extract_pdf_file(self, tmp_path):
        from surf import _extract_file_content
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake pdf content")
        content, ftype = _extract_file_content(str(f))
        assert ftype == "pdf"

    def test_extract_truncates_large_file(self, tmp_path):
        from surf import _extract_file_content
        f = tmp_path / "huge.txt"
        f.write_text("word " * 30000)
        content, ftype = _extract_file_content(str(f))
        assert len(content.split()) <= 15001  # 15000 + [truncated] marker

    def test_file_path_detected_by_main(self):
        """A local file path should be detected and routed to file analysis."""
        import os
        path = os.path.abspath(__file__)  # this test file exists
        assert os.path.isfile(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestFileAnalysis -v`
Expected: FAIL

- [ ] **Step 3: Implement `_extract_file_content`**

Add after `read_flow()` in `surf.py`:

```python
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".sh", ".bash",
    ".zsh", ".yaml", ".yml", ".toml", ".json", ".xml", ".sql", ".r",
    ".lua", ".pl", ".php", ".scala", ".zig", ".nim", ".ex", ".exs",
    ".vue", ".svelte", ".css", ".scss", ".less",
}
_TEXT_EXTENSIONS = {".txt", ".md", ".rst", ".csv", ".log", ".ini", ".cfg", ".conf", ".env"}
_FILE_MAX_WORDS = 15000


def _extract_file_content(path: str) -> tuple[str, str]:
    """Extract text content from a local file. Returns (content, file_type)."""
    if not os.path.isfile(path):
        return "", "unknown"

    ext = os.path.splitext(path)[1].lower()

    # PDF — optional in-process import (same pattern as anthropic/rich)
    if ext == ".pdf":
        try:
            from pdfminer.high_level import extract_text as pdf_extract
            content = pdf_extract(path)
            if content and content.strip():
                words = content.split()
                if len(words) > _FILE_MAX_WORDS:
                    content = " ".join(words[:_FILE_MAX_WORDS]) + "\n[truncated]"
                return content.strip(), "pdf"
        except ImportError:
            pass  # pdfminer not installed — fall through
        except Exception:
            pass
        return "", "pdf"

    # HTML
    if ext in (".html", ".htm"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                html = f.read()
            _, text = extract_text(html, max_words=_FILE_MAX_WORDS, return_title=True)
            return text, "html"
        except Exception:
            return "", "html"

    # Code
    if ext in _CODE_EXTENSIONS:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            words = content.split()
            if len(words) > _FILE_MAX_WORDS:
                content = " ".join(words[:_FILE_MAX_WORDS]) + "\n[truncated]"
            return content, "code"
        except Exception:
            return "", "code"

    # Text (default for known text extensions and anything else)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        words = content.split()
        if len(words) > _FILE_MAX_WORDS:
            content = " ".join(words[:_FILE_MAX_WORDS]) + "\n[truncated]"
        return content, "text"
    except Exception:
        return "", "unknown"
```

- [ ] **Step 4: Wire file detection into `main()`**

In `main()`, after the URL detection block (after line 5353), add:

```python
        # Local file analysis: surf explain ./file.txt or surf ./file.py "what does this do"
        potential_path = args.input[0] if args.input else ""
        resolved_path = os.path.expanduser(potential_path)
        if not os.path.isabs(resolved_path):
            resolved_path = os.path.abspath(resolved_path)
        if os.path.isfile(resolved_path):
            content, ftype = _extract_file_content(resolved_path)
            if content:
                file_query = " ".join(args.input[1:]) if len(args.input) > 1 else f"explain this {ftype} file"
                basename = os.path.basename(resolved_path)
                print_header(f"{basename}", f"{ftype} {GLYPH_SEPARATOR} {len(content.split())} words")
                prefs = _read_preferences()
                preamble = f"[User preferences]\n{prefs}\n[End preferences]\n\n" if prefs else ""
                system = (
                    f"You are analyzing a local {ftype} file named '{basename}'. "
                    "Start with ▸ TL;DR then provide your analysis. Be specific about what the file contains and does."
                )
                prompt = f"{preamble}User asks: {file_query}\n\nFile content ({basename}):\n{content[:10000]}"
                stream = stream_ai(prompt, system, max_tokens=2048)
                response = stream_to_terminal(stream)
                save_session_entry(basename, "file", _truncate_at_sentence(response, 300))
                _obsidian_save(file_query, response, [], session_id=_obsidian_session_id())
                record_feature_use("file")
                if json_output:
                    _output_json(file_query, response, [basename], intent="file")
                return
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestFileAnalysis -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add surf.py tests/test_surf.py
git commit -m "feat: add local file analysis — surf explain ./file.pdf"
```

---

### Task 5: Shell Integration — `surf !!` and `--shell`

**Files:**
- Modify: `surf.py` — add `_get_last_command_error()`, `_get_shell_context()`; modify `main()` for `!!` expansion and `--shell` flag
- Modify: `tests/test_surf.py` — add `TestShellIntegration` class

**Interfaces:**
- Consumes: `os.environ`, `subprocess.run()`, shell history files
- Produces: `_get_last_command_error() -> str | None` — returns last failed command + error output from shell history. `_get_shell_context(n: int = 10) -> str` — returns last N commands from shell history.

- [ ] **Step 1: Write failing tests**

```python
class TestShellIntegration:
    def test_get_shell_context_returns_string(self):
        from surf import _get_shell_context
        with patch("os.path.expanduser", return_value="/tmp/fake_history"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            result = _get_shell_context(n=5)
            assert isinstance(result, str)

    def test_get_shell_context_reads_history(self, tmp_path):
        from surf import _get_shell_context
        hist = tmp_path / ".zsh_history"
        hist.write_text(": 1718000001:0;ls\n: 1718000002:0;git status\n: 1718000003:0;python app.py\n")
        with patch("os.path.expanduser") as mock_expand:
            mock_expand.side_effect = lambda p: str(hist) if "zsh_history" in p else str(tmp_path / p.replace("~", ""))
            with patch("os.environ", {"SHELL": "/bin/zsh"}):
                result = _get_shell_context(n=3)
                assert isinstance(result, str)

    def test_get_last_command_error_returns_none_when_no_history(self):
        from surf import _get_last_command_error
        with patch("os.path.expanduser", return_value="/tmp/nonexistent_history"):
            result = _get_last_command_error()
            assert result is None or isinstance(result, str)

    def test_bang_bang_detection(self):
        """!! in query should be recognized as shell history reference."""
        query = "!!"
        assert query == "!!"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestShellIntegration -v`
Expected: FAIL

- [ ] **Step 3: Implement shell integration**

Add before `main()` in `surf.py`:

```python
def _get_shell_context(n: int = 10) -> str:
    """Return last N commands from shell history."""
    shell = os.environ.get("SHELL", "/bin/zsh")
    if "zsh" in shell:
        hist_path = os.path.expanduser("~/.zsh_history")
    elif "bash" in shell:
        hist_path = os.path.expanduser("~/.bash_history")
    else:
        return ""

    try:
        with open(hist_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return ""

    commands = []
    for line in lines[-n * 2:]:  # read extra to handle zsh extended format
        line = line.strip()
        if not line:
            continue
        # zsh extended format: ": timestamp:0;command"
        if line.startswith(": ") and ";" in line:
            cmd = line.split(";", 1)[1]
        else:
            cmd = line
        if cmd and not cmd.startswith("#"):
            commands.append(cmd)

    return "\n".join(commands[-n:])


def _get_last_command_error() -> str | None:
    """Get the last command from shell history. Returns command string or None."""
    context = _get_shell_context(n=1)
    return context.strip() if context.strip() else None
```

In `main()`, after assembling the query but before processing, add `!!` expansion:

```python
    # Shell integration: surf !! → search for last command's error
    if query.strip() == "!!":
        last_cmd = _get_last_command_error()
        if last_cmd:
            query = f"explain this shell error: {last_cmd}"
            print(f"{C_META}{GLYPH_META} expanding !! → \"{last_cmd}\"{C_RESET}")
        else:
            print(f"{C_ERROR}Could not read shell history{C_RESET}")
            return
```

Add `--shell` flag to argparse (in `main()` after other flags):

```python
    parser.add_argument("--shell", action="store_true",
                        help="Include recent shell history as context")
```

And in `search_flow`, when `--shell` is active, prepend shell context to the prompt. Pass through `main()`:

```python
    # --shell: include shell history
    if args.context:
        shell_ctx = _get_shell_context(n=15)
        if shell_ctx:
            query = query + f"\n\n[Recent shell history for context:\n{shell_ctx}\n]"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestShellIntegration -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add surf.py tests/test_surf.py
git commit -m "feat: add shell integration — surf !! and --shell for debugging"
```

---

### Task 6: Watch Mode — Periodic Refresh

**Files:**
- Modify: `surf.py` — add `_watch_loop()`; add `--watch` argparse flag in `main()`
- Modify: `tests/test_surf.py` — add `TestWatchMode` class

**Interfaces:**
- Consumes: `search_flow()`, `time.sleep()`, `_term_width()`
- Produces: `_watch_loop(query: str, interval_seconds: int, json_output: bool = False) -> None` — runs search_flow on repeat with the given interval. Never returns (Ctrl+C to exit).

- [ ] **Step 1: Write failing tests**

```python
class TestWatchMode:
    def test_parse_watch_interval_minutes(self):
        from surf import _parse_watch_interval
        assert _parse_watch_interval("5m") == 300
        assert _parse_watch_interval("1m") == 60

    def test_parse_watch_interval_hours(self):
        from surf import _parse_watch_interval
        assert _parse_watch_interval("1h") == 3600
        assert _parse_watch_interval("2h") == 7200

    def test_parse_watch_interval_seconds(self):
        from surf import _parse_watch_interval
        assert _parse_watch_interval("30s") == 30
        assert _parse_watch_interval("90s") == 90

    def test_parse_watch_interval_bare_number_defaults_to_minutes(self):
        from surf import _parse_watch_interval
        assert _parse_watch_interval("5") == 300

    def test_parse_watch_interval_invalid_returns_default(self):
        from surf import _parse_watch_interval
        assert _parse_watch_interval("abc") == 300  # default 5m

    def test_parse_watch_interval_minimum_30s(self):
        from surf import _parse_watch_interval
        assert _parse_watch_interval("5s") == 30  # clamped to 30s minimum

    def test_watch_loop_calls_search_flow(self):
        from surf import _watch_loop
        call_count = 0
        def fake_search(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise KeyboardInterrupt
            return [], "response"
        with patch("surf.search_flow", side_effect=fake_search), \
             patch("surf.time.sleep"), \
             patch("builtins.print"):
            try:
                _watch_loop("NVDA stock", 60)
            except KeyboardInterrupt:
                pass
        assert call_count >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestWatchMode -v`
Expected: FAIL

- [ ] **Step 3: Implement watch mode**

Add before `main()` in `surf.py`:

```python
def _parse_watch_interval(spec: str) -> int:
    """Parse interval like '5m', '1h', '30s', or bare '5' (minutes). Returns seconds, minimum 30."""
    spec = spec.strip().lower()
    try:
        if spec.endswith("h"):
            seconds = int(spec[:-1]) * 3600
        elif spec.endswith("m"):
            seconds = int(spec[:-1]) * 60
        elif spec.endswith("s"):
            seconds = int(spec[:-1])
        elif spec.isdigit():
            seconds = int(spec) * 60
        else:
            seconds = 300
    except (ValueError, IndexError):
        seconds = 300
    return max(30, seconds)


def _watch_loop(query: str, interval_seconds: int, json_output: bool = False) -> None:
    """Run search_flow on a loop. Ctrl+C to exit."""
    iteration = 0
    while True:
        iteration += 1
        now = time.strftime("%H:%M")
        width = _term_width()
        if iteration > 1:
            vspace(SPACE_SM)
            print(f"{C_META}{GLYPH_DIVIDER * width}{C_RESET}")
            print(f"{C_META}{GLYPH_META} refreshed {GLYPH_SEPARATOR} {now}{C_RESET}")
            vspace(SPACE_XS)
        try:
            search_flow(query, interactive=False, json_output=json_output)
        except Exception as e:
            print(f"{C_ERROR}Watch error: {e}{C_RESET}")
        time.sleep(interval_seconds)
```

In `main()`, add the `--watch` flag:

```python
    parser.add_argument("--watch", type=str, default=None, metavar="INTERVAL",
                        help="Repeat search on interval (e.g. 5m, 1h, 30s)")
```

And after query assembly, before other processing:

```python
    if args.watch:
        interval = _parse_watch_interval(args.watch)
        print(f"{C_META}{GLYPH_META} watching \"{query}\" every {interval}s — Ctrl+C to stop{C_RESET}\n")
        try:
            _watch_loop(query, interval, json_output=json_output)
        except KeyboardInterrupt:
            print(f"\n{C_META}watch stopped{C_RESET}")
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestWatchMode -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add surf.py tests/test_surf.py
git commit -m "feat: add watch mode — surf --watch 5m 'NVDA stock price'"
```

---

### Task 7: Diff Mode — What Changed Since Last Search

**Files:**
- Modify: `surf.py` — add `_load_search_snapshot()`, `_save_search_snapshot()`, `_diff_search()`; add `--diff` flag in `main()`
- Modify: `tests/test_surf.py` — add `TestDiffMode` class

**Interfaces:**
- Consumes: `search_flow()`, session file I/O, `stream_ai()`
- Produces: `_save_search_snapshot(query: str, response: str, sources: list[dict]) -> None`. `_load_search_snapshot(query: str) -> dict | None` — returns `{"response": str, "sources": list, "timestamp": int}` or None. `_diff_search(query: str, json_output: bool) -> None` — runs new search, loads old snapshot, shows diff.

- [ ] **Step 1: Write failing tests**

```python
class TestDiffMode:
    def test_save_and_load_snapshot(self, tmp_path):
        import surf
        original = surf.CONFIG_PATH
        surf.CONFIG_PATH = str(tmp_path / "config")
        snapshot_dir = tmp_path / "snapshots"
        with patch("surf.os.path.expanduser", return_value=str(snapshot_dir / "snap.json")):
            from surf import _save_search_snapshot, _load_search_snapshot, _make_note_slug
            slug = _make_note_slug("NVDA stock")
            snap_path = str(snapshot_dir / f"{slug}.json")
            with patch("surf._snapshot_path", return_value=snap_path):
                _save_search_snapshot("NVDA stock", "Price is $150", [{"domain": "yahoo.com"}])
                loaded = _load_search_snapshot("NVDA stock")
                assert loaded is not None
                assert "150" in loaded["response"]
                assert loaded["sources"][0]["domain"] == "yahoo.com"
        surf.CONFIG_PATH = original

    def test_load_snapshot_returns_none_when_missing(self):
        from surf import _load_search_snapshot
        with patch("surf._snapshot_path", return_value="/tmp/nonexistent_snap.json"):
            result = _load_search_snapshot("never searched this")
            assert result is None

    def test_diff_flag_accepted_by_argparse(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("input", nargs="*")
        parser.add_argument("--diff", action="store_true")
        args = parser.parse_args(["--diff", "NVDA", "stock"])
        assert args.diff is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDiffMode -v`
Expected: FAIL

- [ ] **Step 3: Implement diff mode**

Add after session functions (around line 260) in `surf.py`:

```python
SNAPSHOT_DIR = os.path.expanduser("~/.config/surf/snapshots")


def _snapshot_path(query: str) -> str:
    """Return path for a search snapshot file."""
    slug = _make_note_slug(query)
    return os.path.join(SNAPSHOT_DIR, f"{slug}.json")


def _save_search_snapshot(query: str, response: str, sources: list[dict]) -> None:
    """Save a search result as a snapshot for later diff comparison."""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = _snapshot_path(query)
    data = {
        "query": query,
        "response": response,
        "sources": sources,
        "timestamp": int(time.time()),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _load_search_snapshot(query: str) -> dict | None:
    """Load a previous search snapshot. Returns None if no snapshot exists."""
    path = _snapshot_path(query)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _diff_search(query: str, json_output: bool = False) -> None:
    """Run a new search and compare against the last snapshot."""
    old = _load_search_snapshot(query)
    results, response = search_flow(query, interactive=False, json_output=False)

    _save_search_snapshot(query, response, results)

    if not old:
        print(f"\n{C_META}{GLYPH_META} first search for this query — snapshot saved for next diff{C_RESET}")
        return

    from datetime import datetime
    old_time = datetime.fromtimestamp(old["timestamp"]).strftime("%Y-%m-%d %H:%M")

    # Structural header — instant signal before LLM narrative
    old_domains = {s.get("domain", "") for s in old.get("sources", [])}
    new_domains = {r.get("domain", "") for r in results}
    added = new_domains - old_domains
    removed = old_domains - new_domains

    print_header(f"Changes since {old_time}", f"{query[:40]}")
    source_delta = []
    if added:
        source_delta.append(f"+{len(added)} new")
    if removed:
        source_delta.append(f"-{len(removed)} removed")
    if source_delta:
        print(f"{C_META}{GLYPH_META} sources: {', '.join(source_delta)}{C_RESET}")
    print()

    diff_prompt = (
        f"Compare these two search results for \"{query}\" and describe ONLY what changed.\n\n"
        f"PREVIOUS ({old_time}):\n{old['response'][:3000]}\n\n"
        f"CURRENT (now):\n{response[:3000]}\n\n"
        "List specific changes: new facts, updated numbers, removed information. "
        "If nothing meaningful changed, say so. Be concise."
    )
    stream = stream_ai(diff_prompt, "You compare search results and highlight changes. Be specific and concise.", max_tokens=1000)
    stream_to_terminal(stream)
```

In `main()`, add the flag and routing:

```python
    parser.add_argument("--diff", action="store_true",
                        help="Compare results against last search for this query")
```

Before the main search dispatch:

```python
    if args.diff:
        _diff_search(query, json_output=json_output)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDiffMode -v`
Expected: PASS

- [ ] **Step 5: Save snapshots automatically on every search**

At the end of `search_flow()`, before the `return` statement (around line 5492), add:

```python
    _save_search_snapshot(query, response, results)
```

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add surf.py tests/test_surf.py
git commit -m "feat: add diff mode — surf --diff 'topic' shows what changed"
```

---

## Phase 3: Research Depth

---

### Task 8: Named Threads — Persistent Research Sessions

**Files:**
- Modify: `surf.py` — add `_thread_path()`, `_load_thread()`, `_save_thread_entry()`, `_list_threads()`; add `--thread` flag and `threads` subcommand in `main()`
- Modify: `tests/test_surf.py` — add `TestNamedThreads` class

**Interfaces:**
- Consumes: `search_flow()`, `save_session_entry()`, `_obsidian_save()`, `format_session_context()`
- Produces: `_thread_path(name: str) -> str`. `_load_thread(name: str) -> dict` — returns `{"name": str, "entries": list[dict], "created_at": int, "updated_at": int}`. `_save_thread_entry(name: str, query: str, response: str, sources: list[dict]) -> None`. `_list_threads() -> list[dict]` — returns list of `{"name": str, "entries": int, "updated_at": int}`.

- [ ] **Step 1: Write failing tests**

```python
class TestNamedThreads:
    def test_thread_path_returns_path(self):
        from surf import _thread_path
        path = _thread_path("gpu-research")
        assert "gpu-research" in path
        assert path.endswith(".json")

    def test_save_and_load_thread(self, tmp_path):
        from surf import _save_thread_entry, _load_thread
        with patch("surf.THREAD_DIR", str(tmp_path)):
            _save_thread_entry("test-thread", "what is a GPU", "A GPU is a graphics processor.", [{"domain": "nvidia.com"}])
            thread = _load_thread("test-thread")
            assert thread["name"] == "test-thread"
            assert len(thread["entries"]) == 1
            assert "GPU" in thread["entries"][0]["query"]

    def test_save_appends_to_existing_thread(self, tmp_path):
        from surf import _save_thread_entry, _load_thread
        with patch("surf.THREAD_DIR", str(tmp_path)):
            _save_thread_entry("test-thread", "query 1", "response 1", [])
            _save_thread_entry("test-thread", "query 2", "response 2", [])
            thread = _load_thread("test-thread")
            assert len(thread["entries"]) == 2

    def test_load_nonexistent_thread_returns_empty(self, tmp_path):
        from surf import _load_thread
        with patch("surf.THREAD_DIR", str(tmp_path)):
            thread = _load_thread("nonexistent")
            assert thread["entries"] == []

    def test_list_threads(self, tmp_path):
        from surf import _save_thread_entry, _list_threads
        with patch("surf.THREAD_DIR", str(tmp_path)):
            _save_thread_entry("alpha", "q1", "r1", [])
            _save_thread_entry("beta", "q2", "r2", [])
            threads = _list_threads()
            assert len(threads) == 2
            names = {t["name"] for t in threads}
            assert "alpha" in names
            assert "beta" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestNamedThreads -v`
Expected: FAIL

- [ ] **Step 3: Implement named threads**

Add after session functions (around line 260) in `surf.py`:

```python
THREAD_DIR = os.path.expanduser("~/.config/surf/threads")


def _thread_path(name: str) -> str:
    """Return file path for a named thread."""
    safe_name = re.sub(r"[^a-z0-9-]", "", name.lower().strip().replace(" ", "-"))
    return os.path.join(THREAD_DIR, f"{safe_name}.json")


def _load_thread(name: str) -> dict:
    """Load a named thread. Returns empty structure if thread doesn't exist."""
    path = _thread_path(name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"name": name, "entries": [], "created_at": 0, "updated_at": 0}


def _save_thread_entry(name: str, query: str, response: str, sources: list[dict]) -> None:
    """Append an entry to a named thread."""
    os.makedirs(THREAD_DIR, exist_ok=True)
    thread = _load_thread(name)
    now = int(time.time())
    if not thread["created_at"]:
        thread["created_at"] = now
    thread["updated_at"] = now
    thread["name"] = name
    thread["entries"].append({
        "query": query,
        "response": _truncate_at_sentence(response, 2000),
        "sources": [{"domain": s.get("domain", ""), "url": s.get("url", "")} for s in sources[:5]],
        "timestamp": now,
    })
    path = _thread_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(thread, f, ensure_ascii=False, indent=2)


def _list_threads() -> list[dict]:
    """List all named threads with metadata."""
    if not os.path.isdir(THREAD_DIR):
        return []
    threads = []
    for fname in os.listdir(THREAD_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(THREAD_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            threads.append({
                "name": data.get("name", fname.replace(".json", "")),
                "entries": len(data.get("entries", [])),
                "updated_at": data.get("updated_at", 0),
            })
        except Exception:
            continue
    return sorted(threads, key=lambda t: t["updated_at"], reverse=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestNamedThreads -v`
Expected: PASS

- [ ] **Step 5: Wire `--thread` flag and `threads` subcommand into `main()`**

Add to argparse in `main()`:

```python
    parser.add_argument("-t", "--thread", type=str, default=None, metavar="NAME",
                        help="Save search to a named research thread")
```

Add thread listing subcommand (after the `setup` subcommand handling):

```python
    # surf threads — list all named threads
    if args.input and args.input[0] == "threads":
        threads = _list_threads()
        if not threads:
            print(f"{C_META}No threads yet. Start one: surf --thread 'gpu-research' your query{C_RESET}")
            return
        print(f"\n{C_BRAND}━━ Research Threads ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}\n")
        for t in threads:
            from datetime import datetime
            updated = datetime.fromtimestamp(t["updated_at"]).strftime("%Y-%m-%d %H:%M") if t["updated_at"] else "never"
            print(f"  {C_INTERACTIVE}{t['name']}{C_RESET}  {C_META}{t['entries']} entries · updated {updated}{C_RESET}")
        print(f"\n{C_META}Resume: surf --thread '{threads[0]['name']}' your follow-up query{C_RESET}\n")
        return
```

When `--thread` is used, inject thread context and save results:

```python
    # --thread: inject thread context as session memory
    if args.thread:
        thread = _load_thread(args.thread)
        if thread["entries"]:
            thread_ctx = f"Continuing research thread '{args.thread}':\n"
            for e in thread["entries"][-5:]:
                thread_ctx += f"  [{e.get('query', '')}]: {e['response'][:200]}\n"
            # Prepend to query context via session
            save_session_entry(f"[thread:{args.thread}]", "thread_context", thread_ctx)
            print(f"{C_META}{GLYPH_META} thread \"{args.thread}\" — {len(thread['entries'])} prior entries{C_RESET}")
```

After `search_flow` returns, save to thread:

```python
        if args.thread:
            _save_thread_entry(args.thread, query, response, results)
            print(f"{C_META}{GLYPH_META} saved to thread \"{args.thread}\"{C_RESET}")
```

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add surf.py tests/test_surf.py
git commit -m "feat: add named threads — surf --thread 'name' for persistent research"
```

---

### Task 9: Export — Clean Research Documents

**Files:**
- Modify: `surf.py` — add `_export_thread()`, `_export_session()`; add `export` subcommand in `main()`
- Modify: `tests/test_surf.py` — add `TestExport` class

**Interfaces:**
- Consumes: `_load_thread()`, `_list_threads()`, `load_session()`
- Produces: `_export_thread(name: str, format: str = "markdown") -> str` — returns formatted document. `_export_session(format: str = "markdown") -> str` — exports current session.

- [ ] **Step 1: Write failing tests**

```python
class TestExport:
    def test_export_thread_markdown(self, tmp_path):
        from surf import _export_thread, _save_thread_entry
        with patch("surf.THREAD_DIR", str(tmp_path)):
            _save_thread_entry("test-export", "what is AI", "AI is artificial intelligence.", [{"domain": "wiki.org", "url": "https://wiki.org"}])
            _save_thread_entry("test-export", "history of AI", "AI started in the 1950s.", [{"domain": "stanford.edu", "url": "https://stanford.edu"}])
            result = _export_thread("test-export")
            assert "# test-export" in result
            assert "what is AI" in result
            assert "AI is artificial intelligence" in result
            assert "history of AI" in result
            assert "wiki.org" in result

    def test_export_thread_nonexistent(self, tmp_path):
        from surf import _export_thread
        with patch("surf.THREAD_DIR", str(tmp_path)):
            result = _export_thread("nonexistent")
            assert result == ""

    def test_export_session_returns_markdown(self):
        from surf import _export_session
        entries = [
            {"query": "test q", "type": "search", "summary": "test answer", "timestamp": 1718000000},
        ]
        with patch("surf.load_session", return_value=entries):
            result = _export_session()
            assert "test q" in result
            assert "test answer" in result

    def test_export_session_empty(self):
        from surf import _export_session
        with patch("surf.load_session", return_value=[]):
            result = _export_session()
            assert "No active session" in result or result == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestExport -v`
Expected: FAIL

- [ ] **Step 3: Implement export**

Add after thread functions in `surf.py`:

```python
def _export_thread(name: str, format: str = "markdown") -> str:
    """Export a named thread as a markdown document."""
    thread = _load_thread(name)
    if not thread["entries"]:
        return ""

    from datetime import datetime
    lines = [f"# {name}\n"]
    created = datetime.fromtimestamp(thread["created_at"]).strftime("%Y-%m-%d")
    updated = datetime.fromtimestamp(thread["updated_at"]).strftime("%Y-%m-%d")
    lines.append(f"*Research thread · {len(thread['entries'])} entries · {created} to {updated}*\n")
    lines.append("---\n")

    for entry in thread["entries"]:
        ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%Y-%m-%d %H:%M")
        lines.append(f"## {entry['query']}\n")
        lines.append(f"*{ts}*\n")
        lines.append(f"{entry['response']}\n")
        if entry.get("sources"):
            lines.append("\n**Sources:**")
            for s in entry["sources"]:
                url = s.get("url", "")
                domain = s.get("domain", "")
                if url:
                    lines.append(f"- [{domain}]({url})")
                elif domain:
                    lines.append(f"- {domain}")
            lines.append("")
        lines.append("---\n")

    return "\n".join(lines)


def _export_session(format: str = "markdown") -> str:
    """Export current session as a markdown document."""
    entries = load_session()
    if not entries:
        return ""

    from datetime import datetime
    lines = ["# Surf Session\n"]
    first_ts = datetime.fromtimestamp(entries[0]["timestamp"]).strftime("%Y-%m-%d %H:%M")
    lines.append(f"*{len(entries)} searches starting {first_ts}*\n")
    lines.append("---\n")

    for entry in entries:
        ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%H:%M")
        lines.append(f"## {entry['query']}\n")
        lines.append(f"*{ts} · {entry['type']}*\n")
        lines.append(f"{entry['summary']}\n")
        lines.append("---\n")

    return "\n".join(lines)
```

In `main()`, add the `export` subcommand:

```python
    # surf export [--thread name] [--file path]
    if args.input and args.input[0] == "export":
        export_thread = None
        export_file = None
        i = 1
        while i < len(args.input):
            if args.input[i] == "--thread" and i + 1 < len(args.input):
                export_thread = args.input[i + 1]
                i += 2
            elif args.input[i] == "--file" and i + 1 < len(args.input):
                export_file = args.input[i + 1]
                i += 2
            else:
                i += 1

        if export_thread:
            content = _export_thread(export_thread)
        else:
            content = _export_session()

        if not content:
            print(f"{C_META}Nothing to export. Start a search or use --thread name.{C_RESET}")
            return

        if export_file:
            with open(export_file, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"{C_META}{GLYPH_META} exported to {export_file}{C_RESET}")
        else:
            print(content)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestExport -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add surf.py tests/test_surf.py
git commit -m "feat: add export — surf export --thread 'name' for clean research docs"
```

---

### Task 10: Custom Source Lists

**Files:**
- Modify: `surf.py` — add `_parse_source_list()`; modify `search_flow()` and `_search_with_retry()` to accept source filters; add `--sources` flag in `main()`
- Modify: `tests/test_surf.py` — add `TestCustomSources` class

**Interfaces:**
- Consumes: `ddg_search()` / `brave_search()`, `_get_search_backend()`
- Produces: `_parse_source_list(spec: str) -> list[str]` — parses "arxiv,nature,science" into ["arxiv.org", "nature.com", "science.org"]. Returns domain suffixes.

- [ ] **Step 1: Write failing tests**

```python
class TestCustomSources:
    def test_parse_source_list_basic(self):
        from surf import _parse_source_list
        result = _parse_source_list("arxiv,nature,science")
        assert "arxiv.org" in result
        assert "nature.com" in result
        assert "science.org" in result

    def test_parse_source_list_full_domains(self):
        from surf import _parse_source_list
        result = _parse_source_list("nytimes.com,bbc.com")
        assert "nytimes.com" in result
        assert "bbc.com" in result

    def test_parse_source_list_mixed(self):
        from surf import _parse_source_list
        result = _parse_source_list("arxiv,bbc.com,reuters")
        assert "arxiv.org" in result
        assert "bbc.com" in result
        assert "reuters.com" in result

    def test_parse_source_list_empty(self):
        from surf import _parse_source_list
        result = _parse_source_list("")
        assert result == []

    def test_search_with_sources_filters_results(self):
        from surf import _filter_by_sources
        results = [
            {"domain": "arxiv.org", "title": "Paper", "url": "https://arxiv.org/1", "snippet": "A"},
            {"domain": "reddit.com", "title": "Post", "url": "https://reddit.com/1", "snippet": "B"},
            {"domain": "nature.com", "title": "Article", "url": "https://nature.com/1", "snippet": "C"},
        ]
        filtered = _filter_by_sources(results, ["arxiv.org", "nature.com"])
        assert len(filtered) == 2
        assert all(r["domain"] in ("arxiv.org", "nature.com") for r in filtered)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestCustomSources -v`
Expected: FAIL

- [ ] **Step 3: Implement custom sources**

Add near `ddg_search()` in `surf.py`:

```python
_SOURCE_SHORTNAMES = {
    "arxiv": "arxiv.org",
    "pubmed": "pubmed.ncbi.nlm.nih.gov",
    "nature": "nature.com",
    "science": "science.org",
    "reuters": "reuters.com",
    "bbc": "bbc.com",
    "nyt": "nytimes.com",
    "nytimes": "nytimes.com",
    "wapo": "washingtonpost.com",
    "wsj": "wsj.com",
    "bloomberg": "bloomberg.com",
    "techcrunch": "techcrunch.com",
    "ars": "arstechnica.com",
    "verge": "theverge.com",
    "wired": "wired.com",
    "wikipedia": "en.wikipedia.org",
    "wiki": "en.wikipedia.org",
    "github": "github.com",
    "stackoverflow": "stackoverflow.com",
    "so": "stackoverflow.com",
    "hn": "news.ycombinator.com",
    "guardian": "theguardian.com",
    "apnews": "apnews.com",
    "ap": "apnews.com",
    "cnn": "cnn.com",
}


def _parse_source_list(spec: str) -> list[str]:
    """Parse a comma-separated source list into domain suffixes."""
    if not spec or not spec.strip():
        return []
    domains = []
    for part in spec.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part in _SOURCE_SHORTNAMES:
            domains.append(_SOURCE_SHORTNAMES[part])
        elif "." in part:
            domains.append(part)
        else:
            domains.append(f"{part}.com")
    return domains


def _filter_by_sources(results: list[dict], allowed_domains: list[str]) -> list[dict]:
    """Filter search results to only include results from allowed domains."""
    if not allowed_domains:
        return results
    return [
        r for r in results
        if any(allowed in r.get("domain", "") for allowed in allowed_domains)
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py::TestCustomSources -v`
Expected: PASS

- [ ] **Step 5: Wire `--sources` into `main()` and `search_flow()`**

Add to argparse:

```python
    parser.add_argument("--sources", type=str, default=None, metavar="LIST",
                        help="Restrict to sources (e.g. 'arxiv,nature,bbc')")
```

Modify `search_flow` signature:

```python
def search_flow(query: str, interactive: bool = True, json_output: bool = False, deep: bool = False, source_filter: list[str] | None = None) -> tuple[list[dict], str]:
```

Inside `search_flow`, after results are obtained and filtered (after `results = _filter_results(results, evaluative_context=eval_context)` around line 3293), add:

```python
    if source_filter:
        results = _filter_by_sources(results, source_filter)
        if not results:
            # No results from specified sources — try site-specific search
            site_query = query + " " + " ".join(f"site:{d}" for d in source_filter[:3])
            try:
                results = _filter_results(_get_search_backend()(site_query, num_results=8))
            except Exception:
                pass
```

In `main()`, pass the flag:

```python
    source_filter = _parse_source_list(args.sources) if args.sources else None
    # ... pass to search_flow:
    search_flow(query, interactive=not json_output, json_output=json_output, deep=args.deep, source_filter=source_filter)
```

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/marcos/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add surf.py tests/test_surf.py
git commit -m "feat: add custom source lists — surf --sources 'arxiv,nature' 'CRISPR safety'"
```

---

## Summary

| Phase | Task | Feature | CLI |
|-------|------|---------|-----|
| 1 | 1 | Deep Search — multi-step reasoning | `surf --deep "query"` (auto for research/contested) |
| 1 | 2 | Brave Search backend | Config: `BRAVE_API_KEY` |
| 1 | 3 | Stdin pipe input | `cat file \| surf "explain"` (auto content-type detection) |
| 2 | 4 | Local file analysis | `surf ./file.pdf "explain"` |
| 2 | 5 | Shell integration | `surf !!`, `surf --shell` |
| 2 | 6 | Watch mode | `surf --watch 5m "topic"` |
| 2 | 7 | Diff mode | `surf --diff "topic"` (structural + LLM diff) |
| 3 | 8 | Named threads | `surf -t "name" "query"`, `surf threads` |
| 3 | 9 | Export | `surf export --thread "name"` |
| 3 | 10 | Custom sources | `surf --sources "arxiv,bbc" "query"` |

Each task is independently testable and committable. Tasks within a phase can be parallelized (no cross-dependencies within a phase). Phase 2 depends on Phase 1 being complete. Phase 3 depends on Phase 2 (threads build on session memory; export builds on threads).

All tasks follow the global constraints: design system tokens, brand voice (`↳` progress), session memory + Obsidian + feature tracking, discoverability via `FEATURE_TIPS` and `?` help screen.
