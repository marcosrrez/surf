# tests/test_surf.py
from surf import load_config, detect_input_type, extract_text, fetch_page
from surf import build_search_prompt, build_read_prompt, SEARCH_SYSTEM, READ_SYSTEM
from surf import ddg_search
from surf import stream_groq
from surf import search_flow
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
