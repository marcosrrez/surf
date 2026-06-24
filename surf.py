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
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed  # used in _handle_scope_expansion fanout
from bs4 import BeautifulSoup
import groq
from groq import Groq

@dataclass
class _SearchMeta:
    """Metadata about a search execution, threaded through the interactive loop."""
    original_query: str
    queries_tried: list[str]
    result_count: int
    confidence_tier: str
    coverage_note: str | None

# ═══════════════════════════════════════════════════════════════════════════════
# surf Design System  ·  docs/product/design-system.md
# All visual decisions resolve to one of these tokens.
# Never use raw ANSI codes or hardcoded spacing outside this block.
# ═══════════════════════════════════════════════════════════════════════════════

# — Spacing tokens (in terminal lines) ————————————————————————————————————————
SPACE_NONE = 0   # elements that belong together — no gap
SPACE_XS   = 1   # within a zone — nearby related elements
SPACE_SM   = 2   # between zones — clear transition, ANSWER BEGINS HERE
SPACE_MD   = 3   # major section break — reserved for dramatic transitions

# Zone spacing rules: from → to → token
ZONE_SPACING = {
    ("query",    "context"):  SPACE_NONE,  # header + sources are one unit
    ("context",  "answer"):   SPACE_SM,    # ← THE key beat: 2 blank lines
    ("answer",   "metadata"): SPACE_XS,    # timing/spend is a caption
    ("metadata", "actions"):  SPACE_NONE,  # GLYPH_DIVIDER handles this visually
    ("actions",  "prompt"):   SPACE_XS,    # breath before the interaction point
}

# — Color tokens (semantic roles) ─────────────────────────────────────────────
C_BRAND        = "\033[35m"    # purple  — header bar, brand identity
C_INTERACTIVE  = "\033[33m"    # amber   — numbers, shortcuts, tips, actions
C_ANSWER_MARK  = "\033[36m"    # cyan    — ▸ TL;DR marker only
C_ANSWER_TEXT  = "\033[1;97m"  # bold white — TL;DR sentence only
C_BODY         = ""             # default — body text (inherits terminal fg)
C_BOLD         = "\033[1m"     # bold    — **key terms** mid-body
C_BOLD_END     = "\033[22m"    # intensity reset (not full reset)
C_META         = "\033[90m"    # dim gray — all secondary info
C_ERROR        = "\033[31m"    # red     — errors only
C_RESET        = "\033[0m"     # full reset — end of any colored span
C_SPEED_FAST   = "\033[32m"    # green   — response ≤ 3s
C_SPEED_MED    = "\033[33m"    # amber   — response ≤ 8s  (= C_INTERACTIVE)
C_SPEED_SLOW   = "\033[90m"    # dim gray — response > 8s (= C_META)

# — Glyph vocabulary (one role per character) ─────────────────────────────────
GLYPH_HEADER_FILL = "━"   # U+2501  thick bar — header zone only
GLYPH_DIVIDER     = "─"   # U+2500  thin rule — action zone separator only
GLYPH_TLDR        = "▸"   # U+25B8  TL;DR marker — answer zone, first line only
GLYPH_META        = "↳"   # U+21B3  metadata prefix — timing, status, tips
GLYPH_PROMPT      = "›"   # U+203A  input prompt — interaction point only
GLYPH_SEPARATOR   = "·"   # U+00B7  inline separator — sources, domains
GLYPH_ELLIPSIS    = "…"   # U+2026  truncation — never three dots
GLYPH_RANGE       = "–"   # U+2013  ranges like 1–5 — en-dash, not hyphen
GLYPH_BULLET      = "•"   # U+2022  list bullets — never - or *

# Financial direction glyphs (financial zone only)
GLYPH_UP   = "▲"   # U+25B2  price increase
GLYPH_DOWN = "▼"   # U+25BC  price decrease
GLYPH_FLAT = "→"   # U+2192  no meaningful change

# — Indent tokens (in character spaces) ───────────────────────────────────────
INDENT_NONE = 0   # full-width: header bar, divider, body text
INDENT_SM   = 2   # result number prefix, footer lines
INDENT_MD   = 5   # domain under result title, sub-items


def vspace(token: int) -> None:
    """Print N blank lines using a spacing token. The only way to add vertical space."""
    for _ in range(token):
        print()


def print_section_break(label: str) -> None:
    """Sub-divider within the answer zone (e.g., 48h weather day break)."""
    width = _term_width()
    label_str = f" {label} "
    dashes = GLYPH_DIVIDER * max(0, width - len(label_str) - INDENT_SM)
    print(f"{' ' * INDENT_SM}{C_META}{label_str}{dashes}{C_RESET}")

# ═══════════════════════════════════════════════════════════════════════════════

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

# ─── Imports from extracted modules ──────────────────────────────────────────
import surf_config
from surf_config import load_config, CONFIG_PATH, SESSION_FILE, SESSION_TTL, THREAD_DIR, SNAPSHOT_DIR
from surf_store import (
    _truncate_at_sentence, load_session, save_session_entry, format_session_context,
    _thread_path, _load_thread, _save_thread_entry, _list_threads,
    _export_thread, _export_session,
    _snapshot_path, _save_search_snapshot, _load_search_snapshot,
    _obsidian_vault_path, _make_note_slug, _make_frontmatter,
    _obsidian_save as _obsidian_save_raw, _obsidian_find_related,
    _vault_retrieve, _format_vault_context,
    _obsidian_link_related, _obsidian_session_id,
    _preferences_path, _read_preferences, _write_preferences,
)
from surf_backends import (
    SSL_CERT, HEADERS, DDG_URL, JINA_BASE,
    fetch_page, _fetch_with_jina, _is_spa_shell,
    ddg_search, brave_search, tavily_search,
    _SOURCE_SHORTNAMES, _parse_source_list, _filter_by_sources,
)


def _get_search_backend() -> callable:
    """Return best available search backend: Tavily > Brave > DDG.
    Defined here (not in surf_backends) so tests can patch surf.ddg_search/brave_search."""
    import surf_config
    config = surf_config.load_config()
    if config.get("TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY"):
        return tavily_search
    if config.get("BRAVE_API_KEY") or os.environ.get("BRAVE_API_KEY"):
        return brave_search
    return ddg_search


def _obsidian_save(query, response, sources, session_id, sparked_by="", deep_dive_of="", depth=""):
    """Wrapper that passes _identify_entity_type to the store module."""
    return _obsidian_save_raw(
        query, response, sources, session_id,
        identify_entity_type_fn=_identify_entity_type,
        sparked_by=sparked_by, deep_dive_of=deep_dive_of, depth=depth,
    )


def _vault_only_search(query: str) -> None:
    """Search only the user's vault — no web queries."""
    vault_notes, _ = _vault_retrieve(query, max_notes=10, max_chars=12000)
    if not vault_notes:
        print(f"{C_META}No matching vault notes for \"{query}\".{C_RESET}")
        print(f"{C_META}Try a regular search: surf {query}{C_RESET}")
        return
    vault_ctx = _format_vault_context(vault_notes)
    n = len(vault_notes)
    _dates = [nd["date"] for nd in vault_notes]
    _range = f"{min(_dates)}–{max(_dates)}" if len(_dates) > 1 else _dates[0]
    print_header(f"Vault: {query.capitalize()}", f"{n} note{'s' if n != 1 else ''} ({_range})")
    prefs = _read_preferences()
    prompt = ""
    if prefs:
        prompt += f"[User preferences]\n{prefs}\n[End preferences]\n\n"
    prompt += f"{vault_ctx}\n\nQuestion: {query}\n\nSynthesize from the vault notes above. Highlight patterns, contradictions, and gaps across the research."
    stream = stream_ai(prompt, VAULT_ONLY_SYSTEM, max_tokens=2048)
    response = stream_to_terminal(stream)
    save_session_entry(f"vault: {query}", "vault", _truncate_at_sentence(response, 300))


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


# Shared rules injected into all search system prompts
SEARCH_SYSTEM = """You are a sharp, well-read research assistant with genuine opinions. You find topics interesting and it shows. You lead with the most surprising or counterintuitive finding, not the most obvious one. You state your read clearly — not "sources suggest" but what you actually think the evidence shows. You are honest about what you don't know, and you say so with wit rather than disclaimers.

Format rules (use exactly):
- First line: "▸ TL;DR  " followed by one concise sentence answer
- Blank line
- 2-4 short paragraphs of detail using plain text
- Use "•" for bullet points, never dashes or asterisks
- Use **bold** for key terms (two asterisks each side)
- When a specific fact comes from a source, cite it inline as [1], [2], etc. matching the numbered snippets
- End after your last paragraph — do not add a Sources line

Voice rules:
- Be direct. Lead with the most useful fact — do not open with context the user didn't ask for.
- No filler phrases ("Great question", "Certainly", "Of course", "It is worth noting that").
- Every sentence must add information not already stated. Never rephrase the TL;DR in the body.
- Use specific language: prefer "reduced by 40%" over "significantly reduced", "in 2026" over "recently".
- If sources only contain one key fact, write one focused paragraph — do not pad.
- For simple factual questions (a name, a date, a number, a definition): one short paragraph — 50 words maximum. Stop when the fact is stated.
- For questions about future events or anything unpredictable: say it cannot be known, then explain what factors are relevant.
- If sources are thin or all repeating the same basic fact, say so clearly rather than padding.
- Never fabricate specific facts not present in the search snippets.
- Lead with the finding that would make someone say "huh, interesting" — not the one they already expected.
- State a clear interpretation: "Brazil looked ordinary" not "Brazil's performance was mixed."
- When data is partial, say so with character: "I've got Group C nailed down — the other eleven are keeping their secrets." Then stop — don't pad.
- Use contractions. Write like a person, not a report.
- TIER GATE: For short factual queries (a score, a date, a name, a definition) — answer plainly in 1-2 sentences. Reserve the opinionated voice for analytical or multi-faceted questions."""

VAULT_CONTEXT_INSTRUCTION = """When prior vault research is provided above:
- Build on it — don't repeat what the user already knows
- Highlight what's NEW in today's web results compared to prior research
- Flag any CONTRADICTIONS between vault findings and current sources
- Surface CONNECTIONS across topics the user may not have noticed
- If prior research is comprehensive and web adds nothing new, say so"""

VAULT_ONLY_SYSTEM = """You synthesize a user's accumulated research on a topic. Same voice as always — sharp, direct, opinionated when the evidence warrants it.

Format rules (use exactly):
- First line: "▸ TL;DR  " followed by one sentence synthesizing what they know
- Blank line
- 2-4 paragraphs connecting findings across their notes
- Highlight patterns, contradictions, and knowledge gaps
- Use "•" for bullet points, never dashes or asterisks
- Use **bold** for key terms

Do not fabricate findings not present in their notes. If they've only scratched the surface, say so."""

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
- Stop at the end of the article's content. Do not continue into comments, related articles, or site navigation.
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
- If there are 2-3 genuinely useful follow-up topics, add a blank line then "Related:" and list them numbered 1-3. If no strong related topics exist, omit this section entirely.
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

GROQ_MODEL = "llama-3.3-70b-versatile"
CLASSIFIER_MODEL = "llama-3.1-8b-instant"

# ─── Claude (primary provider) ───────────────────────────────────────────────

CLAUDE_MODEL = "claude-haiku-4-5"
CLAUDE_SONNET_MODEL = "claude-sonnet-4-6"

def _get_synthesis_model() -> str:
    """
    Return the Claude model for synthesis.
    Config: SYNTHESIS_MODEL=sonnet uses claude-sonnet-4-6 for research/current tier.
    Default and all other values: claude-haiku-4-5.
    """
    val = surf_config.load_config().get("SYNTHESIS_MODEL", "haiku").lower().strip()
    return CLAUDE_SONNET_MODEL if val == "sonnet" else CLAUDE_MODEL

CLAUDE_MONTHLY_BUDGET = 1.00            # USD hard cap per calendar month
_CLAUDE_INPUT_COST  = 1.00 / 1_000_000  # $1.00/MTok
_CLAUDE_OUTPUT_COST = 5.00 / 1_000_000  # $5.00/MTok
_CLAUDE_CACHE_WRITE = 1.25 / 1_000_000  # $1.25/MTok (cache creation)
_CLAUDE_CACHE_READ  = 0.10 / 1_000_000  # $0.10/MTok (cache hit)
CLAUDE_USAGE_FILE = os.path.expanduser("~/.config/surf/claude_usage.json")
FEATURE_USAGE_FILE = os.path.expanduser("~/.config/surf/feature_usage.json")

# Tips written in plain English explaining value, not syntax.
# Shown one per session for features the user hasn't tried yet.
# Disappear once the feature has been used.
FEATURE_TIPS = {
    # Core features — shown to new users first
    "reader":   "tip: press \033[33m1\033[90m to read any result directly in your terminal — no browser needed",
    "summary":  "tip: press \033[33ms1\033[90m for a quick AI summary of the top result",
    "browser":  "tip: press \033[33mo1\033[90m to open a source in your browser, or cmd+click any link",
    "followup": "tip: just type a follow-up question — surf remembers your whole session as context",
    # Power features — shown after core features are mastered
    "session":  "tip: session memory means 'who replaced her?' works without repeating what you were researching",
    "automation": "tip: \033[33msurf 'query' --json | jq .tldr\033[90m  pipes cleanly into scripts and cron jobs",
    "preferences": "tip: type \033[33mprefer: concise answers with data\033[90m after any search to tune surf to how you think",
    "preferences_view": "tip: \033[33msurf prefer\033[90m shows your research profile — edit it anytime in Obsidian",
    "pipe": "tip: \033[33mcat error.log | surf 'explain'\033[90m  pipes content directly into surf for analysis",
}
_session_tip_shown: bool = False  # one tip per session maximum


def _load_feature_usage() -> dict:
    try:
        with open(FEATURE_USAGE_FILE) as f:
            return json.load(f)
    except Exception:
        return {k: 0 for k in FEATURE_TIPS}


def record_feature_use(feature: str) -> None:
    """Increment usage count for a feature. Called when the user actually uses it."""
    data = _load_feature_usage()
    data[feature] = data.get(feature, 0) + 1
    # Track total searches so we can gate the automation tip
    if feature == "search":
        data["_searches"] = data.get("_searches", 0) + 1
    try:
        os.makedirs(os.path.dirname(FEATURE_USAGE_FILE), exist_ok=True)
        with open(FEATURE_USAGE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _get_contextual_tip() -> str | None:
    """Return the first tip for a feature the user hasn't tried. One per session."""
    global _session_tip_shown
    if _session_tip_shown:
        return None
    usage = _load_feature_usage()

    # Gate power tips: only show after core features mastered
    searches = usage.get("_searches", 0)
    core_done = all(usage.get(f, 0) > 0 for f in ["reader", "summary", "browser", "followup"])

    for feature, tip in FEATURE_TIPS.items():
        if feature.startswith("_"):
            continue
        if feature == "session" and not core_done:
            continue  # not yet — teach basics first
        if feature == "automation" and searches < 10:
            continue  # show after they've used surf enough to care
        if feature in ("preferences", "preferences_view") and searches < 15:
            continue  # show after automation — these are the deepest power features
        if usage.get(feature, 0) == 0:
            _session_tip_shown = True
            return tip
    return None





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


def stream_claude(prompt: str, system: str, max_tokens: int = 2048, tier: str = "snippet"):
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
            model=_get_synthesis_model() if tier in ("research", "current") else CLAUDE_MODEL,
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


def stream_ai(prompt: str, system: str, max_tokens: int = 2048, tier: str = "snippet"):
    """Top-level AI stream. Claude primary, Groq → Cerebras → Gemini as fallbacks."""
    yield from stream_claude(prompt, system, max_tokens, tier=tier)


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
        yield from stream_ollama(prompt, system, max_tokens)
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
                sys.stdout.write(f"\r\033[90m↳ using local model\033[0m\n")
                sys.stdout.flush()
                yield from stream_ollama(prompt, system, max_tokens)
                return
        except Exception:
            sys.stdout.write(f"\r\033[90m↳ using local model\033[0m\n")
            sys.stdout.flush()
            yield from stream_ollama(prompt, system, max_tokens)
            return


OLLAMA_BASE = "http://localhost:11434"
OLLAMA_PREFERRED_MODELS = ["gemma2:2b", "phi3:mini", "llama3.2:3b", "gemma:2b", "qwen2.5:3b"]


def _get_ollama_model() -> str | None:
    """Return best available Ollama model, or None if Ollama isn't running."""
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=2)
        if r.status_code != 200:
            return None
        available = [m["name"] for m in r.json().get("models", [])]
        if not available:
            return None
        for preferred in OLLAMA_PREFERRED_MODELS:
            for name in available:
                if preferred.split(":")[0] in name:
                    return name
        return available[0]
    except Exception:
        return None


def stream_ollama(prompt: str, system: str, max_tokens: int = 2048):
    """Stream via local Ollama model. Final fallback — zero cost, fully private."""
    model = _get_ollama_model()
    if not model:
        yield "\033[33m↳ no local model available (install Ollama + run: ollama pull gemma2:2b)\033[0m"
        return

    model_display = model.split(":")[0]
    sys.stdout.write(f"\r\033[90m↳ using local {model_display}\033[0m\n")
    sys.stdout.flush()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/v1/chat/completions",
            json=payload,
            stream=True,
            timeout=60,
        )
        r.raise_for_status()
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
                content = chunk["choices"][0]["delta"].get("content", "")
                if content:
                    yield content
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
    except requests.exceptions.ConnectionError:
        yield "\033[33m↳ Ollama not running (run: ollama serve)\033[0m"
    except Exception:
        yield "\033[33m↳ local model unavailable\033[0m"


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


# ── Classical algorithms ────────────────────────────────────────────────────
# These run between DDG recall and LLM synthesis: zero extra cost, improved
# precision. Used as pre-filters and relevance sorters.

def _cosine_similarity(text1: str, text2: str) -> float:
    """Bag-of-words cosine similarity between two texts. No dependencies."""
    from math import sqrt
    stop = {"the", "a", "an", "is", "it", "in", "of", "to", "and", "for", "on", "at", "by"}
    w1 = {w for w in text1.lower().split() if len(w) > 3 and w not in stop}
    w2 = {w for w in text2.lower().split() if len(w) > 3 and w not in stop}
    if not w1 or not w2:
        return 0.0
    if w1 == w2:
        return 1.0  # identical vocabulary — exact 1.0, avoids float rounding
    all_words = w1 | w2
    v1 = [1 if w in w1 else 0 for w in all_words]
    v2 = [1 if w in w2 else 0 for w in all_words]
    dot = sum(a * b for a, b in zip(v1, v2))
    mag1 = sqrt(sum(a * a for a in v1))
    mag2 = sqrt(sum(b * b for b in v2))
    return dot / (mag1 * mag2) if mag1 * mag2 > 0 else 0.0


def _snippets_are_diverse(results: list[dict], threshold: float = 0.70) -> bool:
    """
    True if results are diverse enough to be worth synthesizing.
    False if most snippets are near-copies of each other (SEO farm signal).
    Uses pairwise cosine similarity — zero LLM cost.
    """
    if len(results) < 2:
        return True
    texts = [r.get("snippet", "") + " " + r.get("title", "") for r in results[:5]]
    similar_pairs = 0
    total_pairs = 0
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            total_pairs += 1
            if _cosine_similarity(texts[i], texts[j]) >= threshold:
                similar_pairs += 1
    # If more than half of pairs are near-identical, sources lack diversity
    return similar_pairs / total_pairs < 0.5 if total_pairs > 0 else True


def _bm25_rank(query: str, results: list[dict], k1: float = 1.5, b: float = 0.75) -> list[dict]:
    """
    Rerank results by BM25 score of snippet+title against query.
    Returns results sorted by relevance — most relevant first.
    Stable: ties preserve original order.
    """
    from math import log
    stop = {"the", "a", "an", "is", "it", "in", "of", "to", "and", "for", "on", "at"}
    q_terms = [w for w in query.lower().split() if len(w) > 2 and w not in stop]
    if not q_terms or not results:
        return results

    # Build document corpus
    docs = [r.get("snippet", "") + " " + r.get("title", "") for r in results]
    doc_words = [d.lower().split() for d in docs]
    avg_dl = sum(len(dw) for dw in doc_words) / len(doc_words)
    N = len(docs)

    def score(doc_w: list[str]) -> float:
        dl = len(doc_w)
        s = 0.0
        for term in q_terms:
            tf = doc_w.count(term)
            if tf == 0:
                continue
            df = sum(1 for dw in doc_words if term in dw)
            idf = log((N - df + 0.5) / (df + 0.5) + 1)
            s += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))
        return s

    scored = [(score(dw), i, r) for i, (dw, r) in enumerate(zip(doc_words, results))]
    scored.sort(key=lambda x: (-x[0], x[1]))  # desc score, stable
    return [r for _, _, r in scored]


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance between two strings."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


# ── End classical algorithms ────────────────────────────────────────────────


def print_header(title: str, meta: str = "", zone_after: int = SPACE_SM) -> None:
    """
    Query Zone → Context Zone (SPACE_NONE between them, both printed here).
    zone_after controls the spacing to the NEXT zone (default SPACE_SM = answer begins).
    """
    width = _term_width()
    max_title = width - 5
    if len(title) > max_title:
        title = title[:max_title - 1] + GLYPH_ELLIPSIS
        line = f"{GLYPH_HEADER_FILL}{GLYPH_HEADER_FILL} {title}"
    else:
        bar = GLYPH_HEADER_FILL * max(0, width - len(title) - 4)
        line = f"{GLYPH_HEADER_FILL}{GLYPH_HEADER_FILL} {title} {bar}" if bar else f"{GLYPH_HEADER_FILL}{GLYPH_HEADER_FILL} {title}"
    # One blank line before header (terminal breath before new response)
    print()
    print(f"{C_BRAND}{line}{C_RESET}")
    if meta:
        print(f"{C_META}{meta}{C_RESET}")
    # Zone transition: Context → [next zone] per caller's instruction
    vspace(zone_after)

def print_status(message: str) -> None:
    """Print a gray status line, overwriting the previous one."""
    sys.stdout.write(f"\r\033[90m{message}\033[0m")
    sys.stdout.flush()

def clear_status() -> None:
    """Clear the status line."""
    sys.stdout.write("\r" + " " * _term_width() + "\r")
    sys.stdout.flush()

