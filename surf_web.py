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


_quality_retry_search = None
_confidence_gate = None
_QUALITY_RETRY_THRESHOLD = 0.45


def _load_surf():
    global _surf_loaded, _assess_intent, _score_source_quality, _filter_and_rank_results
    global _score_content_depth, _build_search_prompt, _stream_ai
    global _VAULT_CONTEXT_INSTRUCTION, _SEARCH_SYSTEM, _SEARCH_SYSTEM_RESEARCH
    global _SEARCH_SYSTEM_CURRENT, _SEARCH_SYSTEM_CONTESTED
    global _quality_retry_search, _confidence_gate
    if _surf_loaded:
        return
    import surf
    _assess_intent = surf.assess_intent
    _score_source_quality = surf.score_source_quality
    _filter_and_rank_results = surf.filter_and_rank_results
    _score_content_depth = surf.score_content_depth
    _build_search_prompt = surf.build_search_prompt
    _stream_ai = surf.stream_ai
    _quality_retry_search = surf._quality_retry_search
    _confidence_gate = surf._confidence_gate
    _VAULT_CONTEXT_INSTRUCTION = surf.VAULT_CONTEXT_INSTRUCTION
    _SEARCH_SYSTEM = surf.SEARCH_SYSTEM
    _SEARCH_SYSTEM_RESEARCH = surf.SEARCH_SYSTEM_RESEARCH
    _SEARCH_SYSTEM_CURRENT = surf.SEARCH_SYSTEM_CURRENT
    _SEARCH_SYSTEM_CONTESTED = surf.SEARCH_SYSTEM_CONTESTED
    _surf_loaded = True


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
        route = intent.get("route", "search")
        tier = intent.get("tier", "snippet")
        domain = intent.get("domain", "general")
        reformulated = intent.get("reformulated_query", query)

        # Instant / conversational — no search needed
        if route == "instant" or tier == "snippet" and len(query.split()) <= 3:
            yield f"data: {json.dumps({'type': 'status', 'content': 'Thinking...'})}\n\n"
            instant_system = (
                "You are surf — a sharp, friendly search assistant. "
                "For greetings, respond warmly in one sentence. "
                "For simple facts, answer directly. "
                "For translations/math, give just the answer."
            )
            try:
                for chunk in _stream_ai(query, instant_system):
                    yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'status', 'content': f'Searching: \"{reformulated[:50]}\"...'})}\n\n"

        # Search with retry — up to 2 attempts if first results are thin
        search_fn = _get_search_backend()
        results = []
        search_query = reformulated or query

        # Date injection for current-events queries — the #1 fix for stale results
        if tier == "current":
            today = time.strftime("%B %d %Y")
            year = time.strftime("%Y")
            if year not in search_query:
                search_query = f"{search_query} {today}"
            # Also try Tavily with news topic if available
            try:
                from surf_backends import tavily_search as _tavily
                news_results = _tavily(search_query, num_results=5, topic="news")
                if news_results:
                    results.extend(news_results)
            except Exception:
                pass

        try:
            main_results = search_fn(search_query, num_results=10)
            seen_urls = {r.get("url") for r in results}
            for r in main_results:
                if r.get("url") not in seen_urls:
                    results.append(r)
                    seen_urls.add(r.get("url"))
            if len(results) < 3 or all(len(r.get("snippet", "")) < 50 for r in results):
                yield f"data: {json.dumps({'type': 'status', 'content': 'First pass thin — trying a different angle...'})}\n\n"
                try:
                    alt_results = ddg_search(f"{search_query} analysis {time.strftime('%Y')}", num_results=8)
                    seen = {r.get("domain") for r in results}
                    for r in alt_results:
                        if r.get("domain") not in seen:
                            results.append(r)
                            seen.add(r.get("domain"))
                except Exception:
                    pass
        except Exception:
            try:
                results = ddg_search(search_query, num_results=10)
            except Exception:
                pass

        if not results:
            yield f"data: {json.dumps({'type': 'error', 'content': 'No results found.'})}\n\n"
            return

        # Quality ranking
        ranked = _filter_and_rank_results(query, results, intent=intent)

        # Confidence gate — escalate tier if snippets are weak
        tier = _confidence_gate(query, ranked, tier)

        # Quality-triggered retry — refuse to settle for weak sources
        _sources_weak = False
        if tier in ("current", "research", "contested") and not fresh:
            top_scores = sorted([r.get("_quality", {}).get("composite", 0.5) for r in ranked[:3]])
            median_q = top_scores[len(top_scores) // 2] if top_scores else 0.5
            if median_q < _QUALITY_RETRY_THRESHOLD:
                yield f"data: {json.dumps({'type': 'status', 'content': 'Sources thin — searching deeper...'})}\n\n"
                retry = _quality_retry_search(query, intent, ranked)
                if retry:
                    ranked = _filter_and_rank_results(query, retry + ranked, intent=intent)
                post_scores = sorted([r.get("_quality", {}).get("composite", 0.5) for r in ranked[:3]])
                _sources_weak = (post_scores[len(post_scores) // 2] if post_scores else 0.5) < _QUALITY_RETRY_THRESHOLD
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
            from surf import extract_text, _chesterton_evaluate_sources
            import requests as req
            from surf_backends import SSL_CERT, HEADERS
            deep_content = []
            fetched_for_commentary = []
            for r in ranked[:5]:
                url = r.get("url", "")
                if not url or not url.startswith("http"):
                    continue
                try:
                    resp = req.get(url, headers=HEADERS, verify=SSL_CERT, timeout=8)
                    resp.raise_for_status()
                    html = resp.text
                    if _is_spa_shell(html):
                        content = _fetch_with_jina(url)
                    else:
                        _, content = extract_text(html, max_words=1500, return_title=True)
                    if content and len(content.split()) > 150:
                        rdomain = r.get("domain", "").removeprefix("www.")
                        idx = len(deep_content)
                        fetched_for_commentary.append((idx, rdomain, content, r))
                        deep_content.append(f"[{rdomain}]\n{content[:2000]}")
                except Exception:
                    continue

            # Chesterton commentary on fetched sources
            if fetched_for_commentary:
                try:
                    commentary = _chesterton_evaluate_sources(query, fetched_for_commentary, domain)
                    for idx, rdomain, content, r in fetched_for_commentary:
                        entry = commentary.get(idx, {})
                        comment = entry.get("comment", "") if isinstance(entry, dict) else str(entry)
                        quality = _score_source_quality(r, domain=domain)
                        yield f"data: {json.dumps({'type': 'reading', 'content': {'domain': rdomain, 'comment': comment, 'quality': quality.get('composite', 0.5)}})}\n\n"
                except Exception:
                    pass

            if deep_content:
                quality_note = ""
                if _sources_weak:
                    quality_note = "\nIMPORTANT: The available sources are limited. State clearly what you found and what you could not find. NEVER tell the user to go check another website — if you couldn't find it, say so directly and stop. No hedging, no suggestions to visit ESPN or FIFA.com.\n"
                elif any(r.get("_quality", {}).get("composite", 0.5) >= 0.7 for r in ranked[:3]):
                    quality_note = "\nNote: weight findings from sources with specific data, named researchers, and cited methodology more heavily than those with vague claims or marketing language.\n"
                base_prompt += f"\n\nFull article content from {len(deep_content)} source(s):{quality_note}\n" + "\n\n---\n\n".join(deep_content)

        # Select system prompt
        system = {
            "current": _SEARCH_SYSTEM_CURRENT,
            "research": _SEARCH_SYSTEM_RESEARCH,
            "contested": _SEARCH_SYSTEM_CONTESTED,
        }.get(tier, _SEARCH_SYSTEM)

        # Stream synthesis
        yield f"data: {json.dumps({'type': 'status', 'content': 'Synthesizing...'})}\n\n"
        answer_text = ""
        try:
            for chunk in _stream_ai(base_prompt, system, tier=tier):
                answer_text += chunk
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

        # Save to session memory + vault
        try:
            from surf_store import save_session_entry, _truncate_at_sentence
            summary = answer_text.strip()
            if "▸ TL;DR" in summary:
                summary = summary.split("▸ TL;DR")[-1].strip()
            save_session_entry(query, "web_search", _truncate_at_sentence(summary, 300))
            sparked = sparked_by if not fresh and vault_notes else ""
            _obsidian_save(query, answer_text, ranked[:5], session_id=_obsidian_session_id(),
                          sparked_by=sparked, depth="exploration" if vault_notes else "lookup")
        except Exception:
            pass

        yield f"data: {json.dumps({'type': 'done', 'content': {'tier': tier, 'domain': domain}})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3939)
