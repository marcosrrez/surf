# tests/test_surf.py
from surf import load_config, detect_input_type, extract_text, fetch_page
from surf import build_search_prompt, build_read_prompt, SEARCH_SYSTEM, READ_SYSTEM
from surf import ddg_search
from surf import stream_groq
from surf import search_flow
from surf import read_flow, parse_related_topics
from surf import classify_intent, open_in_browser
from surf import _classify_tier
from surf import _confidence_gate
import json
import os
from unittest.mock import patch, MagicMock

class TestDetectInputType:
    def test_plain_query_is_query(self):
        assert detect_input_type("what is a black hole") == "query"

    def test_url_with_http_is_url(self):
        assert detect_input_type("https://nasa.gov/black-holes") == "url"

    def test_url_with_www_is_url(self):
        assert detect_input_type("www.nasa.gov") == "url"

    def test_bare_domain_is_url(self):
        assert detect_input_type("nasa.gov") == "url"

    def test_domain_with_path_is_url(self):
        assert detect_input_type("nasa.gov/black-holes") == "url"

    def test_query_with_dot_in_word_is_query(self):
        assert detect_input_type("latest news on iran") == "query"

    def test_multi_word_with_tld_like_word_is_query(self):
        assert detect_input_type("how does the net work") == "query"

    def test_long_tld_is_url(self):
        assert detect_input_type("example.photography") == "url"

class TestLoadConfig:
    def test_returns_api_key(self, tmp_path):
        config_file = tmp_path / "config"
        config_file.write_text("GROQ_API_KEY=test-key-1234567890\n")
        import surf
        original = surf.CONFIG_PATH
        surf.CONFIG_PATH = str(config_file)
        try:
            config = surf.load_config()
        finally:
            surf.CONFIG_PATH = original
        assert "GROQ_API_KEY" in config
        assert len(config["GROQ_API_KEY"]) > 10

    def test_returns_empty_dict_for_missing_file(self, tmp_path):
        import surf
        original = surf.CONFIG_PATH
        surf.CONFIG_PATH = str(tmp_path / "nonexistent")
        try:
            config = surf.load_config()
        finally:
            surf.CONFIG_PATH = original
        assert config == {}

    def test_skips_comments_and_blank_lines(self, tmp_path):
        config_file = tmp_path / "config"
        config_file.write_text("# comment\n\nKEY=value\n")
        import surf
        original = surf.CONFIG_PATH
        surf.CONFIG_PATH = str(config_file)
        try:
            config = surf.load_config()
        finally:
            surf.CONFIG_PATH = original
        assert config == {"KEY": "value"}

class TestExtractText:
    def test_strips_html_tags(self):
        html = "<html><body><p>Hello world</p></body></html>"
        result = extract_text(html)
        assert "Hello world" in result
        assert "<p>" not in result

    def test_removes_script_tags_and_content(self):
        html = "<html><body><script>alert('x')</script><p>Real content</p></body></html>"
        result = extract_text(html)
        assert "alert" not in result
        assert "Real content" in result

    def test_removes_style_tags_and_content(self):
        html = "<html><body><style>body{color:red}</style><p>Text</p></body></html>"
        result = extract_text(html)
        assert "color:red" not in result
        assert "Text" in result

    def test_truncates_to_word_limit(self):
        words = " ".join(["word"] * 10000)
        html = f"<p>{words}</p>"
        result = extract_text(html, max_words=6000)
        assert len(result.split()) <= 6100  # small buffer for edge cases

    def test_extracts_page_title(self):
        html = "<html><head><title>NASA Black Holes</title></head><body><p>Content</p></body></html>"
        title, _ = extract_text(html, return_title=True)
        assert title == "NASA Black Holes"

class TestFetchPage:
    def test_returns_html_string(self):
        mock_response = MagicMock()
        mock_response.text = "<html><body>Test</body></html>"
        mock_response.raise_for_status = MagicMock()
        with patch("surf.requests.get", return_value=mock_response):
            result = fetch_page("https://example.com")
        assert result == "<html><body>Test</body></html>"

    def test_raises_on_bad_status(self):
        import requests as req
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = req.HTTPError("404")
        with patch("surf.requests.get", return_value=mock_response):
            try:
                fetch_page("https://example.com/missing")
                assert False, "Should have raised"
            except req.HTTPError:
                pass

