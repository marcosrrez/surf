#!/usr/bin/env python3
import re
import os
import sys
import json
import shutil
import subprocess
import requests
import atexit
import threading
import itertools
import time
from bs4 import BeautifulSoup
import groq
from groq import Groq

try:
    import readline as _readline
    _HAS_READLINE = True
except ImportError:
    _HAS_READLINE = False

try:
    from prompt_toolkit import prompt as _ptk_prompt
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import Completer, Completion
    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False

try:
    import anthropic as _anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False

try:
    from ddgs import DDGS
    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False

try:
    from rich.console import Console
    from rich.table import Table as RichTable

    _rich_console = Console()

    def _render_rich_tables(text: str) -> str:
        """
        Detect │-delimited table blocks in text, render them with rich,
        and return the text with table blocks replaced by rendered output.
        This is called AFTER streaming to reprint tables cleanly.
        """
        lines = text.split("\n")
        result_lines = []
        table_block = []
        in_table = False

        for line in lines:
            is_table_row = line.strip().startswith("│") and line.strip().endswith("│")
            if is_table_row:
                in_table = True
                table_block.append(line)
            else:
                if in_table and table_block:
                    # Render the accumulated table block
                    rendered = _table_block_to_rich(table_block)
                    result_lines.append(rendered)
                    table_block = []
                    in_table = False
                result_lines.append(line)

        if table_block:
            result_lines.append(_table_block_to_rich(table_block))

        return "\n".join(result_lines)

    def _table_block_to_rich(rows: list[str]) -> str:
        """Convert a list of │-delimited row strings to a rich-rendered table string."""
        import io
        parsed = []
        for row in rows:
            # Split on │, strip whitespace, drop empty first/last from leading/trailing │
            cells = [c.strip() for c in row.split("│")]
            cells = [c for c in cells if c != ""]
            if cells:
                parsed.append(cells)

        if not parsed:
            return "\n".join(rows)

        # First row is the header if it differs from subsequent rows
        headers = parsed[0]
        data_rows = parsed[1:]

        table = RichTable(show_header=True, header_style="bold cyan", border_style="dim")
        for h in headers:
            table.add_column(h)
        for row in data_rows:
            # Pad or truncate to match header count
            padded = row + [""] * max(0, len(headers) - len(row))
            table.add_row(*padded[:len(headers)])

        # Capture rich output to a string
        buf = io.StringIO()
        console = Console(file=buf, highlight=False)
        console.print(table)
        return buf.getvalue().rstrip()

except ImportError:
    _rich_console = None

    def _render_rich_tables(text: str) -> str:
        return text

    def _table_block_to_rich(rows: list[str]) -> str:
        return "\n".join(rows)

CONFIG_PATH = os.path.expanduser("~/.config/surf/config")
SESSION_FILE = os.path.expanduser("~/.config/surf/session.json")
SESSION_TTL = 4 * 60 * 60  # 4 hours — one work session

def load_session() -> list[dict]:
    """Load session entries, returning empty list if expired or missing."""
    try:
        with open(SESSION_FILE) as f:
            data = json.load(f)
        if time.time() > data.get("expires_at", 0):
            return []  # expired
        return data.get("entries", [])
    except Exception:
        return []

def save_session_entry(query: str, entry_type: str, summary: str) -> None:
    """Append a new entry to the session, creating or refreshing as needed."""
    entries = load_session()
    # Remove duplicate queries
    entries = [e for e in entries if e.get("query") != query]
    entries.append({
        "query": query,
        "type": entry_type,
        "summary": summary[:500],  # cap to avoid bloat
        "timestamp": int(time.time()),
    })
    # Keep last 10 entries
    entries = entries[-10:]
    try:
        os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
        with open(SESSION_FILE, "w") as f:
            json.dump({
                "expires_at": int(time.time()) + SESSION_TTL,
                "entries": entries,
            }, f)
    except Exception:
        pass

def format_session_context() -> str:
    """Return session entries as a context string for Groq prompts."""
    entries = load_session()
    if not entries:
        return ""
    lines = ["Earlier in this session:"]
    for e in entries[-5:]:  # last 5 only
        lines.append(f"  [{e['type']}] {e['query']}: {e['summary']}")
    return "\n".join(lines)

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

# Matches: nasa.gov, nasa.gov/path, www.nasa.gov, http://nasa.gov,
# en.wikipedia.org, en.wikipedia.org/wiki/Black_hole
_URL_PATTERN = re.compile(
    r'^(https?://|www\.)'         # explicit scheme or www
    r'|^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,13}(/\S*)?$'  # bare/subdomain like nasa.gov or en.wikipedia.org
)

def detect_input_type(text: str) -> str:
    """Return 'url' if text looks like a URL, 'query' otherwise."""
    text = text.strip()
    if _URL_PATTERN.match(text):
        return "url"
    return "query"

SSL_CERT = "/etc/ssl/cert.pem"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # Omit Accept-Encoding so requests uses its own transparent decompression
    # (gzip/deflate). Advertising "br" causes DDG to return Brotli-compressed
    # content that requests cannot decompress without the optional brotli
    # package, resulting in garbled bytes and zero parsed results.
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}

def fetch_page(url: str) -> str:
    """Fetch a URL and return raw HTML. Raises requests.HTTPError on bad status."""
    if not url.startswith("http"):
        url = "https://" + url
    r = requests.get(url, headers=HEADERS, verify=SSL_CERT, timeout=25)
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

    # Collapse blank lines and strip navigation noise
    nav_patterns = {
        "MEN", "WOMEN", "ACADEMY", "CLUB", "Follow Us", "Login",
        "Create account", "Switch User", "Become a member",
        "Ticket Info", "See Full List", "GET READY FOR",
        "Fill in our form", "Report abuse", "Check out our",
        "Help Centre", "New Enquiry", "Website feedback",
    }
    lines = []
    for l in text.splitlines():
        stripped = l.strip()
        if not stripped:
            continue
        # Skip lines that are purely navigation labels or very short repeated items
        if stripped in nav_patterns:
            continue
        # Skip lines that look like fixture scores (e.g. "1  Arsenal  2  Chelsea")
        if stripped.replace(" ", "").replace("\t", "").lstrip("0123456789").startswith("Arsenal") and len(stripped) < 40:
            continue
        lines.append(l)
    text = "\n".join(lines)

    # Truncate
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]) + "\n[truncated]"

    if return_title:
        return title, text
    return text

def extract_schema_data(html: str) -> dict:
    """
    Extract schema.org JSON-LD structured data from HTML.
    Returns a dict of the most useful fields found, or empty dict.
    Common schemas: LocalBusiness, Person, Product, Article, FAQPage.
    """
    import json as _json
    soup = BeautifulSoup(html, "html.parser")
    schema_data = {}

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            # Handle both single objects and arrays
            items = data if isinstance(data, list) else [data]
            for item in items:
                schema_type = item.get("@type", "")
                # LocalBusiness, MedicalBusiness, Physician, etc.
                if any(t in schema_type for t in ["Business", "Organization", "Person", "Medical"]):
                    for field in ["name", "telephone", "email", "address",
                                  "priceRange", "openingHours", "url",
                                  "description", "areaServed", "currenciesAccepted"]:
                        val = item.get(field)
                        if val:
                            if isinstance(val, dict):
                                # address object
                                parts = [val.get(k, "") for k in
                                         ["streetAddress", "addressLocality",
                                          "addressRegion", "postalCode"]]
                                val = ", ".join(p for p in parts if p)
                            schema_data[field] = val
                # Product pricing
                if "Product" in schema_type:
                    offers = item.get("offers", {})
                    if isinstance(offers, dict):
                        price = offers.get("price") or offers.get("lowPrice")
                        currency = offers.get("priceCurrency", "USD")
                        if price:
                            schema_data["price"] = f"{currency} {price}"
        except Exception:
            continue

    return schema_data

_VALUABLE_PAGE_KEYWORDS = {
    "contact", "rate", "fee", "price", "cost", "about", "service",
    "faq", "info", "team", "staff", "appointment", "book", "schedule",
}

