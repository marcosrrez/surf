# tests/test_surf.py
from surf import load_config, detect_input_type, extract_text, fetch_page
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
