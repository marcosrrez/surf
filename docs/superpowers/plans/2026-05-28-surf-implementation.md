# surf Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `surf` — a terminal command that searches DuckDuckGo or reads any URL, streams a clean AI-generated answer via Groq, and renders it beautifully with rich Unicode formatting.

**Architecture:** Single Python module (`surf.py`) containing all logic, invoked via a shell wrapper at `/usr/local/bin/surf` that activates a local virtualenv. For queries: fetch DDG snippets → stream Groq answer → show numbered results. For URLs: fetch HTML → extract text → stream Groq summary → show related topics.

**Tech Stack:** Python 3.14, groq SDK, requests, beautifulsoup4, pytest (all in `.venv`)

---

## Task 1: Project Setup — virtualenv and dependencies

**Files:**
- Create: `~/termbrowser/.venv/` (virtualenv)
- Create: `~/termbrowser/tests/__init__.py`
- Create: `~/termbrowser/tests/test_surf.py` (empty for now)

- [ ] **Step 1: Create virtualenv**

```bash
cd ~/termbrowser
python3 -m venv .venv
```

Expected: `.venv/` directory created with `bin/python3` inside.

- [ ] **Step 2: Install dependencies**

```bash
~/termbrowser/.venv/bin/pip install groq rich requests beautifulsoup4 pytest
```

Expected: all packages install successfully. If pip errors, run `~/termbrowser/.venv/bin/python3 -m ensurepip --upgrade` first.

- [ ] **Step 3: Verify installs**

```bash
~/termbrowser/.venv/bin/python3 -c "import groq, rich, requests, bs4; print('all good')"
```

Expected: `all good`

- [ ] **Step 4: Create test skeleton**

```bash
mkdir -p ~/termbrowser/tests
touch ~/termbrowser/tests/__init__.py
```

Create `~/termbrowser/tests/test_surf.py`:

```python
# tests/test_surf.py
import sys
sys.path.insert(0, '/Users/marcos/termbrowser')
```

- [ ] **Step 5: Verify pytest runs**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/ -v
```

Expected: `0 passed` with no errors.

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git init && git add -A && git commit -m "chore: project setup with venv and test skeleton"
```

---

## Task 2: Config loader and input type detector

**Files:**
- Create: `~/termbrowser/surf.py`
- Modify: `~/termbrowser/tests/test_surf.py`

- [ ] **Step 1: Write failing tests**

Add to `~/termbrowser/tests/test_surf.py`:

```python
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

class TestLoadConfig:
    def test_returns_api_key(self):
        config = load_config()
        assert "GROQ_API_KEY" in config
        assert len(config["GROQ_API_KEY"]) > 10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -v
```

Expected: `ImportError: cannot import name 'load_config' from 'surf'`

- [ ] **Step 3: Create surf.py with these two functions**

Create `~/termbrowser/surf.py`:

```python
#!/usr/bin/env python3
import re
import os

CONFIG_PATH = os.path.expanduser("~/.config/surf/config")

def load_config() -> dict:
    """Load key=value pairs from ~/.config/surf/config"""
    config = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    config[key.strip()] = value.strip()
    return config

# Matches: nasa.gov, nasa.gov/path, www.nasa.gov, http://nasa.gov
_URL_PATTERN = re.compile(
    r'^(https?://|www\.)'         # explicit scheme or www
    r'|^[a-zA-Z0-9-]+\.[a-zA-Z]{2,6}(/\S*)?$'  # bare domain like nasa.gov
)

def detect_input_type(text: str) -> str:
    """Return 'url' if text looks like a URL, 'query' otherwise."""
    text = text.strip()
    if _URL_PATTERN.match(text):
        return "url"
    return "query"
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -v
```

Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: config loader and URL/query input detector"
```

---

## Task 3: HTML fetcher and text extractor

**Files:**
- Modify: `~/termbrowser/surf.py`
- Modify: `~/termbrowser/tests/test_surf.py`

- [ ] **Step 1: Write failing tests**

Add to `~/termbrowser/tests/test_surf.py`:

```python
from surf import extract_text, fetch_page
from unittest.mock import patch, MagicMock

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestExtractText tests/test_surf.py::TestFetchPage -v
```

Expected: `ImportError: cannot import name 'extract_text'`

- [ ] **Step 3: Implement fetch_page and extract_text in surf.py**

Add to `~/termbrowser/surf.py` (after existing imports):

```python
import requests
from bs4 import BeautifulSoup

