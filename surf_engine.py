"""surf_engine — the unified search pipeline as a typed-event generator.

One engine, two skins: the CLI (surf.py) renders these stages to the terminal,
the web app (surf_web.py) renders them as SSE. Every pipeline capability —
specialized data handlers, quality retry, confidence gate, facet fan-out,
deep reading with live commentary, citation verification, session + vault
memory — flows through here so both surfaces stay in sync.

Events yielded (all dicts):
  {"type": "status",       "content": str}
  {"type": "intent",       "content": {...}}          assess_intent result
  {"type": "sources",      "content": [source, ...]}  ranked results
  {"type": "vault",        "content": {"count", "notes"}}
  {"type": "reading",      "content": {"domain", "quality"}}       live, per fetch
  {"type": "commentary",   "content": {"domain", "comment", "num", "quality"}}
  {"type": "citemap",      "content": [{"num", "domain", "url", "title"}, ...]}
  {"type": "answer_card",  "content": {"kind", "label", "text", ...}} specialized hit
  {"type": "token",        "content": str}            synthesis stream
  {"type": "verification", "content": {"checked", "supported", "claims"}}
  {"type": "related",      "content": [str, str, str]}
  {"type": "error",        "content": str}
  {"type": "done",         "content": {"elapsed", "tier", "spend"}}
"""
from __future__ import annotations

import os
import re
import sys
import time
import queue
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator

sys.path.insert(0, os.path.dirname(__file__))

import surf
from surf_store import (
    save_session_entry, format_session_context, _truncate_at_sentence,
    _vault_retrieve, _format_vault_context, _read_preferences,
    _obsidian_session_id,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

INSTANT_SYSTEM = (
    "You are surf — a sharp, friendly search assistant. "
    "For greetings, respond warmly in one sentence. "
    "For simple facts, answer directly. "
    "For translations/math, give just the answer."
)

_TIER_STATUS = {
    "current":   "Current events — reading today's sources...",
    "research":  "Research question — reading in depth...",
    "contested": "Evaluating from multiple perspectives...",
}


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _source_payload(r: dict) -> dict:
    q = r.get("_quality", {})
    return {
        "title": r.get("title", ""),
        "url": r.get("url", ""),
        "domain": r.get("domain", ""),
        "snippet": r.get("snippet", "")[:200],
        "reliability": q.get("reliability", 0.5),
        "credibility": q.get("credibility", 0.5),
        "composite": q.get("composite", 0.5),
    }


def _run_with_events(fn, *args, callback_kw: str = "on_event", **kwargs) -> Iterator[dict]:
    """Run fn in a thread, yielding its callback events live.

    The blocking stage (deep reading, academic synthesis) emits via a callback;
    this bridges those emissions into the generator so the client sees them as
    they happen. Final element yielded is {"type": "__result__", "value": ...}.
    """
    q: queue.Queue = queue.Queue()
    kwargs[callback_kw] = q.put
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn, *args, **kwargs)
        while True:
            try:
                yield q.get(timeout=0.1)
            except queue.Empty:
                if fut.done():
                    break
        while not q.empty():
            yield q.get_nowait()
        yield {"type": "__result__", "value": fut.result()}


def _save(query: str, answer: str, sources: list[dict], sparked_by: str = "") -> None:
    """Session + vault writes — the web surface learns exactly like the CLI."""
    try:
        summary = (answer or "").strip()
        if "▸ TL;DR" in summary:
            summary = summary.split("▸ TL;DR")[-1].strip()
        save_session_entry(query, "search", _truncate_at_sentence(summary, 300))
        surf._obsidian_save(query, answer or "", sources,
                            session_id=_obsidian_session_id(), sparked_by=sparked_by)
    except Exception:
        pass


def _done_payload(t0: float, tier: str) -> dict:
    payload = {"elapsed": round(time.time() - t0, 1), "tier": tier}
    try:
        if surf._HAS_ANTHROPIC:
            payload["spend"] = surf.claude_monthly_spend()
    except Exception:
        pass
    return payload


def _specialized_events(query: str, source_type: str, t0: float) -> Iterator[dict]:
    """Dispatch weather/finance/academic/wikipedia handlers as events.

    Returns True (via StopIteration value) when the query was fully handled,
    False to fall through to the web search pipeline.
    """
    handler = {
        "weather": surf._handle_weather,
        "academic": surf._handle_academic,
        "financial": surf._handle_financial,
        "factual": surf._handle_factual,
    }.get(source_type)
    if handler is None:
        return False
    label = surf._source_type_name(source_type)
    yield {"type": "status", "content": f"Checking {label}..."}

    try:
        if source_type == "academic":
            result = None
            answer_parts: list[str] = []
            for ev in _run_with_events(handler, query, callback_kw="on_token"):
                if isinstance(ev, dict) and ev.get("type") == "__result__":
                    result = ev["value"]
                elif isinstance(ev, str):
                    # raw token strings from _handle_academic's on_token
                    answer_parts.append(ev)
                    yield {"type": "token", "content": ev}
            if result is None:
                return False
            response, sources, streaming = result
        else:
            result = handler(query)
            if result is None:
                return False
            response, sources, streaming = result
    except Exception:
        return False

    yield {"type": "sources", "content": [_source_payload(s) for s in sources]}
    if source_type == "academic" and answer_parts:
        # complex-query synthesis already streamed as tokens
        answer_text = "".join(answer_parts)
    else:
        answer_text = _strip_ansi(response or "")
        if answer_text:
            yield {"type": "answer_card", "content": {"kind": source_type, "label": label,
                                                      "text": answer_text}}
    _save(query, _strip_ansi(answer_text or ""), sources)
    yield {"type": "done", "content": _done_payload(t0, source_type)}
    return True