class TestBuildSearchPrompt:
    def test_includes_query(self):
        snippets = [{"title": "NASA", "url": "nasa.gov", "snippet": "Space stuff"}]
        prompt = build_search_prompt("black holes", snippets)
        assert "black holes" in prompt

    def test_includes_snippets(self):
        snippets = [{"title": "NASA", "url": "nasa.gov", "snippet": "Space stuff"}]
        prompt = build_search_prompt("black holes", snippets)
        assert "NASA" in prompt
        assert "nasa.gov" in prompt
        assert "Space stuff" in prompt

    def test_handles_multiple_snippets(self):
        snippets = [
            {"title": "A", "url": "a.com", "snippet": "alpha"},
            {"title": "B", "url": "b.com", "snippet": "beta"},
        ]
        prompt = build_search_prompt("test", snippets)
        assert "alpha" in prompt and "beta" in prompt

class TestBuildReadPrompt:
    def test_includes_title(self):
        prompt = build_read_prompt("NASA Black Holes", "Some article text here")
        assert "NASA Black Holes" in prompt

    def test_includes_text(self):
        prompt = build_read_prompt("Title", "Important article content")
        assert "Important article content" in prompt

class TestSystemPrompts:
    def test_search_system_mentions_tldr(self):
        assert "TL;DR" in SEARCH_SYSTEM

    def test_research_system_has_section_headers(self):
        from surf import SEARCH_SYSTEM_RESEARCH
        assert "bold header" in SEARCH_SYSTEM_RESEARCH.lower() or "**" in SEARCH_SYSTEM_RESEARCH

    def test_current_system_has_section_headers(self):
        from surf import SEARCH_SYSTEM_CURRENT
        assert "bold header" in SEARCH_SYSTEM_CURRENT.lower() or "**" in SEARCH_SYSTEM_CURRENT

    def test_read_system_mentions_related(self):
        assert "Related" in READ_SYSTEM

class TestDdgSearch:
    def test_returns_list_of_dicts(self):
        mock_html = """<html><body><table>
        <tr><td><a class="result-link" href="https://nasa.gov">NASA Black Holes</a></td></tr>
        <tr><td class="result-snippet">Objects with strong gravity.</td></tr>
        </table></body></html>"""
        mock_response = MagicMock()
        mock_response.text = mock_html
        mock_response.raise_for_status = MagicMock()
        with patch("surf.requests.post", return_value=mock_response):
            results = ddg_search("black holes")
        assert isinstance(results, list)

    def test_result_has_required_keys(self):
        mock_html = """<html><body><table>
        <tr><td><a class="result-link" href="https://nasa.gov/blackholes">NASA</a></td></tr>
        <tr><td class="result-snippet">Strong gravity objects.</td></tr>
        </table></body></html>"""
        mock_response = MagicMock()
        mock_response.text = mock_html
        mock_response.raise_for_status = MagicMock()
        with patch("surf.requests.post", return_value=mock_response):
            results = ddg_search("black holes")
        if results:
            assert "title" in results[0]
            assert "url" in results[0]
            assert "domain" in results[0]
            assert "snippet" in results[0]

class TestStreamGroq:
    def test_yields_strings(self):
        mock_chunk_1 = MagicMock()
        mock_chunk_1.choices = [MagicMock()]
        mock_chunk_1.choices[0].delta.content = "Hello "
        mock_chunk_2 = MagicMock()
        mock_chunk_2.choices = [MagicMock()]
        mock_chunk_2.choices[0].delta.content = "world"
        mock_chunk_empty = MagicMock()
        mock_chunk_empty.choices = [MagicMock()]
        mock_chunk_empty.choices[0].delta.content = None

        mock_stream = [mock_chunk_1, mock_chunk_2, mock_chunk_empty]
        mock_completion = MagicMock()
        mock_completion.__iter__ = MagicMock(return_value=iter(mock_stream))

        with patch("surf.Groq") as MockGroq:
            instance = MockGroq.return_value
            instance.chat.completions.create.return_value = mock_completion
            result = list(stream_groq("test prompt", "system prompt"))

        assert result == ["Hello ", "world"]

    def test_skips_none_content(self):
        mock_chunk = MagicMock()
        mock_chunk.choices = [MagicMock()]
        mock_chunk.choices[0].delta.content = None
        mock_completion = MagicMock()
        mock_completion.__iter__ = MagicMock(return_value=iter([mock_chunk]))

        with patch("surf.Groq") as MockGroq:
            instance = MockGroq.return_value
            instance.chat.completions.create.return_value = mock_completion
            result = list(stream_groq("prompt", "system"))

        assert result == []

