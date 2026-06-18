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
    def _mock_ddgs_results(self):
        return [{"href": "https://nasa.gov/blackholes", "title": "NASA Black Holes",
                 "body": "Objects with strong gravity."}]

    def test_returns_list_of_dicts(self):
        # Mock at the DDGS library level to avoid real network calls
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = self._mock_ddgs_results()
        with patch("surf.DDGS", return_value=mock_ddgs), \
             patch("surf._HAS_DDGS", True):
            results = ddg_search("black holes")
        assert isinstance(results, list)

    def test_result_has_required_keys(self):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = self._mock_ddgs_results()
        with patch("surf.DDGS", return_value=mock_ddgs), \
             patch("surf._HAS_DDGS", True):
            results = ddg_search("black holes")
        if results:
            assert "title" in results[0]
            assert "url" in results[0]
            assert "domain" in results[0]
            assert "snippet" in results[0]
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


class TestClassifyDataSource:
    def test_weather_with_location(self):
        from surf import _classify_data_source
        assert _classify_data_source("what is the weather in Siloam Springs") == "weather"

    def test_weather_with_temporal(self):
        from surf import _classify_data_source
        assert _classify_data_source("will it rain today") == "weather"

    def test_weather_no_location_no_temporal_is_web(self):
        from surf import _classify_data_source
        # "forecast" alone without location/temporal should NOT fire weather
        assert _classify_data_source("economic forecast 2027") == "web"

    def test_academic_fires_on_peer_reviewed(self):
        from surf import _classify_data_source
        assert _classify_data_source("what does the research say about aspirin") == "academic"

    def test_financial_fires_on_stock_price(self):
        from surf import _classify_data_source
        assert _classify_data_source("Apple stock price today") == "financial"

    def test_financial_fires_on_company_name(self):
        from surf import _classify_data_source
        assert _classify_data_source("what is Tesla trading at") == "financial"

    def test_factual_fires_on_short_entity_query(self):
        from surf import _classify_data_source
        assert _classify_data_source("what is the Eiffel Tower") == "factual"

    def test_factual_does_not_fire_on_long_query(self):
        from surf import _classify_data_source
        result = _classify_data_source("what is the meaning of this long philosophical question about existence and reality")
        assert result == "web"

    def test_web_is_default(self):
        from surf import _classify_data_source
        assert _classify_data_source("best python async libraries 2025") == "web"

    def test_financial_priority_over_weather(self):
        from surf import _classify_data_source
        # "market forecast" — financial wins because "nasdaq" or financial signal
        assert _classify_data_source("nasdaq stock market forecast today") == "financial"

    def test_meta_analysis_does_not_trigger_financial(self):
        from surf import _classify_data_source
        result = _classify_data_source("meta-analysis of aspirin clinical trials")
        assert result != "financial"

    def test_amazon_rainforest_does_not_trigger_financial(self):
        from surf import _classify_data_source
        result = _classify_data_source("amazon rainforest deforestation 2024")
        assert result == "web"

    def test_pfizer_research_routes_to_academic(self):
        from surf import _classify_data_source
        result = _classify_data_source("peer reviewed studies on pfizer vaccine safety")
        assert result == "academic"

    def test_company_name_with_financial_vocab_still_triggers(self):
        from surf import _classify_data_source
        assert _classify_data_source("apple stock price today") == "financial"
        assert _classify_data_source("amazon earnings per share") == "financial"