def stream_to_terminal(stream, results: list[dict] | None = None) -> str:
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
        elif results and re.match(r'^\[\d\]$', word_buf):
            # Inline citation — render as dim clickable link
            idx = int(word_buf[1]) - 1
            if 0 <= idx < len(results):
                url = results[idx].get("url", "")
                domain = results[idx].get("domain", "").removeprefix("www.")
                if url:
                    # OSC 8 hyperlink: dim gray [N] that opens the source
                    sys.stdout.write(f"\033[90m\033]8;;{url}\033\\[{idx+1}]\033]8;;\033\\\033[0m")
                else:
                    sys.stdout.write(f"\033[90m{word_buf}\033[0m")
            else:
                sys.stdout.write(f"\033[90m{word_buf}\033[0m")
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
    """Metadata → Action zone separator. Uses GLYPH_DIVIDER (thin rule)."""
    print(f"{C_META}{GLYPH_DIVIDER * _term_width()}{C_RESET}")


def _link(url: str, text: str) -> str:
    """OSC 8 clickable hyperlink. Cmd+click opens in browser. Degrades gracefully."""
    if not url:
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def _elapsed_color(seconds: float) -> str:
    """Speed token for elapsed time. Uses design system color tokens."""
    if seconds <= 3.0:
        return C_SPEED_FAST
    if seconds <= 8.0:
        return C_SPEED_MED
    return C_SPEED_SLOW


def _shorten_domain(domain: str, max_len: int = 28) -> str:
    """Shorten a long domain to fit cleanly: keep meaningful parts, add … if needed."""
    domain = domain.removeprefix("www.")
    if len(domain) <= max_len:
        return domain
    # Keep first segment + TLD: sagerclassicalacademy.dreamhosters.com → sagerclassical….com
    parts = domain.split(".")
    tld = "." + parts[-1] if len(parts) > 1 else ""
    head = domain[: max_len - len(tld) - 1]
    return f"{head}…{tld}"


def _print_linked_sources(results: list[dict]) -> None:
    """Print a clickable Sources line, width-aware so it never wraps mid-domain."""
    if not results:
        return
    width = _term_width()
    prefix = "Sources: "
    separator = " · "
    parts = []
    used = len(prefix)

    for r in results[:5]:
        url = r.get("url", "")
        raw_domain = r.get("domain", "").removeprefix("www.")
        display = _shorten_domain(raw_domain)
        item_len = len(display) + (len(separator) if parts else 0)
        if parts and used + item_len > width:
            break  # stop before wrapping
        parts.append(_link(url, display) if url else display)
        used += item_len

    print(f"{C_META}{prefix}{(f' {GLYPH_SEPARATOR} ').join(parts)}{C_RESET}")


def print_results(results: list[dict]) -> None:
    """
    Action Zone. Separated from Metadata Zone by GLYPH_DIVIDER (SPACE_NONE — divider handles it).
    Followed by prompt with SPACE_XS.
    """
    # Metadata → Action zone: SPACE_NONE (the divider IS the visual break)
    print_divider()
    for i, r in enumerate(results, 1):
        domain_display = _shorten_domain(r['domain'])
        url = r.get('url', '')
        # INDENT_SM (2 spaces) before number per design system
        print(f"{' ' * INDENT_SM}{C_INTERACTIVE}{i}{C_RESET}  {_link(url, r['title'])}")
        print(f"{' ' * INDENT_MD}{C_META}{_link(url, domain_display)}{C_RESET}")
    vspace(SPACE_XS)
    n = len(results)
    print(f"{C_META}  read in terminal: 1{GLYPH_RANGE}{n}   open in browser: o1{GLYPH_RANGE}o{n}   summary: s1{GLYPH_RANGE}s{n}{C_RESET}")
    tip = _get_contextual_tip()
    if tip:
        print(f"{C_META}  {tip}{C_RESET}")
    # Action → Prompt zone transition: SPACE_XS
    vspace(ZONE_SPACING[("actions", "prompt")])

def print_related(related_lines: list[str]) -> None:
    """Print related topics (article reader). Uses design system tokens."""
    print_divider()
    print(f"{C_META}Related topics:{C_RESET}")
    for line in related_lines:
        print(f"  {C_INTERACTIVE}{line}{C_RESET}")
    vspace(SPACE_XS)
    print(f"{C_META}[ 1{GLYPH_RANGE}{len(related_lines)} ] search topic   [ q ] quit{C_RESET}")

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

_BREAKING_SIGNALS = {
    "breaking", "today", "live", "just announced", "just released",
    "just happened", "right now", "this morning", "this evening",
}


# ─── Specialized query detection ──────────────────────────────────────────────

# Weather: fires only when signal AND (location extractable OR temporal word present)
WEATHER_SIGNALS = {
    "forecast", "weather in", "weather for", "temperature today",
    "rain today", "rain tomorrow", "humidity", "wind speed",
    "uv index", "hourly forecast", "24 hour", "weekend weather",
    "will it rain", "going to snow", "is it going to rain",
}
WEATHER_TEMPORAL = {
    "today", "tomorrow", "tonight", "this weekend", "right now",
    "this morning", "this evening", "currently", "now",
}

# Academic: specific enough that presence alone is sufficient
ACADEMIC_SIGNALS = {
    "peer reviewed", "peer-reviewed", "clinical trial", "meta-analysis",
    "systematic review", "research on", "published paper", "arxiv",
    "pubmed", "what does the science say", "what does the research say",
    "scientific consensus", "randomized controlled", "rct",
    "evidence for", "evidence against", "studies show",
}

# Financial: signal OR recognized ticker/company name
FINANCIAL_SIGNALS = {
    "stock price", "share price", "trading at", "market cap",
    "stock today", "crypto price", "bitcoin price",
    "dow jones", "s&p 500", "nasdaq", "nyse",
}

# Factual: prefix match + length + proper noun (all three required)
FACTUAL_SIGNALS_PREFIX = (
    "what is ", "what are ", "who is ", "who was ",
    "where is ", "when was ", "when did ", "define ",
)

# WMO weather code → 8-char fixed-width description (no emoji for terminal reliability)
WMO_CODES = {
    0: "Sunny   ", 1: "Clear   ", 2: "P.Cloudy", 3: "Overcast",
    45: "Fog     ", 48: "Fog     ",
    51: "Drizzle ", 53: "Drizzle ", 55: "Drizzle ",
    61: "Rain    ", 63: "Rain    ", 65: "Hvy Rain",
    71: "Snow    ", 73: "Snow    ", 75: "Hvy Snow",
    80: "Showers ", 81: "Showers ", 82: "Showers ",
    95: "Tstorm  ", 96: "Tstorm  ", 99: "Tstorm  ",
}

# Company name → ticker (handles aliases; not exhaustive by design)
COMPANY_TICKER_MAP = {
    "apple": "AAPL", "microsoft": "MSFT", "google": "GOOGL",
    "alphabet": "GOOGL", "amazon": "AMZN", "meta": "META",
    "facebook": "META", "nvidia": "NVDA", "tesla": "TSLA",
    "netflix": "NFLX", "adobe": "ADBE", "salesforce": "CRM",
    "intel": "INTC", "amd": "AMD", "qualcomm": "QCOM",
    "oracle": "ORCL", "cisco": "CSCO", "ibm": "IBM",
    "twitter": "X", "spotify": "SPOT", "snap": "SNAP",
    "uber": "UBER", "lyft": "LYFT", "airbnb": "ABNB",
    "palantir": "PLTR", "shopify": "SHOP",
    "jpmorgan": "JPM", "jp morgan": "JPM", "goldman sachs": "GS",
    "goldman": "GS", "bank of america": "BAC", "wells fargo": "WFC",
    "visa": "V", "mastercard": "MA", "berkshire": "BRK-B",
    "berkshire hathaway": "BRK-B", "walmart": "WMT", "target": "TGT",
    "coca cola": "KO", "cocacola": "KO", "pepsi": "PEP",
    "pepsico": "PEP", "disney": "DIS", "nike": "NKE",
    "johnson johnson": "JNJ", "johnson & johnson": "JNJ",
    "pfizer": "PFE", "moderna": "MRNA", "exxon": "XOM",
    "chevron": "CVX", "boeing": "BA", "ford": "F",
    "gm": "GM", "general motors": "GM",
    "bitcoin": "BTC-USD", "btc": "BTC-USD",
    "ethereum": "ETH-USD", "eth": "ETH-USD",
    "dogecoin": "DOGE-USD", "solana": "SOL-USD",
    "cardano": "ADA-USD", "ripple": "XRP-USD",
}

SEARCH_SYSTEM_ACADEMIC = """You are synthesizing peer-reviewed literature.

Format rules:
- First line: "▸ TL;DR  " followed by key finding + confidence level
- Cite inline as [Author et al., YEAR] — never fabricate citations
- Note study types (RCT, meta-analysis, observational, in vitro)
- Note sample sizes when given; distinguish correlation from causation explicitly
- End with "**Limitations:**" section noting gaps in the evidence
- No filler phrases"""


def _date_filter_for_query(query: str) -> "str | None":
    """
    Return an after:YYYY-MM-DD cutoff date for temporal queries, or None.
    Breaking/today/live → 7 days. Other temporal → 30 days. Non-temporal → None.
    """
    from datetime import date, timedelta
    q_lower = query.lower()
    is_breaking = any(s in q_lower for s in _BREAKING_SIGNALS)
    is_temporal = any(s in q_lower for s in _TEMPORAL_SIGNALS)
    if not is_temporal and not is_breaking:
        return None
    days_back = 7 if is_breaking else 30
    cutoff = date.today() - timedelta(days=days_back)
    return cutoff.strftime("%Y-%m-%d")


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
        "how does", "how do ", "how did ", "how was ", "how were ",
        "why does", "why do ", "why did ", "why is ", "why are ", "why was ",
        "explain ", "what caused", "what causes", "what is the difference",
        "what made", "what makes", "how come", "mechanism", "what happened to",
        "how they ", "how arsenal", "how did they", "story of ", "history of ",
        "broke the", "ended the", "broke through",
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
                 "uefa.com", "nfl.com", "nba.com", "mlb.com",
                 "arsenal.com", "manutd.com", "liverpoolfc.com", "chelseafc.com",
                 "mancity.com", "tottenhamhotspur.com"],  # official club sites have primary data
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

SEARCH_SYSTEM_EVALUATIVE = """You are a precise research assistant evaluating a company, product, or service based on independent third-party sources.

Format rules:
- First line: "▸ TL;DR  " followed by one honest verdict sentence — name the entity and the conclusion
- Blank line
- 2-4 sections with **bold headers** organized as: independent ratings/data, user complaints or praise, regulatory or legal record, company claims (clearly labeled)
- Use "•" for bullets; cite sources inline as [1], [2], etc.
- End after your last section — do not add a closing summary sentence

Voice rules:
- Lead with what INDEPENDENT sources say, not the entity's own marketing.
- Label the source perspective explicitly: "AM Best rates..." vs "State Farm says..."
- Distinguish quantitative data (complaint ratio, financial rating, survey score) from subjective opinion.
- If a source appears to have affiliate or commercial relationships with the entity, note it.
- Name weaknesses directly. If independent data shows problems, say so clearly.
- If sources are thin or mostly company-owned, say so rather than padding.
- No filler phrases."""


# ── Evaluative routing ──────────────────────────────────────────────────────
# Detects when a query is asking for evaluation/opinion of a named entity,
# then routes to independence-scored sources and an evaluative system prompt.

_EVALUATIVE_QUERY_SIGNALS = {
    "good", "reliable", "trustworthy", "worth it", "reputable", "legit",
    "legitimate", "scam", "honest", "complaints", "problems", "issues",
    "bad", "recommend", "avoid", "safe", "how good", "is it worth",
}

_MARKETING_VOCAB = frozenset([
    "get a quote", "learn more", "sign up today", "our agents", "we offer",
    "our services", "contact us", "schedule a", "free quote", "get started",
    "apply now", "join us", "our team", "our mission", "trusted by millions",
    "industry leader", "award winning",
])

_DATA_VOCAB = frozenset([
    "rated", "ranked", "rating", "complaint", "survey says", "study found",
    "research shows", "according to", "data shows", "statistics", "per 100",
    "percent of", "compared to average", "score of", "ranked #", "out of 100",
    "am best", "j.d. power", "naic", "consumer reports",
])

_AFFILIATE_URL_SIGNALS = (
    "affiliate", "sponsored", "partner", "refer", "bestinsurance",
    "toptenreviews", "insurancequote", "comparethe", "top10", "best10",
)
_REGULATORY_DOMAIN_SIGNALS = (
    ".gov", ".edu", "naic.org", "ftc.gov", "consumerfinance.gov",
    "bbb.org", "trustpilot.com", "consumeraffairs.com", "glassdoor.com",
    "consumerreports.org", "jdpower.com", "ambest.com",
)
_DATA_SNIPPET_SIGNALS = (
    " rating", " score", "complaint", "ranked", "rated",
    "% of", "per 100", "am best", "j.d. power", "moody", "s&p ",
    "survey of", "study of", "according to",
)
_COMPANY_PROMO_SIGNALS = (
    "get a quote", "our agents", "we offer", "sign up", "learn more about us",
    "trusted by", "industry leader", "award-winning", "schedule a call",
)


def _is_evaluative_query(query: str, tier: str) -> bool:
    """
    True if query is asking for evaluation/opinion of a named entity.
    Only meaningful for contested tier — factual data queries are handled differently.
    """
    if tier not in ("contested", "research"):
        return False
    q_words = set(query.lower().split())
    return bool(q_words & _EVALUATIVE_QUERY_SIGNALS)


def _vocabulary_independence_score(text: str) -> float:
    """
    Returns 0.0 (pure marketing) to 1.0 (data-rich and independent).
    Purely lexical — zero LLM cost.
    """
    text_lower = text.lower()
    marketing_hits = sum(1 for phrase in _MARKETING_VOCAB if phrase in text_lower)
    data_hits = sum(1 for phrase in _DATA_VOCAB if phrase in text_lower)
    if marketing_hits == 0 and data_hits == 0:
        return 0.5  # neutral
    total = marketing_hits + data_hits
    return data_hits / total


def _score_source_independence(result: dict, avoid_signals: list[str] | None = None,
                                source_signals: list[str] | None = None) -> float:
    """
    Score 0.0 (biased/marketing) to 1.0 (independent/data-rich).
    Combines structural URL/snippet signals with LLM-generated query-specific signals.
    """
    url = (result.get("url", "") + " " + result.get("domain", "")).lower()
    snippet = result.get("snippet", "").lower()
    combined = url + " " + snippet

    score = 0.5  # neutral baseline

    # Hard demote: affiliate/referral URL patterns
    if any(s in url for s in _AFFILIATE_URL_SIGNALS):
        score -= 0.35
    # Demote: LLM-identified avoid signals
    if avoid_signals:
        if any(s.lower() in combined for s in avoid_signals):
            score -= 0.25
    # Demote: snippet reads like company self-promotion
    if any(s in snippet for s in _COMPANY_PROMO_SIGNALS):
        score -= 0.20
    # Boost: regulatory or established rating domain
    if any(s in combined for s in _REGULATORY_DOMAIN_SIGNALS):
        score += 0.35
    # Boost: snippet contains quantitative data signals
    data_signals_found = sum(1 for s in _DATA_SNIPPET_SIGNALS if s in snippet)
    score += min(0.20, data_signals_found * 0.07)
    # Boost: LLM-identified source signals appear in content
    if source_signals:
        if any(s.lower() in combined for s in source_signals):
            score += 0.20
    # Boost: vocabulary independence score
    vocab_score = _vocabulary_independence_score(snippet)
    score += (vocab_score - 0.5) * 0.15  # small contribution, -0.075 to +0.075

    return max(0.0, min(1.0, score))


def _evaluate_query_intent(query: str) -> dict:
    """
    One fast Groq 8b call. Generates source profile for evaluative queries:
    - source_signals: terms that appear in authoritative third-party content
    - avoid_signals: terms indicating affiliate/bias
    Falls back gracefully on any error.
    """
    prompt = (
        f'Query: "{query}"\n\n'
        f'What type of entity is being evaluated? What independent third parties '
        f'measure or assess this type of entity — think regulatory agencies, '
        f'professional rating organizations, investigative journalism, consumer '
        f'protection bodies, not review aggregators with affiliate revenue.\n\n'
        f'Return JSON only:\n'
        f'{{"entity_type": "...", "source_signals": ["..."], "avoid_signals": ["..."]}}'
    )
    try:
        chunks = list(stream_groq(
            prompt,
            "Return only a JSON object. No explanation, no markdown.",
            model=CLASSIFIER_MODEL,
            max_tokens=100,
        ))
        raw = "".join(chunks).strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        data = json.loads(raw)
        return {
            "is_evaluative": True,
            "entity_type": data.get("entity_type", "")[:50],
            "source_signals": data.get("source_signals", [])[:6],
            "avoid_signals": data.get("avoid_signals", [])[:4],
        }
    except Exception:
        return {"is_evaluative": True, "source_signals": [], "avoid_signals": []}


# ── End evaluative routing ──────────────────────────────────────────────────


SEARCH_SYSTEM_CURRENT = """You are a sharp analyst synthesizing today's news with genuine opinions. You lead with what's actually surprising or significant — not just what happened, but what it means. You state your read clearly. When coverage is thin or contradictory, you say so in one sentence and explain why.

Format rules:
- First line: "▸ TL;DR  " followed by one concrete, specific sentence — include names, numbers, dates
- Blank line
- 2-4 sections, each with a **bold header** on its own line followed by 1-2 paragraphs
- Section headers should reflect what's actually in the content (e.g., **What's happening**, **Why it matters**, **What's next**)
- Use "•" for bullet points, never dashes
- When a specific fact comes from a source, cite it inline as [1], [2], etc. matching the numbered snippets

Voice rules:
- Be direct. Lead with the most useful fact first.
- No filler phrases. Every sentence must add new information — never restate the TL;DR.
- Use specific language: names, scores, dates, numbers from the sources.
- For simple current-events questions (who won, what was the score): 1-2 paragraphs is enough — do not force section headers on a one-sentence answer.
- If an event is imminent, lead with who is involved and when.
- Note if snippets appear outdated or contradictory; prefer the most recent source.
- If sources are thin, say so in one paragraph rather than padding.
- Start with the most significant development, not the most recent one.
- "Scotland sit top of their group — which is either remarkable or a quiet indictment of Group C, depending on how the next two games go." is better than "Scotland are currently leading Group C."
- Use contractions. Be a person, not a wire service.
- TIER GATE: For simple score/result queries — give the answer plainly first, then add one sentence of context if genuinely useful."""

SEARCH_SYSTEM_RESEARCH = """You are a knowledgeable analyst explaining complex topics with genuine intellectual engagement. You make the interesting parts interesting. You synthesize across sources and state where you land — not "scholars debate" but what the evidence actually shows and where real uncertainty remains.

Format rules:
- First line: "▸ TL;DR  " followed by one clear, direct sentence
- Blank line
- 3-5 sections, each with a **bold header** on its own line followed by 1-2 paragraphs
- Section headers should be meaningful (e.g., **How it works**, **Why it matters**, **Key implications**)
- Use "•" for bullet points where appropriate
- When a specific fact comes from a source, cite it inline as [1], [2], etc. matching the numbered snippets

Voice rules:
- Synthesize across sources — don't summarize each separately.
- Every section must add new information. Never restate the TL;DR or repeat a prior section's point.
- Note where sources agree and where they meaningfully differ.
- If sources only contain one key insight, write one focused section — do not pad.
- No filler phrases.
- Open with the finding that reframes the question, not a definition of terms.
- Use contractions and natural language. Academic prose is a vice, not a virtue.
- TIER GATE: If this is a definitional question (what is X) — define it clearly first, then explain why it's interesting."""

SEARCH_SYSTEM_CONTESTED = """You are an intellectually honest analyst presenting competing views with genuine engagement. You steelman each side before offering your honest read. You are not a pushover — when evidence favors one side clearly, you say so. When it genuinely doesn't, you say that too, and explain why the disagreement persists.

Format rules:
- First line: "▸ TL;DR  " followed by a sentence that names the central tradeoff
- Blank line
- Present each major perspective with its strongest argument
- Use **bold** for key positions and tradeoffs
- End with your honest assessment of which is right for which use case
- When a specific fact comes from a source, cite it inline as [1], [2], etc. matching the numbered snippets

Voice rules:
- Name the tradeoffs explicitly. Don't pick a winner unless evidence is overwhelming.
- The answer is not which side is right — it is which side is right for what.
- No filler phrases.
- "The evidence leans toward X, though Y has a point about Z" is better than "both sides have merit."
- Name the actual tradeoff, not a diplomatic summary of it.
- TIER GATE: State your honest assessment clearly. Epistemic cowardice ("it depends") is worse than being wrong."""


_NAMED_SOURCE_RE = re.compile(
    r'\b(?:from|at|on|via|using|check|see)\s+([A-Za-z0-9][A-Za-z0-9\-\.]{2,}(?:\s*,\s*[A-Za-z0-9][A-Za-z0-9\-\.]{2,})*)',
    re.IGNORECASE,
)

# Well-known sources the user might name → their canonical domain for site: targeting
_KNOWN_SOURCE_DOMAINS = {
    "swe-bench": "swebench.com",
    "swebench": "swebench.com",
    "arc-agi": "arcprize.org",
    "arc-agi-2": "arcprize.org",
    "arxiv": "arxiv.org",
    "github": "github.com",
    "pubmed": "pubmed.ncbi.nlm.nih.gov",
    "wikipedia": "en.wikipedia.org",
    "reddit": "reddit.com",
    "hacker news": "news.ycombinator.com",
    "hn": "news.ycombinator.com",
}


def _extract_named_sources(query: str) -> list[str]:
    """
    Detect when the user explicitly names sources: 'results from SWE-bench, ARC-AGI-2'
    Returns list of site: constraints to add to the DDG query.
    """
    q_lower = query.lower()
    # Check known source names
    constraints = []
    for name, domain in _KNOWN_SOURCE_DOMAINS.items():
        if name in q_lower:
            constraints.append(f"site:{domain}")
    return constraints[:2]  # cap at 2 site constraints


def _clean_conversational_query(query: str) -> str:
    """
    Extract the searchable question from conversational statement+question format.
    'America is 250 years old. is it the longest standing global power?'
    → 'America is it the longest standing global power'
    """
    # Detect: statement sentence followed by a question
    parts = re.split(r'\.\s+', query, maxsplit=1)
    if len(parts) == 2:
        statement, question = parts[0].strip(), parts[1].strip()
        if question and len(question) > 8:
            # Combine: question first (searchable), statement as context
            return f"{statement} {question}"
    return query


