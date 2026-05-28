#!/usr/bin/env python3
import re
import os
import requests
from bs4 import BeautifulSoup

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
