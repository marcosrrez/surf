"""Search backends and web fetching for surf."""
import os
import sys
import requests
from bs4 import BeautifulSoup
import surf_config

try:
    from ddgs import DDGS
    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False

SSL_CERT = "/etc/ssl/cert.pem"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}

DDG_URL = "https://lite.duckduckgo.com/lite/"
JINA_BASE = "https://r.jina.ai/"

# ─── Source lists ────────────────────────────────────────────────────────────

_SOURCE_SHORTNAMES = {
    "arxiv": "arxiv.org", "pubmed": "pubmed.ncbi.nlm.nih.gov",
    "nature": "nature.com", "science": "science.org",
    "reuters": "reuters.com", "bbc": "bbc.com",
    "nyt": "nytimes.com", "nytimes": "nytimes.com",
    "wapo": "washingtonpost.com", "wsj": "wsj.com",
    "bloomberg": "bloomberg.com", "techcrunch": "techcrunch.com",
    "ars": "arstechnica.com", "verge": "theverge.com",
    "wired": "wired.com", "wikipedia": "en.wikipedia.org",
    "wiki": "en.wikipedia.org", "github": "github.com",
    "stackoverflow": "stackoverflow.com", "so": "stackoverflow.com",
    "hn": "news.ycombinator.com", "guardian": "theguardian.com",
    "apnews": "apnews.com", "ap": "apnews.com", "cnn": "cnn.com",
}


def _parse_source_list(spec: str) -> list[str]:
    """Parse a comma-separated source list into domain suffixes."""
    if not spec or not spec.strip():
        return []
    domains = []
    for part in spec.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part in _SOURCE_SHORTNAMES:
            domains.append(_SOURCE_SHORTNAMES[part])
        elif "." in part:
            domains.append(part)
        else:
            domains.append(f"{part}.com")
    return domains


def _filter_by_sources(results: list[dict], allowed_domains: list[str]) -> list[dict]:
    """Filter search results to only include results from allowed domains."""
    if not allowed_domains:
        return results
    return [
        r for r in results
        if any(allowed in r.get("domain", "") for allowed in allowed_domains)
    ]


# ─── Fetching ────────────────────────────────────────────────────────────────

def fetch_page(url: str) -> str:
    """Fetch a URL and return raw HTML. Raises requests.HTTPError on bad status."""
    if not url.startswith("http"):
        url = "https://" + url
    r = requests.get(url, headers=HEADERS, verify=SSL_CERT, timeout=25)
    r.raise_for_status()
    return r.text


def _fetch_with_jina(url: str) -> str:
    """Fetch a JS-rendered page using Jina.ai Reader."""
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


def _is_spa_shell(html: str) -> bool:
    """Return True if html looks like a JS SPA shell with no real content."""
    if len(html) > 15000:
        return False
    has_module_script = 'type="module"' in html or "type='module'" in html
    soup = BeautifulSoup(html, "html.parser")
    body_text = soup.get_text(strip=True)
    return has_module_script and len(body_text) < 500


# ─── Search backends ────────────────────────────────────────────────────────

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
        from urllib.parse import unquote, parse_qs, urlparse
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


def brave_search(query: str, num_results: int = 5) -> list[dict]:
    """Search Brave and return list of {title, url, domain, snippet}. Same format as ddg_search."""
    from urllib.parse import urlparse
    config = surf_config.load_config()
    api_key = config.get("BRAVE_API_KEY", os.environ.get("BRAVE_API_KEY", ""))
    if not api_key:
        return []
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": num_results},
            headers={"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": api_key},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("web", {}).get("results", []):
            url = item.get("url", "")
            parsed = urlparse(url)
            domain = parsed.netloc.removeprefix("www.") if parsed.netloc else ""
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "domain": domain,
                "snippet": item.get("description", ""),
            })
        return results
    except Exception:
        return []


def tavily_search(query: str, num_results: int = 5) -> list[dict]:
    """Search Tavily and return list of {title, url, domain, snippet}. Same format as ddg_search."""
    from urllib.parse import urlparse
    config = surf_config.load_config()
    api_key = config.get("TAVILY_API_KEY", os.environ.get("TAVILY_API_KEY", ""))
    if not api_key:
        return []
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"query": query, "max_results": num_results, "api_key": api_key},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("results", []):
            url = item.get("url", "")
            parsed = urlparse(url)
            domain = parsed.netloc.removeprefix("www.") if parsed.netloc else ""
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "domain": domain,
                "snippet": item.get("content", "")[:300],
            })
        return results
    except Exception:
        return []


def _get_search_backend() -> callable:
    """Return best available search backend: Tavily > Brave > DDG."""
    config = surf_config.load_config()
    if config.get("TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY"):
        return tavily_search
    if config.get("BRAVE_API_KEY") or os.environ.get("BRAVE_API_KEY"):
        return brave_search
    return ddg_search
