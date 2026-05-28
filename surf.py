#!/usr/bin/env python3
import re
import os
import sys
import shutil
import requests
from bs4 import BeautifulSoup
from groq import Groq

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
    r'|^[a-zA-Z0-9-]+\.[a-zA-Z]{2,13}(/\S*)?$'  # bare domain like nasa.gov
)

def detect_input_type(text: str) -> str:
    """Return 'url' if text looks like a URL, 'query' otherwise."""
    text = text.strip()
    if _URL_PATTERN.match(text):
        return "url"
    return "query"

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

DDG_URL = "https://lite.duckduckgo.com/lite/"

def ddg_search(query: str, num_results: int = 5) -> list[dict]:
    """
    Search DuckDuckGo Lite and return list of {title, url, domain, snippet}.
    DDG Lite returns simple HTML — no JS required.
    """
    r = requests.post(
        DDG_URL,
        data={"q": query},
        headers=HEADERS,
        verify=SSL_CERT,
        timeout=10
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    results = []
    links = soup.find_all("a", class_="result-link")
    snippets_els = soup.find_all("td", class_="result-snippet")

    for link, snippet_el in zip(links, snippets_els):
        href = link.get("href", "")
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
            "snippet": snippet_el.get_text(strip=True),
        })

        if len(results) >= num_results:
            break

    return results

GROQ_MODEL = "llama-3.3-70b-versatile"
CLASSIFIER_MODEL = "llama-3.1-8b-instant"

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

def _term_width() -> int:
    return min(shutil.get_terminal_size().columns, 100)

def print_header(title: str, meta: str = "") -> None:
    """Print a Kagi-style header bar."""
    width = _term_width()
    bar = "━" * max(0, width - len(title) - 4)
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
    """Stream Groq output chunk-by-chunk to terminal. Returns full text."""
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
    """Print related topics extracted from Groq's 'Related:' section."""
    print()
    print_divider()
    print("\033[90mRelated topics:\033[0m")
    for line in related_lines:
        print(f"  \033[33m{line}\033[0m")
    print()
    print(f"\033[90m[ 1-{len(related_lines)} ] search topic   [ q ] quit\033[0m")

def search_flow(query: str, interactive: bool = True) -> tuple[list[dict], str]:
    """
    Run the search flow: DDG → Groq → display results.
    Returns (results, groq_response_text).
    """
    print_status("↳ searching DuckDuckGo...")
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
