#!/usr/bin/env python3
"""
surf Quality Benchmark
Measures answer accuracy, completeness, honesty, source quality, and structure.
Run: cd ~/termbrowser && .venv/bin/python3 tests/quality/benchmark.py [--quick N]
"""
import sys
import json
import time
import argparse
import os
from datetime import datetime

sys.path.insert(0, '/Users/marcos/termbrowser')

from surf import (
    ddg_search, stream_groq, build_search_prompt, SEARCH_SYSTEM,
    detect_input_type, fetch_page, extract_text, _is_spa_shell,
    _fetch_with_jina, _filter_results, _identify_entity_type,
    read_flow, classify_intent, CLASSIFIER_MODEL, _has_uncertainty
)

QUERIES_FILE = os.path.join(os.path.dirname(__file__), 'queries.json')
RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')

# ─── Scoring ──────────────────────────────────────────────────────────────────

def score_response(query_def: dict, response: str, sources: list[str]) -> dict:
    """Score a response against its query definition. Returns score dict."""
    scores = {}
    notes = []
    response_lower = response.lower()
    word_count = len(response.split())

    # 1. Structure: Has TL;DR if required
    has_tldr = "▸ TL;DR" in response or "tldr" in response_lower[:100]
    if query_def.get("must_have_tldr", True):
        scores["structure"] = 1 if has_tldr else 0
        if not has_tldr:
            notes.append("MISSING TL;DR")
    else:
        scores["structure"] = 1  # not required, pass

    # 2. Accuracy: Expected strings present
    expected = query_def.get("expected_contains", [])
    if expected:
        def _flexible_match(expected_str, response_lower):
            if expected_str.lower() in response_lower:
                return True
            # Number/unit equivalences
            equivalences = {
                "eight minutes": ["8 minutes", "eight minutes", "8.3 min"],
                "8.3": ["8.3", "8 minutes", "eight minutes"],
                "300,000": ["300,000", "299,792", "299,000"],
                "186,000": ["186,000", "186,282"],
                "gravity": ["gravity", "gravitational", "gravitation"],
                "monty python": ["monty python", "python comedy"],
                "1945": ["1945", "may 8", "august 15", "september 2"],
                "trump": ["trump", "donald trump", "president trump"],
                "eight": ["eight", "8 "],
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

    # 3. Honesty: For unanswerable queries
    if query_def.get("expected_honest"):
        honest_signals = query_def.get("honest_signals", [])
        is_honest = any(s in response_lower for s in honest_signals)
        scores["honesty"] = 1 if is_honest else 0
        if not is_honest:
            notes.append("NOT HONEST ABOUT LIMITATIONS")
    else:
        scores["honesty"] = 1  # not applicable

    # 4. Source quality: No banned domains
    banned = query_def.get("banned_domains", [])
    spam_found = [d for d in sources if any(b in d for b in banned)]
    scores["source_quality"] = 0 if spam_found else 1
    if spam_found:
        notes.append(f"SPAM SOURCES: {spam_found}")

    # Check authoritative sources if required
    auth_sources = query_def.get("expected_sources_authoritative", [])
    if auth_sources:
        min_sources = query_def.get("min_sources", 1)
        has_auth = sum(1 for s in sources if any(a in s for a in auth_sources))
        if has_auth < min_sources:
            scores["source_quality"] = max(0, scores["source_quality"] - 0.5)
            notes.append(f"LOW AUTHORITY SOURCES (found {has_auth}/{min_sources} needed)")

    # 5. Completeness: Word count in range
    min_words = query_def.get("min_words", 10)
    max_words = query_def.get("max_words", 800)
    if word_count < min_words:
        scores["completeness"] = 0
        notes.append(f"TOO SHORT ({word_count} words, min {min_words})")
    elif word_count > max_words:
        scores["completeness"] = 0.5  # verbose but not wrong
        notes.append(f"TOO LONG ({word_count} words, max {max_words})")
    else:
        scores["completeness"] = 1

    # Overall score (0-5)
    total = sum(scores.values())
    scores["total"] = round(total, 2)
    scores["max"] = 5
    scores["pct"] = round(total / 5 * 100)
    scores["notes"] = notes
    return scores


def run_query(query_def: dict) -> dict:
    """Run a single query through surf's pipeline and return results."""
    query = query_def["query"]
    category = query_def.get("category", "unknown")
    start = time.time()
    response = ""
    sources = []

    try:
        if category == "url_read":
            # URL read flow
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
            from surf import READ_SYSTEM
            chunks = list(stream_groq(prompt, READ_SYSTEM, max_tokens=1000))
            response = "".join(chunks)
            sources = [query.split("/")[0]]
        else:
            # Search flow — mirror surf.search_flow's news source boost
            results = _filter_results(ddg_search(query, num_results=5))

            # For news/current-events queries, explicitly check authoritative news sources
            # — only if main results lack reuters/apnews/bbc to keep request rate low
            news_signals = ["news", "latest", "today", "2026", "breaking", "current", "update"]
            is_news_query = any(s in query.lower() for s in news_signals)
            auth_news_domains = ("reuters.com", "apnews.com", "bbc.com",
                                 "bloomberg.com", "wsj.com", "nytimes.com")
            already_has_auth = any(
                any(a in r.get("domain", "") for a in auth_news_domains)
                for r in results
            )
            if is_news_query and not already_has_auth:
                # Single combined targeted query to minimize DDG hits
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

            # Mirror search_flow's verification: if uncertain, fetch top result
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
                                f"Please provide the correct, definitive answer with specific facts. "
                                f"If the venue, date, or any key fact was listed as TBD in the earlier answer, correct it now."
                            )
                            verify_chunks = list(stream_groq(verify_prompt, SEARCH_SYSTEM, max_tokens=1500))
                            response = "".join(verify_chunks)
                    except Exception:
                        pass
    except Exception as e:
        response = f"[ERROR: {e}]"
        sources = []

    elapsed = round(time.time() - start, 1)
    scores = score_response(query_def, response, sources)

    return {
        "id": query_def["id"],
        "query": query,
        "category": category,
        "response_preview": response[:200].replace("\n", " "),
        "sources": sources[:5],
        "elapsed_s": elapsed,
        "word_count": len(response.split()),
        "scores": scores,
    }


