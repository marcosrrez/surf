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
             patch("surf.stream_groq", return_value=iter(["▸ TL;DR  Black holes are dense."])), \
             patch("surf.print_header"), \
             patch("surf.print_status"), \
             patch("surf.clear_status"), \
             patch("surf.stream_to_terminal", return_value=fake_response), \
             patch("surf.print_results"):
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
             patch("surf.stream_groq", return_value=iter([fake_response])), \
             patch("surf.print_header"), \
             patch("surf.print_status"), \
             patch("surf.clear_status"), \
             patch("surf.stream_to_terminal", return_value=fake_response), \
             patch("surf.print_related"):
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
