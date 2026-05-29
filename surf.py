#!/usr/bin/env python3
import re
import os
import sys
import json
import shutil
import subprocess
import requests
import atexit
from bs4 import BeautifulSoup
import groq
from groq import Groq

try:
    import readline as _readline
    _HAS_READLINE = True
except ImportError:
    _HAS_READLINE = False

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

SEARCH_SYSTEM = """You are a precise research assistant answering questions using search result snippets.

Format rules (use exactly):
- First line: "▸ TL;DR  " followed by one concise sentence answer
- Blank line
- 2-4 short paragraphs of detail using plain text
- Use "•" for bullet points, never dashes or asterisks
- Use ALL CAPS sparingly for key terms (not markdown bold)
- Final line: "Sources: domain1.com · domain2.com · domain3.com"

Be direct. No filler phrases like "Great question" or "Certainly". No markdown syntax."""

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
        actual_url = href
        if href:
            from urllib.parse import unquote, urlparse, parse_qs
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

def stream_groq(prompt: str, system: str, model: str = GROQ_MODEL, max_tokens: int = 2048):
    """
    Stream a Groq completion. Yields text chunks as they arrive.
    Loads API key from ~/.config/surf/config.
    """
    config = load_config()
    api_key = config.get("GROQ_API_KEY", os.environ.get("GROQ_API_KEY", ""))
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in ~/.config/surf/config")

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
        sys.stdout.write("\r\033[33m↳ Groq daily limit reached — switching to Cerebras...\033[0m\n")
        sys.stdout.flush()
        yield from stream_cerebras(prompt, system, max_tokens)
    except groq.APIError as e:
        sys.stdout.write(f"\r\033[33m↳ Groq error — switching to Cerebras...\033[0m\n")
        sys.stdout.flush()
        yield from stream_cerebras(prompt, system, max_tokens)

CEREBRAS_MODEL = "llama-3.3-70b"
CEREBRAS_ENDPOINT = "https://api.cerebras.ai/v1/chat/completions"

def stream_cerebras(prompt: str, system: str, max_tokens: int = 2048):
    """
    Stream a Cerebras completion. Used as fallback when Groq is rate-limited.
    Cerebras uses the same Llama 3.3 70B model with an OpenAI-compatible API.
    """
    config = load_config()
    api_key = config.get("CEREBRAS_API_KEY", os.environ.get("CEREBRAS_API_KEY", ""))
    if not api_key:
        yield "[Cerebras API key not configured in ~/.config/surf/config]"
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
                    yield content
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
    except Exception as e:
        yield f"\n[Cerebras error: {e}]"

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
    sys.stdout.write("\r" + " " * _term_width() + "\r")
    sys.stdout.flush()

def stream_to_terminal(stream) -> str:
    """Stream Groq output to terminal with word-aware line wrapping. Returns full text."""
    width = _term_width()
    accumulated = ""
    col = 0
    word_buf = ""

    def flush_word():
        nonlocal col, word_buf
        if not word_buf:
            return
        if col > 0 and col + len(word_buf) > width:
            sys.stdout.write("\n")
            col = 0
        sys.stdout.write(word_buf)
        col += len(word_buf)
        word_buf = ""

    for chunk in stream:
        accumulated += chunk
        for char in chunk:
            if char == "\n":
                flush_word()
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
            else:
                word_buf += char

    flush_word()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return accumulated

def print_divider() -> None:
    print(f"\033[90m{'─' * _term_width()}\033[0m")

def print_results(results: list[dict]) -> None:
    """Print numbered search results list."""
    print()
    print_divider()
    for i, r in enumerate(results, 1):
        domain_display = r['domain'].removeprefix('www.')
        print(f" \033[33m{i}\033[0m  {r['title']}")  # yellow number
        print(f"     \033[90m{domain_display}\033[0m")
    print()
    print(f"\033[90m[ 1-{len(results)} ] full article   [ s1-s{len(results)} ] summary   [ o1-o{len(results)} ] browser   [ n ] new search   [ q ] quit\033[0m")

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
    try:
        results = ddg_search(query)
    except Exception as e:
        clear_status()
        print(f"\033[31mSearch failed: {e}\033[0m")
        return [], ""
    clear_status()

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

    print_status("↳ asking Groq...")
    prompt = build_search_prompt(query, results)
    stream = stream_groq(prompt, SEARCH_SYSTEM)
    clear_status()

    response = stream_to_terminal(stream)
    print_results(results)

    if interactive:
        _handle_results_input(results, context=response)

    return results, response