def _enrich_ddg_query(user_query: str, tier: str = "snippet", source_hint: str = "") -> str:
    """
    Improve DDG search relevance based on query type.

    Temporal queries: inject current year, use session context to generate
    a specific search string (e.g. "who will win" → "PSG Arsenal UCL final 2026").

    Evaluative queries: if source_hint provided (from _evaluate_query_intent),
    append it directly — no extra LLM call needed.

    Research/contested queries: transform journalist phrasing into analyst
    phrasing to surface quality sources (e.g. "how did Arsenal win the PL"
    → "Arsenal 2026 Premier League title tactical analysis statistics").
    """
    year = time.strftime("%Y")
    q_lower = user_query.lower()

    # Pass 0: named source targeting (zero cost)
    # "results from SWE-bench" → add site:swebench.com to query
    named_sites = list(dict.fromkeys(_extract_named_sources(user_query)))  # dedup, preserve order
    if named_sites:
        # Remove everything from the first source-reference word onward, add site: constraints
        # "results from SWE-bench, ARC-AGI-2" → "results"
        clean = re.sub(
            r'\s*\b(from|at|via|using|check|see|on)\b.*$',
            '',
            user_query,
            flags=re.IGNORECASE,
        ).strip().rstrip(',: ')
        if not clean or len(clean.split()) < 2:
            clean = user_query  # fallback: keep original if cleanup removes too much
        site_str = " OR ".join(named_sites)
        return f"{clean} {site_str}".strip()

    # Pass 1: temporal year injection (zero cost)
    is_temporal = any(s in q_lower for s in _TEMPORAL_SIGNALS)
    enriched = user_query
    if is_temporal and year not in user_query:
        enriched = f"{user_query} {year}"

    # Append after:YYYY-MM-DD for temporal queries to force fresh DDG results.
    # Only on the Pass-1 base query; LLM-rewritten queries (Pass 2/3) skip this.
    _date_filter = _date_filter_for_query(user_query)
    if _date_filter and "after:" not in enriched:
        enriched = f"{enriched} after:{_date_filter}"

    # Pass 1b: vague prediction query + session entity extraction (zero cost, zero LLM)
    # "who will win UCL" with session mentioning "PSG vs Arsenal" →
    # pre-populate enriched with those entities so Pass 2 starts from a better base.
    if is_temporal and ("who will win" in q_lower or "who wins" in q_lower or "will win" in q_lower):
        session_ctx_quick = format_session_context()
        if session_ctx_quick:
            # Look for "X vs Y" match patterns in session context
            vs_match = re.search(
                r'\b([A-Z][a-zA-Z\s]{2,20})\s+(?:vs?\.?|versus)\s+([A-Z][a-zA-Z\s]{2,20})\b',
                session_ctx_quick,
            )
            if vs_match:
                team_a = vs_match.group(1).strip()
                team_b = vs_match.group(2).strip()
                enriched = f"{team_a} {team_b} {year}"

    # Pass 2: session-context-aware enrichment for temporal queries
    # Larger window (1200 chars) so specific entities from prior searches aren't cut off.
    # Prompt explicitly forces session entities into the query — this fixes cases like
    # "who will win UCL" where session has "PSG vs Arsenal final tomorrow" but the
    # enricher was generating generic prediction queries instead.
    session_ctx = format_session_context()
    if session_ctx and is_temporal:
        prompt = (
            f"Today is {time.strftime('%B %d, %Y')}.\n\n"
            f"The user asked: \"{user_query}\"\n\n"
            f"What they've already searched this session:\n{session_ctx[:1200]}\n\n"
            f"Generate a precise web search query (max 8 words) for today's results.\n"
            f"CRITICAL: If the session mentions specific teams, people, match dates, or events "
            f"related to this question, those MUST appear in your query. "
            f"A query like 'PSG Arsenal UCL final 2026 predictions' is far better than "
            f"'UEFA Champions League winner prediction'. "
            f"Output ONLY the search query, no quotes, no explanation."
        )
        try:
            chunks = list(stream_groq(
                prompt,
                "You are a search query optimizer. Output only a concise search query. "
                "Always include specific names and events from context. Maximum 8 words.",
                model=CLASSIFIER_MODEL,
                max_tokens=20,
            ))
            generated = "".join(chunks).strip().strip('"').strip("'")
            if generated and 5 < len(generated) < 100:
                return generated
        except Exception:
            pass

    # Pass 4 (before Pass 3): evaluative source hint — append authoritative-source signals
    # directly to the query to surface data-rich sources over SEO content.
    # No extra LLM call — hint comes from _evaluate_query_intent called in search_flow.
    if source_hint and tier in ("contested", "research"):
        enriched_with_hint = f"{user_query} {source_hint}"
        if len(enriched_with_hint) < 120:
            return enriched_with_hint

    # Pass 3: research/contested enrichment — transform journalist phrasing
    # into analyst phrasing to surface quality sources over SEO farms
    if tier in ("research", "contested") and not is_temporal:
        prompt = (
            f"The user asked: \"{user_query}\"\n\n"
            f"Generate a precise web search query (max 8 words) that an analyst "
            f"or journalist would use to find in-depth, data-rich coverage of this topic. "
            f"Use specific terms, include {year} if relevant. "
            f"Output ONLY the search query, no quotes, no explanation."
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


_ENTITY_RE = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})\b')
_LOCATION_RE = re.compile(
    r'\b(northwest|northeast|southeast|southwest|north|south|east|west)\s+\w+', re.IGNORECASE
)


def _extract_specific_entities(query: str) -> list[str]:
    """
    Extract multi-word proper nouns and location phrases from a query.
    These are entities that DDG must match precisely.
    'John Brown University' → ['John Brown University']
    'northwest arkansas restaurants' → ['northwest arkansas']
    """
    entities: list[str] = []
    # Multi-word capitalized phrases (institutions, people, brands)
    for m in _ENTITY_RE.finditer(query):
        phrase = m.group(1)
        if len(phrase.split()) >= 2:
            entities.append(phrase)
    # Location phrases ("northwest arkansas", "south florida")
    for m in _LOCATION_RE.finditer(query):
        entities.append(m.group(0).strip())
    return entities


def _entity_in_results(entity: str, results: list[dict]) -> bool:
    """True if entity phrase (or close match) appears in any result."""
    entity_lower = entity.lower()
    for r in results:
        text = (r.get("title", "") + " " + r.get("snippet", "") + " " + r.get("domain", "")).lower()
        if entity_lower in text:
            return True
        # Fuzzy: check if entity words appear as abbreviation (e.g. JBU for John Brown University)
        entity_words = [w for w in entity_lower.split() if len(w) > 3]
        if entity_words and all(w in text for w in entity_words):
            return True
    return False


def _fix_entity_mismatch(query: str, results: list[dict], ddg_query: str,
                          evaluative_context: dict | None = None) -> tuple[list[dict], str]:
    """
    If a specific entity in the query isn't in any result, retry with quoted exact-match search.
    Returns (new_results_or_original, new_ddg_query_or_original).
    """
    entities = _extract_specific_entities(query)
    for entity in entities:
        if not _entity_in_results(entity, results):
            retry_q = f'"{entity}" ' + " ".join(
                w for w in query.lower().split()
                if w not in entity.lower() and len(w) > 3
            )
            retry_q = retry_q.strip()
            try:
                new_results = _filter_results(
                    ddg_search(retry_q, num_results=5),
                    evaluative_context=evaluative_context,
                )
                if new_results:
                    return new_results, retry_q
            except Exception:
                pass
    return results, ddg_query


def _sources_are_substantive(query: str, snippets: list[dict]) -> bool:
    """
    Fast pre-synthesis check: do these sources actually answer this query?
    Returns False when all snippets are thin or repeating the same basic fact —
    triggering a retry search before we synthesize a padded answer.
    Only runs for research/current tiers where quality matters most.
    """
    if not snippets:
        return False
    combined = " ".join(r.get("snippet", "") + " " + r.get("title", "") for r in snippets[:5])
    prompt = (
        f"Query: {query}\n\n"
        f"Sources available:\n{combined[:600]}\n\n"
        f"Do these sources contain enough specific information to answer the query "
        f"in a substantive way — not just confirming that something happened, but "
        f"explaining how, why, or with what specific detail? Answer YES or NO only."
    )
    try:
        chunks = list(stream_groq(
            prompt,
            "You evaluate source quality. Answer only YES or NO.",
            model=CLASSIFIER_MODEL,
            max_tokens=5,
        ))
        return "YES" in "".join(chunks).upper()
    except Exception:
        return True  # default to proceeding if check fails


def _classify_data_source(query: str) -> str:
    """
    Classify query as weather/academic/financial/factual/web.
    Priority: financial > weather > academic > factual > web.
    Each category has strict AND conditions to prevent over-triggering.
    """
    q = query.lower()

    # Financial: recognized ticker OR financial vocabulary
    if any(s in q for s in FINANCIAL_SIGNALS):
        return "financial"
    _fin_vocab = {"stock", "price", "shares", "trading", "earnings", "ipo",
                  "market cap", "ticker", "invest", "dividend", "portfolio"}
    for name in COMPANY_TICKER_MAP:
        if name in q and any(w in q for w in _fin_vocab):
            return "financial"
    if re.search(r'\b[A-Z]{2,5}\b', query) and any(
        w in q for w in ("stock", "price", "shares", "trading", "ticker")
    ):
        return "financial"

    # Weather: signal AND (location extractable OR temporal word)
    if any(s in q for s in WEATHER_SIGNALS):
        has_temporal = any(t in q for t in WEATHER_TEMPORAL)
        weather_stop = {"what", "is", "the", "weather", "forecast", "for", "in",
                        "today", "tomorrow", "tonight", "this", "weekend",
                        "hourly", "hour", "will", "rain", "snow", "temperature"}
        words = [w for w in query.split() if w.lower() not in weather_stop]
        has_location = any(w[0].isupper() for w in words if len(w) > 2)
        if has_temporal or has_location:
            return "weather"

    # Academic: specific research vocabulary
    if any(s in q for s in ACADEMIC_SIGNALS):
        return "academic"

    # Factual: prefix + short + proper noun
    has_prefix = any(q.startswith(p) for p in FACTUAL_SIGNALS_PREFIX)
    if has_prefix:
        stop = {"the", "a", "an", "is", "are", "was", "were", "what", "how",
                "why", "who", "when", "does", "do", "did", "and", "or"}
        content_words = [w for w in query.split() if w.lower() not in stop]
        has_entity = bool(_extract_specific_entities(query))
        if len(content_words) < 12 and has_entity:
            return "factual"

    return "web"


def _display_specialized_result(
    query: str,
    response: str | None,
    sources: list[dict],
    handler_name: str,
    t0: float,
    streaming: bool = False,
    json_output: bool = False,
) -> tuple[list[dict], str]:
    """
    Shared post-processing for all specialized handlers.
    Handles elapsed time display, source display, session save, Obsidian save.
    Returns (sources, response) matching search_flow return type.
    """
    if json_output:
        source_urls = [s.get("url", s.get("domain", "")) for s in sources]
        _output_json(query, response or "", source_urls, intent=handler_name)
        return sources, response or ""

    if not streaming and response:
        print(response)

    _elapsed = time.time() - t0
    _ec = _elapsed_color(_elapsed)
    print(f"{_ec}{GLYPH_META} {_elapsed:.1f}s · {handler_name}{C_RESET}")
    _print_linked_sources(sources)
    print_results(sources)

    summary = response or ""
    if "▸ TL;DR" in summary:
        summary = summary.split("▸ TL;DR")[-1].strip()
    save_session_entry(query, "search", _truncate_at_sentence(summary, 300))
    _obsidian_save(query, response or "", sources, session_id=_obsidian_session_id())
    record_feature_use("search")

    return sources, response or ""


def _run_specialized_query(
    query: str,
    source_type: str,
    t0: float,
    interactive: bool = True,
    json_output: bool = False,
) -> tuple[list[dict], str] | None:
    """
    Dispatch to the appropriate specialized handler.
    Returns (sources, response) or None if handler failed (fall through to DDG).
    """
    handler_map = {
        "weather":   _handle_weather,
        "academic":  _handle_academic,
        "financial": _handle_financial,
        "factual":   _handle_factual,
    }
    handler = handler_map.get(source_type)
    if not handler:
        return None

    result = handler(query)
    if result is None:
        return None

    response, sources, streaming = result
    return _display_specialized_result(query, response, sources,
                                        _source_type_name(source_type), t0, streaming,
                                        json_output=json_output)


def _source_type_name(source_type: str) -> str:
    return {
        "weather": "Open-Meteo",
        "academic": "PubMed · arXiv",
        "financial": "Yahoo Finance",
        "factual": "Wikipedia",
    }.get(source_type, source_type)


# ─── Weather handler ───────────────────────────────────────────────────────────

_FAHRENHEIT_COUNTRIES = {"US", "BS", "BZ", "KY", "PW", "FM", "MH"}
_WIND_DIRS = ["N ", "NE", "E ", "SE", "S ", "SW", "W ", "NW"]


def _wind_dir_str(degrees: float) -> str:
    return _WIND_DIRS[round(degrees / 45) % 8]


def _temp_color(temp: float, is_fahrenheit: bool) -> str:
    hot = 86 if is_fahrenheit else 30
    cold = 44 if is_fahrenheit else 7
    if temp >= hot:
        return C_INTERACTIVE
    if temp <= cold:
        return C_META
    return C_BODY


def _extract_weather_location(query: str) -> str:
    """Strip weather vocabulary from query, return remaining text as location."""
    stop = {
        "what", "is", "the", "weather", "forecast", "for", "in", "a", "an",
        "today", "tomorrow", "tonight", "this", "weekend", "week", "next",
        "hourly", "24", "hour", "current", "right", "now", "currently",
        "will", "it", "rain", "snow", "temperature", "temp", "going", "to",
        "be", "like", "get", "humidity", "wind", "uv", "conditions",
        "how", "what's", "whats", "outside", "around", "near",
    }
    words = [w for w in query.split() if w.lower().rstrip("?.,!") not in stop]
    location = " ".join(words).strip().rstrip("?.,!")
    if not location or len(location) < 2:
        entities = _extract_specific_entities(query)
        location = entities[0] if entities else ""
    return location


def _handle_weather(query: str) -> "tuple[str, list[dict], bool] | None":
    """
    Fetch weather forecast from Open-Meteo.
    Returns (formatted_response, sources, streaming=False) or None on failure.
    """
    from datetime import datetime

    location = _extract_weather_location(query)
    if not location or len(location) < 2:
        return None

    # Step 1: Geocode
    try:
        geo_r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "format": "json"},
            headers=HEADERS, timeout=1.5,
        )
        geo_r.raise_for_status()
        geo_data = geo_r.json()
        if not geo_data.get("results"):
            return None
        g = geo_data["results"][0]
        lat, lon = g["latitude"], g["longitude"]
        country = g.get("country_code", "US")
        name = g.get("name", location)
        admin = g.get("admin1", "")
        display_loc = f"{name}, {admin}" if admin else name
        timezone = g.get("timezone", "auto")
    except Exception:
        return None

    # Step 2: Fetch forecast
    is_fahrenheit = country in _FAHRENHEIT_COUNTRIES
    temp_unit = "fahrenheit" if is_fahrenheit else "celsius"
    temp_sym = "°F" if is_fahrenheit else "°C"

    try:
        fc_r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon, "timezone": timezone,
                "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,wind_direction_10m,weathercode",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
                "forecast_days": 3, "wind_speed_unit": "mph", "temperature_unit": temp_unit,
            },
            headers=HEADERS, timeout=2.0,
        )
        fc_r.raise_for_status()
        fc_data = fc_r.json()
    except Exception:
        return None

    # Step 3: Format response
    hourly = fc_data.get("hourly", {})
    daily = fc_data.get("daily", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    precips = hourly.get("precipitation_probability", [])
    winds = hourly.get("wind_speed_10m", [])
    wdirs = hourly.get("wind_direction_10m", [])
    wcodes = hourly.get("weathercode", [])

    # Find current hour index
    now = datetime.now()
    cur = 0
    for i, t in enumerate(times):
        try:
            if datetime.fromisoformat(t) >= now:
                cur = i
                break
        except Exception:
            pass

    # Build 8-hour table
    rows = []
    for i in range(8):
        idx = cur + i
        if idx >= len(times):
            break
        try:
            dt = datetime.fromisoformat(times[idx])
            label = "Now    " if i == 0 else dt.strftime("%I%p").lstrip("0").lower().ljust(7)
            temp = round(temps[idx]) if idx < len(temps) else "--"
            precip = precips[idx] if idx < len(precips) else 0
            wind = round(winds[idx]) if idx < len(winds) else "--"
            wdir = _wind_dir_str(wdirs[idx]) if idx < len(wdirs) else "  "
            cond = WMO_CODES.get(wcodes[idx] if idx < len(wcodes) else 0, "Unknown ")
            tc = _temp_color(float(temp), is_fahrenheit)
            rows.append(
                f"  {label:<7}  {tc}{temp:>3}{temp_sym}{C_RESET}"
                f"  {cond}  Wind {wind:>2}mph {wdir}  {precip:>2}% rain"
            )
        except Exception:
            continue

    # Daily summary (next 2 days)
    daily_lines = []
    for i in range(1, min(3, len(daily.get("time", [])))):
        try:
            dt = datetime.fromisoformat(daily["time"][i])
            hi = round(daily["temperature_2m_max"][i])
            lo = round(daily["temperature_2m_min"][i])
            p_mm = daily.get("precipitation_sum", [0, 0, 0])[i] or 0
            p_pct = min(100, int(p_mm * 8))
            daily_lines.append(
                f"  {dt.strftime('%A'):<10}  High {hi}{temp_sym} · Low {lo}{temp_sym} · {p_pct:>2}% rain"
            )
        except Exception:
            continue

    # TL;DR line
    cur_temp = round(temps[cur]) if cur < len(temps) else "?"
    cur_cond = WMO_CODES.get(wcodes[cur] if cur < len(wcodes) else 0, "Unknown").strip().lower()
    tldr = f"{GLYPH_TLDR} TL;DR  Now {cur_temp}{temp_sym} {cur_cond}"
    if len(daily.get("temperature_2m_max", [])) > 1:
        tldr += f", tomorrow high {round(daily['temperature_2m_max'][1])}{temp_sym}."

    coord = f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'W' if lon < 0 else 'E'}"
    ts = datetime.now().strftime("%b %-d %H:%M")

    # Print header before returning (so it appears before the API wait)
    print_header(
        f"{display_loc} — Forecast",
        f"{C_META}{GLYPH_META} Open-Meteo · {coord} · {ts}{C_RESET}",
        zone_after=SPACE_SM,
    )
    print_status(f"↳ fetching weather for {display_loc}...")
    clear_status()

    lines = [f"{C_ANSWER_MARK}{tldr}{C_RESET}", ""]
    lines.extend(rows)
    if daily_lines:
        lines.append("")
        lines.extend(daily_lines)

    response = "\n".join(lines)
    sources = [{
        "title": f"Weather forecast for {display_loc}",
        "url": "https://open-meteo.com/",
        "domain": "open-meteo.com",
        "snippet": f"Current conditions and 3-day forecast for {display_loc}.",
    }]
    return response, sources, False


# ─── Handler stubs (replaced by real implementations in Tasks 1–3) ─────────────

def _detect_ticker(query: str) -> "str | None":
    """Return Yahoo Finance ticker from query text, or None if not recognized."""
    q = query.lower()
    _fin_vocab = {"stock", "price", "shares", "trading", "earnings", "ipo",
                  "market cap", "ticker", "invest", "dividend", "portfolio"}
    for name, ticker in COMPANY_TICKER_MAP.items():
        if name in q and any(w in q for w in _fin_vocab):
            return ticker
    financial_words = {"stock", "price", "shares", "trading", "ticker", "market", "nasdaq", "nyse", "etf"}
    if any(w in q for w in financial_words):
        matches = re.findall(r'\b([A-Z]{2,5})\b', query)
        skip = {"US", "EU", "UK", "AI", "ML", "API", "CEO", "GDP", "IPO", "CIA", "FBI", "NASA", "NYT"}
        for m in matches:
            if m not in skip:
                return m
    return None


def _build_sparkline(prices: "list[float]") -> str:
    """Build single-row block sparkline from a list of prices."""
    blocks = "▁▂▃▄▅▆▇█"
    if len(prices) < 2:
        return "─" * len(prices)
    lo, hi = min(prices), max(prices)
    if hi == lo:
        return "─" * len(prices)
    return "".join(blocks[min(7, int((p - lo) / (hi - lo) * 7.99))] for p in prices)


def _fmt_large(n: float) -> str:
    """Format large number as human-readable: 2980000000000 → $2.98T"""
    if n >= 1e12:
        return f"${n/1e12:.2f}T"
    if n >= 1e9:
        return f"${n/1e9:.1f}B"
    if n >= 1e6:
        return f"${n/1e6:.1f}M"
    return f"${n:,.0f}"