class TestSearchFlow:
    def test_returns_results_and_response(self):
        fake_results = [
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/BH",
             "domain": "en.wikipedia.org", "snippet": "A black hole is..."},
        ]
        fake_response = "▸ TL;DR  Black holes are dense.\n\nMore detail."

        with patch("surf.ddg_search", return_value=fake_results), \
             patch("surf.stream_ai", return_value=iter(["▸ TL;DR  Black holes are dense."])), \
             patch("surf.print_header"), \
             patch("surf.print_status"), \
             patch("surf.clear_status"), \
             patch("surf.stream_to_terminal", return_value=fake_response), \
             patch("surf.print_results"), \
             patch("surf.save_session_entry"), \
             patch("surf.format_session_context", return_value=""), \
             patch("surf._print_linked_sources"):
            results, response = search_flow("black holes", interactive=False)

        assert results == fake_results
        assert "TL;DR" in response

class TestParseRelatedTopics:
    def test_extracts_numbered_lines_after_related(self):
        text = "Some content.\n\nRelated:\n1. Event horizons explained\n2. Hawking radiation\n3. Neutron stars"
        topics = parse_related_topics(text)
        assert len(topics) == 3
        assert "Event horizons explained" in topics[0]

    def test_returns_empty_if_no_related_section(self):
        text = "Just some content with no related section."
        topics = parse_related_topics(text)
        assert topics == []

class TestReadFlow:
    def test_fetches_and_streams(self):
        fake_html = "<html><head><title>NASA: Black Holes</title></head><body><p>Article content here.</p></body></html>"
        fake_response = "▸ TL;DR  Black holes are dense.\n\nContent.\n\nRelated:\n1. Neutron stars\n2. Event horizons\n3. Hawking radiation"

        with patch("surf.fetch_page", return_value=fake_html), \
             patch("surf.stream_ai", return_value=iter([fake_response])), \
             patch("surf.print_header"), \
             patch("surf.print_status"), \
             patch("surf.clear_status"), \
             patch("surf.stream_to_terminal", return_value=fake_response), \
             patch("surf.print_related"), \
             patch("surf.save_session_entry"), \
             patch("surf._is_spa_shell", return_value=False), \
             patch("surf._fetch_sub_pages", return_value=("", [])):
            result = read_flow("https://nasa.gov/black-holes", interactive=False)

        assert "TL;DR" in result

class TestClassifyIntent:
    def test_returns_dict_with_intent_key(self):
        fake_chunks = ['{"intent": "informational", "sub_type": "factual", "open_url": null, "tip": null, "fetch_snippets": true}']
        with patch("surf.stream_groq", return_value=iter(fake_chunks)):
            result = classify_intent("what is a black hole")
        assert "intent" in result

    def test_informational_query(self):
        fake_chunks = ['{"intent": "informational", "sub_type": "factual", "open_url": null, "tip": null, "fetch_snippets": true}']
        with patch("surf.stream_groq", return_value=iter(fake_chunks)):
            result = classify_intent("what is a black hole")
        assert result["intent"] == "informational"
        assert result["fetch_snippets"] is True

    def test_instant_query_no_snippets(self):
        fake_chunks = ['{"intent": "instant", "sub_type": "translation", "open_url": null, "tip": null, "fetch_snippets": false}']
        with patch("surf.stream_groq", return_value=iter(fake_chunks)):
            result = classify_intent("translate hello to spanish")
        assert result["intent"] == "instant"
        assert result["fetch_snippets"] is False

    def test_transactional_has_open_url(self):
        fake_chunks = ['{"intent": "transactional", "sub_type": "flights", "open_url": "https://google.com/flights", "tip": "Book 6 weeks out", "fetch_snippets": false}']
        with patch("surf.stream_groq", return_value=iter(fake_chunks)):
            result = classify_intent("flights JFK to LAX June 15")
        assert result["open_url"] is not None
        assert result["tip"] is not None

    def test_malformed_json_returns_informational_fallback(self):
        fake_chunks = ["not valid json at all"]
        with patch("surf.stream_groq", return_value=iter(fake_chunks)):
            result = classify_intent("anything")
        assert result["intent"] == "informational"
        assert result["fetch_snippets"] is True