SSL_CERT = "/etc/ssl/cert.pem"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

def fetch_page(url: str) -> str:
    """Fetch a URL and return raw HTML. Raises requests.HTTPError on bad status."""
    if not url.startswith("http"):
        url = "https://" + url
    r = requests.get(url, headers=HEADERS, verify=SSL_CERT, timeout=15)
    r.raise_for_status()
    return r.text

def extract_text(html: str, max_words: int = 6000, return_title: bool = False):
    """
    Strip HTML and return clean text.
    If return_title=True, returns (title, text) tuple.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove noise
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "form", "noscript", "iframe"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    text = soup.get_text(separator="\n", strip=True)

    # Collapse blank lines
    lines = [l for l in text.splitlines() if l.strip()]
    text = "\n".join(lines)

    # Truncate
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]) + "\n[truncated]"

    if return_title:
        return title, text
    return text
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestExtractText tests/test_surf.py::TestFetchPage -v
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: HTML fetcher and text extractor"
```

---

## Task 4: Prompt builders

**Files:**
- Modify: `~/termbrowser/surf.py`
- Modify: `~/termbrowser/tests/test_surf.py`

- [ ] **Step 1: Write failing tests**

Add to `~/termbrowser/tests/test_surf.py`:

```python
from surf import build_search_prompt, build_read_prompt, SEARCH_SYSTEM, READ_SYSTEM

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestBuildSearchPrompt tests/test_surf.py::TestBuildReadPrompt tests/test_surf.py::TestSystemPrompts -v
```

Expected: `ImportError: cannot import name 'build_search_prompt'`

- [ ] **Step 3: Implement prompt builders in surf.py**

Add to `~/termbrowser/surf.py`:

```python
SEARCH_SYSTEM = """You are a precise research assistant answering questions using search result snippets.

Format rules (use exactly):
- First line: "▸ TL;DR  " followed by one concise sentence answer
- Blank line
- 2-4 short paragraphs of detail using plain text
- Use "•" for bullet points, never dashes or asterisks
- Use ALL CAPS sparingly for key terms (not markdown bold)
- Final line: "Sources: domain1.com · domain2.com · domain3.com"

Be direct. No filler phrases like "Great question" or "Certainly". No markdown syntax."""

READ_SYSTEM = """You are a precise content extractor summarizing a webpage.

Format rules (use exactly):
- First line: "▸ TL;DR  " followed by one concise sentence
- Blank line
- 3-6 paragraphs preserving key facts and structure
- Use "•" for bullet points, never dashes or asterisks
- Use ALL CAPS sparingly for key terms (not markdown bold)
- After the main content, add a blank line then: "Related:"
- List exactly 3 related topics the user might explore, numbered 1-3
  Example: "1. Event horizons and the Schwarzschild radius"