def _handle_financial(query: str) -> "tuple[str, list[dict], bool] | None":
    """Fetch stock/crypto price from Yahoo Finance. Returns (response, sources, streaming) or None."""
    ticker = _detect_ticker(query)
    if not ticker:
        return None

    print_status(f"↳ fetching {ticker} · Yahoo Finance...")

    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = requests.get(url, params={"interval": "1d", "range": "5d"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=3.0)
        r.raise_for_status()
        data = r.json()
    except Exception:
        clear_status()
        return None

    clear_status()

    try:
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice", 0)
        prev = meta.get("previousClose", price)
        change = price - prev
        pct = (change / prev * 100) if prev else 0
        day_hi = meta.get("regularMarketDayHigh", price)
        day_lo = meta.get("regularMarketDayLow", price)
        wk52_hi = meta.get("fiftyTwoWeekHigh", 0)
        wk52_lo = meta.get("fiftyTwoWeekLow", 0)
        mkt_cap = meta.get("marketCap", 0)
        volume = meta.get("regularMarketVolume", 0)
        avg_vol = meta.get("averageVolume", 0)
        name = meta.get("longName", meta.get("shortName", ticker))
        exchange = meta.get("exchangeName", "")

        if change > 0:
            dir_glyph, dir_color = GLYPH_UP, C_SPEED_FAST
        elif change < 0:
            dir_glyph, dir_color = GLYPH_DOWN, C_ERROR
        else:
            dir_glyph, dir_color = GLYPH_FLAT, C_META

        pct_color = C_INTERACTIVE if abs(pct) > 2 else dir_color

        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c is not None]
        spark = _build_sparkline(closes) if len(closes) >= 2 else ""
        if spark:
            spark = f"{C_META}{spark[:-1]}{dir_color}{spark[-1]}{C_RESET}"

        direction_word = "up" if change > 0 else ("down" if change < 0 else "flat")
        tldr = (f"{GLYPH_TLDR} TL;DR  {name} {direction_word} "
                f"{abs(pct):.2f}% today at ${price:,.2f}.")

        lines = [
            f"{C_ANSWER_MARK}{tldr}{C_RESET}",
            "",
            f"  {'Price':<11}${price:>8,.2f}   "
            f"{dir_color}{dir_glyph} {change:+.2f}{C_RESET}  "
            f"{pct_color}{pct:+.2f}%{C_RESET}",
            f"  {'Day range':<11}${day_lo:,.2f} – ${day_hi:,.2f}",
            f"  {'52-week':<11}${wk52_lo:,.2f} – ${wk52_hi:,.2f}",
        ]
        if mkt_cap:
            lines.append(f"  {'Mkt cap':<11}{_fmt_large(mkt_cap)}")
        if volume:
            vol_str = f"{volume/1e6:.1f}M"
            avg_str = f"  (avg {avg_vol/1e6:.1f}M)" if avg_vol else ""
            lines.append(f"  {'Volume':<11}{vol_str}{avg_str}")
        if spark:
            lines.append("")
            lines.append(f"  {C_META}5d:{C_RESET}  {spark}")

        response = "\n".join(lines)

        import datetime as _dt
        ts = _dt.datetime.now().strftime("%b %-d %H:%M")
        tz_note = " ET" if exchange in ("NYSE", "NASDAQ") else ""
        print_header(f"{ticker} — {name}",
                     f"{C_META}{GLYPH_META} Yahoo Finance · {ts}{tz_note} · {exchange}{C_RESET}",
                     zone_after=SPACE_SM)

        sources = [{
            "title": f"{ticker} — {name} · Yahoo Finance",
            "url": f"https://finance.yahoo.com/quote/{ticker}",
            "domain": "finance.yahoo.com",
            "snippet": f"${price:,.2f} {dir_glyph} {pct:+.2f}% · not financial advice",
        }]
        return response, sources, False

    except (KeyError, IndexError, TypeError):
        return None


def _strip_latex(text: str) -> str:
    """Remove LaTeX math and commands from arXiv abstract text."""
    text = re.sub(r'\$\$[^$]+\$\$', '[math]', text)
    text = re.sub(r'\$[^$]+\$', '[math]', text)
    text = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', text)
    text = re.sub(r'\\[a-zA-Z]+', '', text)
    text = re.sub(r'[{}]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def _search_pubmed(query: str, max_results: int = 4) -> "list[dict]":
    """Search PubMed; return list of {title, authors, year, journal, abstract, link, source}."""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    search_r = requests.get(f"{base}/esearch.fcgi",
                            params={"db": "pubmed", "term": query, "retmode": "json",
                                    "retmax": max_results, "sort": "relevance"},
                            headers=HEADERS, timeout=3.0)
    search_r.raise_for_status()
    ids = search_r.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    summary_r = requests.get(f"{base}/esummary.fcgi",
                             params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
                             headers=HEADERS, timeout=3.0)
    summary_r.raise_for_status()
    summaries = summary_r.json().get("result", {})

    abstract_r = requests.get(f"{base}/efetch.fcgi",
                              params={"db": "pubmed", "id": ",".join(ids),
                                      "rettype": "abstract", "retmode": "text"},
                              headers=HEADERS, timeout=3.0)
    abstract_r.raise_for_status()
    abstract_text = abstract_r.text

    paper_blocks = [b.strip() for b in abstract_text.split("\n\n\n") if b.strip()]
    papers = []
    for pmid in ids:
        s = summaries.get(pmid, {})
        if not s or s.get("error"):
            continue
        authors = s.get("authors", [])
        author_str = authors[0]["name"] if authors else "Unknown"
        if len(authors) > 1:
            author_str += " et al."
        papers.append({
            "title": s.get("title", "").rstrip("."),
            "authors": author_str,
            "year": s.get("pubdate", "")[:4],
            "journal": s.get("fulljournalname", s.get("source", "")),
            "abstract": "",
            "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "source": "PubMed",
            "pmid": pmid,
        })

    for paper in papers:
        for block in paper_blocks:
            if paper["pmid"] in block:
                lines = block.split("\n")
                abstract_lines = []
                in_abstract = False
                for line in lines:
                    if line.startswith("Abstract") or line.startswith("ABSTRACT"):
                        in_abstract = True
                        continue
                    if in_abstract and line.strip():
                        abstract_lines.append(line.strip())
                if abstract_lines:
                    paper["abstract"] = " ".join(abstract_lines)[:500]
                break

    return papers


def _search_arxiv(query: str, max_results: int = 3) -> "list[dict]":
    """Search arXiv; strips LaTeX from abstracts."""
    import xml.etree.ElementTree as ET
    r = requests.get(
        "https://export.arxiv.org/api/query",
        params={"search_query": f"all:{query}", "start": 0,
                "max_results": max_results, "sortBy": "relevance"},
        headers=HEADERS, timeout=3.0,
    )
    r.raise_for_status()
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)
    papers = []
    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        published_el = entry.find("atom:published", ns)
        id_el = entry.find("atom:id", ns)
        authors = entry.findall("atom:author", ns)

        title = title_el.text.strip() if title_el is not None else ""
        abstract = _strip_latex(summary_el.text.strip() if summary_el is not None else "")
        year = published_el.text[:4] if published_el is not None else ""
        link = id_el.text.strip() if id_el is not None else ""
        author_names = [a.find("atom:name", ns).text for a in authors
                        if a.find("atom:name", ns) is not None]
        author_str = author_names[0] if author_names else "Unknown"
        if len(author_names) > 1:
            author_str += " et al."
        if title:
            papers.append({
                "title": title, "authors": author_str, "year": year,
                "journal": "arXiv preprint", "abstract": abstract[:500],
                "link": link, "source": "arXiv",
            })
    return papers


_ACADEMIC_PREAMBLES = (
    "find me a peer reviewed article on ", "find me peer reviewed articles on ",
    "find me a peer-reviewed article on ", "find me studies on ",
    "find me research on ", "find me papers on ",
    "find a peer reviewed article on ", "find peer reviewed articles on ",
    "find a peer-reviewed article on ", "find peer-reviewed articles on ",
    "find peer reviewed studies on ", "find peer-reviewed studies on ",
    "find research on ", "find studies on ", "find papers on ",
    "find an article on ", "find articles on ",
    "look up papers on ", "search for papers on ", "search for studies on ",
    "show me research on ", "show me studies on ", "get me papers on ",
)

_ACADEMIC_STOP = {"the", "a", "an", "is", "are", "was", "were", "for", "on", "in",
                  "of", "to", "and", "or", "but", "with", "from", "by", "at", "about"}


def _handle_academic(query: str) -> "tuple[str, list[dict], bool] | None":
    """Search PubMed + arXiv in parallel; return paper cards or Claude synthesis."""
    # Strip request-phrasing preambles — APIs need clean topic terms, not intent
    search_terms = query
    ql = query.lower()
    for prefix in _ACADEMIC_PREAMBLES:
        if ql.startswith(prefix):
            search_terms = query[len(prefix):]
            break

    print_status("↳ searching PubMed + arXiv...")

    with ThreadPoolExecutor(max_workers=2) as executor:
        pubmed_future = executor.submit(_search_pubmed, search_terms)
        arxiv_future = executor.submit(_search_arxiv, search_terms)
        pubmed_papers: list[dict] = []
        arxiv_papers: list[dict] = []
        future_map = {pubmed_future: "pubmed", arxiv_future: "arxiv"}
        for future in as_completed(future_map, timeout=4.0):
            try:
                res = future.result()
                if future_map[future] == "pubmed":
                    pubmed_papers = res
                else:
                    arxiv_papers = res
            except Exception:
                pass

    # Filter arXiv results: key search terms must appear in title or abstract.
    # Prevents irrelevant physics/CS papers from surfacing for medical/social queries.
    if arxiv_papers and search_terms:
        key_terms = {w.lower().rstrip("s") for w in search_terms.split()
                     if len(w) > 3 and w.lower() not in _ACADEMIC_STOP}
        if key_terms:
            arxiv_papers = [
                p for p in arxiv_papers
                if any(t in (p.get("title", "") + " " + p.get("abstract", "")).lower()
                       for t in key_terms)
            ]

    papers = pubmed_papers + arxiv_papers

    clear_status()

    if not papers:
        return None

    seen_titles: set = set()
    unique_papers = []
    for p in papers:
        key = p["title"].lower()[:50]
        if key not in seen_titles:
            seen_titles.add(key)
            unique_papers.append(p)
    papers = unique_papers[:5]

    synthesis_signals = {"what does", "summarize", "explain", "overview",
                         "what is known", "evidence", "consensus"}
    is_complex = any(s in query.lower() for s in synthesis_signals)

    sources = [{
        "title": f"{p['title'][:60]} — {p['authors']}, {p['year']}",
        "url": p["link"],
        "domain": "pubmed.ncbi.nlm.nih.gov" if p["source"] == "PubMed" else "arxiv.org",
        "snippet": p.get("abstract", "")[:120],
    } for p in papers]

    header_q = f"{query[:50]}… — Academic" if len(query) > 50 else f"{query} — Academic"
    print_header(header_q,
                 f"{C_META}{GLYPH_META} PubMed · arXiv · {len(papers)} result{'s' if len(papers) != 1 else ''}{C_RESET}",
                 zone_after=SPACE_SM)

    if is_complex:
        abstracts_text = "\n\n".join(
            f"[{p['authors']}, {p['year']}] {p['title']}\n{p.get('abstract', '')}"
            for p in papers
        )
        prefs = _read_preferences()
        prompt = f"Query: {query}\n\nPeer-reviewed papers:\n{abstracts_text}"
        if prefs:
            prompt = f"[User preferences]\n{prefs}\n[End preferences]\n\n{prompt}"
        stream = stream_ai(prompt, SEARCH_SYSTEM_ACADEMIC)
        stream_to_terminal(stream, results=sources)
        return None, sources, True

    lines = [
        f"{C_ANSWER_MARK}{GLYPH_TLDR} TL;DR  Found {len(papers)} peer-reviewed result{'s' if len(papers) != 1 else ''}.{C_RESET}",
        "",
    ]
    for i, p in enumerate(papers, 1):
        abstract_preview = p.get("abstract", "")
        if abstract_preview:
            words = abstract_preview.split()
            if len(words) > 40:
                abstract_preview = " ".join(words[:40]) + f"{GLYPH_ELLIPSIS}"
            lines.append(f" {C_INTERACTIVE}{i}{C_RESET}  {p['title'][:_term_width()-6]}")
            lines.append(f"     {C_META}{p['authors']}, {p['year']} · {p['journal']}  [{p['source']}]{C_RESET}")
            lines.append(f"     {C_META}{abstract_preview}{C_RESET}")
        else:
            lines.append(f" {C_INTERACTIVE}{i}{C_RESET}  {p['title'][:_term_width()-6]}")
            lines.append(f"     {C_META}{p['authors']}, {p['year']} · {p['journal']}  [{p['source']}]{C_RESET}")
        lines.append("")

    return "\n".join(lines), sources, False


def _handle_factual(query: str) -> "tuple[str, list[dict], bool] | None":
    """Look up named entity on Wikipedia. Disambiguation → inline menu, not DDG fallback."""
    print_status("↳ looking up Wikipedia...")

    try:
        search_r = requests.get(
            "https://en.wikipedia.org/w/opensearch",
            params={"search": query, "limit": 4, "format": "json"},
            headers=HEADERS, timeout=2.0,
        )
        search_r.raise_for_status()
        search_data = search_r.json()
        titles = search_data[1] if len(search_data) > 1 else []
        descriptions = search_data[2] if len(search_data) > 2 else []
        urls = search_data[3] if len(search_data) > 3 else []
        if not titles:
            clear_status()
            return None
    except Exception:
        clear_status()
        return None

    try:
        title_encoded = titles[0].replace(" ", "_")
        summary_r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{title_encoded}",
            headers=HEADERS, timeout=2.0,
        )
        summary_r.raise_for_status()
        summary = summary_r.json()
    except Exception:
        clear_status()
        return None

    clear_status()

    extract = summary.get("extract", "")
    description = summary.get("description", "")
    page_url = summary.get("content_urls", {}).get("desktop", {}).get("page", urls[0] if urls else "")

    is_disambiguation = (
        len(extract) < 100
        or "may refer to" in extract.lower()
        or "disambiguation" in description.lower()
        or summary.get("type", "") == "disambiguation"
    )

    if is_disambiguation:
        choice_lines = [
            f"{C_ANSWER_MARK}{GLYPH_TLDR} TL;DR  Multiple Wikipedia articles match \"{titles[0]}\" — choose one.{C_RESET}",
            "",
        ]
        max_title_len = max(len(t) for t in titles[:4]) + 2
        descs = descriptions[:4] if descriptions else [""] * len(titles[:4])
        for i, (t, d) in enumerate(zip(titles[:4], descs), 1):
            desc_str = f"  {C_META}{d}{C_RESET}" if d else ""
            choice_lines.append(f" {C_INTERACTIVE}{i}{C_RESET}  {t:<{max_title_len}}{desc_str}")
        response = "\n".join(choice_lines)
        sources = [
            {"title": t, "url": u, "domain": "en.wikipedia.org", "snippet": d}
            for t, d, u in zip(
                titles[:4],
                descs,
                urls[:4] if urls else [""] * 4,
            )
        ]
        print_header(titles[0], f"{C_META}{GLYPH_META} Wikipedia · disambiguation{C_RESET}", zone_after=SPACE_SM)
        return response, sources, False

    paragraphs = [p.strip() for p in extract.split("\n") if p.strip()]
    if paragraphs:
        display_text = paragraphs[0]
        if len(display_text.split()) < 80 and len(paragraphs) > 1:
            display_text += "\n\n" + paragraphs[1]
    else:
        display_text = extract[:400]

    first_sentence = display_text.split(".")[0] + "."
    if len(first_sentence) > 120:
        first_sentence = first_sentence[:117] + GLYPH_ELLIPSIS

    lines = [
        f"{C_ANSWER_MARK}{GLYPH_TLDR} TL;DR  {first_sentence}{C_RESET}",
        "",
        display_text,
    ]
    response = "\n".join(lines)

    print_header(titles[0], f"{C_META}{GLYPH_META} Wikipedia · {description}{C_RESET}", zone_after=SPACE_SM)

    sources = [{
        "title": f"{titles[0]} — Wikipedia",
        "url": page_url,
        "domain": "en.wikipedia.org",
        "snippet": description,
    }]
    return response, sources, False


def _classify_tier(query: str) -> str:
    """Classify query into search tier using heuristics. Returns snippet | current | research | contested."""
    q = " " + query.lower() + " "
    # Current takes priority — time-sensitive queries beat everything
    if any(s in q for s in SEARCH_TIER_SIGNALS["current"]):
        return "current"
    if any(s in q for s in SEARCH_TIER_SIGNALS["contested"]):
        return "contested"
    if any(s in q for s in SEARCH_TIER_SIGNALS["research"]):
        return "research"
    return "snippet"


def _confidence_gate(query: str, results: list[dict], tier: str,
                     entity_type: str | None = None) -> str:
    """
    Check snippet quality. Returns the final tier to use — same as input or escalated one level.
    Never downgrades. Deep tiers (research, contested) pass through unchanged.
    """
    if tier in ("research", "contested") or not results:
        return tier

    year = time.strftime("%Y")
    q_lower = query.lower()
    snippets_text = " ".join(
        r.get("snippet", "") + " " + r.get("title", "") for r in results
    ).lower()

    # Freshness: temporal query but snippets contain no current-year signal
    is_temporal = any(s.strip() in (" " + q_lower + " ") for s in SEARCH_TIER_SIGNALS["current"])
    if is_temporal and year not in snippets_text and str(int(year) - 1) not in snippets_text:
        return "current"

    # Coverage: fewer than 30% of meaningful query words appear in snippets
    query_words = [w for w in q_lower.split() if len(w) > 4]
    if query_words:
        found = sum(1 for w in query_words if w in snippets_text)
        if found / len(query_words) < 0.3:
            return "research"

    # Authority: domain-specific query but zero authoritative sources returned
    if entity_type is None:
        entity_type = _identify_entity_type(query)
    if entity_type and entity_type in SOURCE_HIERARCHY:
        result_domains = {r.get("domain", "") for r in results}
        has_authority = any(
            any(auth in d for auth in SOURCE_HIERARCHY[entity_type])
            for d in result_domains
        )
        if not has_authority:
            return "current"

    return tier


def _deep_research(
    query: str,
    tier: str,
    results: list[dict],
    enriched_query: str = "",
    entity_type: str | None = None,
) -> tuple[str, list[dict]]:
    """
    Fetch real article content for deep-tier searches.
    Shows '↳ reading domain.com...' status per source.
    Returns (combined_content, sources_read). Returns ("", []) if all reads fail.

    Source caps: research tier → up to 5; current/contested → up to 3.
    Quality gate: skip articles under 150 words (was 100).
    """
    # Second-angle DDG search before building sources_to_read.
    # Surfaces sources the first query missed — especially valuable for research
    # queries that benefit from an expert/analytical angle.
    second_angle_results: list[dict] = []
    if enriched_query and tier in ("research", "current", "contested"):
        angle_suffix = {
            "research":  "expert analysis",
            "current":   "latest update",
            "contested": "counterargument criticism",
        }[tier]
        try:
            raw_angle = ddg_search(f"{enriched_query} {angle_suffix}", num_results=4)
            second_angle_results = _filter_results(raw_angle)
        except Exception:
            pass

    # Merge: primary results first, angle results second, dedup by domain
    seen_domains: set[str] = set()
    merged: list[dict] = []
    for r in list(results) + second_angle_results:
        d = r.get("domain", "")
        if d not in seen_domains:
            seen_domains.add(d)
            merged.append(r)

    # Source cap: research gets up to 5; all other deep tiers stay at 3
    source_cap = 5 if tier == "research" else 3
    sources_to_read = merged[:source_cap]

    # Authoritative domain fallback (unchanged from before)
    if entity_type is None:
        entity_type = _identify_entity_type(query)
    if tier in ("current", "contested") and entity_type in SOURCE_HIERARCHY:
        result_domains = {r.get("domain", "") for r in merged}
        has_authority = any(
            any(auth in d for auth in SOURCE_HIERARCHY[entity_type])
            for d in result_domains
        )
        if not has_authority and enriched_query:
            try:
                auth_domains = SOURCE_HIERARCHY[entity_type]
                site_query = enriched_query + " " + " ".join(
                    f"site:{d}" for d in auth_domains[:3]
                )
                targeted = _filter_results(ddg_search(site_query, num_results=3))
                if targeted:
                    sources_to_read = targeted[:2] + sources_to_read[:1]
            except Exception:
                pass

    # Read sources concurrently
    combined: list[str] = []
    sources_read: list[dict] = []
    READ_TIMEOUT = 8

    from urllib.parse import urlparse as _up

    def _read_one_source(idx_and_result):
        idx, r = idx_and_result
        url = r.get("url", "")
        domain = r.get("domain", "").removeprefix("www.")
        if not url or not url.startswith("http"):
            return None
        _parsed = _up(url)
        if _parsed.path in ("", "/", "//"):
            return None
        try:
            r_req = requests.get(url, headers=HEADERS, verify=SSL_CERT, timeout=READ_TIMEOUT)
            r_req.raise_for_status()
            html = r_req.text
            if _is_spa_shell(html):
                content = _fetch_with_jina(url)
            else:
                _, content = extract_text(html, max_words=1500, return_title=True)
            if content and len(content.split()) > 150:
                return (idx, domain, content, r)
        except Exception:
            pass
        return None

    valid_sources = [(i, r) for i, r in enumerate(sources_to_read[:source_cap])
                     if r.get("url", "").startswith("http")]

    domains_reading = [r.get("domain", "").removeprefix("www.") for _, r in valid_sources[:3]]
    sys.stdout.write(f"\r\033[90m↳ reading {' · '.join(domains_reading)}{GLYPH_ELLIPSIS}\033[0m" + " " * 10)
    sys.stdout.flush()

    with ThreadPoolExecutor(max_workers=min(5, len(valid_sources))) as executor:
        for result in executor.map(_read_one_source, valid_sources):
            if result:
                idx, domain, content, r = result
                combined.append(f"[{idx + 1}] {domain}\n{content[:2000]}")
                sources_read.append(r)

    sys.stdout.write("\r" + " " * 70 + "\r")
    sys.stdout.flush()

    return "\n\n---\n\n".join(combined), sources_read


def _rephrase_query(query: str) -> str:
    """Generate an alternative DDG query formulation for retry."""
    prompt = f"Rephrase this search query to find better results. Return ONLY the new query, no quotes, no explanation.\n\nOriginal: {query}"
    try:
        chunks = list(stream_groq(prompt, "You are a search query optimizer. Return only the query string.", model=CLASSIFIER_MODEL, max_tokens=60))
        return "".join(chunks).strip().strip('"').strip("'")
    except Exception:
        return query + " overview"


def _identify_knowledge_gaps(query: str, current_synthesis: str, seen_gaps: set[str] | None = None) -> list[str]:
    """Use LLM to identify what's missing from the current answer. Returns deduped list of follow-up queries."""
    if seen_gaps is None:
        seen_gaps = set()
    prompt = (
        f"Original question: {query}\n\n"
        f"Current answer:\n{current_synthesis[:2000]}\n\n"
        "What important aspects of this question are NOT covered in the answer? "
        "Return a JSON array of 1-3 specific search queries that would fill the gaps. "
        "Return ONLY the JSON array, no explanation. Example: [\"query one\", \"query two\"]"
    )
    try:
        chunks = list(stream_ai(prompt, "You identify knowledge gaps. Return only a JSON array of search queries.", max_tokens=200))
        raw = "".join(chunks).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        gaps = json.loads(raw)
        if isinstance(gaps, list) and all(isinstance(g, str) for g in gaps):
            return [g for g in gaps[:3] if g.lower().strip() not in seen_gaps]
        return []
    except Exception:
        return []


