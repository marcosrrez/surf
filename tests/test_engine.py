"""Tests for surf_engine — the unified event-generator pipeline."""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import surf
import surf_engine


def _results(n=5):
    domains = ["a.com", "b.org", "c.net", "d.io", "e.dev", "f.co", "g.gov", "h.edu"]
    return [{"domain": d, "url": f"https://{d}/x", "title": f"T {d}", "snippet": "S " * 20,
             "_quality": {"reliability": 0.7, "credibility": 0.7, "composite": 0.7}}
            for d in domains[:n]]


_INTENT_SNIPPET = {"route": "search", "tier": "snippet", "domain": "general",
                   "source_strategy": "any", "answer_depth": "concise",
                   "reformulated_query": "test query", "confidence": 0.9, "open_url": None}

_INTENT_RESEARCH = {**_INTENT_SNIPPET, "tier": "research", "answer_depth": "comprehensive"}


def _base_patches(intent, results):
    """Common patches: no network, no LLM, no disk writes."""
    return [
        patch("surf.assess_intent", return_value=dict(intent)),
        patch("surf._classify_data_source", return_value="web"),
        patch("surf._search_with_retry", return_value=(results, ["q"])),
        patch("surf._fix_entity_mismatch", side_effect=lambda q, r, d, evaluative_context=None: (r, d)),
        patch("surf._enrich_ddg_query", side_effect=lambda q, tier="", source_hint="": q),
        patch("surf._generate_subqueries", return_value=[]),
        patch("surf._quality_retry_search", return_value=[]),
        patch("surf.generate_related_searches", return_value=["follow up one?", "two?", "three?"]),
        patch("surf_engine._vault_retrieve", return_value=([], "")),
        patch("surf_engine.format_session_context", return_value=""),
        patch("surf_engine._read_preferences", return_value=""),
        patch("surf_engine.save_session_entry"),
        patch.object(surf, "_obsidian_save", lambda *a, **k: None),
    ]


def _run(query, intent, results, extra_patches=(), **kwargs):
    patches = _base_patches(intent, results) + list(extra_patches)
    for p in patches:
        p.start()
    try:
        return list(surf_engine.search_events(query, **kwargs))
    finally:
        # reverse order: doubly-patched attributes must unwind LIFO or the
        # earlier patch's stop() restores a stale mock into the module
        for p in reversed(patches):
            p.stop()


def _types(events):
    return [e["type"] for e in events]


class TestSnippetFlow:
    def test_event_sequence(self):
        events = _run(
            "what is a black hole", _INTENT_SNIPPET, _results(),
            extra_patches=[patch("surf.stream_ai", return_value=iter(["▸ TL;DR Dense. ", "More [1]."]))],
        )
        t = _types(events)
        assert t[0] == "status"
        assert "intent" in t
        assert "sources" in t
        assert "citemap" in t
        assert "token" in t
        assert "related" in t
        assert t[-1] == "done"
        # sources arrive before tokens
        assert t.index("sources") < t.index("token")

    def test_answer_text_assembled_from_tokens(self):
        events = _run(
            "what is a black hole", _INTENT_SNIPPET, _results(),
            extra_patches=[patch("surf.stream_ai", return_value=iter(["Hello ", "world"]))],
        )
        tokens = "".join(e["content"] for e in events if e["type"] == "token")
        assert tokens == "Hello world"

    def test_citemap_matches_sources(self):
        events = _run(
            "what is a black hole", _INTENT_SNIPPET, _results(3),
            extra_patches=[patch("surf.stream_ai", return_value=iter(["x"]))],
        )
        citemap = next(e["content"] for e in events if e["type"] == "citemap")
        assert [c["num"] for c in citemap] == [1, 2, 3]
        assert citemap[0]["domain"] == "a.com"

    def test_empty_query_errors(self):
        events = list(surf_engine.search_events("   "))
        assert events == [{"type": "error", "content": "Empty query"}]