def search_events(query: str, fresh: bool = False, context: str = "") -> Iterator[dict]:
    """The full surf pipeline as a stream of typed events.

    context: recent conversation turns from the client (query + TL;DR pairs),
    merged with the on-disk session so follow-ups can resolve pronouns even
    across surfaces.
    """
    query = (query or "").strip()
    if not query:
        yield {"type": "error", "content": "Empty query"}
        return
    t0 = time.time()

    # ── Intent ────────────────────────────────────────────────────────────
    yield {"type": "status", "content": "Understanding your intent..."}
    session_ctx = format_session_context()
    if context:
        session_ctx = f"{session_ctx}\n{context}".strip() if session_ctx else context
    vault_depth = len(_vault_retrieve(query, max_notes=10, max_chars=0)[0]) if not fresh else 0
    intent = surf.assess_intent(query, vault_depth=vault_depth, session_context=session_ctx)
    yield {"type": "intent", "content": intent}

    tier = intent.get("tier", "snippet")
    route = intent.get("route", "search")
    domain = intent.get("domain", "general")
    reformulated = intent.get("reformulated_query") or query

    # ── Instant route: no search needed ──────────────────────────────────
    if route == "instant" or (tier == "snippet" and len(query.split()) <= 3):
        yield {"type": "status", "content": "Thinking..."}
        date_line = time.strftime("Today's date: %A, %B %d, %Y.")
        prompt = f"{date_line}\n{context}\n\nUser: {query}" if context else f"{date_line}\n\nUser: {query}"
        answer = ""
        try:
            for chunk in surf.stream_ai(prompt, INSTANT_SYSTEM):
                answer += chunk
                yield {"type": "token", "content": chunk}
        except Exception as e:
            yield {"type": "error", "content": str(e)}
            return
        _save(query, answer, [])
        yield {"type": "done", "content": _done_payload(t0, "instant")}
        return

    # ── Specialized data sources (weather / finance / academic / wiki) ───
    source_type = surf._classify_data_source(query)
    if source_type != "web":
        handled = yield from _specialized_events(query, source_type, t0)
        if handled:
            return

    # ── Web search: merged backends + narrated retry ──────────────────────
    yield {"type": "status", "content": f'Searching: "{reformulated[:50]}"...'}
    eval_context = None
    if intent.get("source_strategy") in ("academic", "authoritative", "official"):
        eval_context = {"is_evaluative": True, "source_signals": [], "avoid_signals": []}
    elif tier in ("contested", "research") and surf._is_evaluative_query(query, tier):
        eval_context = surf._evaluate_query_intent(query)

    clean_query = surf._clean_conversational_query(reformulated)
    ddg_query = surf._enrich_ddg_query(clean_query, tier=tier)
    entity_type = domain if domain in surf.SOURCE_HIERARCHY else surf._identify_entity_type(query)

    try:
        results, _tried = surf._search_with_retry(ddg_query, entity_type=entity_type)
    except Exception:
        try:
            results = surf.ddg_search(reformulated, num_results=10)
        except Exception as e:
            yield {"type": "error", "content": f"Search failed: {e}"}
            return
    results = surf._filter_results(results, evaluative_context=eval_context)

    # ── Facet fan-out for deep tiers ──────────────────────────────────────
    if tier in ("research", "contested") and results:
        subs = surf._generate_subqueries(query, tier)
        if subs:
            yield {"type": "status", "content": "Exploring facets: " + " · ".join(s[:36] for s in subs[:2]) + "..."}
            seen = {r.get("domain", "") for r in results}
            for r in surf._fanout_search(subs):
                if r.get("domain", "") not in seen:
                    results.append(r)
                    seen.add(r.get("domain", ""))

    results, ddg_query = surf._fix_entity_mismatch(query, results, ddg_query,
                                                   evaluative_context=eval_context)
    if not results:
        yield {"type": "error", "content": "No results found."}
        return

    # ── Rank, gate, retry ────────────────────────────────────────────────
    results = surf.filter_and_rank_results(query, results, intent=intent)
    tier = surf._confidence_gate(query, results, tier, entity_type=entity_type)

    sources_weak = False
    if tier in ("current", "research", "contested") and not fresh and results:
        top = sorted(r.get("_quality", {}).get("composite", 0.5) for r in results[:3])
        median_q = top[len(top) // 2] if top else 0.5
        if median_q < surf._QUALITY_RETRY_THRESHOLD:
            yield {"type": "status", "content": "Sources thin — searching deeper..."}
            retry = surf._quality_retry_search(query, intent, results)
            if retry:
                results = surf.filter_and_rank_results(query, retry + results, intent=intent)
            post = sorted(r.get("_quality", {}).get("composite", 0.5) for r in results[:3])
            sources_weak = (post[len(post) // 2] if post else 0.5) < surf._QUALITY_RETRY_THRESHOLD

    yield {"type": "sources", "content": [_source_payload(r) for r in results[:8]]}

    # ── Vault context ─────────────────────────────────────────────────────
    vault_notes, sparked_by = ([], "")
    vault_ctx = ""
    if not fresh:
        vault_notes, sparked_by = _vault_retrieve(query)
        vault_ctx = _format_vault_context(vault_notes)
    if vault_notes:
        yield {"type": "vault", "content": {"count": len(vault_notes),
                                            "notes": [n["query"][:60] for n in vault_notes]}}

    # ── Deep reading with live events ────────────────────────────────────
    deep_content, deep_sources = "", []
    if tier in ("current", "research", "contested"):
        yield {"type": "status", "content": _TIER_STATUS.get(tier, "Reading sources...")}
        result = None
        for ev in _run_with_events(surf._deep_research, query, tier, results,
                                   ddg_query, entity_type=entity_type):
            if ev.get("type") == "__result__":
                result = ev["value"]
            elif ev.get("type") in ("reading", "commentary"):
                yield {"type": ev["type"], "content": {k: v for k, v in ev.items() if k != "type"}}
        if result:
            deep_content, deep_sources = result

    # ── Prompt assembly (citations align with citemap) ────────────────────
    if deep_sources:
        deep_urls = {s.get("url", "") for s in deep_sources}
        cite_sources = deep_sources + [r for r in results if r.get("url", "") not in deep_urls][:3]
    else:
        cite_sources = results[:8]
    yield {"type": "citemap", "content": [
        {"num": i + 1, "domain": s.get("domain", ""), "url": s.get("url", ""),
         "title": s.get("title", "")}
        for i, s in enumerate(cite_sources)
    ]}

    base_prompt = surf.build_search_prompt(query, cite_sources)
    if session_ctx:
        base_prompt = f"{session_ctx}\n\n{base_prompt}"
    if vault_ctx:
        base_prompt = f"{vault_ctx}\n\n{surf.VAULT_CONTEXT_INSTRUCTION}\n\n{base_prompt}"
    prefs = _read_preferences()
    if prefs:
        base_prompt = f"[User preferences]\n{prefs}\n[End preferences]\n\n{base_prompt}"

    if deep_content:
        quality_note = ""
        if sources_weak:
            quality_note = ("\nIMPORTANT: The available sources are limited in quality. "
                            "State clearly what you can confirm from these sources and what "
                            "remains unverified. Do not suggest the user check other sources.\n")
        elif any(r.get("_quality", {}).get("composite", 0.5) >= 0.7 for r in results[:3]):
            quality_note = ("\nNote: weight findings from sources with specific data, named "
                            "researchers, and cited methodology more heavily than those with "
                            "vague claims or marketing language.\n")
        prompt = base_prompt + f"\n\nFull article content from {len(deep_sources)} source(s):{quality_note}\n{deep_content}"
    else:
        prompt = base_prompt

    if eval_context and eval_context.get("is_evaluative"):
        system = surf.SEARCH_SYSTEM_EVALUATIVE
    else:
        system = {
            "current": surf.SEARCH_SYSTEM_CURRENT,
            "research": surf.SEARCH_SYSTEM_RESEARCH,
            "contested": surf.SEARCH_SYSTEM_CONTESTED,
        }.get(tier, surf.SEARCH_SYSTEM)

    # ── Synthesis ─────────────────────────────────────────────────────────
    yield {"type": "status", "content": "Synthesizing..."}
    answer = ""
    try:
        for chunk in surf.stream_ai(prompt, system, tier=tier):
            answer += chunk
            yield {"type": "token", "content": chunk}
    except Exception as e:
        yield {"type": "error", "content": str(e)}
        return

    # ── Grounding pass: verify cited claims against fetched text ──────────
    if deep_sources and "[" in answer:
        src_texts = {i + 1: s.get("_text", "") for i, s in enumerate(deep_sources) if s.get("_text")}
        verification = surf._verify_citations(answer, src_texts)
        if verification.get("checked"):
            yield {"type": "verification", "content": verification}

    # ── Related searches ──────────────────────────────────────────────────
    related = surf.generate_related_searches(query, answer)
    if related:
        yield {"type": "related", "content": related}

    # ── Memory: the web surface learns too ────────────────────────────────
    _save(query, answer, deep_sources or results[:8], sparked_by=sparked_by)

    yield {"type": "done", "content": _done_payload(t0, tier)}