def _deep_search_loop(
    query: str,
    initial_results: list[dict],
    tier: str,
    max_steps: int = 3,
    timeout: float = 45.0,
) -> tuple[str, list[dict], list[str]]:
    """
    Multi-step deep search: search → read → identify gaps → search again → synthesize.
    Returns (final_synthesis, all_sources, step_log).
    Wall-clock timeout caps total duration.
    """
    t_start = time.time()
    all_sources: list[dict] = list(initial_results)
    all_content: list[str] = []
    step_log: list[str] = []
    seen_domains: set[str] = {r.get("domain", "") for r in initial_results}
    seen_gaps: set[str] = set()
    search_fn = _get_search_backend()

    # Initial deep read
    print_status(f"↳ reading {len(initial_results)} sources{GLYPH_ELLIPSIS}")
    step_log.append(f"read {len(initial_results)} initial sources")
    deep_content, deep_sources = _deep_research(query, tier, initial_results, query)
    if deep_content:
        all_content.append(deep_content)
    if deep_sources:
        for s in deep_sources:
            if s.get("domain", "") not in seen_domains:
                all_sources.append(s)
                seen_domains.add(s["domain"])

    # Initial synthesis
    base_prompt = build_search_prompt(query, all_sources)
    if deep_content:
        base_prompt += f"\n\nFull article content:\n{deep_content}"
    print_status(f"↳ synthesizing initial findings{GLYPH_ELLIPSIS}")
    with Spinner("synthesizing..."):
        chunks = list(stream_ai(base_prompt, SEARCH_SYSTEM_RESEARCH, max_tokens=1500))
    current_synthesis = "".join(chunks)
    clear_status()

    # Gap-filling loop
    for step in range(max_steps):
        if time.time() - t_start > timeout:
            step_log.append("timeout — moving to final synthesis")
            break

        gaps = _identify_knowledge_gaps(query, current_synthesis, seen_gaps=seen_gaps)
        if not gaps:
            step_log.append("no gaps found — search complete")
            break

        gap_query = gaps[0]
        seen_gaps.add(gap_query.lower().strip())
        print_status(f"↳ filling gap — {gap_query[:50]}{GLYPH_ELLIPSIS}")
        step_log.append(f"gap search — \"{gap_query}\"")

        try:
            gap_results = _filter_results(search_fn(gap_query, num_results=5))
        except Exception:
            gap_results = []

        new_results = [r for r in gap_results if r.get("domain", "") not in seen_domains]
        if not new_results:
            step_log.append("no new sources found")
            continue

        for r in new_results[:3]:
            seen_domains.add(r["domain"])
            all_sources.append(r)

        gap_content, gap_sources = _deep_research(gap_query, tier, new_results[:3], gap_query)
        if gap_content:
            all_content.append(gap_content)

    # Final synthesis with all accumulated content
    clear_status()
    combined_content = "\n\n---\n\n".join(all_content)

    prefs = _read_preferences()
    _deep_vault_notes, _deep_sparked_by = _vault_retrieve(query)
    _deep_vault_ctx = _format_vault_context(_deep_vault_notes)
    preamble = ""
    if prefs:
        preamble += f"[User preferences]\n{prefs}\n[End preferences]\n\n"
    if _deep_vault_ctx:
        preamble += f"{_deep_vault_ctx}\n\n{VAULT_CONTEXT_INSTRUCTION}\n\n"

    final_prompt = (
        f"{preamble}"
        f"Original question: {query}\n\n"
        f"Research from {len(all_sources)} sources across {len(step_log)} search steps:\n\n"
        f"{combined_content[:8000]}\n\n"
        f"Provide a comprehensive answer. Cite sources inline as [1], [2], etc."
    )

    elapsed = time.time() - t_start
    print(f"{C_META}{GLYPH_META} deep search: {len(step_log)} steps, {len(all_sources)} sources, {elapsed:.0f}s{C_RESET}")
    print_header(query.capitalize(), f"{len(all_sources)} sources {GLYPH_SEPARATOR} deep search")
    stream = stream_ai(final_prompt, SEARCH_SYSTEM_RESEARCH, max_tokens=3000, tier="research")
    final_synthesis = stream_to_terminal(stream, results=all_sources)

    return final_synthesis, all_sources, step_log


def _search_with_retry(query: str, entity_type: str | None = None, search_fn: callable | None = None) -> tuple[list[dict], list[str]]:
    """
    Wrap search backend with up to 3 narrated attempts.
    Returns (results, queries_tried).
    'Thin' means fewer than 3 results or all snippets under 50 chars.
    """
    if search_fn is None:
        search_fn = _get_search_backend()

    def _is_thin(results: list[dict]) -> bool:
        if len(results) < 3:
            return True
        return all(len(r.get("snippet", "")) < 50 for r in results)

    queries_tried = []

    # Attempt 1: original query
    queries_tried.append(query)
    results = search_fn(query)
    results = _filter_results(results)
    if not _is_thin(results):
        return results, queries_tried

    # Attempt 2: rephrased query
    print_status("↳ That first pass was thin — trying a different angle...")
    rephrased = _rephrase_query(query)
    queries_tried.append(rephrased)
    results2 = _filter_results(search_fn(rephrased))
    clear_status()
    if not _is_thin(results2):
        return results2, queries_tried

    # Attempt 3: add domain hint
    print_status("↳ Still not much — adding a source hint...")
    if entity_type and entity_type in _SOURCE_INTELLIGENCE:
        domain_hint = _SOURCE_INTELLIGENCE[entity_type][0].split(".")[0]
    else:
        domain_hint = "wikipedia"
    hinted = f"{query} {domain_hint}"
    queries_tried.append(hinted)
    results3 = _filter_results(search_fn(hinted))
    clear_status()

    # Return best non-empty set, prefer whichever has most results
    best = max([results, results2, results3], key=len)
    return best, queries_tried


def search_flow(query: str, interactive: bool = True, json_output: bool = False, deep: bool = False, source_filter: list[str] | None = None, fresh: bool = False, intent: dict | None = None) -> tuple[list[dict], str]:
    """
    Run the search flow: search → AI synthesis → display results.
    Returns (results, response_text).
    """
    _t_start = time.time()

    # Specialized routing: try dedicated APIs before DDG (runs in json_output mode too)
    _source_type = _classify_data_source(query)
    if _source_type != "web":
        _specialized = _run_specialized_query(query, _source_type, _t_start, interactive,
                                              json_output=json_output)
        if _specialized is not None:
            return _specialized

    # Intent-driven tier — use assess_intent if provided, fall back to keyword heuristics
    if intent and intent.get("tier"):
        tier = intent["tier"]
    else:
        tier = _classify_tier(query)

    # Use reformulated query from intent engine when available
    eval_context = None
    if intent and intent.get("source_strategy") in ("academic", "authoritative", "official"):
        eval_context = {"is_evaluative": True, "source_signals": [], "avoid_signals": []}
    elif tier in ("contested", "research") and _is_evaluative_query(query, tier):
        eval_context = _evaluate_query_intent(query)

    if intent and intent.get("reformulated_query") and intent["reformulated_query"] != query:
        clean_query = intent["reformulated_query"]
        ddg_query = _enrich_ddg_query(clean_query, tier=tier)
    elif eval_context and eval_context.get("source_signals"):
        source_hint = " ".join(eval_context["source_signals"][:3])
        clean_query = _clean_conversational_query(query)
        ddg_query = _enrich_ddg_query(clean_query, tier=tier, source_hint=source_hint)
    else:
        clean_query = _clean_conversational_query(query)
        ddg_query = _enrich_ddg_query(clean_query, tier=tier)

    _entity_domain = intent.get("domain", "") if intent else ""
    _entity_type = _entity_domain if _entity_domain in SOURCE_HIERARCHY else _identify_entity_type(query)

    print_status(f"↳ searching: \"{ddg_query[:55]}\"...")
    try:
        results, _queries_tried = _search_with_retry(ddg_query, entity_type=_entity_type)
        if tier in ("research", "contested") and results:
            alt_query = (
                f"{ddg_query} analysis expert opinion"
                if tier == "research"
                else f"{ddg_query} alternative perspective drawbacks"
            )
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
        _queries_tried = [ddg_query]
        clear_status()
        print(f"\033[31mSearch failed: {e}\033[0m")
        return [], ""
    clear_status()
    results = _filter_results(results, evaluative_context=eval_context)

    # Entity match check: if query mentions a specific institution/location but
    # results are about a different entity, retry with quoted exact-match search
    results, ddg_query = _fix_entity_mismatch(query, results, ddg_query,
                                               evaluative_context=eval_context)

    # Custom source filter: restrict to specified domains
    if source_filter:
        results = _filter_by_sources(results, source_filter)
        if not results:
            site_query = query + " " + " ".join(f"site:{d}" for d in source_filter[:3])
            try:
                results = _filter_results(_get_search_backend()(site_query, num_results=8))
            except Exception:
                pass

    if not results:
        print("\033[90mNo results found.\033[0m")
        return [], ""

    domains = " · ".join(_shorten_domain(r["domain"]) for r in results[:3])
    print_header(query.capitalize(), f"{domains}  ({len(results)} results)")

    news_words = {"news", "latest", "today", "war", "conflict", "update", "breaking", "live"}
    if any(w in query.lower().split() for w in news_words):
        from datetime import datetime
        ts = datetime.now().strftime("%B %d, %Y %H:%M")
        print(f"\033[90mFetched {ts}\033[0m\n")

    # BM25 rerank: sort by relevance to query before deep reading
    if len(results) > 1:
        results = _bm25_rank(query, results)

    # Compute entity type — prefer intent-derived domain, fall back to keyword heuristic
    if not _entity_type:
        _entity_type = _identify_entity_type(query)

    # Adaptive confidence gate — may escalate tier based on snippet quality
    tier = _confidence_gate(query, results, tier, entity_type=_entity_type)

    # Deep search mode: multi-step reasoning loop
    if deep and results:
        synthesis, all_sources, step_log = _deep_search_loop(query, results, tier)
        save_session_entry(query, "deep_search", _truncate_at_sentence(synthesis, 300))
        _deep_note = _obsidian_save(
            query, synthesis, all_sources, session_id=_obsidian_session_id(),
            sparked_by=_deep_sparked_by, depth="exploration",
        )
        record_feature_use("search")
        if json_output:
            _output_json(query, synthesis, [s["domain"] for s in all_sources], intent="deep_search")
            return all_sources, synthesis
        vspace(ZONE_SPACING[("answer", "metadata")])
        _print_linked_sources(all_sources)
        print_results(all_sources)
        _meta = _SearchMeta(
            original_query=query,
            queries_tried=[f"deep:{s}" for s in step_log],
            result_count=len(all_sources),
            confidence_tier="deep",
            coverage_note=f"Deep search: {len(step_log)} steps, {len(all_sources)} sources",
        )
        if interactive:
            _deep_stem = os.path.splitext(os.path.basename(_deep_note))[0] if _deep_note else ""
            _handle_results_input(all_sources, context=synthesis, meta=_meta, parent_note_stem=_deep_stem)
        return all_sources, synthesis

    # Self-evaluating source check: try one more targeted search when sources are thin.
    # Fast cosine check first (free) — if snippets are near-copies, skip the LLM call.
    # Only run on deep tiers where it's worth the extra search.
    _sources_thin = (
        tier in ("research", "current", "contested")
        and results
        and (not _snippets_are_diverse(results) or not _sources_are_substantive(query, results))
    )
    if _sources_thin:
        retry_query = f"{ddg_query} analysis in-depth {time.strftime('%Y')}"
        try:
            print_status("↳ sources thin — searching deeper...")
            retry_results = _filter_results(ddg_search(retry_query, num_results=5))
            if retry_results:
                # Merge, dedup by domain, prefer retry results
                seen = {r["domain"] for r in retry_results}
                for r in results:
                    if r["domain"] not in seen:
                        retry_results.append(r)
                        seen.add(r["domain"])
                results = retry_results
                ddg_query = retry_query
                clear_status()
        except Exception:
            clear_status()

    # Build base prompt (used by all tiers)
    base_prompt = build_search_prompt(query, results)
    session_ctx = format_session_context()
    if session_ctx:
        base_prompt = f"{session_ctx}\n\n{base_prompt}"

    if not fresh:
        vault_notes, _sparked_by_stem = _vault_retrieve(query)
        vault_ctx = _format_vault_context(vault_notes)
    else:
        vault_notes, _sparked_by_stem, vault_ctx = [], "", ""
    if vault_ctx:
        base_prompt = f"{vault_ctx}\n\n{VAULT_CONTEXT_INSTRUCTION}\n\n{base_prompt}"
        _dates = [n["date"] for n in vault_notes]
        _range = f"{min(_dates)}–{max(_dates)}" if len(_dates) > 1 else _dates[0]
        print(f"{C_META}{GLYPH_META} drawing from {len(vault_notes)} vault note{'s' if len(vault_notes) != 1 else ''} ({_range}){C_RESET}")

    # Preferences: user's research profile — always injected, highest priority context
    prefs = _read_preferences()
    if prefs:
        base_prompt = f"[User preferences]\n{prefs}\n[End preferences]\n\n{base_prompt}"

    _t0 = time.time()

    if tier in ("current", "research", "contested"):
        # Show why surf is going deep — teaches users the tier system passively
        _tier_why = {
            "current":   "↳ current events — reading today's sources...",
            "research":  "↳ research question — reading in depth...",
            "contested": "↳ evaluating from multiple perspectives...",
        }
        print_status(_tier_why.get(tier, "↳ thinking..."))
        deep_content, deep_sources = _deep_research(query, tier, results, ddg_query, entity_type=_entity_type)

        if deep_content:
            source_count = len(deep_sources)
            print_status(f"↳ synthesizing {source_count} source{'s' if source_count != 1 else ''}...")
            prompt = base_prompt + f"\n\nFull article content from {source_count} source(s):\n{deep_content}"
            # Select system prompt — evaluative queries get independence-focused voice
            if eval_context and eval_context.get("is_evaluative"):
                system = SEARCH_SYSTEM_EVALUATIVE
            else:
                system = {
                    "current":   SEARCH_SYSTEM_CURRENT,
                    "research":  SEARCH_SYSTEM_RESEARCH,
                    "contested": SEARCH_SYSTEM_CONTESTED,
                }[tier]
        else:
            # All reads failed — fall back to snippets, but keep evaluative voice if relevant
            prompt = base_prompt
            system = SEARCH_SYSTEM_EVALUATIVE if (eval_context and eval_context.get("is_evaluative")) else SEARCH_SYSTEM
            deep_sources = []

        clear_status()
        stream = stream_ai(prompt, system, tier=tier)
        # Deep path: pass results so [1][2][3] citations render as clickable links
        response = stream_to_terminal(stream, results=results)

        # Use deep_sources for the linked sources line if available
        if deep_sources:
            results = deep_sources + [r for r in results if r not in deep_sources][:2]
    else:
        # Snippet path: no inline citations — clean prose reads better without [1][2] noise
        system = SEARCH_SYSTEM
        print_status("↳ thinking...")
        clear_status()
        stream = stream_ai(base_prompt, system, tier=tier)
        response = stream_to_terminal(stream, results=None)

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
                    verify_stream = stream_ai(verify_prompt, system, tier=tier)
                    response = stream_to_terminal(verify_stream, results=results)
            except Exception:
                clear_status()

    # Save to session memory
    # Extract a brief summary: first 200 chars of response after TL;DR
    summary = response.strip()
    if "▸ TL;DR" in summary:
        summary = summary.split("▸ TL;DR")[-1].strip()
    save_session_entry(query, "search", _truncate_at_sentence(summary, 300))
    _depth = "exploration" if vault_notes else "lookup"
    _last_vault_note = _obsidian_save(
        query, response, results, session_id=_obsidian_session_id(),
        sparked_by=_sparked_by_stem, depth=_depth,
    )
    record_feature_use("search")  # counts toward automation tip threshold

    if json_output:
        sources = [r["domain"] for r in results]
        _output_json(query, response, sources, intent="search")
        return results, response

    if _HAS_ANTHROPIC and _claude_budget_ok():
        _usage_d = _claude_usage_load()
        _spent = _usage_d.get("cost_usd", 0.0)
        spend = f" · \033[90mclaude {claude_monthly_spend()}"
        # Nudge toward --usage once spend is meaningful
        if _spent >= 0.50 and _usage_d.get("_usage_hint_shown", 0) == 0:
            spend += " · try \033[33msurf --usage\033[90m"
            _usage_d["_usage_hint_shown"] = 1
            _claude_usage_save(_usage_d)
        spend += "\033[0m"
    else:
        spend = ""
    # Answer → Metadata zone transition: SPACE_XS (timing is a caption)
    vspace(ZONE_SPACING[("answer", "metadata")])
    _ec = _elapsed_color(_elapsed)
    print(f"{_ec}{GLYPH_META} {_elapsed:.1f}s{C_RESET}{spend}")
    _print_linked_sources(results)
    # Metadata → Action zone: SPACE_NONE (print_results starts with divider)
    print_results(results)

    _meta = _SearchMeta(
        original_query=query,
        queries_tried=_queries_tried,
        result_count=len(results),
        confidence_tier=tier,
        coverage_note=(
            f"Searches tried: {'; '.join(_queries_tried)}"
            if len(results) < 3 else None
        ),
    )

    _save_search_snapshot(query, response, results)

    if interactive:
        _parent_stem = os.path.splitext(os.path.basename(_last_vault_note))[0] if _last_vault_note else ""
        _handle_results_input(results, context=response, meta=_meta, parent_note_stem=_parent_stem)

    return results, response

def _classify_and_dispatch(
    choice: str,
    results: list[dict],
    meta: "_SearchMeta | None",
    context: str,
    parent_note_stem: str = "",
) -> "tuple[list[dict], str, _SearchMeta | None, bool]":
    """
    Classify user input and dispatch to the right handler.
    Returns (new_results, new_context, new_meta, should_break).
    """
    cl = choice.lower().strip()
    input_type = _classify_input(choice)

    # ── commands ──────────────────────────────────────────────────────────────
    if input_type == "command":
        if cl == "q":
            return results, context, meta, True
        if cl == "n":
            query = surf_input("New search: ")
            if query:
                search_flow(query)
            return results, context, meta, True
        if cl == "?":
            n = len(results)
            print()
            print("\033[1msurf commands\033[0m")
            print(f"  \033[33m1–{n}\033[0m      read article in terminal")
            print(f"  \033[33ms1–s{n}\033[0m    quick AI summary")
            print(f"  \033[33mo1–o{n}\033[0m    open in browser")
            print(f"  \033[33mn\033[0m        new search")
            print(f"  \033[33mq\033[0m        quit")
            print(f"  \033[33m↵\033[0m        follow-up question")
            print()
            return results, context, meta, False
        if cl.startswith("o") and cl[1:].isdigit():
            idx = int(cl[1:]) - 1
            if 0 <= idx < len(results):
                record_feature_use("browser")
                open_in_browser(results[idx]["url"])
            return results, context, meta, False
        if cl.startswith("s") and cl[1:].isdigit():
            idx = int(cl[1:]) - 1
            if 0 <= idx < len(results):
                record_feature_use("summary")
                read_flow(results[idx]["url"], interactive=True, ai_summary=True, parent_note_stem=parent_note_stem)
                return results, context, meta, True
            return results, context, meta, False
        if cl.isdigit():
            idx = int(cl) - 1
            if 0 <= idx < len(results):
                record_feature_use("reader")
                read_flow(results[idx]["url"], interactive=True, ai_summary=False, parent_note_stem=parent_note_stem)
                return results, context, meta, True
            if results:
                print(f"\033[90mPick 1-{len(results)} to read an article\033[0m")
            return results, context, meta, False
        if choice.lower().startswith("prefer:"):
            _handle_inline_preference(choice[7:].strip())
            return results, context, meta, False
        # dead_end options r/t from _conversational_reply
        if cl == "r" and results:
            read_flow(results[0]["url"], interactive=True, ai_summary=True)
            return results, context, meta, True
        if cl == "t":
            query = surf_input("New search: ")
            if query:
                search_flow(query)
            return results, context, meta, True
        return results, context, meta, False

    # ── casual ────────────────────────────────────────────────────────────────
    if input_type == "casual":
        _conversational_reply("casual", meta=meta, user_text=choice)
        return results, context, meta, False

    # ── correction ────────────────────────────────────────────────────────────
    if input_type == "correction":
        _conversational_reply("correction", meta=meta, user_text=choice)
        record_feature_use("followup")
        new_results, new_context, new_meta = _handle_followup(choice, context="")
        if new_results:
            print_results(new_results)
        return new_results or results, new_context, new_meta, False

    # ── redirect ──────────────────────────────────────────────────────────────
    if input_type == "redirect":
        _conversational_reply("redirect", meta=meta, user_text=choice)
        record_feature_use("followup")
        broader = (meta.original_query if meta else choice) + " comprehensive overview all"
        new_results, new_context, new_meta = _handle_followup(broader, context="")
        if new_results:
            print_results(new_results)
        return new_results or results, new_context, new_meta, False

    # ── scope_expansion ───────────────────────────────────────────────────────
    if input_type == "scope_expansion":
        record_feature_use("followup")
        new_results, new_context, new_meta = _handle_scope_expansion(choice, meta=meta, context=context)
        return new_results or results, new_context, new_meta, False

    # ── followup (default) ────────────────────────────────────────────────────
    record_feature_use("followup")
    if format_session_context():
        record_feature_use("session")
    new_results, new_context, new_meta = _handle_followup(choice, context=context)
    if new_results:
        print_results(new_results)
        results = new_results
    return results, new_context or context, new_meta, False


def _handle_results_input(results: list[dict], context: str = "", meta: "_SearchMeta | None" = None, parent_note_stem: str = "") -> None:
    """Wait for user input and dispatch via _classify_and_dispatch."""
    while True:
        try:
            choice = surf_input("ask a follow-up or type a new search")
        except (KeyboardInterrupt, EOFError):
            break

        if not choice.strip():
            continue

        _add_to_history(choice)
        results, context, meta, should_break = _classify_and_dispatch(choice, results, meta, context, parent_note_stem=parent_note_stem)
        if should_break:
            break

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


