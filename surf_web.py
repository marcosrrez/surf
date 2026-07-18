"""surf web UI — FastAPI app that wraps surf's search pipeline."""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import surf_config

app = FastAPI(title="surf", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "web", "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "web", "templates"))

# Import surf's core pipeline
from surf_backends import _is_spa_shell, _fetch_with_jina


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/tags")
async def get_tags():
    """Return vault tag counts for sidebar."""
    from surf_store import _obsidian_vault_path, _extract_tags
    vault = _obsidian_vault_path()
    if not vault:
        return {"tags": {}}
    surf_dir = os.path.join(vault, "surf")
    if not os.path.isdir(surf_dir):
        return {"tags": {}}
    import re
    tag_counts: dict[str, int] = {}
    for root, _dirs, files in os.walk(surf_dir):
        if "_topics" in root:
            continue
        for fname in files:
            if not fname.endswith(".md"):
                continue
            try:
                text = open(os.path.join(root, fname), encoding="utf-8").read()
                for tag in _extract_tags(text):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            except Exception:
                continue
    return {"tags": dict(sorted(tag_counts.items(), key=lambda x: x[1], reverse=True))}


@app.get("/api/suggest")
async def suggest(q: str):
    """Proxy autocomplete suggestions through the server — browser never talks to DDG directly."""
    if not q or len(q.strip()) < 2:
        return {"suggestions": []}
    import requests as req
    try:
        resp = req.get(
            "https://duckduckgo.com/ac/",
            params={"q": q.strip(), "type": "list"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=3,
        )
        data = resp.json()
        # DDG returns ["query", ["sug1", "sug2", ...]]
        items = data[1] if isinstance(data, list) and len(data) > 1 else []
        return {"suggestions": items[:6]}
    except Exception:
        return {"suggestions": []}


@app.get("/api/article")
async def get_article(url: str):
    """Fetch and return clean article text for the inline reader."""
    import requests as req
    from surf_backends import SSL_CERT, HEADERS, _is_spa_shell, _fetch_with_jina
    from urllib.parse import urlparse
    from surf import extract_text

    domain = urlparse(url).netloc.removeprefix("www.")
    slug_title = urlparse(url).path.split("/")[-1].replace("-", " ").replace("_", " ").title()

    # Strategy 1: direct fetch
    try:
        resp = req.get(url, headers=HEADERS, verify=SSL_CERT, timeout=12)
        resp.raise_for_status()
        html = resp.text
        if not _is_spa_shell(html):
            title, content = extract_text(html, max_words=10000, return_title=True)
            if content and len(content.strip()) > 200:
                return {"title": title or slug_title or domain, "domain": domain, "content": content, "url": url}
    except Exception:
        pass

    # Strategy 2: Jina Reader (handles Cloudflare, JS-heavy, paywalls)
    try:
        content = _fetch_with_jina(url)
        if content and len(content.strip()) > 200:
            return {"title": slug_title or domain, "domain": domain, "content": content, "url": url}
    except Exception:
        pass

    return {"error": "couldn't fetch", "url": url}


@app.get("/api/vault-notes")
async def get_vault_notes(tag: str = None):
    """Return vault notes, optionally filtered by tag."""
    import re
    from surf_store import _obsidian_vault_path, _extract_tags
    vault = _obsidian_vault_path()
    if not vault:
        return {"notes": []}
    surf_dir = os.path.join(vault, "surf")
    if not os.path.isdir(surf_dir):
        return {"notes": []}
    notes = []
    for root, _dirs, files in os.walk(surf_dir):
        if "_topics" in root:
            continue
        for fname in sorted(files, reverse=True):
            if not fname.endswith(".md"):
                continue
            try:
                text = open(os.path.join(root, fname), encoding="utf-8").read()
                tags = _extract_tags(text)
                if tag and tag not in tags:
                    continue
                qm = re.search(r'^query:\s*"(.+)"', text, re.MULTILINE)
                dm = re.search(r"^date:\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
                query_text = qm.group(1) if qm else fname.replace(".md", "")
                date_text = dm.group(1) if dm else ""
                notes.append({"query": query_text, "date": date_text, "tags": list(tags), "stem": fname.replace(".md", "")})
            except Exception:
                continue
    return {"notes": notes[:50]}


@app.get("/api/search")
async def search(q: str, fresh: bool = False):
    """Stream search results as SSE events via the shared surf_engine pipeline."""
    from surf_engine import search_events

    def generate():
        for event in search_events(q, fresh=fresh):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3939)