No filler phrases. No markdown syntax."""

def build_search_prompt(query: str, snippets: list[dict]) -> str:
    """Build Groq prompt for a search query with DDG snippets."""
    snippet_text = ""
    for i, s in enumerate(snippets, 1):
        snippet_text += f"\n[{i}] {s['title']} ({s['url']})\n{s['snippet']}\n"
    return f"Query: {query}\n\nSearch results:\n{snippet_text}"

def build_read_prompt(title: str, text: str) -> str:
    """Build Groq prompt for reading a specific page."""
    return f"Page title: {title}\n\nContent:\n{text}"
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestBuildSearchPrompt tests/test_surf.py::TestBuildReadPrompt tests/test_surf.py::TestSystemPrompts -v
```

Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: Groq prompt builders and system prompts"
```

---

## Task 5: DuckDuckGo search scraper

**Files:**
- Modify: `~/termbrowser/surf.py`
- Modify: `~/termbrowser/tests/test_surf.py`

- [ ] **Step 1: Write failing tests**

Add to `~/termbrowser/tests/test_surf.py`:

```python
from surf import ddg_search

class TestDdgSearch:
    def test_returns_list_of_dicts(self):
        mock_html = """
        <html><body>
        <table>
        <tr><td><a class="result-link" href="https://nasa.gov">NASA Black Holes</a></td></tr>
        <tr><td class="result-snippet">Objects with strong gravity.</td></tr>
        <tr><td><span class="link-text">nasa.gov</span></td></tr>
        </table>
        </body></html>
        """
        mock_response = MagicMock()
        mock_response.text = mock_html
        mock_response.raise_for_status = MagicMock()
        with patch("surf.requests.get", return_value=mock_response):
            results = ddg_search("black holes")
        assert isinstance(results, list)

    def test_result_has_required_keys(self):
        # real network call — skipped in CI, run manually to verify
        import pytest
        pytest.skip("network test — run manually with: .venv/bin/pytest -v -k test_result_has_required_keys --no-header -rN")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDdgSearch -v
```

Expected: `ImportError: cannot import name 'ddg_search'`

- [ ] **Step 3: Implement ddg_search in surf.py**

Add to `~/termbrowser/surf.py`:

```python
DDG_URL = "https://lite.duckduckgo.com/lite/"

def ddg_search(query: str, num_results: int = 5) -> list[dict]:
    """
    Search DuckDuckGo Lite and return list of {title, url, snippet}.
    DDG Lite returns simple HTML — no JS required.
    """
    r = requests.get(
        DDG_URL,
        params={"q": query},
        headers=HEADERS,
        verify=SSL_CERT,
        timeout=10
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    results = []
    # DDG Lite puts results in table rows
    # Pattern: link row → snippet row, repeating
    links = soup.find_all("a", class_="result-link")
    snippets = soup.find_all("td", class_="result-snippet")

    for link, snippet in zip(links, snippets):
        href = link.get("href", "")
        # DDG Lite hrefs are redirect URLs — extract actual URL
        if "uddg=" in href:
            from urllib.parse import unquote, urlparse, parse_qs
            actual_url = parse_qs(urlparse(href).query).get("uddg", [href])[0]
            actual_url = unquote(actual_url)
        else:
            actual_url = href

        domain = actual_url.replace("https://", "").replace("http://", "").split("/")[0]

        results.append({
            "title": link.get_text(strip=True),
            "url": actual_url,
            "domain": domain,
            "snippet": snippet.get_text(strip=True),
        })

        if len(results) >= num_results:
            break

    return results
```

- [ ] **Step 4: Run mocked test**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestDdgSearch::test_returns_list_of_dicts -v
```

Expected: `1 passed`

- [ ] **Step 5: Manually verify against real DDG**

```bash
cd ~/termbrowser && .venv/bin/python3 -c "
from surf import ddg_search
results = ddg_search('what is a black hole')
for r in results:
    print(r['title'], '|', r['domain'])
    print(' ', r['snippet'][:80])
    print()
"
```

Expected: 5 results with titles, domains, and snippets printed.

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: DuckDuckGo Lite search scraper"
```

---

## Task 6: Groq streaming client

**Files:**
- Modify: `~/termbrowser/surf.py`
- Modify: `~/termbrowser/tests/test_surf.py`

- [ ] **Step 1: Write failing tests**

Add to `~/termbrowser/tests/test_surf.py`:

```python
from surf import stream_groq

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestStreamGroq -v
```

Expected: `ImportError: cannot import name 'stream_groq'`

- [ ] **Step 3: Implement stream_groq in surf.py**

Add to `~/termbrowser/surf.py`:

```python
from groq import Groq

GROQ_MODEL = "llama-3.3-70b-versatile"

def stream_groq(prompt: str, system: str, model: str = GROQ_MODEL):
    """
    Stream a Groq completion. Yields text chunks as they arrive.
    Loads API key from ~/.config/surf/config.
    """
    config = load_config()
    api_key = config.get("GROQ_API_KEY", os.environ.get("GROQ_API_KEY", ""))
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in ~/.config/surf/config")

    client = Groq(api_key=api_key)
    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        stream=True,
        max_tokens=2048,
    )
    for chunk in stream:
        content = chunk.choices[0].delta.content
        if content:
            yield content
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestStreamGroq -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: Groq streaming client"
```

---

## Task 7: Terminal renderer

**Files:**
- Modify: `~/termbrowser/surf.py`

No unit tests for rendering (output is visual). We verify manually.

- [ ] **Step 1: Implement renderer functions in surf.py**

Add to `~/termbrowser/surf.py`:

```python
import sys
import shutil

def _term_width() -> int:
    return min(shutil.get_terminal_size().columns, 100)

def print_header(title: str, meta: str = "") -> None:
    """Print a Kagi-style header bar."""
    width = _term_width()
    bar = "━" * (width - len(title) - 4)
    print(f"\n\033[35m━━ {title} {bar}\033[0m")  # purple
    if meta:
        print(f"\033[90m{meta}\033[0m")           # gray
    print()

def print_status(message: str) -> None:
    """Print a gray status line, overwriting the previous one."""
    sys.stdout.write(f"\r\033[90m{message}\033[0m")
    sys.stdout.flush()

def clear_status() -> None:
    """Clear the status line."""
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()

def stream_to_terminal(stream) -> str:
    """Stream Groq output word-by-word to terminal. Returns full text."""
    accumulated = ""
    for chunk in stream:
        sys.stdout.write(chunk)
        sys.stdout.flush()
        accumulated += chunk
    print()  # newline after stream ends
    return accumulated

def print_divider() -> None:
    print(f"\033[90m{'─' * _term_width()}\033[0m")

def print_results(results: list[dict]) -> None:
    """Print numbered search results list."""
    print()
    print_divider()
    for i, r in enumerate(results, 1):
        print(f" \033[33m{i}\033[0m  {r['title']}")  # yellow number
        print(f"     \033[90m{r['domain']}\033[0m")
    print()
    print(f"\033[90m[ 1-{len(results)} ] read full article   [ n ] new search   [ q ] quit\033[0m")

def print_related(related_lines: list[str]) -> None:
    """Print related topics (extracted from Groq's 'Related:' section)."""
    print()
    print_divider()
    print("\033[90mRelated topics:\033[0m")
    for line in related_lines:
        print(f"  \033[33m{line}\033[0m")
    print()
    print(f"\033[90m[ 1-{len(related_lines)} ] search topic   [ q ] quit\033[0m")
```

- [ ] **Step 2: Manually verify rendering looks correct**

```bash
cd ~/termbrowser && .venv/bin/python3 -c "
from surf import print_header, print_status, clear_status, stream_to_terminal, print_results

print_header('What is a black hole', 'wikipedia.org · nasa.gov')
print_status('↳ asking Groq...')
import time; time.sleep(0.5)
clear_status()

def fake_stream():
    import time
    words = '▸ TL;DR  A black hole is a region where gravity is so strong nothing can escape.\n\nFormed from collapsed massive stars, they range from stellar-mass to supermassive scale.\n\n• Found at the center of most galaxies\n• Detected via gravitational waves and X-ray emissions\n\nSources: wikipedia.org · nasa.gov'.split(' ')
    for w in words:
        yield w + ' '
        time.sleep(0.03)

stream_to_terminal(fake_stream())
results = [
    {'title': 'Black hole — Wikipedia', 'domain': 'en.wikipedia.org'},
    {'title': 'What Are Black Holes? — NASA', 'domain': 'nasa.gov'},
    {'title': 'Black hole — Britannica', 'domain': 'britannica.com'},
]
print_results(results)
"
```

Expected: purple header, streaming text, yellow numbered results, gray dividers.

- [ ] **Step 3: Commit**

```bash
cd ~/termbrowser && git add surf.py && git commit -m "feat: terminal renderer with streaming output"
```

---

## Task 8: Search flow

**Files:**
- Modify: `~/termbrowser/surf.py`
- Modify: `~/termbrowser/tests/test_surf.py`

- [ ] **Step 1: Write failing test**

Add to `~/termbrowser/tests/test_surf.py`:

```python
from surf import search_flow

class TestSearchFlow:
    def test_returns_results_and_response(self):
        fake_results = [
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/BH",
             "domain": "en.wikipedia.org", "snippet": "A black hole is..."},
        ]
        fake_chunks = ["▸ TL;DR  Black holes are dense.", "\n\nMore detail here."]

        with patch("surf.ddg_search", return_value=fake_results), \
             patch("surf.stream_groq", return_value=iter(fake_chunks)), \
             patch("surf.print_header"), \
             patch("surf.print_status"), \
             patch("surf.clear_status"), \
             patch("surf.stream_to_terminal", return_value="▸ TL;DR  Black holes are dense.\n\nMore detail."), \
             patch("surf.print_results"):
            results, response = search_flow("black holes", interactive=False)

        assert results == fake_results
        assert "TL;DR" in response
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSearchFlow -v
```

Expected: `ImportError: cannot import name 'search_flow'`

- [ ] **Step 3: Implement search_flow in surf.py**

Add to `~/termbrowser/surf.py`:

```python
def search_flow(query: str, interactive: bool = True) -> tuple[list[dict], str]:
    """
    Run the search flow: DDG → Groq → display results.
    Returns (results, groq_response_text).
    If interactive=True, prompts user to pick a result.
    """
    print_status(f"↳ searching DuckDuckGo...")
    results = ddg_search(query)
    clear_status()

    if not results:
        print("\033[90mNo results found.\033[0m")
        return [], ""

    domains = " · ".join(r["domain"] for r in results[:3])
    print_header(query, domains)

    print_status("↳ asking Groq...")
    prompt = build_search_prompt(query, results)
    stream = stream_groq(prompt, SEARCH_SYSTEM)
    clear_status()

    response = stream_to_terminal(stream)
    print_results(results)

    if interactive:
        _handle_results_input(results)

    return results, response