def _handle_followup(question: str, context: str = "") -> "tuple[list[dict], str, _SearchMeta]":
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
    _search_fn = _get_search_backend()
    try:
        search_results = _search_fn(search_query)
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
                auth_results = _filter_results(_search_fn(auth_query, num_results=3))
                seen = {r["domain"] for r in search_results}
                for r in auth_results:
                    if r["domain"] not in seen:
                        search_results.append(r)
                        seen.add(r["domain"])
            except Exception:
                pass
    clear_status()

    domains = " · ".join(_shorten_domain(r["domain"]) for r in search_results[:3]) if search_results else ""
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
    base_prompt = "\n\n".join(prompt_parts)

    # Apply the same tier routing as search_flow — follow-ups deserve deep reads too
    tier = _classify_tier(question)
    tier = _confidence_gate(question, search_results, tier)

    _t0 = time.time()
    if tier in ("current", "research", "contested") and search_results:
        deep_content, deep_sources = _deep_research(question, tier, search_results, search_query)
        if deep_content:
            prompt = base_prompt + f"\n\nFull article content:\n{deep_content}"
            system = {
                "current":   SEARCH_SYSTEM_CURRENT,
                "research":  SEARCH_SYSTEM_RESEARCH,
                "contested": SEARCH_SYSTEM_CONTESTED,
            }[tier]
            if deep_sources:
                search_results = deep_sources + [r for r in search_results if r not in deep_sources][:2]
        else:
            prompt, system = base_prompt, SEARCH_SYSTEM
    else:
        prompt, system = base_prompt, SEARCH_SYSTEM

    stream = stream_ai(prompt, system)
    # Only pass results for deep-tier follow-ups where citations make sense
    cite_results = search_results if tier in ("current", "research", "contested") else None
    response = stream_to_terminal(stream, results=cite_results)
    _elapsed = time.time() - _t0

    if _HAS_ANTHROPIC and _claude_budget_ok():
        _usage_d = _claude_usage_load()
        _spent = _usage_d.get("cost_usd", 0.0)
        spend = f" · \033[90mclaude {claude_monthly_spend()}"
        # Nudge toward --usage once spend is meaningful
        if _spent >= 0.50 and _usage_d.get("_usage_hint_shown", 0) == 0:
            spend += " · try \033[33msurf --usage\033[90m"
            _usage_d["_usage_hint_shown"] = 1
            _claude_usage_save(_usage_d)
        spend += "\033[0m"
    else:
        spend = ""
    vspace(ZONE_SPACING[("answer", "metadata")])
    _ec = _elapsed_color(_elapsed)
    print(f"{_ec}{GLYPH_META} {_elapsed:.1f}s{C_RESET}{spend}")
    _print_linked_sources(search_results)
    _fup_meta = _SearchMeta(
        original_query=question,
        queries_tried=[search_query],
        result_count=len(search_results),
        confidence_tier=tier,
        coverage_note=None,
    )
    return search_results, response, _fup_meta

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

def _is_homepage_url(url: str) -> bool:
    """True if the URL points to a site root/homepage rather than a specific article."""
    from urllib.parse import urlparse as _up2
    p = _up2(url)
    return p.path in ("", "/", "//") or (p.path.rstrip("/") == "" and not p.query)


def read_flow(url: str, interactive: bool = True, ai_summary: bool = True, json_output: bool = False, parent_note_stem: str = "") -> str:
    """
    Run the read flow: fetch URL → extract text → Groq → display.
    Returns the Groq response text (or raw extracted text in raw mode).
    """
    domain_display = url.replace("https://", "").replace("http://", "").split("/")[0]

    # Detect homepage URLs early — switch to headlines mode to avoid dumping navigation
    if _is_homepage_url(url):
        print(f"\n{C_META}{GLYPH_META} {domain_display} is a homepage — showing top headlines instead of full content.{C_RESET}")
        print(f"{C_META}  use {C_INTERACTIVE}o{C_META} to open the site in your browser for full navigation.{C_RESET}\n")
        # Continue with read — but ai_summary=True so it summarizes instead of full-article mode
        ai_summary = True
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

    # Fetch relevant sub-pages — only for non-news pages where sub-content adds value
    # Skip for homepages and news sites (sub-pages are "about", "books", etc. — useless for news)
    sub_labels = []
    _news_domains = {"apnews.com", "bbc.com", "reuters.com", "cnn.com", "nytimes.com",
                     "foxnews.com", "cbsnews.com", "nbcnews.com", "npr.org", "theguardian.com",
                     "washingtonpost.com", "usatoday.com", "abcnews.go.com"}
    _skip_subpages = _is_homepage_url(url) or domain_display in _news_domains
    try:
        is_spa = _is_spa_shell(html)
        if is_spa:
            print_status(f"↳ {domain_display} is JS-rendered — using Jina reader...")
            time.sleep(0.3)
        if not _skip_subpages:
            sub_page_text, sub_labels = _fetch_sub_pages(html, url, max_pages=3)
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
    save_session_entry(url, "url", _truncate_at_sentence(summary, 300))

    # Save article read to Obsidian vault
    _obs_note_path = _obsidian_save(
        title or url, summary, [], session_id=_obsidian_session_id(),
        deep_dive_of=parent_note_stem, depth="deep-dive" if parent_note_stem else "lookup",
    )
    if _obs_note_path:
        print(f"\033[90m↳ saved to vault\033[0m")

    if json_output:
        _output_json(url, response, [domain], url=url, intent="read")
        return response

    related = parse_related_topics(response) if ai_summary else []
    domain_link = _link(url, domain)
    # Answer → Metadata/Action zone: SPACE_XS then divider
    vspace(ZONE_SPACING[("answer", "metadata")])
    print_divider()
    _vault_hint = "   open in obsidian: v" if _obsidian_vault_path() else ""
    if related:
        print(f"{C_META}  related: 1{GLYPH_RANGE}{len(related)}   open {domain_link}: o   follow-up: ?   quit: q{_vault_hint}{C_RESET}")
    else:
        print(f"{C_META}  open {domain_link}: o   follow-up: ?   new search: n   quit: q{_vault_hint}{C_RESET}")
    vspace(ZONE_SPACING[("actions", "prompt")])

    if interactive:
        _handle_article_input(url, related, response, note_path=_obs_note_path)

    return response

# ─── Local file analysis ─────────────────────────────────────────────────────

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".sh", ".bash",
    ".zsh", ".yaml", ".yml", ".toml", ".json", ".xml", ".sql", ".r",
    ".lua", ".pl", ".php", ".scala", ".zig", ".nim", ".ex", ".exs",
    ".vue", ".svelte", ".css", ".scss", ".less",
}
_FILE_MAX_WORDS = 15000