class TestDeepFlow:
    def test_reading_and_commentary_events_stream_live(self):
        def fake_deep(query, tier, results, enriched="", entity_type=None, on_event=None):
            on_event({"type": "reading", "domain": "a.com", "quality": 0.8})
            on_event({"type": "commentary", "domain": "a.com", "comment": "solid", "num": 1, "quality": 0.8})
            src = dict(results[0])
            src["_text"] = "a.com body text with facts"
            return "[1] a.com\nbody", [src]

        events = _run(
            "how does mRNA work", _INTENT_RESEARCH, _results(),
            extra_patches=[
                patch("surf._deep_research", side_effect=fake_deep),
                patch("surf.stream_ai", return_value=iter(["▸ TL;DR It works [1]. ", "Body [1]."])),
                patch("surf._verify_citations", return_value={"checked": 2, "supported": 2, "claims": []}),
            ],
        )
        t = _types(events)
        assert "reading" in t and "commentary" in t
        assert t.index("reading") < t.index("token")
        assert "verification" in t
        ver = next(e["content"] for e in events if e["type"] == "verification")
        assert ver["supported"] == 2

    def test_citemap_leads_with_deep_sources(self):
        def fake_deep(query, tier, results, enriched="", entity_type=None, on_event=None):
            src = dict(results[2])  # c.net read first despite rank 3
            src["_text"] = "text"
            return "[1] c.net\ntext", [src]

        events = _run(
            "how does mRNA work", _INTENT_RESEARCH, _results(4),
            extra_patches=[
                patch("surf._deep_research", side_effect=fake_deep),
                patch("surf.stream_ai", return_value=iter(["x [1]"])),
                patch("surf._verify_citations", return_value={}),
            ],
        )
        citemap = next(e["content"] for e in events if e["type"] == "citemap")
        assert citemap[0]["domain"] == "c.net"  # [1] resolves to what the model actually read

    def test_fanout_merges_new_domains(self):
        captured = {}

        def fake_rank(query, results, intent=None):
            captured["domains"] = [r["domain"] for r in results]
            return results

        events = _run(
            "how does mRNA work", _INTENT_RESEARCH, _results(2),
            extra_patches=[
                patch("surf._generate_subqueries", return_value=["facet one", "facet two"]),
                patch("surf._fanout_search", return_value=[
                    {"domain": "new.com", "url": "https://new.com/1", "title": "N", "snippet": "s" * 30},
                    {"domain": "a.com", "url": "https://a.com/dup", "title": "D", "snippet": "s" * 30},
                ]),
                patch("surf._deep_research", return_value=("", [])),
                patch("surf.stream_ai", return_value=iter(["x"])),
                patch("surf.filter_and_rank_results", side_effect=fake_rank),
            ],
        )
        assert "new.com" in captured["domains"]
        assert captured["domains"].count("a.com") == 1  # deduped


class TestSpecializedFlow:
    def test_weather_becomes_answer_card_with_ansi_stripped(self):
        weather_text = "\033[36m▸ TL;DR  Now 18°C clear\033[0m\n\n  Now  18°C"
        src = [{"domain": "open-meteo.com", "url": "https://open-meteo.com/", "title": "W", "snippet": "s"}]
        events = _run(
            "weather in London today", _INTENT_SNIPPET, _results(),
            extra_patches=[
                patch("surf._classify_data_source", return_value="weather"),
                patch("surf._handle_weather", return_value=(weather_text, src, False)),
            ],
        )
        t = _types(events)
        assert "answer_card" in t and t[-1] == "done"
        card = next(e["content"] for e in events if e["type"] == "answer_card")
        assert card["kind"] == "weather"
        assert "\033[" not in card["text"]
        assert "18°C" in card["text"]

    def test_failed_handler_falls_through_to_search(self):
        events = _run(
            "weather in London today", _INTENT_SNIPPET, _results(),
            extra_patches=[
                patch("surf._classify_data_source", return_value="weather"),
                patch("surf._handle_weather", return_value=None),
                patch("surf.stream_ai", return_value=iter(["fallback answer"])),
            ],
        )
        t = _types(events)
        assert "answer_card" not in t
        assert "token" in t and t[-1] == "done"

    def test_academic_synthesis_streams_tokens(self):
        def fake_academic(query, on_token=None):
            for tok in ["Papers ", "say ", "yes."]:
                on_token(tok)
            src = [{"domain": "arxiv.org", "url": "https://arxiv.org/1", "title": "P", "snippet": "s"}]
            return "Papers say yes.", src, True

        events = _run(
            "peer reviewed studies on sleep", _INTENT_SNIPPET, _results(),
            extra_patches=[
                patch("surf._classify_data_source", return_value="academic"),
                patch("surf._handle_academic", side_effect=fake_academic),
            ],
        )
        tokens = "".join(e["content"] for e in events if e["type"] == "token")
        assert tokens == "Papers say yes."
        assert _types(events)[-1] == "done"