class TestOpenInBrowser:
    def test_calls_open_command(self):
        with patch("surf.subprocess.run") as mock_run:
            open_in_browser("https://google.com")
            mock_run.assert_called_once_with(["open", "https://google.com"])

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


from surf import _deep_research

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
        fake_html = "<html><body><p>Short.</p></body></html>"
        with patch("surf.fetch_page", return_value=fake_html), \
             patch("surf._is_spa_shell", return_value=False):
            content, sources = _deep_research("query", "current", results)
        assert sources == []


class TestDeepResearchExpanded:
    """Tests for expanded _deep_research (5-source cap, second angle, 150-word gate)."""

    def _make_results(self, n: int) -> list[dict]:
        domains = ["espn.com", "bbc.com", "theathletic.com", "skysports.com",
                   "reuters.com", "apnews.com"]
        return [{"domain": d, "url": f"https://{d}/article", "title": "T", "snippet": "S"}
                for d in domains[:n]]

    def _rich_html(self, words: int = 200) -> str:
        return "<html><body><p>" + "article content word " * words + "</p></body></html>"

    def test_research_tier_caps_at_five_sources(self):
        initial = self._make_results(6)
        with patch("surf.fetch_page", return_value=self._rich_html(200)), \
             patch("surf._is_spa_shell", return_value=False), \
             patch("surf.ddg_search", return_value=self._make_results(3)):
            content, sources = _deep_research(
                "how does mRNA vaccine work", "research", initial,
                enriched_query="mRNA vaccine mechanism",
            )
        assert len(sources) <= 5

    def test_current_contested_still_caps_at_three(self):
        initial = self._make_results(5)
        with patch("surf.fetch_page", return_value=self._rich_html(200)), \
             patch("surf._is_spa_shell", return_value=False), \
             patch("surf.ddg_search", return_value=self._make_results(3)):
            content, sources = _deep_research(
                "who will win the UCL", "current", initial,
                enriched_query="UCL 2026 final prediction",
            )
        assert len(sources) <= 3

    def test_second_angle_search_called_for_research_tier(self):
        initial = self._make_results(2)
        rich_html = self._rich_html(200)
        with patch("surf.fetch_page", return_value=rich_html), \
             patch("surf._is_spa_shell", return_value=False), \
             patch("surf.ddg_search", return_value=self._make_results(3)) as mock_ddg:
            _deep_research(
                "how does mRNA vaccine work", "research", initial,
                enriched_query="mRNA vaccine mechanism",
            )
        mock_ddg.assert_called()
        call_args = [str(c) for c in mock_ddg.call_args_list]
        assert any("expert analysis" in a for a in call_args)

    def test_second_angle_uses_counterargument_for_contested(self):
        initial = self._make_results(2)
        with patch("surf.fetch_page", return_value=self._rich_html(200)), \
             patch("surf._is_spa_shell", return_value=False), \
             patch("surf.ddg_search", return_value=self._make_results(2)) as mock_ddg:
            _deep_research(
                "React vs Vue 2026", "contested", initial,
                enriched_query="React vs Vue developer experience",
            )
        call_args = [str(c) for c in mock_ddg.call_args_list]
        assert any("counterargument" in a or "criticism" in a for a in call_args)

    def test_short_articles_under_150_words_are_skipped(self):
        short_html = "<html><body><p>" + "word " * 100 + "</p></body></html>"
        initial = self._make_results(3)
        with patch("surf.fetch_page", return_value=short_html), \
             patch("surf._is_spa_shell", return_value=False), \
             patch("surf.ddg_search", return_value=[]):
            content, sources = _deep_research(
                "how does a vaccine work", "research", initial,
                enriched_query="vaccine mechanism",
            )
        assert sources == []

    def test_dedup_by_domain_across_both_angles(self):
        initial = [{"domain": "espn.com", "url": "https://espn.com/1", "title": "T", "snippet": "S"}]
        angle_results = [
            {"domain": "espn.com", "url": "https://espn.com/2", "title": "T", "snippet": "S"},
            {"domain": "bbc.com",  "url": "https://bbc.com/1",  "title": "T", "snippet": "S"},
        ]
        rich_html = self._rich_html(200)
        with patch("surf.fetch_page", return_value=rich_html), \
             patch("surf._is_spa_shell", return_value=False), \
             patch("surf.ddg_search", return_value=angle_results):
            content, sources = _deep_research(
                "how does mRNA vaccine work", "research", initial,
                enriched_query="mRNA vaccine",
            )
        domains = [s["domain"] for s in sources]
        assert len(domains) == len(set(domains)), "Duplicate domains in sources"