JINA_BASE = "https://r.jina.ai/"

def _is_spa_shell(html: str) -> bool:
    """Return True if html looks like a JS SPA shell with no real content."""
    if len(html) > 15000:
        return False  # too big to be a shell
    # SPA shells typically have a module script and almost no body text
    has_module_script = 'type="module"' in html or "type='module'" in html
    soup = BeautifulSoup(html, "html.parser")
    body_text = soup.get_text(strip=True)
    return has_module_script and len(body_text) < 500

_UNCERTAINTY_SIGNALS = [
    "to be confirmed", "to be determined", "tbd", "yet to be announced",
    "not yet confirmed", "not yet announced", "will be confirmed",
    "will be determined", "has not been announced", "have not been announced",
    "remains to be", "is yet to", "are yet to",
]

def _has_uncertainty(text: str) -> bool:
    """Return True if response contains stale/uncertain data signals."""
    text_lower = text.lower()
    return any(signal in text_lower for signal in _UNCERTAINTY_SIGNALS)

def _fetch_with_jina(url: str) -> str:
    """
    Fetch a JS-rendered page using Jina.ai Reader.
    Returns rendered markdown text, or empty string on failure.
    """
    jina_url = JINA_BASE + url
    try:
        r = requests.get(
            jina_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain"},
            verify=SSL_CERT,
            timeout=20,
        )
        r.raise_for_status()
        return r.text
    except Exception:
        return ""

def _get_sitemap_urls(base_url: str) -> list[str]:
    """Fetch sitemap.xml and return a list of page URLs."""
    import re
    try:
        r = requests.get(
            base_url.rstrip("/") + "/sitemap.xml",
            headers=HEADERS,
            verify=SSL_CERT,
            timeout=8,
        )
        if not r.ok:
            return []
        return re.findall(r"<loc>(.*?)</loc>", r.text)
    except Exception:
        return []

def _fetch_sub_pages(html: str, base_url: str, max_pages: int = 3) -> tuple[str, list[str]]:
    """
    Fetch relevant sub-pages for a URL.
    For normal sites: extract links from HTML.
    For JS SPAs: use sitemap.xml + Jina.ai reader.
    Returns (combined_text, list_of_fetched_page_labels).
    """
    from urllib.parse import urljoin, urlparse
    base_domain = urlparse(base_url).netloc
    extra_texts = []
    fetched_labels = []

    if _is_spa_shell(html):
        # SPA: use sitemap to discover pages, Jina to read them
        sitemap_urls = _get_sitemap_urls(base_url)
        candidate_urls = []
        for url in sitemap_urls:
            if url == base_url or url == base_url.rstrip("/"):
                continue  # skip homepage, already read
            path = urlparse(url).path.lower()
            if any(kw in path for kw in _VALUABLE_PAGE_KEYWORDS):
                label = path.strip("/").split("/")[-1] or path.strip("/")
                candidate_urls.append((label, url))

        for label, page_url in candidate_urls[:max_pages]:
            jina_text = _fetch_with_jina(page_url)
            if jina_text and len(jina_text.strip()) > 100:
                extra_texts.append(f"\n\n--- {label} ---\n{jina_text[:2000]}")
                fetched_labels.append(label[:20])
    else:
        # Normal site: extract internal links from HTML
        soup = BeautifulSoup(html, "html.parser")
        candidate_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("#") or href.startswith("mailto:"):
                continue
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.netloc != base_domain:
                continue
            path = parsed.path.lower()
            link_text = a.get_text(strip=True).lower()
            if any(kw in path or kw in link_text for kw in _VALUABLE_PAGE_KEYWORDS):
                candidate_links.append((link_text or path.strip("/"), full_url))

        seen = set()
        unique_links = []
        for text, url in candidate_links:
            if url not in seen and url != base_url:
                seen.add(url)
                unique_links.append((text, url))

        for link_text, page_url in unique_links[:max_pages]:
            try:
                page_html = fetch_page(page_url)
                _, page_text = extract_text(page_html, max_words=800, return_title=True)
                if page_text.strip():
                    extra_texts.append(f"\n\n--- {link_text} ({page_url}) ---\n{page_text.strip()}")
                    label = link_text.split("/")[-1].strip() or link_text
                    fetched_labels.append(label[:20])
            except Exception:
                continue

    return "".join(extra_texts), fetched_labels


SEARCH_SYSTEM = """You are a precise research assistant answering questions using search result snippets.

Format rules (use exactly):
- First line: "▸ TL;DR  " followed by one concise sentence answer
- Blank line
- 2-4 short paragraphs of detail using plain text
- Use "•" for bullet points, never dashes or asterisks
- Use **bold** for key terms (two asterisks each side)
- End after your last paragraph — do not add a Sources line

Voice rules:
- Be direct. No filler phrases like "Great question", "Certainly", or "Of course".
- For simple factual questions (a name, a date, a definition): one short paragraph is enough — do not pad with restatements or obvious context.
- For questions about future events, prices, or anything inherently unpredictable: say clearly that it cannot be known in advance, then explain what factors are relevant.
- Never fabricate specific facts not present in the search snippets."""

FULL_ARTICLE_SYSTEM = """You are a precise article formatter. Given a webpage's text, present the COMPLETE article content — do not summarize, condense, or omit anything from the article itself.

Format rules:
- Preserve every section, statistic, and fact from the article
- Format tables using clean space-aligned columns with a ─── separator line under headers. When a source has side-by-side tables, render them as separate sequential tables, each with their own header row:

  Example:
  Team               Goals Conceded - Set Pieces
  ───────────────────────────────────────────────
  Arsenal            7
  Brighton           7

  Team               XG Against - Set Pieces
  ──────────────────────────────────────────
  Arsenal            6.49
  Brentford          9.94

- Use section headers in ALL CAPS followed by a blank line
- Preserve all bullet points using •
- STOP rendering when you encounter any of the following — these are website navigation, not article content:
  • Repeated short menu labels (MEN, WOMEN, ACADEMY, CLUB)
  • Fixture or results listings (match scores, dates, kick-off times)
  • Contact information (addresses, phone numbers, email forms)
  • Social media prompts (Follow Us, share buttons)
  • Login/membership prompts (Login, Create account, Become a member)
  • Copyright notices and legal text
  End your output at the last meaningful paragraph of the article, before any of the above.
- Do NOT add commentary, analysis, or your own words
- Do NOT add "Related:" or topic suggestions at the end

Output the complete article content only, formatted for clean terminal reading."""

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

FOLLOWUP_SYSTEM = """You are a precise research assistant answering follow-up questions.

Rules:
- Answer ONLY from the provided context — do not add information from outside it
- If the context contains statistics or data relevant to the question, CITE THEM SPECIFICALLY with numbers
- For contested questions, acknowledge the debate and present evidence from multiple perspectives in the context
- Do NOT invent, cite, or reference any external sources or URLs — you have not fetched them
- If the context does not contain enough information to answer well, say so clearly
- Start with "▸ " followed by a direct one-sentence answer
- Keep the response focused and under 200 words
- Do NOT add a "Sources:" line"""

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
    """Search DuckDuckGo and return list of {title, url, domain, snippet}."""
    from urllib.parse import urlparse

    if _HAS_DDGS:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=num_results))
        results = []
        for r in raw:
            url = r.get("href", "")
            parsed = urlparse(url)
            domain = parsed.netloc.removeprefix("www.") if parsed.netloc else url.split("/")[0]
            results.append({
                "title": r.get("title", ""),
                "url": url,
                "domain": domain,
                "snippet": r.get("body", ""),
            })
        return results

    # Fallback: scrape DDG Lite directly
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
        from urllib.parse import unquote, parse_qs
        href = link.get("href", "")
        actual_url = href
        if href:
            parsed = urlparse(href)
            uddg = parse_qs(parsed.query).get("uddg", [])
            if uddg:
                actual_url = unquote(uddg[0])
            elif parsed.scheme in ("http", "https"):
                actual_url = href

        parsed_actual = urlparse(actual_url)
        domain = parsed_actual.netloc.removeprefix("www.") if parsed_actual.netloc else actual_url.split("/")[0]

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

