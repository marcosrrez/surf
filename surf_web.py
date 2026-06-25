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
from surf_backends import searxng_search, tavily_search, ddg_search, fetch_page, _is_spa_shell, _fetch_with_jina
from surf_backends import _get_search_backend
from surf_store import _vault_retrieve, _format_vault_context, _obsidian_save, _obsidian_session_id

# These imports need the full surf module loaded
_surf_loaded = False
_assess_intent = None
_score_source_quality = None
_filter_and_rank_results = None
_score_content_depth = None
_build_search_prompt = None
_stream_ai = None
_VAULT_CONTEXT_INSTRUCTION = ""
_SEARCH_SYSTEM = ""
_SEARCH_SYSTEM_RESEARCH = ""
_SEARCH_SYSTEM_CURRENT = ""
_SEARCH_SYSTEM_CONTESTED = ""


def _load_surf():
    global _surf_loaded, _assess_intent, _score_source_quality, _filter_and_rank_results
    global _score_content_depth, _build_search_prompt, _stream_ai
    global _VAULT_CONTEXT_INSTRUCTION, _SEARCH_SYSTEM, _SEARCH_SYSTEM_RESEARCH
    global _SEARCH_SYSTEM_CURRENT, _SEARCH_SYSTEM_CONTESTED
    if _surf_loaded:
        return
    import surf
    _assess_intent = surf.assess_intent
    _score_source_quality = surf.score_source_quality
    _filter_and_rank_results = surf.filter_and_rank_results
    _score_content_depth = surf.score_content_depth
    _build_search_prompt = surf.build_search_prompt
    _stream_ai = surf.stream_ai
    _VAULT_CONTEXT_INSTRUCTION = surf.VAULT_CONTEXT_INSTRUCTION
    _SEARCH_SYSTEM = surf.SEARCH_SYSTEM
    _SEARCH_SYSTEM_RESEARCH = surf.SEARCH_SYSTEM_RESEARCH
    _SEARCH_SYSTEM_CURRENT = surf.SEARCH_SYSTEM_CURRENT
    _SEARCH_SYSTEM_CONTESTED = surf.SEARCH_SYSTEM_CONTESTED
    _surf_loaded = True


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/search")
async def search(q: str, fresh: bool = False):
    """Stream search results as SSE events."""
    _load_surf()

    def generate():
        query = q.strip()
        if not query:
            yield f"data: {json.dumps({'type': 'error', 'content': 'Empty query'})}\n\n"
            return

        # Intent
        yield f"data: {json.dumps({'type': 'status', 'content': 'Understanding your intent...'})}\n\n"
        from surf_store import format_session_context
        session_ctx = format_session_context()
        vault_depth = len(_vault_retrieve(query, max_notes=10, max_chars=0)[0]) if not fresh else 0
        intent = _assess_intent(query, vault_depth=vault_depth, session_context=session_ctx)
        yield f"data: {json.dumps({'type': 'intent', 'content': intent})}\n\n"

        # Search
        tier = intent.get("tier", "snippet")
        domain = intent.get("domain", "general")
        reformulated = intent.get("reformulated_query", query)
        yield f"data: {json.dumps({'type': 'status', 'content': f'Searching: \"{reformulated[:50]}\"...'})}\n\n"

        search_fn = _get_search_backend()
        try:
            results = search_fn(reformulated or query)
        except Exception:
            results = ddg_search(reformulated or query)

        if not results:
            yield f"data: {json.dumps({'type': 'error', 'content': 'No results found.'})}\n\n"
            return

        # Quality ranking
        ranked = _filter_and_rank_results(query, results, intent=intent)
        sources = []
        for r in ranked[:8]:
            q_dict = r.get("_quality", {})
            sources.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "domain": r.get("domain", ""),
                "snippet": r.get("snippet", "")[:200],
                "reliability": q_dict.get("reliability", 0.5),
                "credibility": q_dict.get("credibility", 0.5),
                "composite": q_dict.get("composite", 0.5),
            })
        yield f"data: {json.dumps({'type': 'sources', 'content': sources})}\n\n"

        # Vault context
        vault_ctx = ""
        vault_notes = []
        if not fresh:
            vault_notes, sparked_by = _vault_retrieve(query)
            vault_ctx = _format_vault_context(vault_notes)
        if vault_notes:
            yield f"data: {json.dumps({'type': 'vault', 'content': {'count': len(vault_notes), 'notes': [n['query'][:60] for n in vault_notes]}})}\n\n"

        # Build prompt
        base_prompt = _build_search_prompt(query, ranked[:5])
        if vault_ctx:
            base_prompt = f"{vault_ctx}\n\n{_VAULT_CONTEXT_INSTRUCTION}\n\n{base_prompt}"

        # Deep read for research/contested tiers
        if tier in ("research", "current", "contested"):
            yield f"data: {json.dumps({'type': 'status', 'content': 'Reading sources in depth...'})}\n\n"
            from surf import extract_text
            deep_content = []
            for r in ranked[:3]:
                url = r.get("url", "")
                if not url or not url.startswith("http"):
                    continue
                try:
                    import requests as req
                    from surf_backends import SSL_CERT, HEADERS
                    resp = req.get(url, headers=HEADERS, verify=SSL_CERT, timeout=8)
                    resp.raise_for_status()
                    html = resp.text
                    if _is_spa_shell(html):
                        content = _fetch_with_jina(url)
                    else:
                        _, content = extract_text(html, max_words=1500, return_title=True)
                    if content and len(content.split()) > 150:
                        rdomain = r.get("domain", "").removeprefix("www.")
                        depth = _score_content_depth(content)
                        quality = _score_source_quality(r, domain=domain)
                        yield f"data: {json.dumps({'type': 'reading', 'content': {'domain': rdomain, 'depth': round(depth, 2), 'quality': quality.get('composite', 0.5)}})}\n\n"
                        deep_content.append(f"[{rdomain}]\n{content[:2000]}")
                except Exception:
                    continue

            if deep_content:
                base_prompt += f"\n\nFull article content from {len(deep_content)} source(s):\n" + "\n\n---\n\n".join(deep_content)

        # Select system prompt
        system = {
            "current": _SEARCH_SYSTEM_CURRENT,
            "research": _SEARCH_SYSTEM_RESEARCH,
            "contested": _SEARCH_SYSTEM_CONTESTED,
        }.get(tier, _SEARCH_SYSTEM)

        # Stream synthesis
        yield f"data: {json.dumps({'type': 'status', 'content': 'Synthesizing...'})}\n\n"
        try:
            for chunk in _stream_ai(base_prompt, system, tier=tier):
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3939)