def _handle_results_input(results: list[dict], context: str = "") -> None:
    """Wait for user to pick a result or ask a follow-up question."""
    while True:
        try:
            choice = input("\n› ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        _add_to_history(choice)
        cl = choice.lower()

        if cl == "q":
            break
        elif cl == "n":
            query = input("New search: ").strip()
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
            # Follow-up question
            _handle_followup(choice, context=context)
        # empty input: loop again

def _handle_followup(question: str, context: str = "") -> None:
    """Answer a follow-up question using web search + article context."""
    # Search DDG for fresh perspectives on the question
    print_status("↳ searching for perspectives...")
    try:
        search_results = ddg_search(question)
    except Exception:
        search_results = []
    clear_status()

    domains = " · ".join(r["domain"].removeprefix("www.") for r in search_results[:3]) if search_results else ""
    print_header(question.capitalize(), domains)

    # Build prompt combining article context + web snippets
    prompt_parts = []
    if context:
        prompt_parts.append(f"Article context (already read):\n{context[:2000]}")
    if search_results:
        snippets = ""
        for i, r in enumerate(search_results, 1):
            snippets += f"[{i}] {r['title']} ({r['domain']})\n{r['snippet']}\n\n"
        prompt_parts.append(f"Web search results for '{question}':\n{snippets}")
    prompt_parts.append(f"Question: {question}")
    prompt = "\n\n".join(prompt_parts)

    # Use SEARCH_SYSTEM so it properly cites sources from web results
    stream = stream_groq(prompt, SEARCH_SYSTEM)
    stream_to_terminal(stream)

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

def read_flow(url: str, interactive: bool = True, ai_summary: bool = True) -> str:
    """
    Run the read flow: fetch URL → extract text → Groq → display.
    Returns the Groq response text (or raw extracted text in raw mode).
    """
    print_status(f"↳ fetching {url[:60]}...")
    try:
        html = fetch_page(url)
    except Exception as e:
        clear_status()
        err = str(e)
        if "401" in err or "403" in err or "Forbidden" in err or "Unauthorized" in err:
            print(f"\033[33m⚠ This page blocks automated access (paywall or bot protection).\033[0m")
            print(f"\033[90mOpening in your browser instead...\033[0m")
            open_in_browser(url)
        else:
            print(f"\033[31mCould not fetch page: {e}\033[0m")
        return ""

    title, text = extract_text(html, return_title=True)
    clear_status()

    domain = url.replace("https://", "").replace("http://", "").split("/")[0]
    print_header(title or url, domain)

    if not ai_summary:
        # Full article mode — Groq formats everything, no summarizing
        print_status("↳ formatting full article...")
        prompt = build_read_prompt(title, text)
        stream = stream_groq(prompt, FULL_ARTICLE_SYSTEM, max_tokens=6000)
        clear_status()
        response = stream_to_terminal(stream)
    else:
        # Summary mode — concise AI digest
        print_status("↳ summarizing...")
        prompt = build_read_prompt(title, text)
        stream = stream_groq(prompt, READ_SYSTEM)
        clear_status()
        response = stream_to_terminal(stream)

    related = parse_related_topics(response) if ai_summary else []
    if related:
        print()
        print_divider()
        print(f"\033[90m[ 1-{len(related)} ] search related   [ o ] open in browser   [ ? ] follow-up   [ q ] quit\033[0m")
    else:
        print()
        print_divider()
        print(f"\033[90m[ o ] open in browser   [ ? ] follow-up   [ n ] new search   [ q ] quit\033[0m")

    if interactive:
        _handle_article_input(url, related, response)

    return response

def _handle_article_input(url: str, related: list[str], context: str) -> None:
    """Interactive prompt after reading an article."""
    while True:
        try:
            choice = input("\n› ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        _add_to_history(choice)
        cl = choice.lower()

        if cl == "q":
            break
        elif cl == "n":
            query = input("New search: ").strip()
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
            # Follow-up question with article context
            _handle_followup(choice, context=context)
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
    _setup_readline()
    import argparse
    parser = argparse.ArgumentParser(
        prog="surf",
        description="Search or read any URL — Kagi-style, in your terminal."
    )
    parser.add_argument("input", nargs="+", help="A search query or URL")
    args = parser.parse_args()

    query = " ".join(args.input)
    _add_to_history(query)

    try:
        if detect_input_type(query) == "url":
            read_flow(query)
            return

        print_status("↳ understanding your intent...")
        intent = classify_intent(query)
        clear_status()

        if intent["intent"] == "instant":
            print_header(query.capitalize())
            stream = stream_groq(f"Answer this directly and concisely: {query}", SEARCH_SYSTEM)
            stream_to_terminal(stream)

        elif intent["intent"] == "transactional" and intent.get("open_url"):
            # Search DDG for context
            print_status("↳ searching for options...")
            try:
                results = ddg_search(query)
            except Exception:
                results = []
            clear_status()

            domains = " · ".join(r["domain"].removeprefix("www.") for r in results[:3])
            print_header(query.capitalize(), domains if domains else "")

            if intent.get("tip"):
                print(f"\033[33m▸ Tip\033[0m  {intent['tip']}\n")

            # Stream a Groq summary of the route/options
            if results:
                print_status("↳ summarizing options...")
                prompt = build_search_prompt(query, results)
                stream = stream_groq(prompt, SEARCH_SYSTEM)
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
                    choice = input("\n› ").strip().lower()
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
            search_flow(query)

    except KeyboardInterrupt:
        print("\n\033[90mbye\033[0m")

if __name__ == "__main__":
    main()