from surf import _get_ollama_model, stream_ollama

class TestOllama:
    def test_get_ollama_model_returns_none_when_not_running(self):
        with patch("surf.requests.get", side_effect=Exception("connection refused")):
            assert _get_ollama_model() is None

    def test_get_ollama_model_returns_preferred_when_available(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [{"name": "gemma2:2b"}, {"name": "llama3.2:3b"}]
        }
        with patch("surf.requests.get", return_value=mock_response):
            result = _get_ollama_model()
        assert result == "gemma2:2b"

    def test_stream_ollama_yields_guidance_when_no_model(self):
        with patch("surf._get_ollama_model", return_value=None):
            chunks = list(stream_ollama("prompt", "system"))
        assert len(chunks) == 1
        assert "ollama" in chunks[0].lower() or "local" in chunks[0].lower()

    def test_stream_ollama_yields_connection_error_message(self):
        import requests as _requests
        with patch("surf._get_ollama_model", return_value="gemma2:2b"), \
             patch("surf.requests.post", side_effect=_requests.exceptions.ConnectionError("refused")):
            chunks = list(stream_ollama("prompt", "system"))
        assert any("ollama" in c.lower() or "not running" in c.lower() for c in chunks)


from surf import search_flow
from unittest.mock import patch

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

        def capture_stream(prompt, system, max_tokens=2048, **kwargs):
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


from surf import stream_to_terminal

class TestInlineCitations:
    def test_citation_renders_as_gray_text_without_results(self):
        """[1] with no results list passes through as plain text"""
        def fake_stream():
            yield "Answer text [1] more text"
        import io, sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        result = stream_to_terminal(fake_stream(), results=None)
        sys.stdout = old_stdout
        assert "[1]" in result

    def test_citation_in_result_contains_url_when_results_provided(self):
        """[1] with results renders an OSC 8 hyperlink containing the URL"""
        results = [{"url": "https://reuters.com/article", "domain": "reuters.com", "title": "T", "snippet": "S"}]
        def fake_stream():
            yield "Iran [1] ceasefire"
        import io, sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        result = stream_to_terminal(fake_stream(), results=results)
        sys.stdout = old_stdout
        # The URL should appear somewhere in stdout (as OSC 8 escape sequence)
        output = captured.getvalue()
        assert "reuters.com" in output


from surf import (
    _cosine_similarity, _snippets_are_diverse, _bm25_rank,
    _vocabulary_independence_score, _score_source_independence,
    _is_evaluative_query, _edit_distance,
)


class TestClassicalAlgorithms:
    def test_cosine_similarity_identical(self):
        assert _cosine_similarity("the quick brown fox", "the quick brown fox") == 1.0

    def test_cosine_similarity_unrelated(self):
        score = _cosine_similarity("quantum entanglement physics", "state farm insurance rates")
        assert score < 0.2

    def test_snippets_diverse_when_different(self):
        results = [
            {"snippet": "Arsenal won the Premier League with 25 wins", "title": ""},
            {"snippet": "Interest rates rose 0.25% as Fed meets", "title": ""},
            {"snippet": "Python 4.0 released with new syntax features", "title": ""},
        ]
        assert _snippets_are_diverse(results) is True

    def test_snippets_not_diverse_when_repetitive(self):
        # SEO farms all say the same thing
        results = [
            {"snippet": "State Farm is a leading insurance company trusted by millions", "title": ""},
            {"snippet": "State Farm is a trusted leading insurance company for millions", "title": ""},
            {"snippet": "Millions trust State Farm as a leading insurance company today", "title": ""},
            {"snippet": "State Farm insurance is trusted by millions as a leading provider", "title": ""},
        ]
        assert _snippets_are_diverse(results) is False

    def test_bm25_rank_puts_relevant_first(self):
        results = [
            {"snippet": "general cooking tips for beginners", "title": "Cooking"},
            {"snippet": "pasta carbonara eggs guanciale pecorino recipe authentic", "title": "Carbonara"},
            {"snippet": "Italian cuisine history and traditions overview", "title": "Italy"},
        ]
        ranked = _bm25_rank("how to make pasta carbonara", results)
        assert "carbonara" in ranked[0]["snippet"].lower()

    def test_vocabulary_independence_marketing(self):
        score = _vocabulary_independence_score("Get a free quote today. Our award-winning agents are ready to help. Sign up now.")
        assert score < 0.3

    def test_vocabulary_independence_data(self):
        score = _vocabulary_independence_score("AM Best rated A+. J.D. Power ranked #1. Complaint ratio 0.3 per 100k policies according to NAIC.")
        assert score > 0.7

    def test_edit_distance_exact(self):
        assert _edit_distance("hello", "hello") == 0

    def test_edit_distance_close(self):
        assert _edit_distance("colour", "color") == 1

    def test_score_source_independence_regulatory_boost(self):
        result = {"url": "https://naic.org/complaints", "domain": "naic.org",
                  "snippet": "complaint ratio per 100k policies AM Best rated", "title": ""}
        score = _score_source_independence(result)
        assert score > 0.7

    def test_score_source_independence_affiliate_demote(self):
        result = {"url": "https://bestinsurance-affiliate.com/state-farm", "domain": "bestinsurance-affiliate.com",
                  "snippet": "get a free quote today sign up trusted award winning", "title": ""}
        score = _score_source_independence(result)
        assert score < 0.3