def _handle_results_input(results: list[dict]) -> None:
    """Wait for user to pick a result number or quit."""
    while True:
        try:
            choice = input("\n› ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "q":
            break
        elif choice == "n":
            query = input("New search: ").strip()
            if query:
                search_flow(query)
            break
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(results):
                read_flow(results[idx]["url"])
                break
            else:
                print(f"\033[90mPick a number between 1 and {len(results)}\033[0m")
```

- [ ] **Step 4: Run test — should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSearchFlow -v
```

Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: search flow — DDG + Groq + interactive results"
```

---

## Task 9: Read flow

**Files:**
- Modify: `~/termbrowser/surf.py`
- Modify: `~/termbrowser/tests/test_surf.py`

- [ ] **Step 1: Write failing test**

Add to `~/termbrowser/tests/test_surf.py`:

```python
from surf import read_flow, parse_related_topics

class TestParseRelatedTopics:
    def test_extracts_numbered_lines_after_related(self):
        text = "Some content here.\n\nRelated:\n1. Event horizons explained\n2. Hawking radiation\n3. Neutron stars"
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
        fake_chunks = ["▸ TL;DR  Black holes are dense.\n\nContent.\n\nRelated:\n1. Neutron stars\n2. Event horizons\n3. Hawking radiation"]

        with patch("surf.fetch_page", return_value=fake_html), \
             patch("surf.stream_groq", return_value=iter(fake_chunks)), \
             patch("surf.print_header"), \
             patch("surf.print_status"), \
             patch("surf.clear_status"), \
             patch("surf.stream_to_terminal", return_value=fake_chunks[0]), \
             patch("surf.print_related"):
            read_flow("https://nasa.gov/black-holes", interactive=False)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestParseRelatedTopics tests/test_surf.py::TestReadFlow -v
```

Expected: `ImportError: cannot import name 'read_flow'`

- [ ] **Step 3: Implement parse_related_topics and read_flow in surf.py**

Add to `~/termbrowser/surf.py`:

```python
def parse_related_topics(text: str) -> list[str]:
    """Extract numbered lines from the 'Related:' section of Groq's response."""
    if "Related:" not in text:
        return []
    related_section = text.split("Related:")[-1]
    topics = []
    for line in related_section.strip().splitlines():
        line = line.strip()
        # Match lines like "1. Topic name" or "1) Topic name"
        if line and line[0].isdigit() and len(line) > 3:
            topics.append(line)  # keeps "1. Topic name" format for display
    return topics[:3]

def read_flow(url: str, interactive: bool = True) -> str:
    """
    Run the read flow: fetch URL → extract text → Groq → display.
    Returns the Groq response text.
    """
    print_status(f"↳ fetching {url[:60]}...")
    try:
        html = fetch_page(url)
    except Exception as e:
        clear_status()
        print(f"\033[31mCould not fetch page: {e}\033[0m")
        return ""

    title, text = extract_text(html, return_title=True)
    clear_status()

    domain = url.replace("https://", "").replace("http://", "").split("/")[0]
    print_header(title or url, domain)

    print_status("↳ asking Groq...")
    prompt = build_read_prompt(title, text)
    stream = stream_groq(prompt, READ_SYSTEM)
    clear_status()

    response = stream_to_terminal(stream)

    related = parse_related_topics(response)
    if related:
        print_related(related)
        if interactive:
            _handle_related_input(related)

    return response

def _handle_related_input(related: list[str]) -> None:
    """Wait for user to pick a related topic or quit."""
    while True:
        try:
            choice = input("\n› ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "q":
            break
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(related):
                # Strip the "1. " or "1) " prefix before searching
                topic = related[idx]
                if len(topic) > 2 and topic[1] in ".)":
                    topic = topic[2:].strip()
                search_flow(topic)
                break
            else:
                print(f"\033[90mPick a number between 1 and {len(related)}\033[0m")
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestParseRelatedTopics tests/test_surf.py::TestReadFlow -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: read flow — fetch, extract, Groq, related topics"
```

---

## Task 10: Intent classifier and open-in-browser

**Files:**
- Modify: `~/termbrowser/surf.py`
- Modify: `~/termbrowser/tests/test_surf.py`

- [ ] **Step 1: Write failing tests**

Add to `~/termbrowser/tests/test_surf.py`:

```python
from surf import classify_intent, open_in_browser
import json

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestClassifyIntent tests/test_surf.py::TestOpenInBrowser -v
```

Expected: `ImportError: cannot import name 'classify_intent'`

- [ ] **Step 3: Implement classify_intent and open_in_browser in surf.py**

Add to `~/termbrowser/surf.py`:

```python
import json
import subprocess

CLASSIFIER_MODEL = "llama-3.1-8b-instant"

CLASSIFIER_SYSTEM = """You are an intent classifier. Given a user query, return ONLY a JSON object — no explanation, no markdown, no code block. Just raw JSON.

Schema:
{
  "intent": one of: "informational" | "current_events" | "how_to" | "transactional" | "comparison" | "instant" | "navigation",
  "sub_type": string describing the specific type (e.g. "flights", "translation", "product_price"),
  "open_url": string URL to open directly, or null,
  "tip": short helpful tip relevant to this query, or null,
  "fetch_snippets": boolean — true if live web data is needed
}

Rules:
- "instant": translation, math, definitions, conversions — answer from knowledge, no search
- "transactional": buying, booking, reserving — construct the best deep-link URL if possible
- "navigation": user wants to go to a specific site — set open_url to that site
- "current_events": news, latest, today, recent — always fetch_snippets: true
- "how_to": step-by-step instructions — fetch_snippets: true
- "comparison": best X, vs, compare — fetch_snippets: true
- "informational": everything else — fetch_snippets: true

For transactional flights, construct Google Flights URL:
https://www.google.com/flights#search;f=ORIGIN;t=DEST;d=YYYY-MM-DD"""

_INTENT_FALLBACK = {
    "intent": "informational",
    "sub_type": "general",
    "open_url": None,
    "tip": None,
    "fetch_snippets": True,
}

def classify_intent(query: str) -> dict:
    """
    Classify the user's intent using a fast small model.
    Returns a dict with intent, sub_type, open_url, tip, fetch_snippets.
    Falls back to informational on any error.
    """
    try:
        chunks = list(stream_groq(query, CLASSIFIER_SYSTEM, model=CLASSIFIER_MODEL))
        raw = "".join(chunks).strip()
        # Strip markdown code blocks if model adds them despite instructions
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception:
        return _INTENT_FALLBACK.copy()

def open_in_browser(url: str) -> None:
    """Open a URL in the system default browser (macOS)."""
    subprocess.run(["open", url])
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestClassifyIntent tests/test_surf.py::TestOpenInBrowser -v
```

Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "feat: intent classifier and open-in-browser"
```

---

## Task 11: Main entry point and install

**Files:**
- Modify: `~/termbrowser/surf.py` (add `main()`)
- Create: `/usr/local/bin/surf` (shell wrapper — replaces existing stub)

- [ ] **Step 1: Add main() to surf.py**

Add to the bottom of `~/termbrowser/surf.py`:

```python
def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog="surf",
        description="Search or read any URL — Kagi-style, in your terminal."
    )
    parser.add_argument("input", nargs="+", help="A search query or URL")
    args = parser.parse_args()

    query = " ".join(args.input)

    try:
        if detect_input_type(query) == "url":
            read_flow(query)
            return

        # Classify intent for queries
        print_status("↳ understanding your intent...")
        intent = classify_intent(query)
        clear_status()

        if intent["intent"] == "instant":
            # Answer directly — no search needed
            print_header(query)
            stream = stream_groq(f"Answer this directly and concisely: {query}", SEARCH_SYSTEM)
            stream_to_terminal(stream)

        elif intent["intent"] == "transactional" and intent.get("open_url"):
            # Open best URL directly + show tip
            print_header(query)
            if intent.get("tip"):
                print(f"\033[33m▸ Tip\033[0m  {intent['tip']}\n")
            print(f"\033[32mOpening {intent['open_url'].split('/')[2]} in your browser...\033[0m")
            open_in_browser(intent["open_url"])

        elif intent["intent"] == "navigation" and intent.get("open_url"):
            open_in_browser(intent["open_url"])

        else:
            # All other intents: search flow
            search_flow(query)

    except KeyboardInterrupt:
        print("\n\033[90mbye\033[0m")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full test suite**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Update /usr/local/bin/surf to use the real module**

```bash
cat > /usr/local/bin/surf << 'EOF'
#!/bin/zsh
source /Users/marcos/termbrowser/.venv/bin/activate
exec python3 /Users/marcos/termbrowser/surf.py "$@"
EOF
chmod +x /usr/local/bin/surf
```

- [ ] **Step 4: End-to-end test — search**

```bash
surf what is a black hole
```

Expected: status line, purple header, streaming answer with TL;DR, numbered results, interactive prompt.

- [ ] **Step 5: End-to-end test — URL**

```bash
surf en.wikipedia.org/wiki/Black_hole
```

Expected: status line, purple header, streaming article summary, Related: section with 3 topics, interactive prompt.

- [ ] **Step 6: End-to-end test — current events**

```bash
surf latest news on Iran
```

Expected: fresh answer synthesized from DDG snippets (not Groq's training data), with source domains.

- [ ] **Step 7: Final commit**

```bash
cd ~/termbrowser && git add surf.py && git commit -m "feat: main() entry point — surf is ready"
```

---

## All tests

```bash
cd ~/termbrowser && .venv/bin/pytest tests/ -v --tb=short
```

## Quick reference

```bash
surf what is a black hole          # search query
surf latest news on Iran           # current events (uses DDG snippets)
surf nasa.gov/missions             # read specific URL
surf https://en.wikipedia.org/...  # full URL
```