class TestFollowupContext:
    def test_context_reaches_intent_assessment(self):
        captured = {}

        def fake_assess(query, vault_depth=0, session_context=""):
            captured["ctx"] = session_context
            return dict(_INTENT_SNIPPET)

        events = _run(
            "who replaced her", _INTENT_SNIPPET, _results(),
            extra_patches=[
                patch("surf.assess_intent", side_effect=fake_assess),
                patch("surf.stream_ai", return_value=iter(["x"])),
            ],
            context="Q: who is the ECB president\nA: Christine Lagarde leads the ECB.",
        )
        assert "Lagarde" in captured["ctx"]
        assert _types(events)[-1] == "done"


class TestEnginePureHelpers:
    def test_strip_ansi(self):
        assert surf_engine._strip_ansi("\033[36mhi\033[0m") == "hi"

    def test_passage_selection_prefers_relevant_paragraphs(self):
        relevant = "Rust ownership means every value has one owner enforced by the borrow checker. " * 3
        noise = "A completely different paragraph about gardening tulips in spring weather. " * 3
        content = "\n\n".join([noise, relevant, noise, relevant, noise, noise])
        out = surf._select_relevant_passages("rust ownership borrow checker", content, max_chars=600)
        assert "ownership" in out
        assert "tulips" not in out

    def test_extract_cited_claims(self):
        claims = surf._extract_cited_claims("Rust is memory safe [1]. No cite here. Both matter [2][3].")
        assert claims == [("Rust is memory safe.", [1]), ("Both matter.", [2, 3])]

    def test_merged_search_dedupes_and_keeps_primary_order(self):
        prim = [{"url": "https://a.com/x", "domain": "a.com", "title": "t", "snippet": "s"}]
        sec = [{"url": "https://a.com/x/", "domain": "a.com", "title": "t", "snippet": "s"},
               {"url": "https://b.com/y", "domain": "b.com", "title": "t", "snippet": "s"}]
        with patch("surf._get_search_backend", return_value=surf.tavily_search), \
             patch("surf.tavily_search", side_effect=lambda q, n=10: list(prim)), \
             patch("surf.ddg_search", return_value=list(sec)):
            merged = surf._merged_search("q")
        assert [r["domain"] for r in merged] == ["a.com", "b.com"]

    def test_merged_search_single_engine_when_only_ddg(self):
        with patch("surf._get_search_backend", return_value=surf.ddg_search), \
             patch("surf.ddg_search", return_value=[{"url": "https://a.com", "domain": "a.com",
                                                     "title": "t", "snippet": "s"}]) :
            merged = surf._merged_search("q")
        assert len(merged) == 1

    def test_verify_citations_parses_verdicts(self):
        answer = "The sky is blue because of Rayleigh scattering [1]. Cats have nine lives [2]."
        src = {1: "Rayleigh scattering explains the blue sky.", 2: "Cats are mammals."}
        fake = iter(['[{"claim": 1, "verdict": "supported"}, {"claim": 2, "verdict": "unsupported"}]'])
        with patch("surf.stream_ai", return_value=fake):
            out = surf._verify_citations(answer, src)
        assert out["checked"] == 2
        assert out["supported"] == 1
        assert out["claims"][1]["verdict"] == "unsupported"

    def test_verify_citations_empty_on_garbage(self):
        with patch("surf.stream_ai", return_value=iter(["not json at all"])):
            out = surf._verify_citations("Claim here [1].", {1: "text"})
        assert out == {}

    def test_generate_subqueries_only_for_deep_tiers(self):
        assert surf._generate_subqueries("q", "snippet") == []
        with patch("surf.stream_groq", return_value=iter(['["facet one query", "facet two query"]'])):
            subs = surf._generate_subqueries("how does X work", "research")
        assert subs == ["facet one query", "facet two query"]

    def test_generate_related_searches_parses_json(self):
        with patch("surf.stream_groq", return_value=iter(['["What about A?", "How does B?", "Why C?"]'])):
            rel = surf.generate_related_searches("query", "answer text")
        assert len(rel) == 3