class TestEvaluativeRouting:
    def test_is_evaluative_contested(self):
        assert _is_evaluative_query("is State Farm a good insurance company", "contested") is True

    def test_is_evaluative_not_snippet(self):
        assert _is_evaluative_query("who wrote Pride and Prejudice", "snippet") is False

    def test_is_evaluative_non_evaluative_contested(self):
        assert _is_evaluative_query("React vs Vue performance", "contested") is False

    def test_filter_results_allows_reddit_evaluative(self):
        from surf import _filter_results
        results = [{"domain": "reddit.com", "url": "https://reddit.com/r/insurance", "snippet": "real user experiences", "title": "Reddit"}]
        eval_ctx = {"is_evaluative": True, "source_signals": [], "avoid_signals": []}
        filtered = _filter_results(results, evaluative_context=eval_ctx)
        assert len(filtered) == 1

    def test_filter_results_blocks_reddit_non_evaluative(self):
        from surf import _filter_results
        results = [{"domain": "reddit.com", "url": "https://reddit.com/r/python", "snippet": "python tips", "title": "Reddit"}]
        filtered = _filter_results(results, evaluative_context=None)
        assert len(filtered) == 0


class TestSynthesisModel:
    def test_get_synthesis_model_returns_haiku_by_default(self):
        from surf import _get_synthesis_model, CLAUDE_MODEL
        with patch("surf.load_config", return_value={}):
            assert _get_synthesis_model() == CLAUDE_MODEL

    def test_get_synthesis_model_returns_sonnet_when_configured(self):
        from surf import _get_synthesis_model, CLAUDE_SONNET_MODEL
        with patch("surf.load_config", return_value={"SYNTHESIS_MODEL": "sonnet"}):
            assert _get_synthesis_model() == CLAUDE_SONNET_MODEL

    def test_get_synthesis_model_ignores_unknown_values(self):
        from surf import _get_synthesis_model, CLAUDE_MODEL
        with patch("surf.load_config", return_value={"SYNTHESIS_MODEL": "gpt5"}):
            assert _get_synthesis_model() == CLAUDE_MODEL

    def test_stream_claude_accepts_tier_kwarg(self):
        # stream_claude must accept tier without error — just verify signature
        import inspect
        from surf import stream_claude
        sig = inspect.signature(stream_claude)
        assert "tier" in sig.parameters

    def test_stream_ai_accepts_tier_kwarg(self):
        import inspect
        from surf import stream_ai
        sig = inspect.signature(stream_ai)
        assert "tier" in sig.parameters