def _extract_file_content(path: str) -> tuple[str, str]:
    """Extract text content from a local file. Returns (content, file_type)."""
    if not os.path.isfile(path):
        return "", "unknown"

    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        try:
            from pdfminer.high_level import extract_text as pdf_extract
            content = pdf_extract(path)
            if content and content.strip():
                words = content.split()
                if len(words) > _FILE_MAX_WORDS:
                    content = " ".join(words[:_FILE_MAX_WORDS]) + "\n[truncated]"
                return content.strip(), "pdf"
        except ImportError:
            pass
        except Exception:
            pass
        return "", "pdf"

    if ext in (".html", ".htm"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                html = f.read()
            _, text = extract_text(html, max_words=_FILE_MAX_WORDS, return_title=True)
            return text, "html"
        except Exception:
            return "", "html"

    if ext in _CODE_EXTENSIONS:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            words = content.split()
            if len(words) > _FILE_MAX_WORDS:
                content = " ".join(words[:_FILE_MAX_WORDS]) + "\n[truncated]"
            return content, "code"
        except Exception:
            return "", "code"

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        words = content.split()
        if len(words) > _FILE_MAX_WORDS:
            content = " ".join(words[:_FILE_MAX_WORDS]) + "\n[truncated]"
        return content, "text"
    except Exception:
        return "", "unknown"


def _handle_article_input(url: str, related: list[str], context: str, note_path: str | None = None) -> None:
    """Interactive prompt after reading an article."""
    followup_results: list[dict] = []
    while True:
        try:
            choice = surf_input("ask a follow-up or open a related topic")
        except (KeyboardInterrupt, EOFError):
            break

        _add_to_history(choice)
        cl = choice.lower()

        if cl == "q":
            break
        elif cl == "?":
            print()
            print("\033[1msurf reader commands\033[0m")
            print(f"  \033[33mo\033[0m              open in browser  (or cmd+click the link in the header)")
            print(f"  \033[33mv\033[0m              open in Obsidian  (if vault configured)")
            print(f"  \033[33m?\033[0m              ask a follow-up question about this article")
            print(f"  \033[33m/note <text>\033[0m   append a note to the vault entry")
            print(f"  \033[33mn\033[0m              new search")
            print(f"  \033[33mq\033[0m              quit")
            print()
        elif cl == "n":
            query = surf_input("New search: ")
            if query:
                search_flow(query)
            break
        elif cl == "o":
            open_in_browser(url)
        elif cl == "v":
            vault = _obsidian_vault_path()
            if vault and note_path and os.path.exists(note_path):
                import urllib.parse
                vault_name = os.path.basename(vault.rstrip("/").rstrip(os.sep))
                rel = os.path.relpath(note_path, vault)
                obs_url = f"obsidian://open?vault={urllib.parse.quote(vault_name)}&file={urllib.parse.quote(rel)}"
                open_in_browser(obs_url)
            else:
                vault = _obsidian_vault_path()
                if not vault:
                    print(f"\033[90m(no vault configured — run 'surf setup' to add one)\033[0m")
                elif not note_path:
                    print(f"\033[90m(note was not saved — check vault permissions)\033[0m")
                else:
                    print(f"\033[90m(note file not found)\033[0m")
        elif cl.isdigit():
            idx = int(cl) - 1
            if followup_results and 0 <= idx < len(followup_results):
                read_flow(followup_results[idx]["url"], interactive=True, ai_summary=False)
                break
            elif 0 <= idx < len(related):
                topic = related[idx]
                if len(topic) > 2 and topic[1] in ".)":
                    topic = topic[2:].strip()
                search_flow(topic)
                break
            else:
                n = len(followup_results) if followup_results else len(related)
                if n:
                    print(f"\033[90mPick 1-{n} or type a follow-up question\033[0m")
        elif choice.lower().startswith("prefer:"):
            _handle_inline_preference(choice[7:].strip())
        elif choice.lower().startswith("/note ") or choice.lower().startswith("/note\t"):
            note_text = choice[6:].strip()
            if not note_text:
                print(f"\033[90mUsage: /note <your thought>\033[0m")
            elif note_path and os.path.exists(note_path):
                ts = time.strftime("%H:%M")
                with open(note_path, "a", encoding="utf-8") as _nf:
                    _nf.write(f"\n\n> 📝 {ts}: {note_text}\n")
                print(f"\033[90m↳ note saved to vault\033[0m")
            else:
                vault = _obsidian_vault_path()
                if not vault:
                    print(f"\033[90m(no vault configured — run 'surf setup' to add one)\033[0m")
                else:
                    print(f"\033[90m(note was not saved — check vault permissions)\033[0m")
        elif choice.strip():
            if _is_casual_input(choice):
                print(f"\033[90m(surf is a search tool — try asking a question or picking a result)\033[0m")
            else:
                new_results, new_response, _ = _handle_followup(choice, context=context)
                if new_results:
                    print_results(new_results)
                    followup_results = new_results
                context = new_response
        # empty input: loop again

# ─── Intent Engine ────────────────────────────────────────────────────────────

_INSTANT_PATTERNS = re.compile(
    r"^\d[\d\s\+\-\*/\.\(\)]+$"
    r"|^convert\s"
    r"|^translate\s"
    r"|^\d+\s*(?:kg|lb|km|mi|cm|inch|ft|oz|g|c|f|celsius|fahrenheit)\s+(?:to|in)\s"
    r"|^what is \d",
    re.IGNORECASE,
)

ASSESS_INTENT_SYSTEM = """You assess search intent. Think step by step, then return JSON.

STEP 1 — Understand the query:
- What is the user actually asking? Resolve pronouns ("this", "that", "it") using session context if provided.
- Emotional register: curious / urgent / skeptical / worried / academic
- Is this time-sensitive? (about recent/current events, scores, prices, news)
- Domain: sports / finance / medical / science / tech / legal / news / general
  If ambiguous, note it: "ambiguous: X or Y"
- Task type: lookup (single fact) / analysis (how/why) / comparison (A vs B) / decision (should I) / troubleshooting (fix/debug) / emotional (worried/stressed)

STEP 2 — Based on your understanding, return ONLY this JSON (no explanation, no markdown):
{
  "route": "search" or "instant" or "transactional" or "navigation",
  "tier": "snippet" or "current" or "research" or "contested",
  "domain": string (one of: sports, finance, medical, science, tech, legal, news, general),
  "source_strategy": "any" or "authoritative" or "academic" or "official",
  "answer_depth": "concise" or "analytical" or "comprehensive" or "empathetic",
  "reformulated_query": "optimized search terms — strip filler, add precision",
  "confidence": 0.0 to 1.0,
  "open_url": null or string URL for transactional/navigation
}

Rules:
- route "instant": math, translations, unit conversions, simple definitions
- route "transactional": booking flights/hotels, buying — include open_url with best deep link
- route "navigation": user wants a specific website — include open_url
- tier "snippet": simple factual lookups, definitions
- tier "current": anything about recent events, scores, today, this week, latest, prices
- tier "research": how/why questions, mechanisms, peer review, academic, deep explanations
- tier "contested": comparisons, evaluations, "should I", "best X", "is X safe", opinions
- source_strategy "academic": when user asks for research, studies, peer review, evidence
- source_strategy "authoritative": medical safety, legal questions, financial decisions
- source_strategy "official": government data, regulations, official stats
- answer_depth "empathetic": when user expresses worry, stress, personal struggle
- answer_depth "concise": simple facts, definitions, one-answer questions
- answer_depth "analytical": how/why, mechanisms, explanations
- answer_depth "comprehensive": complex multi-faceted topics, deep research
- reformulated_query: remove filler ("find me", "can you", "I want to know"), keep domain terms
- confidence: lower when query is ambiguous or context-dependent"""

_ASSESS_FALLBACK = {
    "route": "search",
    "tier": "snippet",
    "domain": "general",
    "source_strategy": "any",
    "answer_depth": "analytical",
    "reformulated_query": "",
    "confidence": 0.5,
    "open_url": None,
}


def assess_intent(query: str, vault_depth: int = 0, session_context: str = "") -> dict:
    """Unified intent assessment — replaces classify_intent + _classify_tier + _evaluate_query_intent.

    Two-stage decomposition in one 8b LLM call:
    Stage 1: understand the query (reasoning)
    Stage 2: structured JSON output (extraction)
    """
    if _INSTANT_PATTERNS.search(query):
        return {**_ASSESS_FALLBACK, "route": "instant", "tier": "snippet",
                "answer_depth": "concise", "confidence": 0.95,
                "reformulated_query": query}

    context_block = ""
    if session_context:
        context_block += f"\nRecent session:\n{session_context}\n"
    if vault_depth > 0:
        context_block += f"\nUser has {vault_depth} prior vault notes on related topics.\n"

    prompt = f"Query: \"{query}\"\n{context_block}"

    try:
        chunks = list(stream_groq(prompt, ASSESS_INTENT_SYSTEM, model=CLASSIFIER_MODEL, max_tokens=300))
        raw = "".join(chunks).strip()
        json_start = raw.find("{")
        json_end = raw.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            raw = raw[json_start:json_end]
        result = json.loads(raw)
        validated = {}
        validated["route"] = result.get("route", "search") if result.get("route") in ("search", "instant", "transactional", "navigation") else "search"
        validated["tier"] = result.get("tier", "snippet") if result.get("tier") in ("snippet", "current", "research", "contested") else "snippet"
        validated["domain"] = result.get("domain", "general") or "general"
        validated["source_strategy"] = result.get("source_strategy", "any") if result.get("source_strategy") in ("any", "authoritative", "academic", "official") else "any"
        validated["answer_depth"] = result.get("answer_depth", "analytical") if result.get("answer_depth") in ("concise", "analytical", "comprehensive", "empathetic") else "analytical"
        validated["reformulated_query"] = result.get("reformulated_query", "") or query
        validated["confidence"] = min(1.0, max(0.0, float(result.get("confidence", 0.5))))
        validated["open_url"] = result.get("open_url")
        # Post-validation coherence rules
        if validated["tier"] in ("current", "research", "contested") and validated["route"] == "instant":
            validated["route"] = "search"
        if validated["route"] == "transactional" and not validated["open_url"]:
            validated["route"] = "search"
        q_lower = query.lower()
        if validated["route"] == "instant" and validated["domain"] in ("finance", "sports", "news"):
            validated["route"] = "search"
            validated["tier"] = "current"
        if validated["route"] == "instant" and any(w in q_lower for w in ("weather", "forecast", "temperature")):
            validated["route"] = "search"
            validated["tier"] = "current"
        if validated["route"] == "instant" and any(w in q_lower for w in ("stock", "price", "shares", "market cap")):
            validated["route"] = "search"
            validated["tier"] = "current"
        if validated["answer_depth"] == "empathetic" and validated["route"] == "instant":
            validated["route"] = "search"
        if validated["route"] == "instant" and any(w in q_lower for w in ("best ", "top ", "recommend", "comparison", "vs ", "versus ")):
            validated["route"] = "search"
            validated["tier"] = "contested"
        return validated
    except Exception:
        return {**_ASSESS_FALLBACK, "reformulated_query": query}


# Legacy classifier — kept for backward compatibility during transition
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
    """Legacy classifier — kept for backward compatibility."""
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
        return input("› ").strip()
    try:
        completer = _DDGCompleter() if _HAS_PROMPT_TOOLKIT else None
        kwargs: dict = {}
        if placeholder:
            try:
                from prompt_toolkit.formatted_text import HTML as _HTML
                kwargs["placeholder"] = _HTML(
                    f'<ansibrightblack>{placeholder}</ansibrightblack>'
                )
            except Exception:
                pass
        return _ptk_prompt(
            "› ",
            history=FileHistory(HISTORY_FILE),
            auto_suggest=AutoSuggestFromHistory(),
            completer=completer,
            complete_while_typing=False,
            **kwargs,
        ).strip()
    except (KeyboardInterrupt, EOFError):
        raise KeyboardInterrupt

# ─── Input classifier ──────────────────────────────────────────────────────────

_COMMAND_TOKENS = {"q", "n", "?", "prefer:"}
_REDIRECT_PHRASES = {"your job", "try harder", "you missed", "not good enough", "do better", "try again"}
_CORRECTION_STARTERS = ("no,", "no ", "not ", "i meant", "actually", "wait,", "wrong,")
_SCOPE_PHRASES = {"the others", "all of them", "the rest", "show me more", "what about the",
                  "what about all", "what about groups", "and the other", "the remaining",
                  "other groups", "other teams", "other countries"}


def _classify_input(text: str) -> str:
    """
    Classify interactive input into one of six types.
    Pattern-match first (instant). LLM fallback only for genuine ambiguity.
    Returns: 'command' | 'casual' | 'correction' | 'redirect' | 'scope_expansion' | 'followup'
    """
    t = text.strip()
    if not t:
        return "followup"
    tl = t.lower()

    # command — exact tokens or numeric/prefixed patterns
    if tl in _COMMAND_TOKENS:
        return "command"
    if len(tl) <= 3 and (tl.isdigit() or (len(tl) >= 2 and tl[0] in "os" and tl[1:].isdigit())):
        return "command"
    if tl.startswith("prefer:"):
        return "command"

    # redirect — contains redirect phrase (check before casual to avoid false casual match)
    if any(phrase in tl for phrase in _REDIRECT_PHRASES):
        return "redirect"

    # casual — short, no question mark, starts with casual word
    words = tl.split()
    if len(words) <= 4 and "?" not in tl and words[0] in _CASUAL_STARTERS:
        return "casual"
    if tl.rstrip("!").strip() in _CASUAL_STARTERS:
        return "casual"

    # correction — starts with correction phrase
    if any(tl.startswith(s) for s in _CORRECTION_STARTERS):
        return "correction"

    # scope_expansion — contains scope phrase
    if any(phrase in tl for phrase in _SCOPE_PHRASES):
        return "scope_expansion"

    # Default: followup (safe, sends to existing _handle_followup)
    return "followup"


# ─── Conversational response layer ─────────────────────────────────────────────

_CASUAL_REPLIES = [
    "Glad that's useful — anything else you want to dig into?",
    "Happy to keep going — what's next?",
    "Sure — what else can I find for you?",
]

_REDIRECT_REPLIES_NO_NOTE = [
    "Fair enough — let me come at this from a wider angle.",
    "You're right — I'll broaden the search.",
    "Point taken — trying a different approach.",
]


def _conversational_reply(
    reply_type: str,
    meta: "_SearchMeta | None" = None,
    user_text: str = "",
) -> None:
    """
    Print a short conversational response in the professor voice. Two sentences max.
    reply_type: 'redirect' | 'casual' | 'correction' | 'dead_end'
    """
    import random

    if reply_type == "casual":
        print(f"\033[90m{random.choice(_CASUAL_REPLIES)}\033[0m")
        print()

    elif reply_type == "redirect":
        if meta and meta.coverage_note:
            tried = meta.queries_tried[-1] if meta.queries_tried else "that"
            print(f"\033[90mFair point — I'll widen the search beyond \"{tried}\".\033[0m")
        else:
            print(f"\033[90m{random.choice(_REDIRECT_REPLIES_NO_NOTE)}\033[0m")
        print()

    elif reply_type == "correction":
        print(f"\033[90mGot it — starting fresh with that.\033[0m")
        print()

    elif reply_type == "dead_end":
        tried_str = ""
        if meta and meta.queries_tried:
            tried_str = f" (tried: {len(meta.queries_tried)} searches)"
        print(f"\033[90mThree angles, not much to show for it{tried_str}.\033[0m")
        print(f"\033[90m  \033[33mr\033[90m — read the best result I found\033[0m")
        print(f"\033[90m  \033[33mt\033[90m — try a completely different search\033[0m")
        print()


# ─── Scope expansion fanout ────────────────────────────────────────────────────

def _extract_expansion_items(user_text: str, context: str = "") -> list[str]:
    """
    Use LLM to extract the list of items the user wants to expand to.
    e.g. "what about groups A B D E F G" → ["Group A", "Group B", "Group D", ...]
    Returns a list of strings, empty list on failure.
    """
    prompt = (
        f"The user asked: \"{user_text}\"\n"
        f"Context: \"{context[:200]}\"\n\n"
        "List the specific items they want information about. "
        "Return each item on its own line, nothing else. "
        "Maximum 8 items. If you can't identify specific items, return nothing."
    )
    try:
        chunks = list(stream_groq(prompt, "Extract list items. One per line. No numbering, no bullets.", model=CLASSIFIER_MODEL, max_tokens=100))
        raw = "".join(chunks).strip()
        items = [line.strip() for line in raw.splitlines() if line.strip()]
        return items[:8]
    except Exception:
        return []


def _fanout_search_one(item: str, base_query: str) -> tuple[str, list[dict], str]:
    """
    Search for one item in a fanout. Returns (item, results, synthesis).
    Runs in a thread — must not call any terminal output functions directly.
    """
    query = f"{base_query} {item}"
    try:
        results = _filter_results(ddg_search(query, num_results=3))
    except Exception:
        results = []
    if not results:
        return item, [], f"Nothing found for {item}."
    snippets = "\n".join(f"[{i+1}] {r['snippet']}" for i, r in enumerate(results[:3]))
    prompt = (
        f"Question: What is the current status of {item} in this context: {base_query}?\n\n"
        f"Sources:\n{snippets}\n\n"
        "Answer in 1-2 sentences. Be specific. Lead with the most interesting fact. "
        "State your read clearly. If sources are empty or vague, say so in one sentence."
    )
    try:
        chunks = list(stream_groq(prompt, "You are a sharp research assistant. One to two sentences only.", model=CLASSIFIER_MODEL, max_tokens=120))
        synthesis = "".join(chunks).strip()
    except Exception:
        synthesis = results[0]["snippet"][:200] if results else "No data found."
    return item, results, synthesis


def _handle_scope_expansion(
    user_text: str,
    meta: "_SearchMeta | None",
    context: str,
) -> tuple[list[dict], str, "_SearchMeta"]:
    """
    Fan out searches for multiple items. Stream results as they land.
    Returns (combined_results, combined_response, new_meta).
    """
    base_query = meta.original_query if meta else user_text
    items = _extract_expansion_items(user_text, context=base_query)

    if not items:
        # Fallback: treat as a redirect and do a broader search
        _conversational_reply("redirect", meta=meta, user_text=user_text)
        new_results, new_response, new_meta = _handle_followup(user_text, context=context)
        fallback_meta = _SearchMeta(
            original_query=user_text,
            queries_tried=[user_text],
            result_count=len(new_results),
            confidence_tier="current",
            coverage_note=None,
        )
        return new_results, new_response, fallback_meta

    count = len(items)
    print(f"\033[90mOn it — checking {count} {'item' if count == 1 else 'items'} now.\033[0m\n")

    all_results: list[dict] = []
    all_syntheses: list[str] = []
    queries_tried: list[str] = []

    with ThreadPoolExecutor(max_workers=min(6, count)) as executor:
        futures = {executor.submit(_fanout_search_one, item, base_query): item for item in items}
        for future in as_completed(futures):
            item, results, synthesis = future.result()
            queries_tried.append(f"{base_query} {item}")
            print_header(item, "")
            print(synthesis)
            print()
            all_results.extend(results)
            all_syntheses.append(f"**{item}:** {synthesis}")

    combined_response = "\n\n".join(all_syntheses)
    new_meta = _SearchMeta(
        original_query=base_query,
        queries_tried=queries_tried,
        result_count=len(all_results),
        confidence_tier="current",
        coverage_note=None,
    )
    return all_results, combined_response, new_meta


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


_SPAM_DOMAINS = {
    "roblox.com", "y8.com", "grindsuccess.com", "quora.com",
    "pinterest.com", "facebook.com", "instagram.com", "twitter.com",
    "tiktok.com", "reddit.com",
    # Generic "news analysis" spam farms observed in results
    "desirs-volupte.com", "austrianfood.net", "thedailyjagran.com",
    "wanttoknowit.com", "quickapedia.com", "feeddi.com",
    # Sports SEO farms that outrank real journalism
    "athletics-info.com", "blazetrends.com", "newz.com", "pulseheadlines.com",
    "thegoldenkeys.co.uk", "footballbh.net", "newsanyway.com",
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
    # High-confidence multi-word sports signals — one hit is enough
    high_confidence_sports = {"premier league", "champions league", "world cup",
                               "nfl", "nba", "mlb", "nhl"}
    if any(s in text_lower for s in high_confidence_sports):
        return "sports"

    best_match = None
    best_score = 0
    for entity_type, keywords in signals.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_match = entity_type
    return best_match if best_score >= 2 else None

def _filter_results(results: list[dict], evaluative_context: dict | None = None) -> list[dict]:
    """
    Filter and optionally rerank results.
    - Always: remove spam domains
    - Evaluative queries: independence-score and sort; conditionally allow Reddit
    """
    filtered = []
    for r in results:
        domain = r.get("domain", "")
        # Reddit: allow for evaluative queries only
        if "reddit.com" in domain:
            if evaluative_context and evaluative_context.get("is_evaluative"):
                filtered.append(r)  # allow — will compete on independence score
            continue
        if domain not in _SPAM_DOMAINS:
            filtered.append(r)

    if not evaluative_context or not evaluative_context.get("is_evaluative"):
        return filtered

    # For evaluative queries: score and sort by independence
    avoid_signals = evaluative_context.get("avoid_signals", [])
    source_signals = evaluative_context.get("source_signals", [])

    scored = [
        (_score_source_independence(r, avoid_signals, source_signals), r)
        for r in filtered
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]

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




def _display_preferences() -> None:
    """Show current preferences.md contents in the terminal."""
    path = _preferences_path()
    prefs = _read_preferences()
    print()
    print(f"{C_BRAND}━━ Your preferences ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print()
    if prefs:
        print(f"{C_META}  {path}{C_RESET}")
        print()
        for line in prefs.splitlines():
            if line.startswith("#"):
                print(f"  {C_BOLD}{line.lstrip('#').strip()}{C_RESET}")
            elif line.startswith("- ") or line.startswith("• "):
                print(f"  {C_INTERACTIVE}•{C_RESET}  {line[2:]}")
            elif line.strip():
                print(f"  {C_META}{line}{C_RESET}")
        print()
    else:
        print(f"  {C_META}No preferences set yet.{C_RESET}")
        print(f"  {C_META}Run 'surf setup' to create a research profile.{C_RESET}")
        print(f"  {C_META}Or type 'prefer: your preference' after any search.{C_RESET}")
        print()
    print(f"{C_META}  edit: {path or '(run surf setup first)'}{C_RESET}")
    print(f"{C_META}  add:  surf prefer: [anything]{C_RESET}")
    print()


def _handle_inline_preference(text: str) -> None:
    """
    Append a preference fragment to preferences.md. Called when user types
    'prefer: some text' from any search prompt.
    """
    if not text.strip():
        return
    path = _write_preferences(f"- {text.strip()}", append=True)
    if path:
        print(f"{C_SPEED_FAST}{GLYPH_META} saved to preferences.md{C_RESET}  "
              f"{C_META}\"{text.strip()}\" will apply to future searches{C_RESET}")
    else:
        print(f"{C_META}{GLYPH_META} no preferences file configured — run 'surf setup' first{C_RESET}")


# Banner uses bright variants for visibility on dark terminals.
# \033[96m = bright cyan (waves), \033[1;95m = bold bright magenta (SURF), \033[97m = bright white (tagline)
_W = "\033[96m"   # bright cyan waves
_S = "\033[1;95m" # bold bright magenta — SURF letters
_T = "\033[97m"   # bright white — tagline
_R = C_RESET

_SETUP_BANNER = (
    f"\n"
    f"{_W}  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~{_R}\n"
    f"{_W}  ~{_R}                                                          {_W}~{_R}\n"
    f"{_W}  ~{_R}   {_S} ____  _   _ ____  ___  {_R}                         {_W}~{_R}\n"
    f"{_W}  ~{_R}   {_S}/ ___|| | | |  _ \\|  _| {_R}                         {_W}~{_R}\n"
    f"{_W}  ~{_R}   {_S}\\___ \\| | | | |_) | |_  {_R}                         {_W}~{_R}\n"
    f"{_W}  ~{_R}   {_S} ___) | |_| |  _ <|  __|{_R}                         {_W}~{_R}\n"
    f"{_W}  ~{_R}   {_S}|____/ \\___/|_| \\_\\_|   {_R}                         {_W}~{_R}\n"
    f"{_W}  ~{_R}                                                          {_W}~{_R}\n"
    f"{_W}  ~{_R}   {_T}AI-powered search for your terminal{_R}                {_W}~{_R}\n"
    f"{_W}  ~{_R}   {_T}setup wizard  ·  q to exit anytime{_R}                 {_W}~{_R}\n"
    f"{_W}  ~{_R}                                                          {_W}~{_R}\n"
    f"{_W}  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~{_R}\n"
)


def _detect_obsidian_vaults() -> list[str]:
    """Scan common macOS/Linux locations for Obsidian vaults (.obsidian folder)."""
    candidates = [
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~/"),
        os.path.expanduser("~/Library/Mobile Documents/iCloud~md~obsidian/Documents"),
    ]
    vaults = []
    for base in candidates:
        if not os.path.isdir(base):
            continue
        try:
            for item in os.listdir(base):
                full = os.path.join(base, item)
                if os.path.isdir(full) and os.path.isdir(os.path.join(full, ".obsidian")):
                    vaults.append(full)
        except PermissionError:
            continue
    return vaults[:5]


def _has_any_api_key() -> bool:
    """True if any AI provider key is configured."""
    cfg = load_config()
    return any(cfg.get(k) for k in [
        "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY",
        "CEREBRAS_API_KEY", "OLLAMA_BASE",
    ]) or any(os.environ.get(k) for k in [
        "ANTHROPIC_API_KEY", "GROQ_API_KEY",
    ])


def _mark_first_run_complete() -> None:
    """Write a marker so we don't repeat the first-run interstitial."""
    marker = os.path.expanduser("~/.config/surf/.onboarded")
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "w").write("1")
    except Exception:
        pass


def _is_first_run() -> bool:
    """True if this is the first time surf has been used."""
    marker = os.path.expanduser("~/.config/surf/.onboarded")
    config_exists = os.path.exists(os.path.expanduser("~/.config/surf/config"))
    return not os.path.exists(marker) and not config_exists


def _save_config_key(key: str, value: str) -> None:
    """Add or update a single key in ~/.config/surf/config."""
    config_path = os.path.expanduser("~/.config/surf/config")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    lines = []
    try:
        with open(config_path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        pass

    # Update existing line or append
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(config_path, "w") as f:
        f.writelines(new_lines)


def _generate_demo_query(work: str) -> str:
    """Generate a contextual first-search question from the user's work description."""
    if not work:
        return ""
    work_lower = work.lower()
    if any(w in work_lower for w in ["software", "developer", "engineer", "coding", "programmer", "ai", "ml"]):
        return "what are the most important AI coding tools in 2026"
    if any(w in work_lower for w in ["health", "doctor", "physician", "medical", "clinical", "nurse"]):
        return "what are the most effective treatments for burnout in healthcare workers"
    if any(w in work_lower for w in ["investor", "finance", "trading", "fund", "capital"]):
        return "what sectors are outperforming in 2026 and why"
    if any(w in work_lower for w in ["journalist", "writer", "reporter", "media", "editor"]):
        return "what are the biggest underreported stories right now"
    if any(w in work_lower for w in ["research", "academic", "professor", "phd", "scientist"]):
        return "what research methodologies are being disrupted by AI"
    if any(w in work_lower for w in ["lawyer", "legal", "attorney", "law"]):
        return "how is AI changing legal research and contract review"
    return "what are the most important developments in AI this week"


def _generate_preferences_from_answers(answers: dict) -> None:
    """Use Claude to turn three free-text answers into preferences.md. Streams output."""
    prompt = (
        f"The user is setting up surf, a terminal AI search tool.\n"
        f"Convert their answers into a preferences.md file.\n\n"
        f"Work: {answers.get('work', 'not specified')}\n"
        f"Answer style: {answers.get('style', 'not specified')}\n"
        f"Source preferences: {answers.get('sources', 'not specified')}\n\n"
        f"Write a preferences.md with these sections (only include sections with real content):\n"
        f"# surf preferences\n"
        f"## About me\n"
        f"## Answer style\n"
        f"## Preferred sources\n"
        f"## Excluded sources\n"
        f"## Notes\n\n"
        f"Be concise. Under 200 words. First person. Only include what the user actually said."
    )
    prefs_system = "You write clean, concise preferences files. Use markdown. Be direct. No filler."
    try:
        chunks = list(stream_ai(prompt, prefs_system, max_tokens=400))
        prefs_text = "".join(chunks)
        # Print it so user can see what was written
        for line in prefs_text.splitlines():
            if line.startswith("#"):
                print(f"  {C_BOLD}{line}{C_RESET}")
            else:
                print(f"  {C_META}{line}{C_RESET}")
        print()
        path = _write_preferences(prefs_text)
        if path:
            print(f"{C_SPEED_FAST}{GLYPH_META} saved to {path}{C_RESET}")
            print(f"{C_META}  edit anytime in Obsidian or with your text editor{C_RESET}\n")
    except Exception:
        print(f"{C_META}{GLYPH_META} could not generate preferences — skipping.{C_RESET}\n")


def _run_preferences_conversation() -> None:
    """
    Three-question preferences setup. Claude writes preferences.md from the answers.
    Ends with a demo search so setup closes with surf working, not just configured.
    Type 'q' or press Ctrl+C at any point to exit gracefully.
    """
    print(_SETUP_BANNER)

    existing_key = load_config().get("ANTHROPIC_API_KEY", "")

    if existing_key:
        masked = "*" * 12 + existing_key[-4:]
        print(f"{C_BRAND}━━ Claude API key ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
        print()
        print(f"  {C_SPEED_FAST}✓{C_RESET}  {C_META}Already configured: {masked}{C_RESET}")
        print(f"  {C_META}Press Enter to keep it, or paste a new key to replace it.{C_RESET}")
        print()
    else:
        print(f"{C_BRAND}━━ Claude API key ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
        print()
        print(f"  {C_META}1. open → {_link('https://console.anthropic.com/settings/keys', 'console.anthropic.com/settings/keys')} (cmd+click){C_RESET}")
        print(f"  {C_META}2. click \"Create Key\", name it \"surf\", copy it{C_RESET}")
        print(f"  {C_META}3. paste it here{C_RESET}")
        print()
        print(f"  {C_META}cost: ~$0.0004/search · free $5 credit on signup · hard cap: $1/month{C_RESET}")
        print()

    print(f"  {C_META}(press Enter to skip, type q to exit setup){C_RESET}")
    print()

    try:
        key = surf_input("Enter to keep · q to exit").strip()
    except (KeyboardInterrupt, EOFError):
        print(f"\n{C_META}{GLYPH_META} setup cancelled.{C_RESET}\n")
        return

    if key.lower() == "q":
        print(f"\n{C_META}{GLYPH_META} setup exited. run 'surf setup' anytime to continue.{C_RESET}\n")
        return

    if key and key.startswith("sk-ant"):
        print(f"\n{C_SPEED_FAST}{GLYPH_META} Claude connected ✓{C_RESET}  {C_META}haiku-4.5 · $0.0004/query{C_RESET}\n")
        _save_config_key("ANTHROPIC_API_KEY", key)
    elif key and not existing_key:
        print(f"\n{C_META}{GLYPH_META} key saved — surf will let you know if it doesn't authenticate.{C_RESET}\n")
        _save_config_key("ANTHROPIC_API_KEY", key)
    elif not key and existing_key:
        print(f"\n{C_META}{GLYPH_META} keeping existing key.{C_RESET}\n")
    else:
        print(f"\n{C_META}{GLYPH_META} skipping Claude key. you can add it later via 'surf setup'.{C_RESET}\n")

    # Preferences conversation
    vspace(SPACE_SM)
    print(f"{C_BRAND}━━ Let's tune surf to how you think ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print()
    print(f"  {C_META}Three questions. Press Enter to skip any, or type q to exit.{C_RESET}")
    print()

    answers = {}

    for question, key_name, example in [
        ("What kind of work do you do?", "work",
         "e.g. \"software engineer\", \"healthcare researcher\", \"investor\""),
        ("What do you want from surf's answers?", "style",
         "e.g. \"concise with data\", \"deep explanations\", \"code examples\""),
        ("Any sources you love or avoid?", "sources",
         "e.g. \"prefer arxiv and HN, avoid Medium\""),
    ]:
        print(f"{C_META}{GLYPH_META}{C_RESET} {question}")
        print(f"  {C_META}({example}){C_RESET}")
        try:
            val = surf_input("type or Enter to skip").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{C_META}{GLYPH_META} setup cancelled.{C_RESET}\n")
            return
        if val.lower() == "q":
            print(f"\n{C_META}{GLYPH_META} setup exited. run 'surf setup' anytime to continue.{C_RESET}\n")
            return
        answers[key_name] = val
        print()

    # Generate preferences.md from answers
    if any(answers.values()):
        print(f"{C_META}{GLYPH_META} writing preferences.md...{C_RESET}\n")
        _generate_preferences_from_answers(answers)
    else:
        print(f"{C_META}{GLYPH_META} skipping preferences — add them anytime with 'prefer: ...' after any search.{C_RESET}\n")

    # Vault setup (brief)
    if not _obsidian_vault_path():
        detected = _detect_obsidian_vaults()
        if detected:
            vspace(SPACE_SM)
            print(f"{C_META}{GLYPH_META}{C_RESET} Save research to Obsidian?")
            print(f"  {C_SPEED_FAST}✓{C_RESET}  Detected: {C_INTERACTIVE}{detected[0]}{C_RESET}")
            print(f"\n  {C_INTERACTIVE}y{C_RESET}  yes — save every search as a linked note")
            print(f"  {C_INTERACTIVE}s{C_RESET}  skip")
            print()
            try:
                vc = surf_input("y or s").strip().lower()
            except (KeyboardInterrupt, EOFError):
                vc = "s"
            if vc == "y":
                _save_config_key("OBSIDIAN_VAULT", detected[0])
                print(f"\n{C_SPEED_FAST}{GLYPH_META} vault configured: {detected[0]}{C_RESET}\n")

    _mark_first_run_complete()

    # First-win: demo search based on what they told us
    vspace(SPACE_SM)
    demo_query = _generate_demo_query(answers.get("work", ""))
    print(f"{C_BRAND}━━ Let's try it ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print()
    if demo_query:
        print(f"  {C_META}Based on your profile, a question surf thinks you'd care about:{C_RESET}")
        print(f"\n  {C_INTERACTIVE}→{C_RESET}  {demo_query}\n")
        print(f"  {C_INTERACTIVE}y{C_RESET}  search this")
        print(f"  {C_INTERACTIVE}q{C_RESET}  quit setup")
        print(f"  {C_INTERACTIVE}↵{C_RESET}  or type your own first search")
        print()
        try:
            first = surf_input("y, q, or type a question").strip()
        except (KeyboardInterrupt, EOFError):
            first = "q"
        if first.lower() == "q":
            print(f"\n{C_META}{GLYPH_META} all set. run 'surf [anything]' to start searching.{C_RESET}\n")
            return
        query_to_run = demo_query if first.lower() == "y" else (first or demo_query)
    else:
        print(f"  {C_META}What do you want to search first? (q to exit){C_RESET}\n")
        try:
            query_to_run = surf_input("type a question or q to exit").strip()
        except (KeyboardInterrupt, EOFError):
            query_to_run = ""
        if query_to_run.lower() == "q":
            print(f"\n{C_META}{GLYPH_META} all set. run 'surf [anything]' to start searching.{C_RESET}\n")
            return

    if query_to_run:
        print()
        search_flow(query_to_run, interactive=True)


def _first_run_interstitial(query: str) -> None:
    """
    First-ever surf run: search first (on Groq), then offer Claude setup.
    The user sees value before being asked for anything.
    """
    print(f"\n{C_META}{GLYPH_META} first time? let's do this search, then take 90 seconds to set up.{C_RESET}\n")

    # Run the actual search — Groq fallback works without config
    results, response = search_flow(query, interactive=False)

    if not results:
        # Search failed — go straight to setup
        _run_preferences_conversation()
        return

    # Offer the Claude upgrade
    vspace(SPACE_SM)
    print(f"{C_META}{GLYPH_META} that was surf on Groq (free, public). add a Claude key for:{C_RESET}\n")
    print(f"  {C_INTERACTIVE}•{C_RESET}  {C_META}private synthesis  (your queries don't train any model){C_RESET}")
    print(f"  {C_INTERACTIVE}•{C_RESET}  {C_META}deeper research    (reads full articles, not just snippets){C_RESET}")
    print(f"  {C_INTERACTIVE}•{C_RESET}  {C_META}Obsidian vault     (every search becomes a linked note){C_RESET}")
    print()
    print(f"  {C_INTERACTIVE}a{C_RESET}  add Claude key now — 60 seconds")
    print(f"  {C_INTERACTIVE}s{C_RESET}  skip — keep using Groq")
    print(f"  {C_INTERACTIVE}q{C_RESET}  quit")
    print()

    try:
        choice = surf_input("a to set up Claude, s to skip").lower().strip()
    except (KeyboardInterrupt, EOFError):
        choice = "q"

    if choice == "q":
        return
    if choice == "a":
        _run_preferences_conversation()
    else:
        # Mark as seen so we don't show again
        _mark_first_run_complete()
        print(f"\n{C_META}{GLYPH_META} using Groq. run 'surf setup' anytime to add Claude.{C_RESET}\n")


def _setup_prompt(label: str, current: str, secret: bool = False) -> str:
    """Print a labeled prompt with current value shown. Returns new value or current."""
    display = ("*" * 12 + current[-4:]) if (secret and current) else (current or "not set")
    color = C_SPEED_FAST if current else C_META
    print(f"  {C_BOLD}{label}{C_RESET}")
    print(f"  Current: {color}{display}{C_RESET}")
    try:
        new = input(f"  New value (Enter to keep): ").strip()
    except (KeyboardInterrupt, EOFError):
        new = ""
    return new if new else current


def _run_setup() -> None:
    """Interactive configuration wizard. Run with: surf setup"""
    # If called via 'surf setup' without --full, run the conversation flow
    # The full form wizard is still available via 'surf setup --full'
    if "--full" not in sys.argv:
        _run_preferences_conversation()
        return

    config_path = os.path.expanduser("~/.config/surf/config")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    # Load current config
    cfg: dict[str, str] = {}
    try:
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    cfg[k.strip()] = v.strip()
    except FileNotFoundError:
        pass

    print(_SETUP_BANNER)

    # ── Section 1: API Keys ───────────────────────────────────────────────────
    print(f"{C_BOLD}1. API Keys{C_RESET}")
    print(f"{C_META}  Claude is the primary provider. Others are free fallbacks.{C_RESET}")
    print(f"{C_META}  Get Claude key: claude.ai/settings → API Keys{C_RESET}")
    print()

    cfg["ANTHROPIC_API_KEY"] = _setup_prompt(
        "Claude API key (primary — $1/month for ~2500 searches)",
        cfg.get("ANTHROPIC_API_KEY", ""), secret=True
    )
    print()
    cfg["GROQ_API_KEY"] = _setup_prompt(
        "Groq API key (free fallback — console.groq.com)",
        cfg.get("GROQ_API_KEY", ""), secret=True
    )
    print()
    cfg["GEMINI_API_KEY"] = _setup_prompt(
        "Gemini API key (free fallback — aistudio.google.com)",
        cfg.get("GEMINI_API_KEY", ""), secret=True
    )
    print()
    cfg["CEREBRAS_API_KEY"] = _setup_prompt(
        "Cerebras API key (free fallback — inference.cerebras.ai)",
        cfg.get("CEREBRAS_API_KEY", ""), secret=True
    )
    print()
    cfg["BRAVE_API_KEY"] = _setup_prompt(
        "Brave Search API key (better results — brave.com/search/api, free 2k/mo)",
        cfg.get("BRAVE_API_KEY", ""), secret=True
    )
    print()
    cfg["TAVILY_API_KEY"] = _setup_prompt(
        "Tavily API key (best results — tavily.com, free 1k/mo)",
        cfg.get("TAVILY_API_KEY", ""), secret=True
    )
    print()

    # ── Section 2: Research preferences ───────────────────────────────────────
    print(f"{C_BOLD}2. Research preferences{C_RESET}")
    print()

    current_model = cfg.get("SYNTHESIS_MODEL", "haiku")
    print(f"  {C_BOLD}Synthesis model{C_RESET}")
    print(f"  Current: {C_INTERACTIVE}{current_model}{C_RESET}")
    print(f"  {C_META}haiku  = fast, cheap ($0.0004/query) — default{C_RESET}")
    print(f"  {C_META}sonnet = deeper analysis, 4x cost — recommended for research{C_RESET}")
    try:
        choice = input("  Choose haiku or sonnet (Enter to keep): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        choice = ""
    if choice in ("haiku", "sonnet"):
        cfg["SYNTHESIS_MODEL"] = choice
    print()

    # ── Section 3: Obsidian vault ─────────────────────────────────────────────
    print(f"{C_BOLD}3. Obsidian vault{C_RESET}")
    print(f"{C_META}  Save every search as a linked markdown note in your Obsidian vault.{C_RESET}")
    print(f"{C_META}  Your research stays local — private, searchable, wiki-linked.{C_RESET}")
    print()

    detected = _detect_obsidian_vaults()
    current_vault = cfg.get("OBSIDIAN_VAULT", "")

    if detected:
        print(f"  {C_SPEED_FAST}Detected Obsidian vaults:{C_RESET}")
        for i, v in enumerate(detected, 1):
            print(f"    {C_INTERACTIVE}{i}{C_RESET}  {v}")
        print(f"    {C_INTERACTIVE}n{C_RESET}  Enter path manually")
        print(f"    {C_INTERACTIVE}0{C_RESET}  Skip / disable")
        print()
        try:
            vchoice = input("  Choose vault (Enter to keep current): ").strip()
        except (KeyboardInterrupt, EOFError):
            vchoice = ""
        if vchoice.isdigit() and 1 <= int(vchoice) <= len(detected):
            cfg["OBSIDIAN_VAULT"] = detected[int(vchoice) - 1]
        elif vchoice == "0":
            cfg.pop("OBSIDIAN_VAULT", None)
        elif vchoice.lower() == "n":
            try:
                manual = input("  Vault path: ").strip()
            except (KeyboardInterrupt, EOFError):
                manual = ""
            if manual and os.path.isdir(manual):
                cfg["OBSIDIAN_VAULT"] = manual
            elif manual:
                print(f"  {C_META}Path not found — skipping{C_RESET}")
        elif not vchoice and current_vault:
            pass  # keep current
    else:
        print(f"  {C_META}No Obsidian vaults detected on this machine.{C_RESET}")
        print(f"  {C_META}Install Obsidian (obsidian.md) and create a vault first.{C_RESET}")
        print(f"  {C_META}Or enter a path to any folder to save markdown files there.{C_RESET}")
        print()
        if current_vault:
            print(f"  Current: {C_INTERACTIVE}{current_vault}{C_RESET}")
        try:
            manual = input("  Vault path (Enter to skip): ").strip()
        except (KeyboardInterrupt, EOFError):
            manual = ""
        if manual:
            if os.path.isdir(manual):
                cfg["OBSIDIAN_VAULT"] = manual
            else:
                try:
                    os.makedirs(manual, exist_ok=True)
                    cfg["OBSIDIAN_VAULT"] = manual
                    print(f"  {C_SPEED_FAST}Created vault folder at {manual}{C_RESET}")
                except Exception:
                    print(f"  {C_META}Could not create folder — skipping{C_RESET}")
    print()

    # ── Section 4: Claude budget ───────────────────────────────────────────────
    print(f"{C_BOLD}4. Claude monthly budget{C_RESET}")
    print(f"{C_META}  Default is $1.00/month. Increase for heavier use.{C_RESET}")
    print()
    current_budget = str(CLAUDE_MONTHLY_BUDGET)
    print(f"  {C_BOLD}Monthly budget (USD){C_RESET}")
    print(f"  Current: {C_INTERACTIVE}${current_budget}{C_RESET}")
    try:
        new_budget = input("  New budget (Enter to keep): ").strip().lstrip("$")
    except (KeyboardInterrupt, EOFError):
        new_budget = ""
    if new_budget:
        try:
            float(new_budget)  # validate
            cfg["CLAUDE_MONTHLY_BUDGET"] = new_budget
        except ValueError:
            print(f"  {C_META}Invalid amount — keeping ${current_budget}{C_RESET}")
    print()

    # ── Write config ──────────────────────────────────────────────────────────
    lines = ["# surf configuration — generated by surf setup\n"]
    key_order = [
        "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY",
        "SYNTHESIS_MODEL", "OBSIDIAN_VAULT", "CLAUDE_MONTHLY_BUDGET",
    ]
    written = set()
    for key in key_order:
        if key in cfg and cfg[key]:
            lines.append(f"{key}={cfg[key]}\n")
            written.add(key)
    # Write any keys not in the standard order
    for key, val in cfg.items():
        if key not in written and val:
            lines.append(f"{key}={val}\n")

    with open(config_path, "w") as f:
        f.writelines(lines)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"{C_BRAND}━━ Configuration saved to {config_path} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print()

    def _status(key: str, label: str) -> None:
        val = cfg.get(key, "")
        if val:
            display = ("*" * 8 + val[-4:]) if "KEY" in key or "API" in key.upper() else val
            print(f"  {C_SPEED_FAST}✓{C_RESET}  {label}: {C_META}{display}{C_RESET}")
        else:
            print(f"  {C_META}–{C_RESET}  {label}: {C_META}not set{C_RESET}")

    _status("ANTHROPIC_API_KEY", "Claude API key")
    _status("GROQ_API_KEY", "Groq API key")
    _status("GEMINI_API_KEY", "Gemini API key")
    _status("CEREBRAS_API_KEY", "Cerebras API key")
    print()
    _status("SYNTHESIS_MODEL", "Synthesis model")
    _status("OBSIDIAN_VAULT", "Obsidian vault")
    print()
    print(f"  Run {C_INTERACTIVE}surf what is a black hole{C_RESET} to try it out.")
    print()


# ─── Shell integration ───────────────────────────────────────────────────────

def _get_shell_context(n: int = 10) -> str:
    """Return last N commands from shell history."""
    shell = os.environ.get("SHELL", "/bin/zsh")
    if "zsh" in shell:
        hist_path = os.path.expanduser("~/.zsh_history")
    elif "bash" in shell:
        hist_path = os.path.expanduser("~/.bash_history")
    else:
        return ""

    try:
        with open(hist_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return ""

    commands = []
    for line in lines[-n * 2:]:
        line = line.strip()
        if not line:
            continue
        if line.startswith(": ") and ";" in line:
            cmd = line.split(";", 1)[1]
        else:
            cmd = line
        if cmd and not cmd.startswith("#"):
            commands.append(cmd)

    return "\n".join(commands[-n:])


def _get_last_command_error() -> str | None:
    """Get the last command from shell history. Returns command string or None."""
    context = _get_shell_context(n=1)
    return context.strip() if context.strip() else None


# ─── Watch mode ──────────────────────────────────────────────────────────────

def _parse_watch_interval(spec: str) -> int:
    """Parse interval like '5m', '1h', '30s', or bare '5' (minutes). Returns seconds, minimum 30."""
    spec = spec.strip().lower()
    try:
        if spec.endswith("h"):
            seconds = int(spec[:-1]) * 3600
        elif spec.endswith("m"):
            seconds = int(spec[:-1]) * 60
        elif spec.endswith("s"):
            seconds = int(spec[:-1])
        elif spec.isdigit():
            seconds = int(spec) * 60
        else:
            seconds = 300
    except (ValueError, IndexError):
        seconds = 300
    return max(30, seconds)


def _watch_loop(query: str, interval_seconds: int, json_output: bool = False, deep: bool = False, source_filter: list[str] | None = None) -> None:
    """Run search_flow on a loop. Ctrl+C to exit."""
    iteration = 0
    while True:
        iteration += 1
        now = time.strftime("%H:%M")
        width = _term_width()
        if iteration > 1:
            vspace(SPACE_SM)
            print(f"{C_META}{GLYPH_DIVIDER * width}{C_RESET}")
            print(f"{C_META}{GLYPH_META} refreshed {GLYPH_SEPARATOR} {now}{C_RESET}")
            vspace(SPACE_XS)
        try:
            search_flow(query, interactive=False, json_output=json_output, deep=deep, source_filter=source_filter)
        except Exception as e:
            print(f"{C_ERROR}Watch error: {e}{C_RESET}")
        time.sleep(interval_seconds)


# ─── Diff mode ───────────────────────────────────────────────────────────────

def _diff_search(query: str, json_output: bool = False, deep: bool = False, source_filter: list[str] | None = None) -> None:
    """Run a new search and compare against the last snapshot."""
    old = _load_search_snapshot(query)
    results, response = search_flow(query, interactive=False, json_output=False, deep=deep, source_filter=source_filter)

    _save_search_snapshot(query, response, results)

    if not old:
        print(f"\n{C_META}{GLYPH_META} first search for this query — snapshot saved for next diff{C_RESET}")
        return

    from datetime import datetime
    old_time = datetime.fromtimestamp(old["timestamp"]).strftime("%Y-%m-%d %H:%M")

    old_domains = {s.get("domain", "") for s in old.get("sources", [])}
    new_domains = {r.get("domain", "") for r in results}
    added = new_domains - old_domains
    removed = old_domains - new_domains

    print_header(f"Changes since {old_time}", f"{query[:40]}")
    source_delta = []
    if added:
        source_delta.append(f"+{len(added)} new")
    if removed:
        source_delta.append(f"-{len(removed)} removed")
    if source_delta:
        print(f"{C_META}{GLYPH_META} sources: {', '.join(source_delta)}{C_RESET}")
    print()

    diff_prompt = (
        f"Compare these two search results for \"{query}\" and describe ONLY what changed.\n\n"
        f"PREVIOUS ({old_time}):\n{old['response'][:3000]}\n\n"
        f"CURRENT (now):\n{response[:3000]}\n\n"
        "List specific changes: new facts, updated numbers, removed information. "
        "If nothing meaningful changed, say so. Be concise."
    )
    stream = stream_ai(diff_prompt, "You compare search results and highlight changes. Be specific and concise.", max_tokens=1000)
    stream_to_terminal(stream)


# ─── Stdin ───────────────────────────────────────────────────────────────────

_STDIN_MAX_CHARS = 20000


def _read_stdin() -> str | None:
    """Read piped stdin content. Returns None if stdin is a terminal or empty."""
    if sys.stdin.isatty():
        return None
    try:
        content = sys.stdin.read()
        if not content or not content.strip():
            return None
        if len(content) > _STDIN_MAX_CHARS:
            content = content[:_STDIN_MAX_CHARS] + "\n[truncated]"
        return content
    except Exception:
        return None


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
    parser.add_argument("--full", action="store_true",
                        help="Full configuration wizard (advanced)")
    parser.add_argument("--deep", action="store_true",
                        help="Multi-step deep search — searches, reads, identifies gaps, repeats")
    parser.add_argument("--watch", type=str, default=None, metavar="INTERVAL",
                        help="Repeat search on interval (e.g. 5m, 1h, 30s)")
    parser.add_argument("--diff", action="store_true",
                        help="Compare results against last search for this query")
    parser.add_argument("-t", "--thread", type=str, default=None, metavar="NAME",
                        help="Save search to a named research thread")
    parser.add_argument("--sources", type=str, default=None, metavar="LIST",
                        help="Restrict to sources (e.g. 'arxiv,nature,bbc')")
    parser.add_argument("-f", "--fresh", action="store_true",
                        help="Skip vault context — search the web with fresh eyes")
    parser.add_argument("setup", nargs="?", const="setup",
                        help="Interactive configuration wizard")
    args = parser.parse_args()
    json_output = args.json_output

    # surf setup — interactive configuration wizard
    if (args.input and args.input[0] == "setup") or getattr(args, "setup", None) == "setup":
        _run_setup()
        return

    # surf prefer — show or add preferences inline
    if args.input and args.input[0].lower() == "prefer":
        remainder = " ".join(args.input[1:]).strip()
        if remainder:
            # surf prefer some text — treat as inline preference
            _handle_inline_preference(remainder)
        else:
            # surf prefer — display current preferences
            _display_preferences()
        return

    # surf threads — list all named threads
    if args.input and args.input[0] == "threads":
        threads = _list_threads()
        if not threads:
            print(f"{C_META}No threads yet. Start one: surf -t 'gpu-research' your query{C_RESET}")
            return
        print(f"\n{C_BRAND}{GLYPH_HEADER_FILL * 2} Research Threads {GLYPH_HEADER_FILL * 50}{C_RESET}\n")
        from datetime import datetime
        for t in threads:
            updated = datetime.fromtimestamp(t["updated_at"]).strftime("%Y-%m-%d %H:%M") if t["updated_at"] else "never"
            print(f"  {C_INTERACTIVE}{t['name']}{C_RESET}  {C_META}{t['entries']} entries {GLYPH_SEPARATOR} updated {updated}{C_RESET}")
        print(f"\n{C_META}Resume: surf -t '{threads[0]['name']}' your follow-up query{C_RESET}\n")
        return

    # surf export [--thread name] [--file path]
    if args.input and args.input[0] == "export":
        export_thread = args.thread  # argparse may have consumed -t/--thread
        export_file = None
        i = 1
        while i < len(args.input):
            if args.input[i] == "--file" and i + 1 < len(args.input):
                export_file = args.input[i + 1]
                i += 2
            else:
                i += 1

        if export_thread:
            content = _export_thread(export_thread)
        else:
            content = _export_session()

        if not content:
            print(f"{C_META}Nothing to export. Start a search or use --thread name.{C_RESET}")
            return

        if export_file:
            with open(export_file, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"{C_META}{GLYPH_META} exported to {export_file}{C_RESET}")
        else:
            print(content)
        return

    # First-run: detect new users and route through the interstitial
    if _is_first_run() and args.input and not args.usage:
        query = " ".join(args.input)
        _first_run_interstitial(query)
        return

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

    # Vault-only search: vault: prefix
    if query.lower().startswith("vault:"):
        _vault_query = query[6:].strip()
        if _vault_query:
            _vault_only_search(_vault_query)
        else:
            print(f"{C_META}Usage: surf vault: <query>{C_RESET}")
        return

    # Stdin pipe: cat file | surf "explain this"
    piped_content = _read_stdin()
    if piped_content:
        content_type = "text"
        if any(sig in piped_content for sig in ["Traceback", "Error:", "Exception:", "at line", "FAILED"]):
            content_type = "error"
        elif any(sig in piped_content[:200] for sig in ["def ", "function ", "class ", "import ", "const ", "var ", "#include"]):
            content_type = "code"

        _system_by_type = {
            "error": "You diagnose errors and stack traces from the terminal. Start with ▸ TL;DR naming the root cause, then explain the fix. Be specific.",
            "code": "You analyze source code. Start with ▸ TL;DR describing what the code does, then provide detailed analysis.",
            "text": "You analyze content piped from the terminal. Start with ▸ TL;DR followed by your analysis. Be direct and specific.",
        }

        label = query.capitalize() if query else f"Analyzing piped {content_type}"
        print_header(label)
        prefs = _read_preferences()
        preamble = f"[User preferences]\n{prefs}\n[End preferences]\n\n" if prefs else ""
        prompt = f"{preamble}User piped the following {content_type} and asks: {query or 'explain this'}\n\nContent:\n{piped_content}"
        stream = stream_ai(prompt, _system_by_type[content_type], max_tokens=2048)
        response = stream_to_terminal(stream)
        save_session_entry(query or "piped input", "pipe", _truncate_at_sentence(response, 300))
        _obsidian_save(query or "piped input", response, [], session_id=_obsidian_session_id())
        record_feature_use("pipe")
        if json_output:
            _output_json(query or "piped input", response, [], intent="pipe")
        return

    # Shell integration: surf !! → search for last command's error
    if query.strip() == "!!":
        last_cmd = _get_last_command_error()
        if last_cmd:
            query = f"explain this shell error: {last_cmd}"
            print(f"{C_META}{GLYPH_META} expanding !! {GLYPH_SEPARATOR} \"{last_cmd}\"{C_RESET}")
        else:
            print(f"{C_ERROR}Could not read shell history{C_RESET}")
            return

    # --watch: periodic refresh
    if args.watch:
        interval = _parse_watch_interval(args.watch)
        interval_label = args.watch if any(c.isalpha() for c in args.watch) else f"{args.watch}m"
        print(f"{C_META}{GLYPH_META} watching \"{query[:50]}\" every {interval_label} {GLYPH_SEPARATOR} Ctrl+C to stop{C_RESET}")
        vspace(SPACE_XS)
        try:
            _source_filter = _parse_source_list(args.sources) if args.sources else None
            _watch_loop(query, interval, json_output=json_output, deep=args.deep, source_filter=_source_filter)
        except KeyboardInterrupt:
            print(f"\n{C_META}watch stopped{C_RESET}")
        return

    # --diff: compare against last search
    if args.diff:
        _source_filter = _parse_source_list(args.sources) if args.sources else None
        _diff_search(query, json_output=json_output, deep=args.deep, source_filter=_source_filter)
        return

    # Local file analysis: surf ./file.py "what does this do"
    # Guard: only treat input as a file path if it contains / or . (not bare words)
    potential_path = args.input[0] if args.input else ""
    _looks_like_path = "/" in potential_path or "." in potential_path
    resolved_path = os.path.expanduser(potential_path) if _looks_like_path else ""
    if _looks_like_path and not os.path.isabs(resolved_path):
        resolved_path = os.path.abspath(resolved_path)
    if _looks_like_path and os.path.isfile(resolved_path):
        content, ftype = _extract_file_content(resolved_path)
        if content:
            file_query = " ".join(args.input[1:]) if len(args.input) > 1 else f"explain this {ftype} file"
            basename = os.path.basename(resolved_path)
            print_header(f"{basename}", f"{ftype} {GLYPH_SEPARATOR} {len(content.split())} words")
            prefs = _read_preferences()
            preamble = f"[User preferences]\n{prefs}\n[End preferences]\n\n" if prefs else ""
            system = (
                f"You are analyzing a local {ftype} file named '{basename}'. "
                "Start with ▸ TL;DR then provide your analysis. Be specific about what the file contains and does."
            )
            prompt = f"{preamble}User asks: {file_query}\n\nFile content ({basename}):\n{content[:10000]}"
            stream = stream_ai(prompt, system, max_tokens=2048)
            response = stream_to_terminal(stream)
            save_session_entry(basename, "file", _truncate_at_sentence(response, 300))
            _obsidian_save(file_query, response, [], session_id=_obsidian_session_id())
            record_feature_use("file")
            if json_output:
                _output_json(file_query, response, [basename], intent="file")
            return

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

        _session_ctx = format_session_context()
        _vault_depth = len(_vault_retrieve(query, max_notes=10, max_chars=0)[0]) if not args.fresh else 0
        with Spinner("understanding your intent..."):
            intent = assess_intent(query, vault_depth=_vault_depth, session_context=_session_ctx)

        if intent["route"] == "instant":
            print_header(query.capitalize())
            instant_system = (
                "Answer in one sentence maximum — often just a word or number is right. "
                "Calculations: output the number only, e.g. '51'. "
                "Translations: output the word only, e.g. 'Hola'. "
                "Conversions: number + unit, e.g. '5,280 feet'. "
                "Definitions: one sentence. No explanation, no context, no filler."
            )
            stream = stream_ai(f"{query}", instant_system)
            stream_to_terminal(stream)

        elif intent["route"] == "transactional" and intent.get("open_url"):
            # Search DDG for context
            try:
                with Spinner("searching for options..."):
                    results = ddg_search(query)
            except Exception:
                results = []

            domains = " · ".join(_shorten_domain(r["domain"]) for r in results[:3])
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

        elif intent["route"] == "navigation" and intent.get("open_url"):
            open_in_browser(intent["open_url"])

        else:
            # --thread: inject thread context before search
            if args.thread:
                thread = _load_thread(args.thread)
                if thread["entries"]:
                    thread_ctx = f"Continuing research thread '{args.thread}':\n"
                    for e in thread["entries"][-5:]:
                        thread_ctx += f"  [{e.get('query', '')}]: {e['response'][:200]}\n"
                    save_session_entry(f"[thread:{args.thread}]", "thread_context", thread_ctx)
                    print(f"{C_META}{GLYPH_META} thread \"{args.thread}\" {GLYPH_SEPARATOR} {len(thread['entries'])} prior entries{C_RESET}")

            source_filter = _parse_source_list(args.sources) if args.sources else None
            results, response = search_flow(query, interactive=not json_output, json_output=json_output, deep=args.deep, source_filter=source_filter, fresh=args.fresh, intent=intent)

            # --thread: save results to thread
            if args.thread and response:
                _save_thread_entry(args.thread, query, response, results)
                print(f"{C_META}{GLYPH_META} saved to thread \"{args.thread}\"{C_RESET}")

    except KeyboardInterrupt:
        print("\n\033[90mbye\033[0m")

if __name__ == "__main__":
    main()
