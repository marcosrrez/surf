#!/usr/bin/env python3
"""
surf Quality + Delight Benchmark

Quality: accuracy, completeness, honesty, source quality, structure.
Delight: TL;DR sharpness, format cleanliness, conciseness, voice (LLM-judged).

Run:
  cd ~/termbrowser && .venv/bin/python3 tests/quality/benchmark.py [--quick N] [--category X] [--save] [--report]
"""
import sys
import json
import re
import time
import argparse
import os
from datetime import datetime

sys.path.insert(0, '/Users/marcos/termbrowser')

from surf import (
    ddg_search, stream_groq, build_search_prompt, SEARCH_SYSTEM,
    detect_input_type, fetch_page, extract_text, _is_spa_shell,
    _fetch_with_jina, _filter_results, _identify_entity_type,
    read_flow, classify_intent, CLASSIFIER_MODEL, _has_uncertainty,
    READ_SYSTEM,
)

QUERIES_FILE = os.path.join(os.path.dirname(__file__), 'queries.json')
RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m~\033[0m"


# ─── Quality Scoring ──────────────────────────────────────────────────────────

def score_response(query_def: dict, response: str, sources: list[str]) -> dict:
    scores = {}
    notes = []
    response_lower = response.lower()
    word_count = len(response.split())

    # 1. Structure
    has_tldr = "▸ TL;DR" in response or "tldr" in response_lower[:100]
    if query_def.get("must_have_tldr", True):
        scores["structure"] = 1 if has_tldr else 0
        if not has_tldr:
            notes.append("MISSING TL;DR")
    else:
        scores["structure"] = 1

    # 2. Accuracy
    expected = query_def.get("expected_contains", [])
    if expected:
        def _flexible_match(expected_str, response_lower):
            if expected_str.lower() in response_lower:
                return True
            equivalences = {
                "eight minutes": ["8 minutes", "eight minutes", "8.3 min"],
                "8.3": ["8.3", "8 minutes", "eight minutes"],
                "300,000": ["300,000", "299,792", "299,000", "3×10", "3 ×", "3x10"],
                "186,000": ["186,000", "186,282"],
                "gravity": ["gravity", "gravitational", "gravitation"],
                "monty python": ["monty python", "python comedy"],
                "1945": ["1945", "may 8", "august 15", "september 2"],
                "trump": ["trump", "donald trump", "president trump"],
                "eight": ["eight", "8 "],
                "5,280": ["5,280", "5280"],
                "5280": ["5280", "5,280"],
                "51": ["51", "51.0", "fifty-one"],
            }
            for key, variants in equivalences.items():
                if expected_str.lower() == key:
                    return any(v in response_lower for v in variants)
            return False

        found = sum(1 for e in expected if _flexible_match(e, response_lower))
        scores["accuracy"] = round(found / len(expected), 2)
        missing = [e for e in expected if not _flexible_match(e, response_lower)]
        if missing:
            notes.append(f"MISSING: {missing}")
    else:
        scores["accuracy"] = 1.0

    # 3. Honesty
    if query_def.get("expected_honest"):
        honest_signals = query_def.get("honest_signals", [])
        is_honest = any(s in response_lower for s in honest_signals)
        scores["honesty"] = 1 if is_honest else 0
        if not is_honest:
            notes.append("NOT HONEST ABOUT LIMITATIONS")
    else:
        scores["honesty"] = 1

    # 4. Source quality
    banned = query_def.get("banned_domains", [])
    spam_found = [d for d in sources if any(b in d for b in banned)]
    scores["source_quality"] = 0 if spam_found else 1
    if spam_found:
        notes.append(f"SPAM SOURCES: {spam_found}")

    auth_sources = query_def.get("expected_sources_authoritative", [])
    if auth_sources:
        min_sources = query_def.get("min_sources", 1)
        has_auth = sum(1 for s in sources if any(a in s for a in auth_sources))
        if has_auth < min_sources:
            scores["source_quality"] = max(0, scores["source_quality"] - 0.5)
            notes.append(f"LOW AUTHORITY SOURCES (found {has_auth}/{min_sources} needed)")

    # 5. Completeness
    min_words = query_def.get("min_words", 10)
    max_words = query_def.get("max_words", 800)
    if word_count < min_words:
        scores["completeness"] = 0
        notes.append(f"TOO SHORT ({word_count} words, min {min_words})")
    elif word_count > max_words:
        scores["completeness"] = 0.5
        notes.append(f"TOO LONG ({word_count} words, max {max_words})")
    else:
        scores["completeness"] = 1

    total = sum(scores.values())
    scores["total"] = round(total, 2)
    scores["max"] = 5
    scores["pct"] = round(total / 5 * 100)
    scores["notes"] = notes
    return scores


# ─── Delight Scoring ──────────────────────────────────────────────────────────

FILLER_PHRASES = [
    "great question", "certainly!", "as an ai", "i'm unable",
    "of course!", "i'd be happy to", "it's important to note that",
    "in conclusion,", "to summarize,", "i hope this helps",
]

META_TLDR_STARTS = [
    "this article", "this page", "this discusses", "this covers",
    "the article", "the page", "the content", "the text",
    "this response", "the following",
]

VOICE_JUDGE_SYSTEM = """You are evaluating AI search responses for a terminal tool called surf, built for developers who want sharp answers without opening a browser.

Rate the VOICE quality — not accuracy, just how it feels to read.

Return ONLY valid JSON (no markdown, no explanation): {"score": N, "reason": "one short sentence"}

Scoring guide:
2 = Excellent. Feels like a sharp tool, not a chatbot. Direct, no padding, reads naturally in a terminal. A developer would reach for this daily.
1 = Acceptable. Gets the job done but slightly verbose, has minor filler, or feels a bit generic.
0 = Poor. Chatbot-like, padded, repetitive, or reads like it's trying too hard."""


def _extract_tldr(response: str) -> str:
    for line in response.splitlines():
        if "▸ TL;DR" in line:
            return line.replace("▸ TL;DR", "").strip()
    return ""


def score_delight(query_def: dict, response: str) -> dict:
    """Score on design/delight dimensions. Returns dict with total 0-5."""
    scores = {}
    notes = []
    response_lower = response.lower()

    # 1. TL;DR quality (0-1)
    tldr = _extract_tldr(response)
    if not tldr:
        if not query_def.get("must_have_tldr", True):
            scores["tldr_quality"] = 1  # not required, no penalty
        else:
            scores["tldr_quality"] = 0
            notes.append("NO TL;DR")
    else:
        tldr_words = len(tldr.split())
        is_meta = any(tldr.lower().startswith(p) for p in META_TLDR_STARTS)
        if is_meta:
            scores["tldr_quality"] = 0.5
            notes.append(f"TL;DR is meta: \"{tldr[:60]}\"")
        elif tldr_words < 5:
            scores["tldr_quality"] = 0.5
            notes.append(f"TL;DR too brief ({tldr_words} words)")
        elif tldr_words > 50:
            scores["tldr_quality"] = 0.5
            notes.append(f"TL;DR too long ({tldr_words} words)")
        else:
            scores["tldr_quality"] = 1

    # 2. Format cleanliness (0-1)
    has_sources = "sources:" in response_lower
    has_bad_bullets = bool(re.search(r'^\s*[-–]\s+\w', response, re.MULTILINE))
    # ** is rendered as bold in the terminal — not a format defect
    has_filler = any(f in response_lower for f in FILLER_PHRASES)
    format_issues = []
    if not has_sources and query_def.get("must_have_tldr", True):
        format_issues.append("no Sources line")
    if has_bad_bullets:
        format_issues.append("uses - for bullets")
    if has_filler:
        filler_found = [f for f in FILLER_PHRASES if f in response_lower]
        format_issues.append(f"filler: {filler_found[0]!r}")

    if not format_issues:
        scores["format_clean"] = 1
    elif len(format_issues) == 1:
        scores["format_clean"] = 0.5
        notes.append(f"FORMAT: {format_issues[0]}")
    else:
        scores["format_clean"] = 0
        notes.append(f"FORMAT: {'; '.join(format_issues)}")

    # 3. Conciseness (0-1)
    word_count = len(response.split())
    category = query_def.get("category", "")
    max_expected = {
        "instant": 80, "factual": 180, "how_to": 350,
        "research": 400, "current_events": 300, "url_read": 500,
    }.get(category, 300)

    if word_count > max_expected * 1.5:
        scores["conciseness"] = 0
        notes.append(f"VERBOSE ({word_count} words, expected ≤{max_expected})")
    elif word_count > max_expected:
        scores["conciseness"] = 0.5
        notes.append(f"SLIGHTLY VERBOSE ({word_count} words)")
    else:
        scores["conciseness"] = 1

    # 4. Voice — LLM judge (0-2)
    voice = _judge_voice(query_def["query"], response)
    scores["voice"] = voice["score"]
    scores["voice_reason"] = voice["reason"]
    if voice["score"] < 2:
        notes.append(f"VOICE {voice['score']}/2: {voice['reason']}")

    total = scores["tldr_quality"] + scores["format_clean"] + scores["conciseness"] + scores["voice"]
    scores["total"] = round(total, 2)
    scores["max"] = 5
    scores["pct"] = round(total / 5 * 100)
    scores["notes"] = notes
    return scores


def _judge_voice(query: str, response: str) -> dict:
    """Call LLM to rate voice quality. Returns {"score": 0-2, "reason": str}."""
    prompt = f"Query: {query}\n\nResponse:\n{response[:800]}"
    try:
        chunks = list(stream_groq(prompt, VOICE_JUDGE_SYSTEM, model=CLASSIFIER_MODEL, max_tokens=120))
        raw = "".join(chunks).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        result = json.loads(raw)
        score = max(0, min(2, int(result.get("score", 1))))
        return {"score": score, "reason": result.get("reason", "—")}
    except Exception:
        return {"score": 1, "reason": "could not evaluate"}


# ─── Query Runner ─────────────────────────────────────────────────────────────

def run_query(query_def: dict) -> dict:
    query = query_def["query"]
    category = query_def.get("category", "unknown")
    start = time.time()
    response = ""
    sources = []

    try:
        if category == "url_read":
            html = fetch_page(f"https://{query}" if not query.startswith("http") else query)
            if _is_spa_shell(html):
                content = _fetch_with_jina(f"https://{query}")
                title = ""
                for line in content.splitlines():
                    if line.startswith("Title:"):
                        title = line.replace("Title:", "").strip()
                        break
                text = content
            else:
                title, text = extract_text(html, return_title=True)
            prompt = f"Page title: {title}\n\nContent:\n{text[:4000]}"
            chunks = list(stream_groq(prompt, READ_SYSTEM, max_tokens=1500))
            response = "".join(chunks)
            sources = [query.split("/")[0]]
        else:
            results = _filter_results(ddg_search(query, num_results=5))

            news_signals = ["news", "latest", "today", "2026", "breaking", "current", "update"]
            is_news_query = any(s in query.lower() for s in news_signals)
            auth_news_domains = ("reuters.com", "apnews.com", "bbc.com",
                                 "bloomberg.com", "wsj.com", "nytimes.com")
            already_has_auth = any(
                any(a in r.get("domain", "") for a in auth_news_domains)
                for r in results
            )
            if is_news_query and not already_has_auth:
                try:
                    targeted = _filter_results(
                        ddg_search(f"reuters bbc apnews {query}", num_results=4)
                    )
                    seen = {r["domain"] for r in results}
                    for r in targeted:
                        if r["domain"] not in seen:
                            results.append(r)
                            seen.add(r["domain"])
                except Exception:
                    pass

            sources = [r["domain"] for r in results]
            prompt = build_search_prompt(query, results)
            chunks = list(stream_groq(prompt, SEARCH_SYSTEM, max_tokens=1500))
            response = "".join(chunks)

            if _has_uncertainty(response) and results:
                top_url = results[0].get("url", "")
                if top_url and top_url.startswith("http"):
                    try:
                        page_html = fetch_page(top_url)
                        if _is_spa_shell(page_html):
                            page_content = _fetch_with_jina(top_url)
                        else:
                            _, page_content = extract_text(page_html, max_words=2000, return_title=True)
                        if page_content and len(page_content) > 200:
                            verify_prompt = (
                                f"Original search snippets gave an uncertain answer about: {query}\n\n"
                                f"Here is the actual current content from {results[0].get('domain', 'the top source')}:\n"
                                f"{page_content[:3000]}\n\n"
                                f"Please provide the correct, definitive answer with specific facts."
                            )
                            verify_chunks = list(stream_groq(verify_prompt, SEARCH_SYSTEM, max_tokens=1500))
                            response = "".join(verify_chunks)
                    except Exception:
                        pass
    except Exception as e:
        response = f"[ERROR: {e}]"
        sources = []

    elapsed = round(time.time() - start, 1)
    quality = score_response(query_def, response, sources)
    delight = score_delight(query_def, response)

    return {
        "id": query_def["id"],
        "query": query,
        "category": category,
        "response": response,  # full response, not truncated
        "sources": sources[:5],
        "elapsed_s": elapsed,
        "word_count": len(response.split()),
        "quality": quality,
        "delight": delight,
        # keep legacy key for any tooling that reads it
        "scores": quality,
    }


# ─── Reports ──────────────────────────────────────────────────────────────────

def print_summary(results: list[dict]) -> None:
    """Compact category summary + per-query line."""
    q_total = sum(r["quality"]["total"] for r in results)
    d_total = sum(r["delight"]["total"] for r in results)
    max_pts = len(results) * 5
    q_pct = round(q_total / max_pts * 100) if max_pts else 0
    d_pct = round(d_total / max_pts * 100) if max_pts else 0

    print()
    print(f"\033[1m{'─' * 72}\033[0m")
    print(f"\033[1msurf Benchmark — {datetime.now().strftime('%Y-%m-%d %H:%M')}\033[0m")
    print(f"\033[1m{'─' * 72}\033[0m")

    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)

    print("\n\033[1mBy Category (quality / delight):\033[0m")
    for cat, rs in sorted(by_cat.items()):
        qa = round(sum(r["quality"]["pct"] for r in rs) / len(rs))
        da = round(sum(r["delight"]["pct"] for r in rs) / len(rs))
        bar = "█" * (qa // 10) + "░" * (10 - qa // 10)
        sym = PASS if qa >= 80 else (WARN if qa >= 60 else FAIL)
        print(f"  {sym} {cat:<20} {bar} {qa}% quality · {da}% delight")

    print("\n\033[1mQuery Results:\033[0m")
    for r in results:
        q = r["quality"]
        d = r["delight"]
        sym = PASS if q["pct"] >= 80 else (WARN if q["pct"] >= 60 else FAIL)
        q_notes = "; ".join(q["notes"]) if q["notes"] else ""
        d_notes = "; ".join(n for n in d["notes"] if not n.startswith("VOICE")) if d["notes"] else ""
        notes_str = ""
        if q_notes or d_notes:
            parts = []
            if q_notes:
                parts.append(q_notes)
            if d_notes:
                parts.append(f"\033[36m[delight: {d_notes}]\033[0m")
            notes_str = f"  \033[90m{'; '.join(parts)}\033[0m"
        print(f"  {sym} [{r['id']:<15}] Q:{q['pct']:3d}% D:{d['pct']:3d}%  {r['elapsed_s']}s  {r['query'][:40]}{notes_str}")

    print()
    print(f"\033[1m{'─' * 72}\033[0m")
    q_bar = "█" * (q_pct // 5) + "░" * (20 - q_pct // 5)
    d_bar = "█" * (d_pct // 5) + "░" * (20 - d_pct // 5)
    q_color = "\033[32m" if q_pct >= 90 else ("\033[33m" if q_pct >= 70 else "\033[31m")
    d_color = "\033[36m" if d_pct >= 80 else ("\033[33m" if d_pct >= 60 else "\033[31m")
    print(f"\033[1mQuality: {q_color}{q_pct}%\033[0m  {q_bar}  ({round(q_total,1)}/{max_pts} pts)")
    print(f"\033[1mDelight: {d_color}{d_pct}%\033[0m  {d_bar}  ({round(d_total,1)}/{max_pts} pts)")
    avg_t = round(sum(r["elapsed_s"] for r in results) / len(results), 1)
    print(f"Avg time: {avg_t}s  |  Queries: {len(results)}")
    print(f"\033[1m{'─' * 72}\033[0m\n")


def print_report(results: list[dict]) -> None:
    """Verbose report: full query → response → scores for every result."""
    width = 72
    sep = "═" * width
    thin = "─" * width

    print(f"\n\033[1msurf Full Response Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\033[0m\n")

    for r in results:
        q = r["quality"]
        d = r["delight"]
        sym = PASS if q["pct"] >= 80 else (WARN if q["pct"] >= 60 else FAIL)
        print(f"\033[1m{sep}\033[0m")
        print(f" {sym} [{r['id']}]  Q: {q['pct']}%  D: {d['pct']}%  {r['elapsed_s']}s  \033[90m{r['category']}\033[0m")
        print(f" \033[1m{r['query']}\033[0m")
        print(f"\033[90m{thin}\033[0m")

        # Print the response with a leading indent
        for line in r["response"].splitlines():
            print(f"  {line}")

        print(f"\033[90m{thin}\033[0m")

        # Quality notes
        if q["notes"]:
            print(f"  \033[31mQuality issues: {'; '.join(q['notes'])}\033[0m")

        # Delight notes
        d_non_voice = [n for n in d["notes"] if not n.startswith("VOICE")]
        if d_non_voice:
            print(f"  \033[33mDelight issues: {'; '.join(d_non_voice)}\033[0m")

        # Voice judgment
        voice_score = d.get("voice", 1)
        voice_reason = d.get("voice_reason", "—")
        voice_color = "\033[32m" if voice_score == 2 else ("\033[33m" if voice_score == 1 else "\033[31m")
        print(f"  {voice_color}Voice {voice_score}/2\033[0m — {voice_reason}")

        print()

    print_summary(results)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="surf quality + delight benchmark")
    parser.add_argument("--quick", type=int, metavar="N", help="Run only first N queries")
    parser.add_argument("--category", help="Run only queries in this category")
    parser.add_argument("--save", action="store_true", help="Save results to JSON")
    parser.add_argument("--report", action="store_true", help="Print full response report")
    args = parser.parse_args()

    with open(QUERIES_FILE) as f:
        queries = json.load(f)

    if args.category:
        queries = [q for q in queries if q.get("category") == args.category]
    if args.quick:
        queries = queries[:args.quick]

    print(f"\nRunning {len(queries)} queries against surf...\n")

    results = []
    for i, q in enumerate(queries, 1):
        print(f"  [{i:2d}/{len(queries)}] {q['id']:<18} {q['query'][:45]}", end="", flush=True)
        result = run_query(q)
        qp = result["quality"]["pct"]
        dp = result["delight"]["pct"]
        sym = PASS if qp >= 80 else (WARN if qp >= 60 else FAIL)
        print(f"  {sym} Q:{qp}% D:{dp}% ({result['elapsed_s']}s)")
        results.append(result)
        time.sleep(0.3)

    if args.report:
        print_report(results)
    else:
        print_summary(results)

    if args.save:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        fname = os.path.join(RESULTS_DIR, f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(fname, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Results saved to {fname}")

if __name__ == "__main__":
    main()