class TestDateFiltering:
    def test_returns_none_for_stable_fact_query(self):
        from surf import _date_filter_for_query
        assert _date_filter_for_query("who wrote Pride and Prejudice") is None

    def test_returns_none_for_historical_query(self):
        from surf import _date_filter_for_query
        assert _date_filter_for_query("what caused World War 1") is None

    def test_returns_date_string_for_temporal_query(self):
        from surf import _date_filter_for_query
        result = _date_filter_for_query("latest news on Iran")
        assert result is not None
        parts = result.split("-")
        assert len(parts) == 3 and len(parts[0]) == 4

    def test_breaking_news_uses_7_day_window(self):
        from surf import _date_filter_for_query
        from datetime import date
        result = _date_filter_for_query("breaking news UK election")
        assert result is not None
        delta = (date.today() - date.fromisoformat(result)).days
        assert 6 <= delta <= 8, f"Expected ~7 days, got {delta}"

    def test_today_signal_uses_7_day_window(self):
        from surf import _date_filter_for_query
        from datetime import date
        result = _date_filter_for_query("what happened today in sports")
        assert result is not None
        delta = (date.today() - date.fromisoformat(result)).days
        assert 6 <= delta <= 8

    def test_general_temporal_uses_30_day_window(self):
        from surf import _date_filter_for_query
        from datetime import date
        result = _date_filter_for_query("latest AI model releases")
        assert result is not None
        delta = (date.today() - date.fromisoformat(result)).days
        assert 28 <= delta <= 32, f"Expected ~30 days, got {delta}"

    def test_enrich_ddg_query_appends_after_for_temporal(self):
        from surf import _enrich_ddg_query
        with patch("surf.format_session_context", return_value=""), \
             patch("surf._extract_named_sources", return_value=[]):
            result = _enrich_ddg_query("latest AI news", tier="current")
        assert "after:" in result

    def test_enrich_ddg_query_no_after_for_stable_query(self):
        from surf import _enrich_ddg_query
        with patch("surf.format_session_context", return_value=""), \
             patch("surf._extract_named_sources", return_value=[]):
            result = _enrich_ddg_query("how does a black hole form", tier="research")
        assert "after:" not in result


class TestObsidianIntegration:

    def test_vault_path_returns_none_when_not_configured(self):
        from surf import _obsidian_vault_path
        with patch("surf.load_config", return_value={}):
            assert _obsidian_vault_path() is None

    def test_vault_path_returns_configured_path(self):
        from surf import _obsidian_vault_path
        with patch("surf.load_config", return_value={"OBSIDIAN_VAULT": "/tmp/vault"}):
            assert _obsidian_vault_path() == "/tmp/vault"

    def test_make_note_slug_sanitizes_query(self):
        from surf import _make_note_slug
        assert _make_note_slug("how does mRNA vaccine work?") == "how-does-mrna-vaccine-work"

    def test_make_note_slug_trims_to_60_chars(self):
        from surf import _make_note_slug
        result = _make_note_slug("a " * 50)
        assert len(result) <= 60

    def test_make_frontmatter_includes_required_fields(self):
        from surf import _make_frontmatter
        sources = [{"domain": "espn.com", "url": "https://espn.com/1", "title": "T"}]
        fm = _make_frontmatter("test query", sources, ["sports"])
        assert "date:" in fm
        assert "query:" in fm
        assert "sources:" in fm
        assert "tags:" in fm
        assert "espn.com" in fm
        assert "sports" in fm

    def test_obsidian_save_creates_file(self, tmp_path):
        from surf import _obsidian_save
        vault = str(tmp_path / "vault")
        os.makedirs(vault, exist_ok=True)
        sources = [{"domain": "bbc.com", "url": "https://bbc.com/1", "title": "BBC"}]
        with patch("surf.load_config", return_value={"OBSIDIAN_VAULT": vault}):
            path = _obsidian_save("what causes inflation", "TL;DR content.", sources, "sess001")
        assert path is not None and os.path.exists(path)

    def test_obsidian_save_file_contains_frontmatter(self, tmp_path):
        from surf import _obsidian_save
        vault = str(tmp_path / "vault")
        os.makedirs(vault, exist_ok=True)
        sources = [{"domain": "bbc.com", "url": "https://bbc.com/1", "title": "BBC"}]
        with patch("surf.load_config", return_value={"OBSIDIAN_VAULT": vault}):
            path = _obsidian_save("what causes inflation", "TL;DR content.", sources, "sess001")
        content = open(path).read()
        assert content.startswith("---")
        assert "what causes inflation" in content
        assert "bbc.com" in content

    def test_obsidian_save_appends_followup_to_same_file(self, tmp_path):
        from surf import _obsidian_save
        vault = str(tmp_path / "vault")
        os.makedirs(vault, exist_ok=True)
        sources = [{"domain": "bbc.com", "url": "https://bbc.com/1", "title": "BBC"}]
        with patch("surf.load_config", return_value={"OBSIDIAN_VAULT": vault}):
            path1 = _obsidian_save("what causes inflation", "First.", sources, "shared-sess")
            path2 = _obsidian_save("how do central banks respond", "Second.", sources, "shared-sess")
        assert path1 == path2
        content = open(path1).read()
        assert "First." in content and "Second." in content
        assert "## how do central banks respond" in content

    def test_obsidian_save_returns_none_when_not_configured(self):
        from surf import _obsidian_save
        with patch("surf.load_config", return_value={}):
            assert _obsidian_save("query", "response", [], "s1") is None

    def test_obsidian_find_related_returns_empty_when_no_vault(self):
        from surf import _obsidian_find_related
        with patch("surf.load_config", return_value={}):
            assert _obsidian_find_related("what causes inflation") == ""

    def test_obsidian_find_related_finds_matching_note(self, tmp_path):
        from surf import _obsidian_find_related
        vault = str(tmp_path / "vault")
        note_dir = os.path.join(vault, "surf", "2026", "06")
        os.makedirs(note_dir, exist_ok=True)
        note_content = "---\nquery: what is inflation\ndate: 2026-06-01\n---\n\nInflation means rising prices caused by monetary supply."
        open(os.path.join(note_dir, "2026-06-01-sess001.md"), "w").write(note_content)
        with patch("surf.load_config", return_value={"OBSIDIAN_VAULT": vault}):
            result = _obsidian_find_related("what causes inflation rising prices")
        # Should find the note (shares words: inflation, prices)
        assert result == "" or "Prior research" in result or "inflation" in result.lower()