# ─── Claude (primary provider) ───────────────────────────────────────────────

CLAUDE_MODEL = "claude-haiku-4-5"
CLAUDE_MONTHLY_BUDGET = 1.00            # USD hard cap per calendar month
_CLAUDE_INPUT_COST  = 1.00 / 1_000_000  # $1.00/MTok
_CLAUDE_OUTPUT_COST = 5.00 / 1_000_000  # $5.00/MTok
_CLAUDE_CACHE_WRITE = 1.25 / 1_000_000  # $1.25/MTok (cache creation)
_CLAUDE_CACHE_READ  = 0.10 / 1_000_000  # $0.10/MTok (cache hit)
CLAUDE_USAGE_FILE = os.path.expanduser("~/.config/surf/claude_usage.json")


def _claude_usage_load() -> dict:
    try:
        with open(CLAUDE_USAGE_FILE) as f:
            data = json.load(f)
        month = time.strftime("%Y-%m")
        if data.get("month") != month:
            return {"month": month, "cost_usd": 0.0, "calls": 0}
        return data
    except Exception:
        return {"month": time.strftime("%Y-%m"), "cost_usd": 0.0, "calls": 0}


def _claude_usage_save(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CLAUDE_USAGE_FILE), exist_ok=True)
        with open(CLAUDE_USAGE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _claude_budget_ok() -> bool:
    return _claude_usage_load().get("cost_usd", 0.0) < CLAUDE_MONTHLY_BUDGET


def _claude_record(usage) -> float:
    cost = (
        getattr(usage, "input_tokens", 0)               * _CLAUDE_INPUT_COST +
        getattr(usage, "output_tokens", 0)              * _CLAUDE_OUTPUT_COST +
        getattr(usage, "cache_creation_input_tokens", 0) * _CLAUDE_CACHE_WRITE +
        getattr(usage, "cache_read_input_tokens", 0)    * _CLAUDE_CACHE_READ
    )
    data = _claude_usage_load()
    data["cost_usd"] = round(data.get("cost_usd", 0.0) + cost, 6)
    data["calls"]    = data.get("calls", 0) + 1
    _claude_usage_save(data)
    return cost


def claude_monthly_spend() -> str:
    """Return a human-readable spend string, e.g. '$0.34/$1.00'."""
    d = _claude_usage_load()
    return f"${d.get('cost_usd', 0.0):.2f}/${CLAUDE_MONTHLY_BUDGET:.2f}"


def stream_claude(prompt: str, system: str, max_tokens: int = 2048):
    """Stream Claude Haiku — primary provider. Falls to Groq on failure or budget exhaustion."""
    if not _HAS_ANTHROPIC:
        yield from stream_groq(prompt, system, max_tokens)
        return

    config = load_config()
    api_key = config.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
    if not api_key:
        yield from stream_groq(prompt, system, max_tokens)
        return

    if not _claude_budget_ok():
        data = _claude_usage_load()
        sys.stdout.write(
            f"\r\033[90m↳ Claude budget used ({claude_monthly_spend()}) — using Groq\033[0m\n"
        )
        sys.stdout.flush()
        yield from stream_groq(prompt, system, max_tokens)
        return

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield text
            _claude_record(stream.get_final_message().usage)

    except _anthropic.AuthenticationError:
        sys.stdout.write("\r\033[33m↳ Claude auth failed — using Groq\033[0m\n")
        sys.stdout.flush()
        yield from stream_groq(prompt, system, max_tokens)
    except _anthropic.RateLimitError:
        sys.stdout.write("\r\033[90m↳ Claude rate limit — using Groq\033[0m\n")
        sys.stdout.flush()
        yield from stream_groq(prompt, system, max_tokens)
    except Exception:
        sys.stdout.write("\r\033[90m↳ using Groq\033[0m\n")
        sys.stdout.flush()
        yield from stream_groq(prompt, system, max_tokens)


def stream_ai(prompt: str, system: str, max_tokens: int = 2048):
    """Top-level AI stream. Claude primary, Groq → Cerebras → Gemini as fallbacks."""
    yield from stream_claude(prompt, system, max_tokens)


def stream_groq(prompt: str, system: str, model: str = GROQ_MODEL, max_tokens: int = 2048):
    """
    Stream a Groq completion. Yields text chunks as they arrive.
    Loads API key from ~/.config/surf/config.
    """
    config = load_config()
    api_key = config.get("GROQ_API_KEY", os.environ.get("GROQ_API_KEY", ""))
    if not api_key:
        # No Groq key — skip silently to Cerebras
        yield from stream_cerebras(prompt, system, max_tokens)
        return

    client = Groq(api_key=api_key)
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            stream=True,
            max_tokens=max_tokens,
        )
        for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content
    except groq.RateLimitError:
        sys.stdout.write("\r\033[90m↳ using Cerebras\033[0m\n")
        sys.stdout.flush()
        yield from stream_cerebras(prompt, system, max_tokens)
    except groq.APIError:
        sys.stdout.write("\r\033[90m↳ using Cerebras\033[0m\n")
        sys.stdout.flush()
        yield from stream_cerebras(prompt, system, max_tokens)

CEREBRAS_MODEL = "gpt-oss-120b"
CEREBRAS_ENDPOINT = "https://api.cerebras.ai/v1/chat/completions"

_CEREBRAS_THINKING_RE = re.compile(
    r'^(We need to|Let me|Let\'s|I need to|I\'ll|I will|First,|To answer|'
    r'Looking at|Based on the|The user|The question)',
    re.IGNORECASE,
)

def _strip_cerebras_thinking(text: str) -> str:
    """Remove reasoning preamble from gpt-oss-120b output before the actual answer."""
    if "▸ TL;DR" in text:
        # Everything before ▸ TL;DR is thinking — drop it
        return text[text.index("▸"):]
    # If no TL;DR, check for thinking-pattern opening lines and drop them
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not _CEREBRAS_THINKING_RE.match(stripped):
            return "\n".join(lines[i:])
    return text


def stream_cerebras(prompt: str, system: str, max_tokens: int = 2048):
    """
    Stream a Cerebras completion. Used as fallback when Groq is rate-limited.
    Thinking tokens from gpt-oss-120b are stripped before output.
    """
    config = load_config()
    api_key = config.get("CEREBRAS_API_KEY", os.environ.get("CEREBRAS_API_KEY", ""))
    if not api_key:
        # No Cerebras key — skip silently to Gemini
        yield from stream_gemini(prompt, system, max_tokens)
        return

    payload = {
        "model": CEREBRAS_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "stream": True,
    }

    try:
        r = requests.post(
            CEREBRAS_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            stream=True,
            verify=SSL_CERT,
            timeout=30,
        )
        r.raise_for_status()

        # Buffer full response so we can strip thinking preamble before yielding
        full_response = []
        for line in r.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8") if isinstance(line, bytes) else line
            if not decoded.startswith("data: "):
                continue
            data = decoded[6:]
            if data.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                content = chunk["choices"][0]["delta"].get("content", "")
                if content:
                    full_response.append(content)
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
        cleaned = _strip_cerebras_thinking("".join(full_response))
        yield cleaned
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        if code == 429:
            sys.stdout.write("\r\033[90m↳ using Gemini\033[0m\n")
            sys.stdout.flush()
            yield from stream_gemini(prompt, system, max_tokens)
        elif code in (401, 403):
            yield "\033[33m↳ Cerebras auth failed — check CEREBRAS_API_KEY\033[0m"
        else:
            sys.stdout.write(f"\r\033[90m↳ Cerebras error ({code}) — using Gemini\033[0m\n")
            sys.stdout.flush()
            yield from stream_gemini(prompt, system, max_tokens)
    except Exception:
        sys.stdout.write("\r\033[90m↳ using Gemini\033[0m\n")
        sys.stdout.flush()
        yield from stream_gemini(prompt, system, max_tokens)

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:streamGenerateContent"

