# tests/test_surf.py
from surf import load_config, detect_input_type

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