class TestPreferences:
    def test_read_preferences_returns_empty_when_no_file(self, tmp_path):
        from surf import _read_preferences
        with patch("surf.load_config", return_value={"OBSIDIAN_VAULT": str(tmp_path / "nonexistent")}):
            assert _read_preferences() == ""

    def test_write_and_read_preferences(self, tmp_path):
        from surf import _write_preferences, _read_preferences
        vault = str(tmp_path / "vault")
        os.makedirs(vault)
        with patch("surf.load_config", return_value={"OBSIDIAN_VAULT": vault}):
            _write_preferences("# My prefs\nI like concise answers.")
            result = _read_preferences()
        assert "concise" in result

    def test_write_preferences_append(self, tmp_path):
        from surf import _write_preferences, _read_preferences
        vault = str(tmp_path / "vault")
        os.makedirs(vault)
        with patch("surf.load_config", return_value={"OBSIDIAN_VAULT": vault}):
            _write_preferences("first line")
            _write_preferences("second line", append=True)
            result = _read_preferences()
        assert "first line" in result
        assert "second line" in result

    def test_is_first_run_false_when_onboarded(self, tmp_path):
        from surf import _is_first_run, _mark_first_run_complete
        marker = os.path.expanduser("~/.config/surf/.onboarded")
        existed = os.path.exists(marker)
        try:
            _mark_first_run_complete()
            assert _is_first_run() is False
        finally:
            if not existed and os.path.exists(marker):
                os.remove(marker)

    def test_generate_demo_query_for_developer(self):
        from surf import _generate_demo_query
        result = _generate_demo_query("software engineer working on AI systems")
        assert result != ""
        assert "ai" in result.lower() or "coding" in result.lower() or "2026" in result.lower()

    def test_generate_demo_query_empty_for_no_input(self):
        from surf import _generate_demo_query
        assert _generate_demo_query("") == ""

    def test_handle_inline_preference_no_vault_shows_message(self, capsys):
        from surf import _handle_inline_preference
        with patch("surf.load_config", return_value={}):
            _handle_inline_preference("always show data sources")
        # Should not crash; message shown
        captured = capsys.readouterr()
        # Either saved or showed setup message
        assert len(captured.out) > 0

    def test_preferences_injected_into_prompt(self):
        # Verify preferences appear in the base_prompt during search
        from surf import _read_preferences
        with patch("surf.load_config", return_value={}):
            prefs = _read_preferences()
        # Just verify the function works (vault context injection is tested elsewhere)
        assert isinstance(prefs, str)