def _gemini_request(api_key: str, payload: dict, timeout: int = 30):
    """Make one Gemini streaming request. Returns response object."""
    return requests.post(
        GEMINI_ENDPOINT,
        params={"key": api_key, "alt": "sse"},
        headers={"Content-Type": "application/json"},
        json=payload,
        stream=True,
        verify=SSL_CERT,
        timeout=timeout,
    )


def _gemini_iter_chunks(r) -> list[str]:
    """Iterate SSE lines from a Gemini streaming response, yield text chunks."""
    for line in r.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8") if isinstance(line, bytes) else line
        if not decoded.startswith("data: "):
            continue
        data = decoded[6:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
            parts = chunk.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            for part in parts:
                text = part.get("text", "")
                if text:
                    yield text
        except (json.JSONDecodeError, KeyError, IndexError):
            continue


def stream_gemini(prompt: str, system: str, max_tokens: int = 2048):
    """Stream a Gemini completion. Tertiary fallback after Cerebras. Retries once on 429."""
    config = load_config()
    api_key = config.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
    if not api_key:
        yield "\033[33m↳ Monthly budget reached. Add GROQ_API_KEY or GEMINI_API_KEY to ~/.config/surf/config for unlimited free queries.\033[0m"
        return

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    for attempt in range(2):
        try:
            r = _gemini_request(api_key, payload)
            r.raise_for_status()
            yield from _gemini_iter_chunks(r)
            return
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code == 429 and attempt == 0:
                # Back off and retry once
                sys.stdout.write("\r\033[90m↳ Gemini rate limit — retrying in 5s...\033[0m")
                sys.stdout.flush()
                time.sleep(5)
                sys.stdout.write("\r" + " " * 50 + "\r")
                sys.stdout.flush()
                continue
            elif code in (401, 403):
                yield "\033[33m↳ Gemini auth failed — check GEMINI_API_KEY\033[0m"
                return
            else:
                yield f"\033[33m↳ Gemini error ({code}).\033[0m"
                return
        except Exception:
            yield "\033[33m↳ Gemini unavailable.\033[0m"
            return


class Spinner:
    """Animated braille spinner for the thinking phase."""
    FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    def __init__(self, message: str = ""):
        self.message = message
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        for frame in itertools.cycle(self.FRAMES):
            if self._stop_event.is_set():
                break
            sys.stdout.write(f"\r\033[90m{frame} {self.message}\033[0m")
            sys.stdout.flush()
            time.sleep(0.08)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop_event.set()
        self._thread.join()
        sys.stdout.write("\r" + " " * min(_term_width(), 60) + "\r")
        sys.stdout.flush()


def _term_width() -> int:
    return min(shutil.get_terminal_size().columns, 100)

_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')

def print_header(title: str, meta: str = "") -> None:
    """Print a Kagi-style header bar. Truncates long titles with … rather than wrapping."""
    width = _term_width()
    max_title = width - 5  # room for "━━ " prefix and one trailing char
    if len(title) > max_title:
        title = title[:max_title - 1] + "…"
        line = f"━━ {title}"
    else:
        bar = "━" * max(0, width - len(title) - 4)
        line = f"━━ {title} {bar}" if bar else f"━━ {title}"
    print(f"\n\033[35m{line}\033[0m")
    if meta:
        print(f"\033[90m{meta}\033[0m")
    print()

def print_status(message: str) -> None:
    """Print a gray status line, overwriting the previous one."""
    sys.stdout.write(f"\r\033[90m{message}\033[0m")
    sys.stdout.flush()

def clear_status() -> None:
    """Clear the status line."""
    sys.stdout.write("\r" + " " * _term_width() + "\r")
    sys.stdout.flush()

def stream_to_terminal(stream) -> str:
    """Stream output with word-aware wrapping, TL;DR styling, bold, and bullet indent."""
    width = _term_width()
    accumulated = ""
    col = 0
    word_buf = ""
    blank_lines = 0       # consecutive blank lines seen
    in_tldr_line = False  # currently on the ▸ TL;DR line
    tldr_done = False     # TL;DR line has been output
    in_bold = False       # inside a **...** span

    def flush_word():
        nonlocal col, word_buf
        if not word_buf:
            return
        vis_len = len(word_buf)
        if col > 0 and col + vis_len > width:
            sys.stdout.write("\n")
            sys.stdout.flush()
            col = 0
        if in_tldr_line:
            # Force bright-white bold regardless of current state
            sys.stdout.write(f"\033[1;97m{word_buf}\033[0m")
        else:
            # Output raw — inherits current terminal bold state from ** toggles
            sys.stdout.write(word_buf)
        sys.stdout.flush()
        col += vis_len
        word_buf = ""

    for chunk in stream:
        accumulated += chunk
        for char in chunk:
            if char == "\n":
                flush_word()
                if in_tldr_line:
                    sys.stdout.write("\033[0m")
                    in_tldr_line = False
                # Collapse consecutive blank lines to at most one
                if col == 0:
                    blank_lines += 1
                    if blank_lines <= 1:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                else:
                    blank_lines = 0
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                col = 0
            elif char == " ":
                flush_word()
                if col > 0 and col < width:
                    sys.stdout.write(" ")
                    col += 1
            elif char == "\t":
                flush_word()
                sys.stdout.write("  ")
                col += 2
            elif char == "*" and word_buf.endswith("*"):
                # Second consecutive * — this is a ** bold toggle
                word_buf = word_buf[:-1]  # strip the pending single *
                flush_word()
                in_bold = not in_bold
                sys.stdout.write("\033[1m" if in_bold else "\033[22m")
                sys.stdout.flush()
            else:
                word_buf += char
                blank_lines = 0
                # Detect TL;DR line: response opens with ▸
                if not tldr_done and col == 0 and word_buf == "▸":
                    sys.stdout.write("\033[36m▸\033[0m")
                    sys.stdout.flush()
                    col += 1
                    word_buf = ""
                    in_tldr_line = True
                    tldr_done = True
                # Bullet indent: 2 spaces before • at line start
                elif col == 0 and word_buf == "•":
                    sys.stdout.write("  ")
                    col += 2

    flush_word()
    if in_tldr_line:
        sys.stdout.write("\033[0m")
    if in_bold:
        sys.stdout.write("\033[22m")
    sys.stdout.write("\n")
    sys.stdout.flush()
    return accumulated

def print_divider() -> None:
    print(f"\033[90m{'─' * _term_width()}\033[0m")


def _link(url: str, text: str) -> str:
    """OSC 8 clickable hyperlink. Cmd+click opens in browser. Degrades gracefully."""
    if not url:
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def _print_linked_sources(results: list[dict]) -> None:
    """Print a clickable Sources line using OSC 8 hyperlinks."""
    if not results:
        return
    parts = []
    for r in results[:5]:
        url = r.get("url", "")
        domain = r["domain"].removeprefix("www.")
        parts.append(_link(url, domain) if url else domain)
    print(f"\033[90mSources: {' · '.join(parts)}\033[0m")


def print_results(results: list[dict]) -> None:
    """Print numbered search results with clickable OSC 8 hyperlinks."""
    print()
    print_divider()
    for i, r in enumerate(results, 1):
        domain_display = r['domain'].removeprefix('www.')
        url = r.get('url', '')
        print(f" \033[33m{i}\033[0m  {_link(url, r['title'])}")
        print(f"     \033[90m{_link(url, domain_display)}\033[0m")
    print()
    n = len(results)
    print(f"\033[90m  reader: 1–{n}   summary: s1–s{n}   browser: o1–o{n}\033[0m")
    print(f"\033[90m  new: n   quit: q\033[0m")

def print_related(related_lines: list[str]) -> None:
    """Print related topics extracted from Groq's 'Related:' section."""
    print()
    print_divider()
    print("\033[90mRelated topics:\033[0m")
    for line in related_lines:
        print(f"  \033[33m{line}\033[0m")
    print()
    print(f"\033[90m[ 1-{len(related_lines)} ] search topic   [ q ] quit\033[0m")

def _output_json(query: str, response: str, sources: list[str],
                 url: str = "", intent: str = "") -> None:
    """Print structured JSON to stdout and exit."""
    # Extract TL;DR line
    tldr = ""
    body = response
    if "▸ TL;DR" in response:
        parts = response.split("▸ TL;DR", 1)
        rest = parts[1].strip()
        first_newline = rest.find("\n")
        if first_newline > 0:
            tldr = rest[:first_newline].strip()
            body = rest[first_newline:].strip()
        else:
            tldr = rest.strip()
            body = ""

    # Strip ANSI color codes from body
    import re as _re
    body = _re.sub(r'\033\[[0-9;]*m', '', body)

    print(json.dumps({
        "query": query,
        "url": url,
        "intent": intent,
        "tldr": tldr,
        "answer": body,
        "sources": sources,
    }, ensure_ascii=False, indent=2))

_TEMPORAL_SIGNALS = {
    "will", "who will", "who wins", "winner", "predict", "prediction",
    "odds", "chance", "favorite", "favourite", "expect", "likely",
    "latest", "current", "today", "this week", "this month", "this year",
    "right now", "at the moment", "upcoming", "next", "soon",
}

# SEARCH_TIER_SIGNALS["current"] intentionally overlaps with _TEMPORAL_SIGNALS.
# _TEMPORAL_SIGNALS drives year-injection in _enrich_ddg_query (operational).
# SEARCH_TIER_SIGNALS["current"] drives tier classification (routing).
# Keep both in sync when adding temporal signals.
SEARCH_TIER_SIGNALS = {
    "current": {
        " will ", "who will", "predict", "prediction", "odds", "chance",
        "favorite", "favourite", " expect", "likely", "latest", "current ",
        " today", "this week", "this month", "upcoming", " next ", " soon",
        "winner", "who wins", "going to win", "going to beat", "forecast",
    },
    "research": {
        "how does", "how do ", "why does", "why do ", "why is ", "why are ",
        "explain ", "what causes", "what is the difference",
        "what makes", "how come", "mechanism", "what happens when",
    },
    "contested": {
        " best ", " vs ", " versus ", "compare", "should i ", "worth it",
        "better than", "recommend", "which is better", "which should",
        "pros and cons", "advantages", "disadvantages",
    },
}

# SOURCE_HIERARCHY: authoritative reading targets for deep-tier research (Tasks 4-5).
# Distinct from _SOURCE_INTELLIGENCE (used in _handle_followup for targeted re-queries).
# These serve different purposes — keep them separate.
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
- Note if snippets appear outdated or contradictory; prefer the most recent source.
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


def _enrich_ddg_query(user_query: str) -> str:
    """
    Improve DDG search relevance for time-sensitive queries.

    Two passes:
    1. If the query has temporal/predictive signals and lacks the current year,
       append it — "who will win the UCL" → "who will win the UCL 2026".
    2. If session context has relevant facts, use the fast classifier to generate
       a more specific search string — context "PSG vs Arsenal final" turns
       "who will win" into "PSG Arsenal Champions League final 2026 predictions".
    """
    year = time.strftime("%Y")
    q_lower = user_query.lower()

    # Pass 1: temporal year injection (zero cost)
    is_temporal = any(s in q_lower for s in _TEMPORAL_SIGNALS)
    enriched = user_query
    if is_temporal and year not in user_query:
        enriched = f"{user_query} {year}"

    # Pass 2: session-context-aware query generation (one fast classifier call)
    session_ctx = format_session_context()
    if session_ctx and is_temporal:
        prompt = (
            f"Today is {time.strftime('%B %d, %Y')}.\n\n"
            f"The user asked: \"{user_query}\"\n\n"
            f"What they've already searched this session:\n{session_ctx[:600]}\n\n"
            f"Generate a precise web search query (max 8 words) that will find "
            f"today's relevant results — include specific names, the year, and "
            f"any known context. Output ONLY the search query, no quotes, no explanation."
        )
        try:
            chunks = list(stream_groq(
                prompt,
                "You are a search query optimizer. Output only a concise search query. Maximum 8 words.",
                model=CLASSIFIER_MODEL,
                max_tokens=20,
            ))
            generated = "".join(chunks).strip().strip('"').strip("'")
            if generated and 5 < len(generated) < 100:
                return generated
        except Exception:
            pass

    return enriched


def search_flow(query: str, interactive: bool = True, json_output: bool = False) -> tuple[list[dict], str]:
    """
    Run the search flow: DDG → Groq → display results.
    Returns (results, groq_response_text).
    """
    ddg_query = _enrich_ddg_query(query)
    print_status(f"↳ searching: \"{ddg_query[:55]}\"...")
    try:
        results = ddg_search(ddg_query)
        if _needs_multi_search(query) and results:
            alt_query = f"{ddg_query} analysis expert opinion"
            try:
                alt_results = ddg_search(alt_query, num_results=3)
                # Merge, dedup by domain
                seen_domains = {r["domain"] for r in results}
                for r in alt_results:
                    if r["domain"] not in seen_domains:
                        results.append(r)
                        seen_domains.add(r["domain"])
            except Exception:
                pass

        # For news/current-events queries, explicitly check authoritative news sources
        # — only when main results lack them, using a single combined query to minimize DDG hits
        news_signals = ["news", "latest", "today", "2026", "breaking", "current", "update"]
        is_news_query = any(s in query.lower() for s in news_signals)
        auth_news_domains = ("reuters.com", "apnews.com", "bbc.com",
                             "bloomberg.com", "wsj.com", "nytimes.com")
        already_has_auth = any(
            any(a in r.get("domain", "") for a in auth_news_domains)
            for r in (results or [])
        )
        if is_news_query and results is not None and not already_has_auth:
            try:
                targeted = ddg_search(f"reuters bbc apnews {ddg_query}", num_results=4)
                seen = {r["domain"] for r in results}
                for r in targeted:
                    if r["domain"] not in seen:
                        results.append(r)
                        seen.add(r["domain"])
            except Exception:
                pass
    except Exception as e:
        clear_status()
        print(f"\033[31mSearch failed: {e}\033[0m")
        return [], ""
    clear_status()
    results = _filter_results(results)

    if not results:
        print("\033[90mNo results found.\033[0m")
        return [], ""

    domains = " · ".join(r["domain"].removeprefix("www.") for r in results[:3])
    print_header(query.capitalize(), f"{domains}  ({len(results)} results)")

    news_words = {"news", "latest", "today", "war", "conflict", "update", "breaking", "live"}
    if any(w in query.lower().split() for w in news_words):
        from datetime import datetime
        ts = datetime.now().strftime("%B %d, %Y %H:%M")
        print(f"\033[90mFetched {ts}\033[0m\n")

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

    # If response contains uncertainty signals, fetch the top result to verify
    if _has_uncertainty(response) and results:
        top_url = results[0].get("url", "")
        if top_url and top_url.startswith("http"):
            print_status("↳ answer uncertain — verifying from source...")
            try:
                page_html = fetch_page(top_url)
                if _is_spa_shell(page_html):
                    page_content = _fetch_with_jina(top_url)
                else:
                    _, page_content = extract_text(page_html, max_words=2000, return_title=True)
                if page_content and len(page_content) > 200:
                    # Re-ask Groq with the actual page content
                    verify_prompt = (
                        f"Original search snippets gave an uncertain answer about: {query}\n\n"
                        f"Here is the actual current content from {results[0].get('domain', 'the top source')}:\n"
                        f"{page_content[:3000]}\n\n"
                        f"Please provide the correct, definitive answer with specific facts. "
                        f"If the venue, date, or any key fact was listed as TBD in the earlier answer, correct it now."
                    )
                    clear_status()
                    print(f"\n\033[90m↳ verifying from {results[0].get('domain', 'source')}...\033[0m")
                    verify_stream = stream_ai(verify_prompt, SEARCH_SYSTEM)
                    response = stream_to_terminal(verify_stream)
            except Exception:
                clear_status()

    # Save to session memory
    # Extract a brief summary: first 200 chars of response after TL;DR
    summary = response.strip()
    if "▸ TL;DR" in summary:
        summary = summary.split("▸ TL;DR")[-1].strip()
    save_session_entry(query, "search", summary[:300])

    if json_output:
        sources = [r["domain"] for r in results]
        _output_json(query, response, sources, intent="search")
        return results, response

    spend = f" · claude {claude_monthly_spend()}" if (_HAS_ANTHROPIC and _claude_budget_ok()) else ""
    print(f"\033[90m↳ {_elapsed:.1f}s{spend}\033[0m")
    _print_linked_sources(results)
    print_results(results)

    if interactive:
        _handle_results_input(results, context=response)

    return results, response

def _handle_results_input(results: list[dict], context: str = "") -> None:
    """Wait for user to pick a result or ask a follow-up question."""
    while True:
        try:
            choice = surf_input()
        except (KeyboardInterrupt, EOFError):
            break

        _add_to_history(choice)
        cl = choice.lower()

        if cl == "q":
            break
        elif cl == "n":
            query = surf_input("New search: ")
            if query:
                search_flow(query)
            break
        elif cl.startswith("o") and cl[1:].isdigit():
            idx = int(cl[1:]) - 1
            if 0 <= idx < len(results):
                open_in_browser(results[idx]["url"])
            else:
                print(f"\033[90mPick o1-o{len(results)}\033[0m")
        elif cl.startswith("s") and cl[1:].isdigit():
            # AI summary
            idx = int(cl[1:]) - 1
            if 0 <= idx < len(results):
                read_flow(results[idx]["url"], interactive=True, ai_summary=True)
                break
            else:
                print(f"\033[90mPick s1-s{len(results)}\033[0m")
        elif cl.isdigit():
            # Raw read
            idx = int(cl) - 1
            if 0 <= idx < len(results):
                read_flow(results[idx]["url"], interactive=True, ai_summary=False)
                break
            else:
                print(f"\033[90mPick 1-{len(results)}\033[0m")
        elif choice.strip():
            if _is_casual_input(choice):
                print(f"\033[90m(surf is a search tool — try asking a question or picking a result)\033[0m")
            else:
                new_results, new_response = _handle_followup(choice, context=context)
                if new_results:
                    print_results(new_results)
                    results = new_results
                context = new_response
        # empty input: loop again

def _contextualize_query(question: str, context: str) -> str:
    """
    Use the fast classifier model to generate a targeted DDG search query
    from a follow-up question + article context.
    Returns the query string, or the original question if generation fails.
    """
    if not context:
        return question
    prompt = (
        f"Article context (first 800 chars):\n{context[:800]}\n\n"
        f"Follow-up question: {question}\n\n"
        f"Generate a specific web search query (max 10 words) that would find "
        f"the answer to this question given the context. "
        f"Include specific names, places, or identifiers from the context. "
        f"Output ONLY the search query, no quotes, no explanation."
    )
    try:
        chunks = list(stream_groq(
            prompt,
            "You are a search query generator. Output only a search query string, nothing else. Maximum 10 words.",
            model=CLASSIFIER_MODEL,
            max_tokens=30,
        ))
        query = "".join(chunks).strip().strip('"').strip("'")
        return query if query else question
    except Exception:
        return question


def _handle_followup(question: str, context: str = "") -> tuple[list[dict], str]:
    """
    Answer a follow-up question using web search + article context.
    Returns (search_results, response) so callers can show the results list.
    """
    search_query = _contextualize_query(question, context)
    entity_type = _identify_entity_type(context) or _identify_entity_type(question)
    if entity_type:
        print_status(f"↳ searching {entity_type} sources for: \"{search_query[:40]}\"...")
    else:
        print_status(f"↳ searching: \"{search_query[:55]}\"...")
    try:
        search_results = ddg_search(search_query)
    except Exception:
        search_results = []
    search_results = _filter_results(search_results)

    if entity_type and entity_type in _SOURCE_INTELLIGENCE:
        authoritative_domains = _SOURCE_INTELLIGENCE[entity_type]
        result_domains = {r.get("domain", "") for r in search_results}
        has_authoritative = any(
            any(auth in domain for auth in authoritative_domains)
            for domain in result_domains
        )
        if not has_authoritative or len(search_results) < 3:
            auth_query = search_query + " " + authoritative_domains[0].split(".")[0]
            try:
                auth_results = _filter_results(ddg_search(auth_query, num_results=3))
                seen = {r["domain"] for r in search_results}
                for r in auth_results:
                    if r["domain"] not in seen:
                        search_results.append(r)
                        seen.add(r["domain"])
            except Exception:
                pass
    clear_status()

    domains = " · ".join(r["domain"].removeprefix("www.") for r in search_results[:3]) if search_results else ""
    print_header(question.capitalize(), domains)

    prompt_parts = []
    session_ctx = format_session_context()
    if session_ctx and not context:
        prompt_parts.append(session_ctx)
    if context:
        prompt_parts.append(f"Article context (already read):\n{context[:2000]}")
    if search_results:
        snippets = ""
        for i, r in enumerate(search_results, 1):
            snippets += f"[{i}] {r['title']} ({r['domain']})\n{r['snippet']}\n\n"
        prompt_parts.append(f"Web search results for '{question}':\n{snippets}")
    prompt_parts.append(f"Question: {question}")
    prompt = "\n\n".join(prompt_parts)

    _t0 = time.time()
    stream = stream_ai(prompt, SEARCH_SYSTEM)
    response = stream_to_terminal(stream)
    _elapsed = time.time() - _t0

    print(f"\033[90m↳ {_elapsed:.1f}s\033[0m")
    _print_linked_sources(search_results)
    return search_results, response

def parse_related_topics(text: str) -> list[str]:
    """Extract numbered lines from the 'Related:' section of Groq's response."""
    if "Related:" not in text:
        return []
    related_section = text.split("Related:")[-1]
    topics = []
    for line in related_section.strip().splitlines():
        line = line.strip()
        if line and line[0].isdigit() and len(line) > 3:
            topics.append(line)  # keeps "1. Topic name" format for display
    return topics[:3]

def read_flow(url: str, interactive: bool = True, ai_summary: bool = True, json_output: bool = False) -> str:
    """
    Run the read flow: fetch URL → extract text → Groq → display.
    Returns the Groq response text (or raw extracted text in raw mode).
    """
    domain_display = url.replace("https://", "").replace("http://", "").split("/")[0]
    try:
        with Spinner(f"reading {domain_display}..."):
            html = fetch_page(url)
    except Exception as e:
        err = str(e)
        if "401" in err or "403" in err or "Forbidden" in err or "Unauthorized" in err:
            print(f"\033[33m⚠ This page blocks automated access (paywall or bot protection).\033[0m")
            print(f"\033[90mOpening in your browser instead...\033[0m")
            open_in_browser(url)
        elif "timed out" in err or "timeout" in err.lower() or "TimeoutError" in err:
            print(f"\033[33m⚠ {domain_display} timed out. Use [ o ] to open in browser.\033[0m")
        else:
            print(f"\033[31mCould not fetch page: {e}\033[0m")
        return ""

    # For JS SPAs, the fetched HTML is a shell — use Jina to get real content
    if _is_spa_shell(html):
        jina_content = _fetch_with_jina(url)
        if jina_content:
            # Extract title from Jina's markdown header
            title = ""
            for line in jina_content.splitlines():
                if line.startswith("Title:"):
                    title = line.replace("Title:", "").strip()
                    break
            text = jina_content
        else:
            title, text = extract_text(html, return_title=True)
    else:
        title, text = extract_text(html, return_title=True)

    # Extract schema.org structured data — fast, accurate, zero tokens
    schema = extract_schema_data(html if not _is_spa_shell(html) else "")
    if schema:
        schema_lines = ["Structured data from page:"]
        field_labels = {
            "name": "Name", "telephone": "Phone", "email": "Email",
            "address": "Address", "priceRange": "Price Range",
            "openingHours": "Hours", "areaServed": "Area Served",
            "description": "Description", "price": "Price"
        }
        for key, label in field_labels.items():
            if key in schema:
                schema_lines.append(f"  {label}: {schema[key]}")
        text = "\n".join(schema_lines) + "\n\n" + text

    # Fetch relevant sub-pages (contact, rates, about) and append their content
    sub_labels = []
    try:
        is_spa = _is_spa_shell(html)
        if is_spa:
            print_status(f"↳ {domain_display} is JS-rendered — using Jina reader...")
            time.sleep(0.3)  # brief pause so user can read
        sub_page_text, sub_labels = _fetch_sub_pages(html, url, max_pages=4)
        if sub_page_text:
            text = text + sub_page_text
    except Exception:
        pass

    domain = url.replace("https://", "").replace("http://", "").split("/")[0]
    print_header(title or url, _link(url, domain))

    # Show transparency line: what was actually read
    if sub_labels:
        label_str = ", ".join(sub_labels)
        print(f"\033[90m↳ read {domain} + {len(sub_labels)} sub-page{'s' if len(sub_labels) != 1 else ''} ({label_str})\033[0m\n")
    else:
        print(f"\033[90m↳ read {domain}\033[0m\n")

    if not ai_summary:
        # Full article mode — Groq formats everything, no summarizing
        print_status("↳ formatting full article...")
        prompt = build_read_prompt(title, text)
        stream = stream_ai(prompt, FULL_ARTICLE_SYSTEM, max_tokens=6000)
        clear_status()
        response = stream_to_terminal(stream)
    else:
        # Summary mode — concise AI digest
        print_status("↳ summarizing...")
        prompt = build_read_prompt(title, text)
        stream = stream_ai(prompt, READ_SYSTEM)
        clear_status()
        response = stream_to_terminal(stream)

    # Save to session memory
    summary = response.strip()
    if "▸ TL;DR" in summary:
        summary = summary.split("▸ TL;DR")[-1].strip()
    save_session_entry(url, "url", summary[:300])

    if json_output:
        _output_json(url, response, [domain], url=url, intent="read")
        return response

    related = parse_related_topics(response) if ai_summary else []
    domain_link = _link(url, domain)
    print()
    print_divider()
    if related:
        print(f"\033[90m  related: 1–{len(related)}   open {domain_link}: o   follow-up: ?   quit: q\033[0m")
    else:
        print(f"\033[90m  open {domain_link}: o   follow-up: ?   new search: n   quit: q\033[0m")

    if interactive:
        _handle_article_input(url, related, response)

    return response

def _handle_article_input(url: str, related: list[str], context: str) -> None:
    """Interactive prompt after reading an article."""
    while True:
        try:
            choice = surf_input()
        except (KeyboardInterrupt, EOFError):
            break

        _add_to_history(choice)
        cl = choice.lower()

        if cl == "q":
            break
        elif cl == "n":
            query = surf_input("New search: ")
            if query:
                search_flow(query)
            break
        elif cl == "o":
            open_in_browser(url)
        elif cl.isdigit():
            idx = int(cl) - 1
            if 0 <= idx < len(related):
                topic = related[idx]
                if len(topic) > 2 and topic[1] in ".)":
                    topic = topic[2:].strip()
                search_flow(topic)
                break
            else:
                print(f"\033[90mPick 1-{len(related)} or type a follow-up question\033[0m")
        elif choice.strip():
            if _is_casual_input(choice):
                print(f"\033[90m(surf is a search tool — try asking a question or picking a result)\033[0m")
            else:
                new_results, new_response = _handle_followup(choice, context=context)
                if new_results:
                    print_results(new_results)
                context = new_response
        # empty input: loop again

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
    Falls back to informational on any error.
    """
    try:
        chunks = list(stream_groq(query, CLASSIFIER_SYSTEM, model=CLASSIFIER_MODEL))
        raw = "".join(chunks).strip()
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

def _build_booking_sites(query: str, intent: dict) -> list[dict]:
    """Build a list of booking site options for transactional queries."""
    sub = intent.get("sub_type", "").lower()
    open_url = intent.get("open_url", "")

    if "flight" in sub or "flight" in query.lower():
        # Try to extract route from the Google Flights URL the classifier built
        return [
            {"name": "Google Flights", "domain": "google.com/flights", "url": open_url or "https://www.google.com/flights"},
            {"name": "Kayak", "domain": "kayak.com", "url": f"https://www.kayak.com/flights"},
            {"name": "Expedia", "domain": "expedia.com", "url": "https://www.expedia.com/Flights"},
            {"name": "Skyscanner", "domain": "skyscanner.com", "url": "https://www.skyscanner.com"},
        ]
    elif "hotel" in sub or "hotel" in query.lower():
        return [
            {"name": "Booking.com", "domain": "booking.com", "url": "https://www.booking.com"},
            {"name": "Hotels.com", "domain": "hotels.com", "url": "https://www.hotels.com"},
            {"name": "Expedia", "domain": "expedia.com", "url": "https://www.expedia.com/Hotels"},
            {"name": "Airbnb", "domain": "airbnb.com", "url": "https://www.airbnb.com"},
        ]
    else:
        # Generic: use the classifier's URL + a few alternatives
        sites = []
        if open_url:
            from urllib.parse import urlparse
            domain = urlparse(open_url).netloc.removeprefix("www.")
            sites.append({"name": domain, "domain": domain, "url": open_url})
        sites.append({"name": "Google", "domain": "google.com", "url": f"https://www.google.com/search?q={query.replace(' ', '+')}"})
        return sites

HISTORY_FILE = os.path.expanduser("~/.config/surf/history")

if _HAS_PROMPT_TOOLKIT:
    class _DDGCompleter(Completer):
        """Tab-completion using DuckDuckGo autocomplete API."""
        def get_completions(self, document, complete_event):
            word = document.text.strip()
            if len(word) < 2:
                return
            try:
                r = requests.get(
                    "https://ac.duckduckgo.com/ac/",
                    params={"q": word, "type": "list"},
                    headers=HEADERS,
                    verify=SSL_CERT,
                    timeout=2,
                )
                if r.ok:
                    data = r.json()
                    suggestions = data[1] if len(data) > 1 else []
                    for s in suggestions[:6]:
                        yield Completion(s, start_position=-len(word))
            except Exception:
                return

def surf_input(placeholder: str = "") -> str:
    """
    Smart input prompt with history, DDG autocomplete, and ghost suggestions.
    Falls back to plain input() if prompt_toolkit is unavailable.
    """
    if not _HAS_PROMPT_TOOLKIT:
        return input(f"\n{placeholder}› ").strip()
    try:
        completer = _DDGCompleter() if _HAS_PROMPT_TOOLKIT else None
        return _ptk_prompt(
            "› ",
            history=FileHistory(HISTORY_FILE),
            auto_suggest=AutoSuggestFromHistory(),
            completer=completer,
            complete_while_typing=False,
        ).strip()
    except (KeyboardInterrupt, EOFError):
        raise KeyboardInterrupt

_CASUAL_STARTERS = {
    "that's", "thats", "wow", "amazing", "awesome", "great", "nice", "cool",
    "interesting", "fascinating", "incredible", "unbelievable", "haha", "lol",
    "yeah", "yes", "no", "ok", "okay", "sure", "thanks", "thank", "good",
    "bad", "sad", "happy", "excited", "oh", "ah", "hmm", "well",
}

def _is_casual_input(text: str) -> bool:
    """Return True if text is a casual comment/reaction, not a search query."""
    text = text.strip()
    if not text:
        return False
    words = text.lower().split()
    # Very short exclamatory inputs with no question mark
    if len(words) <= 4 and "?" not in text and words[0] in _CASUAL_STARTERS:
        return True
    # Pure exclamations
    if text.rstrip("!").strip().lower() in _CASUAL_STARTERS:
        return True
    return False

def _needs_multi_search(query: str) -> bool:
    """True if query is complex enough to benefit from a second search."""
    complex_signals = [
        "vs", "versus", "compare", "difference", "predict", "odds", "chance",
        "best", "worst", "top", "rank", "should i", "which is", "how does",
        "why did", "who won", "what happened", "latest", "news", "2025", "2026",
    ]
    q = query.lower()
    return any(s in q for s in complex_signals)

_SPAM_DOMAINS = {
    "roblox.com", "y8.com", "grindsuccess.com", "quora.com",
    "pinterest.com", "facebook.com", "instagram.com", "twitter.com",
    "tiktok.com", "reddit.com",
    # Generic "news analysis" spam farms observed in results
    "desirs-volupte.com", "austrianfood.net", "thedailyjagran.com",
    "wanttoknowit.com", "quickapedia.com", "feeddi.com",
}

# Authoritative sources by content category
# These are the places where specific types of information reliably live
_SOURCE_INTELLIGENCE = {
    "sports": ["espn.com", "bbc.com/sport", "theathletic.com", "skysports.com", "uefa.com"],
    "therapist": ["psychologytoday.com", "therapyden.com", "goodtherapy.org", "zocdoc.com"],
    "doctor": ["healthgrades.com", "zocdoc.com", "vitals.com", "npiregistry.cms.hhs.gov"],
    "lawyer": ["avvo.com", "martindale.com", "lawyers.com", "justia.com"],
    "restaurant": ["yelp.com", "tripadvisor.com", "opentable.com"],
    "hotel": ["booking.com", "tripadvisor.com", "expedia.com"],
    "product": ["amazon.com", "bestbuy.com", "consumerreports.org"],
    "company": ["crunchbase.com", "linkedin.com", "bloomberg.com"],
    "medical": ["pubmed.ncbi.nlm.nih.gov", "mayoclinic.org", "webmd.com"],
    "legal": ["law.cornell.edu", "justia.com", "findlaw.com"],
    "academic": ["scholar.google.com", "arxiv.org", "semanticscholar.org"],
    "news": ["reuters.com", "apnews.com", "bbc.com"],
    "finance": ["sec.gov", "finance.yahoo.com", "bloomberg.com"],
    "government": ["usa.gov", "congress.gov", "regulations.gov"],
}

def _identify_entity_type(text: str) -> str | None:
    """
    Identify what type of entity the content is about.
    Returns a key from _SOURCE_INTELLIGENCE or None.
    """
    text_lower = text.lower()[:2000]
    signals = {
        "sports": ["football", "soccer", "basketball", "baseball", "tennis",
                   "nfl", "nba", "mlb", "nhl", "premier league", "champions league",
                   "world cup", "playoff", "standings"],
        "therapist": ["therapist", "counselor", "psychologist", "therapy", "counseling", "mental health", "lac", "lcsw", "lpc"],
        "doctor": ["physician", "doctor", "md ", "medical doctor", "clinic", "patient", "diagnosis"],
        "lawyer": ["attorney", "lawyer", "law firm", "legal", "esq", "counsel", "litigation"],
        "restaurant": ["restaurant", "menu", "cuisine", "dining", "reservations", "chef"],
        "hotel": ["hotel", "resort", "check-in", "amenities", "rooms", "suites"],
        "product": ["price", "buy now", "add to cart", "shipping", "model number"],
        "company": ["founded", "headquarters", "employees", "revenue", "ceo", "startup"],
        "medical": ["symptoms", "treatment", "diagnosis", "clinical", "study", "patients"],
        "finance": ["stock", "shares", "earnings", "market cap", "dividend", "sec filing"],
        "news": ["latest news", "breaking news", "today", "this week", "2026",
                 "current events", "what happened", "update", "announced", "released"],
    }
    best_match = None
    best_score = 0
    for entity_type, keywords in signals.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_match = entity_type
    return best_match if best_score >= 2 else None

def _filter_results(results: list[dict]) -> list[dict]:
    """Remove low-quality domains from search results."""
    return [r for r in results if r.get("domain", "") not in _SPAM_DOMAINS]

def _setup_readline() -> None:
    """Enable up-arrow history and Ctrl+R search for all input() calls."""
    if not _HAS_READLINE:
        return
    try:
        _readline.read_history_file(HISTORY_FILE)
    except FileNotFoundError:
        pass
    _readline.set_history_length(500)
    atexit.register(_readline.write_history_file, HISTORY_FILE)

def _add_to_history(text: str) -> None:
    """Add a string to readline history."""
    if _HAS_READLINE and text.strip():
        _readline.add_history(text.strip())

def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog="surf",
        description="Search or read any URL — Kagi-style, in your terminal."
    )
    parser.add_argument("input", nargs="*", help="A search query or URL")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output as JSON (for scripts and automation)")
    parser.add_argument("--usage", action="store_true",
                        help="Show Claude monthly spend and exit")
    args = parser.parse_args()
    json_output = args.json_output

    if args.usage:
        data = _claude_usage_load()
        spent = data.get("cost_usd", 0.0)
        calls = data.get("calls", 0)
        remaining = max(0.0, CLAUDE_MONTHLY_BUDGET - spent)
        bar_filled = int(spent / CLAUDE_MONTHLY_BUDGET * 20)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        print(f"\n\033[1mClaude usage — {data.get('month', 'this month')}\033[0m")
        print(f"  {bar}  \033[1m${spent:.3f}\033[0m / ${CLAUDE_MONTHLY_BUDGET:.2f}")
        print(f"  {calls} queries  ·  ${remaining:.3f} remaining")
        if remaining > 0:
            est = int(remaining / 0.0004) if spent > 0 else 2500
            print(f"  ≈ {est:,} queries left this month")
        print()
        return

    if not args.input:
        parser.print_help()
        return

    query = " ".join(args.input)
    _add_to_history(query)

    try:
        session_entries = load_session()
        if session_entries and len(session_entries) > 0:
            recent = session_entries[-1]
            age_min = int((time.time() - recent.get("timestamp", 0)) / 60)
            if age_min < 60:
                print(f"\033[90m↳ session: {len(session_entries)} earlier {'search' if len(session_entries) == 1 else 'searches'} ({age_min}m ago)\033[0m")

        if detect_input_type(query) == "url":
            read_flow(query, interactive=not json_output, json_output=json_output)
            return

        with Spinner("understanding your intent..."):
            intent = classify_intent(query)

        if intent["intent"] == "instant":
            print_header(query.capitalize())
            stream = stream_ai(f"Answer this directly and concisely: {query}", SEARCH_SYSTEM)
            stream_to_terminal(stream)

        elif intent["intent"] == "transactional" and intent.get("open_url"):
            # Search DDG for context
            try:
                with Spinner("searching for options..."):
                    results = ddg_search(query)
            except Exception:
                results = []

            domains = " · ".join(r["domain"].removeprefix("www.") for r in results[:3])
            print_header(query.capitalize(), domains if domains else "")

            if intent.get("tip"):
                print(f"\033[33m▸ Tip\033[0m  {intent['tip']}\n")

            # Stream a Groq summary of the route/options
            if results:
                print_status("↳ thinking...")
                prompt = build_search_prompt(query, results)
                stream = stream_ai(prompt, SEARCH_SYSTEM)
                clear_status()
                stream_to_terminal(stream)

            # Build booking sites based on the intent's open_url and sub_type
            sub = intent.get("sub_type", "")
            # Extract route/params from the DDG query for deep links
            booking_sites = _build_booking_sites(query, intent)

            # Show numbered booking site options
            print()
            print_divider()
            print("\033[90mBook on:\033[0m")
            for i, site in enumerate(booking_sites, 1):
                print(f" \033[33m{i}\033[0m  {site['name']}")
                print(f"     \033[90m{site['domain']}\033[0m")
            print()
            print(f"\033[90m[ 1-{len(booking_sites)} ] open site   [ q ] quit\033[0m")

            # Wait for user to pick
            while True:
                try:
                    choice = surf_input().lower()
                except (KeyboardInterrupt, EOFError):
                    break
                if choice == "q":
                    break
                elif choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(booking_sites):
                        open_in_browser(booking_sites[idx]["url"])
                        break
                    else:
                        print(f"\033[90mPick a number between 1 and {len(booking_sites)}\033[0m")

        elif intent["intent"] == "navigation" and intent.get("open_url"):
            open_in_browser(intent["open_url"])

        else:
            search_flow(query, interactive=not json_output, json_output=json_output)

    except KeyboardInterrupt:
        print("\n\033[90mbye\033[0m")

if __name__ == "__main__":
    main()