# ─── Report ───────────────────────────────────────────────────────────────────

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m~\033[0m"

def print_report(results: list[dict]) -> None:
    total_score = sum(r["scores"]["total"] for r in results)
    max_score = len(results) * 5
    overall_pct = round(total_score / max_score * 100) if max_score else 0

    print()
    print(f"\033[1m{'─' * 70}\033[0m")
    print(f"\033[1msurf Quality Benchmark — {datetime.now().strftime('%Y-%m-%d %H:%M')}\033[0m")
    print(f"\033[1m{'─' * 70}\033[0m")

    # Category breakdown
    by_cat = {}
    for r in results:
        cat = r["category"]
        by_cat.setdefault(cat, []).append(r["scores"]["pct"])

    print("\n\033[1mBy Category:\033[0m")
    for cat, pcts in sorted(by_cat.items()):
        avg = round(sum(pcts) / len(pcts))
        bar = "█" * (avg // 10) + "░" * (10 - avg // 10)
        symbol = PASS if avg >= 80 else (WARN if avg >= 60 else FAIL)
        print(f"  {symbol} {cat:<20} {bar} {avg}%")

    # Individual results
    print("\n\033[1mQuery Results:\033[0m")
    for r in results:
        s = r["scores"]
        pct = s["pct"]
        symbol = PASS if pct >= 80 else (WARN if pct >= 60 else FAIL)
        notes = f"  \033[90m{'; '.join(s['notes'])}\033[0m" if s["notes"] else ""
        print(f"  {symbol} [{r['id']:<15}] {pct:3d}%  {r['elapsed_s']}s  {r['query'][:45]}{notes}")

    # Overall
    print()
    print(f"\033[1m{'─' * 70}\033[0m")
    bar = "█" * (overall_pct // 5) + "░" * (20 - overall_pct // 5)
    color = "\033[32m" if overall_pct >= 80 else ("\033[33m" if overall_pct >= 60 else "\033[31m")
    print(f"\033[1mOverall Score: {color}{overall_pct}%\033[0m  {bar}  ({round(total_score, 1)}/{max_score} pts)")
    avg_time = round(sum(r["elapsed_s"] for r in results) / len(results), 1)
    print(f"Avg response time: {avg_time}s  |  Queries: {len(results)}")
    print(f"\033[1m{'─' * 70}\033[0m")
    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="surf quality benchmark")
    parser.add_argument("--quick", type=int, metavar="N",
                        help="Run only first N queries (for fast spot-checking)")
    parser.add_argument("--category", help="Run only queries in this category")
    parser.add_argument("--save", action="store_true", help="Save results to JSON")
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
        print(f"  [{i:2d}/{len(queries)}] {q['id']:<18} {q['query'][:50]}", end="", flush=True)
        result = run_query(q)
        pct = result["scores"]["pct"]
        symbol = PASS if pct >= 80 else (WARN if pct >= 60 else FAIL)
        print(f"  {symbol} {pct}% ({result['elapsed_s']}s)")
        results.append(result)
        time.sleep(0.5)  # brief pause between API calls

    print_report(results)

    if args.save:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        fname = os.path.join(RESULTS_DIR, f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(fname, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to {fname}")

if __name__ == "__main__":
    main()