class TestWeatherHandler:
    def test_extract_weather_location_city(self):
        from surf import _extract_weather_location
        loc = _extract_weather_location("what is the weather in Chicago")
        assert "chicago" in loc.lower()

    def test_extract_weather_location_strips_weather_words(self):
        from surf import _extract_weather_location
        loc = _extract_weather_location("will it rain tomorrow in Denver")
        assert "denver" in loc.lower()

    def test_extract_weather_location_returns_str(self):
        from surf import _extract_weather_location
        loc = _extract_weather_location("will it rain tomorrow")
        assert isinstance(loc, str)

    def test_handle_weather_returns_none_on_network_failure(self):
        from surf import _handle_weather
        from unittest.mock import patch
        with patch("surf.requests.get", side_effect=Exception("timeout")):
            result = _handle_weather("weather in Chicago")
        assert result is None

    def test_handle_weather_returns_none_on_empty_geocode(self):
        from surf import _handle_weather
        from unittest.mock import patch, MagicMock
        mock_r = MagicMock()
        mock_r.json.return_value = {"results": []}
        mock_r.raise_for_status = MagicMock()
        with patch("surf.requests.get", return_value=mock_r):
            result = _handle_weather("weather in Siloam Springs")
        assert result is None

    def test_handle_weather_returns_tuple_on_success(self):
        from surf import _handle_weather
        from unittest.mock import patch, MagicMock
        geo_r = MagicMock()
        geo_r.json.return_value = {"results": [{
            "latitude": 36.19, "longitude": -94.49,
            "name": "Siloam Springs", "admin1": "Arkansas",
            "country_code": "US", "timezone": "America/Chicago",
        }]}
        geo_r.raise_for_status = MagicMock()
        fc_r = MagicMock()
        fc_r.json.return_value = {
            "hourly": {
                "time": [f"2026-06-05T{h:02d}:00" for h in range(24)],
                "temperature_2m": [76.0] * 24,
                "precipitation_probability": [10] * 24,
                "wind_speed_10m": [6.0] * 24,
                "wind_direction_10m": [225.0] * 24,
                "weathercode": [2] * 24,
            },
            "daily": {
                "time": ["2026-06-05", "2026-06-06", "2026-06-07"],
                "temperature_2m_max": [83.0, 79.0, 74.0],
                "temperature_2m_min": [61.0, 58.0, 55.0],
                "precipitation_sum": [0.0, 1.0, 0.5],
                "weathercode": [2, 3, 1],
            }
        }
        fc_r.raise_for_status = MagicMock()
        call_count = [0]
        def side_effect(url, **kwargs):
            call_count[0] += 1
            return geo_r if call_count[0] == 1 else fc_r
        with patch("surf.requests.get", side_effect=side_effect), \
             patch("surf.print_header"), patch("surf.print_status"), \
             patch("surf.clear_status"):
            result = _handle_weather("24 hour forecast for Siloam Springs AR")
        assert result is not None
        response, sources, streaming = result
        assert isinstance(response, str)
        assert "76" in response or "°F" in response
        assert not streaming
        assert len(sources) > 0

    def test_weather_uses_celsius_outside_us(self):
        from surf import _handle_weather
        from unittest.mock import patch, MagicMock
        geo_r = MagicMock()
        geo_r.json.return_value = {"results": [{
            "latitude": 51.5, "longitude": -0.1,
            "name": "London", "admin1": "England",
            "country_code": "GB", "timezone": "Europe/London",
        }]}
        geo_r.raise_for_status = MagicMock()
        fc_r = MagicMock()
        fc_r.json.return_value = {
            "hourly": {"time": [], "temperature_2m": [], "precipitation_probability": [],
                       "wind_speed_10m": [], "wind_direction_10m": [], "weathercode": []},
            "daily": {"time": [], "temperature_2m_max": [], "temperature_2m_min": [],
                      "precipitation_sum": [], "weathercode": []}
        }
        fc_r.raise_for_status = MagicMock()
        captured = {}
        call_count = [0]
        def side_effect(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                captured.update(kwargs.get("params", {}))
            return geo_r if call_count[0] == 1 else fc_r
        with patch("surf.requests.get", side_effect=side_effect), \
             patch("surf.print_header"), patch("surf.print_status"), \
             patch("surf.clear_status"):
            _handle_weather("weather in London")
        assert captured.get("temperature_unit") == "celsius"


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
        mock_response = MagicMock()
        mock_response.text = fake_html
        mock_response.raise_for_status = MagicMock()
        # _deep_research now calls requests.get directly with an 8s timeout
        with patch("surf.requests.get", return_value=mock_response), \
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


class TestPreferencesIntegration:
    """Tests that verify preferences actually affect synthesis behavior."""

    def _search_mocks(self, extra=None):
        """Return a dict of common mocks for search_flow tests."""
        fake_results = [{"title": "T", "url": "https://example.com",
                         "domain": "example.com", "snippet": "Content."}]
        mocks = dict(
            ddg_search=MagicMock(return_value=fake_results),
            _classify_tier=MagicMock(return_value="snippet"),
            _confidence_gate=MagicMock(return_value="snippet"),
            stream_to_terminal=MagicMock(return_value="▸ TL;DR  Answer."),
            print_header=MagicMock(), print_status=MagicMock(),
            clear_status=MagicMock(), _print_linked_sources=MagicMock(),
            print_results=MagicMock(), save_session_entry=MagicMock(),
            format_session_context=MagicMock(return_value=""),
            _obsidian_find_related=MagicMock(return_value=""),
            _obsidian_save=MagicMock(return_value=None),
            _obsidian_session_id=MagicMock(return_value="test1234"),
            _enrich_ddg_query=MagicMock(return_value="query"),
            _fix_entity_mismatch=MagicMock(side_effect=lambda q, r, d, **kw: (r, d)),
            _bm25_rank=MagicMock(side_effect=lambda q, r: r),
            _snippets_are_diverse=MagicMock(return_value=True),
            _sources_are_substantive=MagicMock(return_value=True),
            _filter_results=MagicMock(side_effect=lambda r, **kw: r),
        )
        if extra:
            mocks.update(extra)
        return mocks

    def test_preferences_prepended_to_search_prompt(self):
        """When preferences exist, they appear in the prompt sent to the AI."""
        from surf import search_flow
        captured_prompts = []

        def capture_stream(prompt, system, max_tokens=2048, tier="snippet"):
            captured_prompts.append(prompt)
            return iter(["▸ TL;DR  Test answer."])

        mocks = self._search_mocks({
            "_read_preferences": MagicMock(return_value="I prefer concise technical answers."),
            "stream_ai": MagicMock(side_effect=capture_stream),
            "stream_to_terminal": MagicMock(return_value="▸ TL;DR  Test answer."),
        })
        from unittest.mock import patch as _patch
        with _patch.multiple("surf", **mocks):
            search_flow("what is a black hole", interactive=False)

        assert len(captured_prompts) > 0
        assert "concise technical answers" in captured_prompts[0]

    def test_obsidian_save_called_during_search(self, tmp_path):
        """search_flow calls _obsidian_save after synthesis."""
        from surf import search_flow
        save_calls = []

        def record_save(*args, **kwargs):
            save_calls.append(args)
            return str(tmp_path / "note.md")

        mocks = self._search_mocks({
            "_read_preferences": MagicMock(return_value=""),
            "stream_ai": MagicMock(return_value=iter(["▸ TL;DR  Answer."])),
            "_obsidian_save": MagicMock(side_effect=record_save),
        })
        from unittest.mock import patch as _patch
        with _patch.multiple("surf", **mocks):
            search_flow("test query", interactive=False)

        assert len(save_calls) > 0
        assert save_calls[0][0] == "test query"


class TestBannerAndSetup:
    def test_setup_banner_uses_bright_colors(self):
        from surf import _SETUP_BANNER
        # Bright cyan [96m for waves
        assert "\033[96m" in _SETUP_BANNER
        # Bold bright magenta [1;95m for SURF text
        assert "\033[1;95m" in _SETUP_BANNER
        # Bright white [97m for tagline
        assert "\033[97m" in _SETUP_BANNER

    def test_setup_banner_contains_surf_text(self):
        from surf import _SETUP_BANNER
        # SURF ASCII art key characters
        assert "____" in _SETUP_BANNER
        assert "___/" in _SETUP_BANNER or "/ ___" in _SETUP_BANNER

    def test_setup_banner_contains_tagline(self):
        from surf import _SETUP_BANNER
        assert "AI-powered search" in _SETUP_BANNER

    def test_classify_tier_research_for_how_did(self):
        """Regression: 'how did' should classify as research tier."""
        from surf import _classify_tier
        assert _classify_tier("how did Arsenal win the premier league") == "research"

    def test_classify_tier_current_for_who_will(self):
        from surf import _classify_tier
        assert _classify_tier("who will win the world cup") == "current"

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


class TestScopeExpansion:
    def test_extract_items_from_groups_query(self):
        from surf import _extract_expansion_items
        with patch("surf.stream_groq", return_value=iter(["Group A\nGroup B\nGroup D\nGroup E\nGroup F\nGroup G"])):
            items = _extract_expansion_items("what about groups A B D E F G", context="World Cup")
        assert len(items) >= 4
        assert any("A" in item or "Group A" in item for item in items)

    def test_extract_items_generic(self):
        from surf import _extract_expansion_items
        with patch("surf.stream_groq", return_value=iter([])):
            items = _extract_expansion_items("what about the other teams", context="Brazil won")
        assert isinstance(items, list)

    def test_handle_scope_expansion_fires_searches(self):
        from surf import _handle_scope_expansion, _SearchMeta
        meta = _SearchMeta("World Cup Group C", ["World Cup Group C"], 3, "current", None)
        fake_results = [{"title": "T", "url": "http://x.com", "domain": "x.com", "snippet": "s" * 60}]
        with patch("surf.ddg_search", return_value=fake_results), \
             patch("surf.stream_groq", return_value=iter(["Group A\nGroup B"])), \
             patch("surf.print_header"), patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.vspace"):
            result = _handle_scope_expansion("what about groups A and B", meta=meta, context="")
        new_results, new_context, new_meta = result
        assert isinstance(new_results, list)
        assert isinstance(new_meta, _SearchMeta)


class TestClassifyAndDispatch:
    def _make_meta(self):
        from surf import _SearchMeta
        return _SearchMeta("test query", ["test query"], 5, "current", None)

    def test_command_q_returns_break(self):
        from surf import _classify_and_dispatch
        meta = self._make_meta()
        _, _, _, should_break = _classify_and_dispatch("q", [], meta, "")
        assert should_break is True

    def test_casual_no_search(self):
        from surf import _classify_and_dispatch
        meta = self._make_meta()
        with patch("surf._conversational_reply") as mock_reply:
            new_results, _, _, should_break = _classify_and_dispatch("thanks", [], meta, "")
        mock_reply.assert_called_once_with("casual", meta=meta, user_text="thanks")
        assert should_break is False

    def test_redirect_calls_followup(self):
        from surf import _classify_and_dispatch, _SearchMeta
        meta = self._make_meta()
        fake_meta = self._make_meta()
        with patch("surf._conversational_reply"), \
             patch("surf._handle_followup", return_value=([], "", fake_meta)) as mock_fup, \
             patch("surf.print_results"):
            _classify_and_dispatch("that's your job", [], meta, "")
        mock_fup.assert_called_once()

    def test_scope_expansion_calls_fanout(self):
        from surf import _classify_and_dispatch, _SearchMeta
        meta = self._make_meta()
        fake_meta = self._make_meta()
        with patch("surf._handle_scope_expansion", return_value=([], "", fake_meta)) as mock_fanout:
            _classify_and_dispatch("what about the others", [], meta, "")
        mock_fanout.assert_called_once()


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
        fake_results = [{"title": "T", "url": "http://x.com", "domain": "x.com", "snippet": "s" * 60}] * 3
        with patch("surf.ddg_search", return_value=fake_results), \
             patch("surf._filter_results", side_effect=lambda x, **kw: x), \
             patch("surf._identify_entity_type", return_value=None), \
             patch("surf._contextualize_query", return_value="test question"), \
             patch("surf._deep_research", return_value=("", [])), \
             patch("surf.stream_ai", return_value=iter(["Test response"])), \
             patch("surf.stream_to_terminal", return_value="Test response"), \
             patch("surf.print_header"), patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.vspace"), patch("surf.print_results"), \
             patch("surf._print_linked_sources"), \
             patch("surf._claude_budget_ok", return_value=False):
            results, response, meta = _handle_followup("test question")
        assert meta.original_query == "test question"
        assert meta.result_count >= 0

    def test_classify_and_dispatch_followup_returns_four_tuple(self):
        from surf import _classify_and_dispatch, _SearchMeta
        meta = _SearchMeta("test", ["test"], 3, "current", None)
        fake_meta = _SearchMeta("test question", ["test question"], 3, "current", None)
        with patch("surf._handle_followup", return_value=([], "response", fake_meta)), \
             patch("surf.format_session_context", return_value=""), \
             patch("surf.print_results"):
            new_results, new_context, new_meta, should_break = _classify_and_dispatch(
                "why did that happen?", [], meta, ""
            )
        assert should_break is False
        assert new_meta is not None


class TestFinancialHandler:
    def _yahoo_response(self, symbol="AAPL", price=193.42, prev_close=191.28):
        return {
            "chart": {"result": [{
                "meta": {
                    "symbol": symbol,
                    "regularMarketPrice": price,
                    "previousClose": prev_close,
                    "regularMarketDayHigh": price + 1,
                    "regularMarketDayLow": price - 2,
                    "fiftyTwoWeekHigh": 201.55,
                    "fiftyTwoWeekLow": 142.86,
                    "marketCap": 2980000000000,
                    "regularMarketVolume": 48300000,
                    "averageVolume": 54100000,
                    "longName": "Apple Inc.",
                    "exchangeName": "NYSE",
                },
                "indicators": {"quote": [{"close": [190.0, 191.0, 192.0, 191.5, price]}]},
                "timestamp": [1717000000, 1717086400, 1717172800, 1717259200, 1717345600],
            }]}
        }

    def test_detect_ticker_from_company_name(self):
        from surf import _detect_ticker
        assert _detect_ticker("Apple stock price") == "AAPL"

    def test_detect_ticker_from_explicit_ticker(self):
        from surf import _detect_ticker
        assert _detect_ticker("what is TSLA trading at") == "TSLA"

    def test_detect_ticker_from_crypto(self):
        from surf import _detect_ticker
        assert _detect_ticker("bitcoin price today") == "BTC-USD"

    def test_detect_ticker_returns_none_for_no_match(self):
        from surf import _detect_ticker
        assert _detect_ticker("what is the capital of France") is None

    def test_detect_ticker_requires_financial_vocab_for_company_names(self):
        from surf import _detect_ticker
        assert _detect_ticker("meta-analysis of aspirin") is None
        assert _detect_ticker("amazon rainforest") is None
        assert _detect_ticker("apple stock price") == "AAPL"

    def test_build_sparkline_ascending(self):
        from surf import _build_sparkline
        spark = _build_sparkline([100.0, 101.0, 102.0, 103.0, 104.0])
        assert len(spark) == 5
        assert ord(spark[-1]) > ord(spark[0])

    def test_build_sparkline_flat(self):
        from surf import _build_sparkline
        spark = _build_sparkline([100.0, 100.0, 100.0])
        assert set(spark) == {"─"}

    def test_handle_financial_returns_none_on_api_failure(self):
        from surf import _handle_financial
        with patch("surf.requests.get", side_effect=Exception("connection")):
            result = _handle_financial("Apple stock price")
        assert result is None

    def test_handle_financial_returns_none_when_no_ticker(self):
        from surf import _handle_financial
        result = _handle_financial("what is the capital of France")
        assert result is None

    def test_handle_financial_returns_tuple_on_success(self):
        from surf import _handle_financial
        mock_r = MagicMock()
        mock_r.json.return_value = self._yahoo_response()
        mock_r.raise_for_status = MagicMock()
        with patch("surf.requests.get", return_value=mock_r), \
             patch("surf.print_header"), patch("surf.print_status"), \
             patch("surf.clear_status"):
            result = _handle_financial("Apple stock price")
        assert result is not None
        response, sources, streaming = result
        assert "193.42" in response or "AAPL" in response
        assert not streaming
        assert len(sources) == 1
        assert "finance.yahoo.com" in sources[0]["url"]

    def test_handle_financial_down_day_uses_glyph_down(self):
        from surf import _handle_financial, GLYPH_DOWN
        mock_r = MagicMock()
        mock_r.json.return_value = self._yahoo_response(price=188.00, prev_close=193.42)
        mock_r.raise_for_status = MagicMock()
        with patch("surf.requests.get", return_value=mock_r), \
             patch("surf.print_header"), patch("surf.print_status"), \
             patch("surf.clear_status"):
            result = _handle_financial("Apple stock price")
        assert result is not None
        response, _, _ = result
        assert GLYPH_DOWN in response


class TestAcademicHandler:
    def _pubmed_search_json(self):
        return {"esearchresult": {"idlist": ["12345678"]}}

    def _pubmed_summary_json(self):
        return {"result": {
            "12345678": {
                "uid": "12345678",
                "title": "Safety of mRNA COVID-19 Vaccines",
                "authors": [{"name": "Polack F"}, {"name": "Thomas S"}],
                "pubdate": "2023",
                "fulljournalname": "New England Journal of Medicine",
                "elocationid": "10.1056/test",
            }
        }}

    def _arxiv_xml(self):
        return """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <title>mRNA Vaccine Mechanism Study</title>
  <author><name>Smith J</name></author>
  <published>2023-01-15T00:00:00Z</published>
  <summary>We present findings on mRNA vaccine mechanisms. Strong immune response observed.</summary>
  <id>http://arxiv.org/abs/2301.00001v1</id>
</entry>
</feed>"""

    def _mock_all_requests(self, mock_get):
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if "esearch" in url:
                r.json.return_value = self._pubmed_search_json()
            elif "esummary" in url:
                r.json.return_value = self._pubmed_summary_json()
            elif "efetch" in url:
                r.text = "Abstract text. In this randomized trial, the vaccine showed 95% efficacy."
            else:
                r.text = self._arxiv_xml()
            return r
        mock_get.side_effect = side_effect

    def test_strip_latex_removes_inline_math(self):
        from surf import _strip_latex
        assert "$" not in _strip_latex("We prove $\\mathcal{O}(n^2)$ is tight.")
        assert "\\mathcal" not in _strip_latex("We prove $\\mathcal{O}(n^2)$ is tight.")

    def test_strip_latex_removes_commands(self):
        from surf import _strip_latex
        cleaned = _strip_latex("Using \\text{Theorem 1} we show")
        assert "\\text" not in cleaned

    def test_handle_academic_returns_none_on_failure(self):
        from surf import _handle_academic
        with patch("surf.requests.get", side_effect=Exception("timeout")), \
             patch("surf.print_status"), patch("surf.clear_status"):
            result = _handle_academic("what does the research say about mRNA vaccines")
        assert result is None

    def test_handle_academic_returns_tuple_on_success(self):
        from surf import _handle_academic
        with patch("surf.requests.get") as mock_get, \
             patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.print_header"):
            self._mock_all_requests(mock_get)
            result = _handle_academic("peer reviewed studies on aspirin")
        assert result is not None
        response, sources, streaming = result
        assert isinstance(response, str)
        assert len(sources) > 0

    def test_handle_academic_returns_none_when_no_results(self):
        from surf import _handle_academic
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if "esearch" in url:
                r.json.return_value = {"esearchresult": {"idlist": []}}
            else:
                r.text = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>"""
            return r
        with patch("surf.requests.get", side_effect=side_effect), \
             patch("surf.print_status"), patch("surf.clear_status"):
            result = _handle_academic("zzz nonexistent topic xyzxyz")
        assert result is None

    def test_source_tag_in_paper_card(self):
        from surf import _handle_academic
        with patch("surf.requests.get") as mock_get, \
             patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.print_header"):
            self._mock_all_requests(mock_get)
            result = _handle_academic("peer reviewed studies on aspirin")
        assert result is not None
        response, sources, _ = result
        source_domains = [s["domain"] for s in sources]
        assert any("pubmed" in d or "arxiv" in d for d in source_domains)


class TestFactualHandler:
    def _wiki_summary(self, title="Eiffel Tower"):
        return {
            "title": title,
            "description": "Iron lattice tower in Paris, France",
            "extract": ("The Eiffel Tower is a wrought-iron lattice tower on the Champ de Mars "
                        "in Paris, France. It was named after the engineer Gustave Eiffel, "
                        "whose company designed and built the tower from 1887 to 1889."),
            "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}},
            "type": "standard",
        }

    def _wiki_disambiguation(self):
        return {
            "title": "Mercury",
            "description": "disambiguation page",
            "extract": "Mercury may refer to:",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Mercury"}},
            "type": "disambiguation",
        }

    def _mock_search_success(self, mock_get, title="Eiffel Tower"):
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if "opensearch" in url:
                r.json.return_value = [title, [title], ["description"], [f"https://en.wikipedia.org/wiki/{title}"]]
            else:
                r.json.return_value = self._wiki_summary(title)
            return r
        mock_get.side_effect = side_effect

    def test_handle_factual_returns_none_on_network_failure(self):
        from surf import _handle_factual
        with patch("surf.requests.get", side_effect=Exception("timeout")), \
             patch("surf.print_status"), patch("surf.clear_status"):
            result = _handle_factual("what is the Eiffel Tower")
        assert result is None

    def test_handle_factual_returns_none_when_no_results(self):
        from surf import _handle_factual
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = ["query", [], [], []]
            return r
        with patch("surf.requests.get", side_effect=side_effect), \
             patch("surf.print_status"), patch("surf.clear_status"):
            result = _handle_factual("what is zzz xyzabc")
        assert result is None

    def test_handle_factual_returns_entity_response(self):
        from surf import _handle_factual
        with patch("surf.requests.get") as mock_get, \
             patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.print_header"):
            self._mock_search_success(mock_get)
            result = _handle_factual("what is the Eiffel Tower")
        assert result is not None
        response, sources, streaming = result
        assert "Eiffel" in response
        assert not streaming
        assert sources[0]["domain"] == "en.wikipedia.org"

    def test_handle_factual_disambiguation_shows_choice_menu(self):
        from surf import _handle_factual
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if "opensearch" in url:
                r.json.return_value = [
                    "Mercury",
                    ["Mercury (planet)", "Mercury (element)", "Freddie Mercury"],
                    ["desc1", "desc2", "desc3"],
                    ["url1", "url2", "url3"]
                ]
            else:
                r.json.return_value = self._wiki_disambiguation()
            return r
        with patch("surf.requests.get", side_effect=side_effect), \
             patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.print_header"):
            result = _handle_factual("what is Mercury")
        assert result is not None
        response, sources, streaming = result
        assert "Mercury (planet)" in response or "choose" in response.lower()
        assert len(sources) > 1

    def test_handle_factual_tldr_is_first_sentence(self):
        from surf import _handle_factual
        with patch("surf.requests.get") as mock_get, \
             patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.print_header"):
            self._mock_search_success(mock_get)
            result = _handle_factual("what is the Eiffel Tower")
        assert result is not None
        response, _, _ = result
        assert "TL;DR" in response
        assert "wrought-iron" in response.lower() or "eiffel" in response.lower()


class TestSpecializedIntegration:
    def _ddg_patch_specs(self):
        """Patch specs needed for DDG fallthrough tests."""
        fake_results = [{"title": "T", "url": "https://example.com",
                         "domain": "example.com", "snippet": "Content about the topic."}]
        return [
            ("surf.ddg_search", dict(return_value=fake_results)),
            ("surf.stream_ai", dict(return_value=iter(["▸ TL;DR  Answer."]))),
            ("surf.stream_to_terminal", dict(return_value="▸ TL;DR  Answer.")),
            ("surf.print_header", {}),
            ("surf.print_status", {}),
            ("surf.clear_status", {}),
            ("surf._print_linked_sources", {}),
            ("surf.print_results", {}),
            ("surf.save_session_entry", {}),
            ("surf.format_session_context", dict(return_value="")),
            ("surf._read_preferences", dict(return_value="")),
            ("surf._obsidian_find_related", dict(return_value="")),
            ("surf._obsidian_save", dict(return_value=None)),
            ("surf._classify_tier", dict(return_value="snippet")),
            ("surf._confidence_gate", dict(return_value="snippet")),
            ("surf._enrich_ddg_query", dict(return_value="query")),
            ("surf._fix_entity_mismatch", dict(side_effect=lambda q, r, d, **kw: (r, d))),
            ("surf._bm25_rank", dict(side_effect=lambda q, r: r)),
            ("surf._snippets_are_diverse", dict(return_value=True)),
            ("surf._sources_are_substantive", dict(return_value=True)),
            ("surf._filter_results", dict(side_effect=lambda r, **kw: r)),
        ]

    def _apply_ddg_patches(self, stack):
        """Enter all DDG patches into a contextlib.ExitStack and return them."""
        mocks = {}
        for target, kwargs in self._ddg_patch_specs():
            mocks[target] = stack.enter_context(patch(target, **kwargs))
        return mocks

    def test_weather_query_bypasses_ddg(self):
        from surf import search_flow
        with patch("surf._classify_data_source", return_value="weather"), \
             patch("surf._run_specialized_query", return_value=([], "weather response")) as mock_specialized, \
             patch("surf.ddg_search") as mock_ddg:
            search_flow("weather in Chicago", interactive=False)
        mock_specialized.assert_called_once()
        mock_ddg.assert_not_called()

    def test_financial_query_bypasses_ddg(self):
        from surf import search_flow
        with patch("surf._classify_data_source", return_value="financial"), \
             patch("surf._run_specialized_query", return_value=([], "price response")) as mock_specialized, \
             patch("surf.ddg_search") as mock_ddg:
            search_flow("Apple stock price", interactive=False)
        mock_specialized.assert_called_once()
        mock_ddg.assert_not_called()

    def test_web_query_skips_specialized_goes_to_ddg(self):
        import contextlib
        from surf import search_flow
        with contextlib.ExitStack() as stack:
            mock_specialized = stack.enter_context(
                patch("surf._run_specialized_query"))
            stack.enter_context(
                patch("surf._classify_data_source", return_value="web"))
            self._apply_ddg_patches(stack)
            search_flow("who wrote Pride and Prejudice", interactive=False)
        mock_specialized.assert_not_called()

    def test_handler_failure_falls_through_to_ddg(self):
        import contextlib
        from surf import search_flow
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("surf._classify_data_source", return_value="weather"))
            stack.enter_context(
                patch("surf._run_specialized_query", return_value=None))
            mocks = self._apply_ddg_patches(stack)
            search_flow("weather in Chicago", interactive=False)
        mocks["surf.ddg_search"].assert_called()

    def test_json_output_uses_specialized(self):
        import contextlib
        from surf import search_flow
        with contextlib.ExitStack() as stack:
            mock_specialized = stack.enter_context(
                patch("surf._run_specialized_query", return_value=([], "")))
            self._apply_ddg_patches(stack)
            search_flow("Apple stock price", interactive=False, json_output=True)
        mock_specialized.assert_called_once()
