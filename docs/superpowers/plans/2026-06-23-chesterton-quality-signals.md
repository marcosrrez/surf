# Chesterton Source Quality — Missing Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the 9 missing source quality signals from the Chesterton Source Quality Framework research, transforming `score_source_quality` from a snippet-only heuristic into a two-axis quality engine that evaluates actual content.

**Architecture:** Two-phase scoring. Phase 1 (snippet-level, zero cost) adds recency extraction, clickbait detection, and structural signals to the existing `score_source_quality()`. Phase 2 (content-level, runs after page fetch) adds depth scoring, "shows its work" detection, and feeds the Chesterton commentary assessment back into quality scores. Both phases compose into a NATO Admiralty-inspired two-axis score: source reliability × information credibility.

**Tech Stack:** Python 3.14, regex for zero-cost signals, Groq Llama 3.1 8B for content assessment. No new dependencies.

## Global Constraints

- All snippet-level signals must be zero-cost (regex/string ops, no LLM)
- Content-level signals run only for research/contested tier (intent-gated)
- The existing `score_source_quality()` return type changes from `float` to `dict` with `reliability` and `credibility` axes
- `filter_and_rank_results()` must still work with a single composite score derived from the two axes
- All changes in `surf.py` — no new files
- Tests in `tests/test_surf.py`
- Pre-existing test failures: `test_returns_results_and_response` (search_flow signature) and `test_make_note_slug_sanitizes_query` (stop word change) — ignore these

---

### Task 1: Two-Axis Quality Score — NATO Admiralty Model

**Files:**
- Modify: `surf.py:1214-1260` (score_source_quality)
- Modify: `surf.py:1263-1301` (filter_and_rank_results)
- Test: `tests/test_surf.py`

**Interfaces:**
- Produces: `score_source_quality()` returns `dict` with keys `reliability: float`, `credibility: float`, `composite: float` (all 0.0–1.0)
- Produces: `filter_and_rank_results()` stores `_quality` dict on each result instead of `_quality_score` float

- [ ] **Step 1: Write failing tests for two-axis return type**

```python
class TestSourceQualityTwoAxis:
    def test_returns_dict_with_two_axes(self):
        result = {"domain": "nature.com", "url": "https://nature.com/articles/123", "snippet": "A study of 500 participants found...", "title": "Research findings"}
        score = score_source_quality(result, domain="science")
        assert isinstance(score, dict)
        assert "reliability" in score
        assert "credibility" in score
        assert "composite" in score
        assert 0.0 <= score["reliability"] <= 1.0
        assert 0.0 <= score["credibility"] <= 1.0
        assert 0.0 <= score["composite"] <= 1.0

    def test_spam_domain_scores_zero_on_both_axes(self):
        result = {"domain": "quickapedia.com", "url": "https://quickapedia.com/health", "snippet": "Top 10 tips", "title": "Tips"}
        score = score_source_quality(result)
        assert score["reliability"] == 0.0
        assert score["credibility"] == 0.0
        assert score["composite"] == 0.0

    def test_high_reliability_low_credibility(self):
        # Nature domain (reliable) but snippet is a thin editorial with no data
        result = {"domain": "nature.com", "url": "https://nature.com/opinion/123", "snippet": "In our opinion, this matters for society.", "title": "Opinion: Why this matters"}
        score = score_source_quality(result, domain="science")
        assert score["reliability"] >= 0.6  # nature.com is reliable
        assert score["credibility"] < score["reliability"]  # but this piece has no evidence

    def test_low_reliability_has_data(self):
        # Unknown blog but snippet is full of data
        result = {"domain": "randomhealth.blog", "url": "https://randomhealth.blog/fasting", "snippet": "A 2024 study of 20,000 adults found 91% higher cardiovascular mortality risk according to researchers at AHA.", "title": "Fasting study results"}
        score = score_source_quality(result, domain="medical")
        assert score["credibility"] > score["reliability"]  # content is data-rich even though source is unknown
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSourceQualityTwoAxis -v`
Expected: FAIL — score_source_quality returns float, not dict

- [ ] **Step 3: Refactor score_source_quality to return two-axis dict**

Split the existing signals into two axes:

**Reliability axis** (is this source generally trustworthy?):
- Domain authority boosts (SOURCE_HIERARCHY, _QUALITY_DOMAIN_BOOSTS, .gov/.edu)
- Allowlisted domains
- Affiliate URL demote
- Spam domain reject
- Regulatory domain boost

**Credibility axis** (does THIS SPECIFIC piece show evidence?):
- Factual density (numbers, dates, percentages)
- Attribution density ("according to", "study found")
- Marketing vocabulary ratio (demote)
- Listicle pattern (demote)
- Promotional snippet signals (demote)
- Data vocabulary signals (boost)

```python
def score_source_quality(result: dict, domain: str = "general", source_strategy: str = "any") -> dict:
    """Two-axis quality score: source reliability × information credibility.
    
    Returns dict with reliability (0.0-1.0), credibility (0.0-1.0), composite (0.0-1.0).
    """
    url = (result.get("url", "") + " " + result.get("domain", "")).lower()
    snippet = (result.get("snippet", "") + " " + result.get("title", "")).lower()
    rdomain = result.get("domain", "").lower()

    if rdomain in _SPAM_DOMAINS:
        return {"reliability": 0.0, "credibility": 0.0, "composite": 0.0}

    # ── Reliability axis (source-level trust) ──
    rel = 0.5
    if rdomain in _ALLOWLISTED_DOMAINS:
        rel = 0.7
    if any(s in url for s in _AFFILIATE_URL_SIGNALS):
        rel -= 0.30
    if any(s in url for s in _REGULATORY_DOMAIN_SIGNALS):
        rel += 0.20
    if domain in _QUALITY_DOMAIN_BOOSTS:
        if any(d in url for d in _QUALITY_DOMAIN_BOOSTS[domain]):
            rel += 0.20
    if source_strategy == "academic" and any(d in url for d in ("arxiv", "pubmed", "pmc.ncbi", "scholar", "jstor")):
        rel += 0.15
    if source_strategy == "authoritative" and any(d in url for d in (".gov", ".edu", "reuters", "apnews")):
        rel += 0.15
    if source_strategy == "official" and ".gov" in url:
        rel += 0.20
    rel = max(0.0, min(1.0, rel))

    # ── Credibility axis (content-level evidence) ──
    cred = 0.5
    if any(s in snippet for s in _COMPANY_PROMO_SIGNALS):
        cred -= 0.20
    marketing_hits = sum(1 for p in _MARKETING_VOCAB if p in snippet)
    if marketing_hits >= 2:
        cred -= 0.15
    if _LISTICLE_RE.search(result.get("title", "")):
        cred -= 0.15
    factual_hits = len(_FACTUAL_DENSITY_RE.findall(snippet))
    cred += min(0.25, factual_hits * 0.06)
    attr_hits = sum(1 for p in _ATTRIBUTION_PHRASES if p in snippet)
    cred += min(0.20, attr_hits * 0.10)
    data_hits = sum(1 for s in _DATA_SNIPPET_SIGNALS if s in snippet)
    cred += min(0.15, data_hits * 0.05)
    cred = max(0.0, min(1.0, cred))

    composite = 0.45 * rel + 0.55 * cred
    return {"reliability": round(rel, 2), "credibility": round(cred, 2), "composite": round(composite, 2)}
```

- [ ] **Step 4: Update filter_and_rank_results to use new dict return**

```python
# In filter_and_rank_results, change:
quality = score_source_quality(r, domain=domain, source_strategy=source_strategy)
# to use quality["composite"] for ranking:
quality_dict = score_source_quality(r, domain=domain, source_strategy=source_strategy)
quality = quality_dict["composite"]
# ...
r["_quality"] = quality_dict  # store full dict instead of float
```

Also update `_deep_research` quality calls:
```python
quality = score_source_quality(r, domain=_qdomain)
# becomes:
quality_dict = score_source_quality(r, domain=_qdomain)
quality = quality_dict["composite"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSourceQualityTwoAxis -v`
Expected: 4 PASS

- [ ] **Step 6: Run full test suite for regressions**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -k "not test_returns_results and not test_make_note_slug" -v`
Expected: All pass (130+)

- [ ] **Step 7: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py
git commit -m "refactor: two-axis quality score (NATO Admiralty model) — reliability × credibility"
```

---

### Task 2: Publication Recency Signal

**Files:**
- Modify: `surf.py:1187-1197` (add recency regex near other signal constants)
- Modify: `surf.py` score_source_quality credibility axis
- Test: `tests/test_surf.py`

**Interfaces:**
- Consumes: `score_source_quality()` dict return from Task 1
- Produces: recency signal integrated into credibility axis

- [ ] **Step 1: Write failing tests**

```python
class TestRecencySignal:
    def test_recent_date_boosts_credibility(self):
        result = {"domain": "example.com", "url": "https://example.com/article", "snippet": "Published June 2026. A study found that 40% of participants...", "title": "New research"}
        score = score_source_quality(result)
        assert score["credibility"] >= 0.6

    def test_old_date_no_boost(self):
        result = {"domain": "example.com", "url": "https://example.com/article", "snippet": "Published 2018. This article discusses general health.", "title": "Health tips"}
        score = score_source_quality(result)
        score_no_date = score_source_quality({"domain": "example.com", "url": "https://example.com/other", "snippet": "This article discusses general health.", "title": "Health tips"})
        assert score["credibility"] <= score_no_date["credibility"] + 0.01  # old date doesn't boost

    def test_current_year_in_snippet_boosts(self):
        import time
        year = time.strftime("%Y")
        result = {"domain": "example.com", "url": "https://example.com/article", "snippet": f"Updated {year}. According to researchers, the data shows 25% improvement.", "title": "Research update"}
        score = score_source_quality(result)
        assert score["credibility"] >= 0.65
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestRecencySignal -v`
Expected: FAIL — recency not yet factored into credibility

- [ ] **Step 3: Add recency extraction and scoring**

Add regex constant near `_FACTUAL_DENSITY_RE`:

```python
_RECENCY_RE = re.compile(
    r"(?:published|updated|posted|dated|as of)\s+(?:"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}"
    r"|\d{4})"
    r"|(?:20[12]\d)\b",
    re.IGNORECASE,
)
```

Add recency scoring inside the credibility axis of `score_source_quality`:

```python
    # Recency: boost for current/recent year, neutral for older
    import time as _time
    _current_year = int(_time.strftime("%Y"))
    _years_found = [int(y) for y in re.findall(r"\b(20[12]\d)\b", snippet)]
    if _years_found:
        _newest = max(_years_found)
        if _newest >= _current_year:
            cred += 0.10
        elif _newest >= _current_year - 1:
            cred += 0.05
        elif _newest <= _current_year - 5:
            cred -= 0.05
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestRecencySignal -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py
git commit -m "feat: publication recency signal in credibility axis"
```

---

### Task 3: Content Depth Scoring (Post-Fetch)

**Files:**
- Modify: `surf.py` (add `score_content_depth()`, update `_deep_research` to use it)
- Test: `tests/test_surf.py`

**Interfaces:**
- Consumes: fetched page content (string) from `_read_one_source`
- Produces: `score_content_depth(content) -> float` (0.0–1.0), updates `_quality` dict on result

- [ ] **Step 1: Write failing tests**

```python
class TestContentDepthScoring:
    def test_long_structured_content_scores_high(self):
        content = "# Introduction\n\n" + "This is a detailed paragraph with data. " * 50 + "\n\n## Methodology\n\n" + "We studied 500 participants. " * 30 + "\n\n## Results\n\n" + "The findings show 40% improvement. " * 30
        score = score_content_depth(content)
        assert score >= 0.7

    def test_short_listicle_scores_low(self):
        content = "1. Tip one\n2. Tip two\n3. Tip three\n4. Tip four\n5. Tip five"
        score = score_content_depth(content)
        assert score <= 0.3

    def test_moderate_content_scores_mid(self):
        content = "This article covers the topic. " * 40 + "\n\n" + "Another point is made here. " * 30
        score = score_content_depth(content)
        assert 0.3 <= score <= 0.7

    def test_content_with_methodology_gets_boost(self):
        with_method = "## Methodology\nWe conducted a randomized controlled trial with 200 participants over 12 months. " + "Details follow. " * 40
        without_method = "Here are some general thoughts about the topic. " * 50
        assert score_content_depth(with_method) > score_content_depth(without_method)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestContentDepthScoring -v`
Expected: FAIL — function not defined

- [ ] **Step 3: Implement score_content_depth**

Add after `score_source_quality`:

```python
_DEPTH_METHODOLOGY_SIGNALS = frozenset([
    "methodology", "method", "participants", "sample size", "randomized",
    "controlled trial", "we conducted", "we analyzed", "we measured",
    "data collection", "statistical analysis", "p-value", "confidence interval",
])
_DEPTH_LIMITATION_SIGNALS = frozenset([
    "limitation", "caveat", "however", "although", "despite",
    "future research", "this study does not", "one weakness",
    "should be interpreted with caution", "cannot conclude",
])


def score_content_depth(content: str) -> float:
    """Score 0.0-1.0 for fetched page content depth. Zero LLM cost."""
    if not content:
        return 0.0
    words = content.split()
    word_count = len(words)
    content_lower = content.lower()

    score = 0.3  # baseline

    # Word count tiers
    if word_count >= 2000:
        score += 0.25
    elif word_count >= 1000:
        score += 0.15
    elif word_count >= 500:
        score += 0.05
    elif word_count < 200:
        score -= 0.15

    # Heading density (structure)
    headings = len(re.findall(r"^#{1,3}\s|\n[A-Z][A-Z\s]{3,}[A-Z]\n", content))
    if headings >= 3:
        score += 0.10
    elif headings == 0 and word_count > 500:
        score -= 0.05

    # Methodology / shows its work
    method_hits = sum(1 for s in _DEPTH_METHODOLOGY_SIGNALS if s in content_lower)
    score += min(0.15, method_hits * 0.04)

    # Acknowledges limitations
    limit_hits = sum(1 for s in _DEPTH_LIMITATION_SIGNALS if s in content_lower)
    score += min(0.10, limit_hits * 0.05)

    # Factual density in body
    factual_hits = len(_FACTUAL_DENSITY_RE.findall(content_lower[:3000]))
    score += min(0.10, factual_hits * 0.01)

    return max(0.0, min(1.0, score))
```

- [ ] **Step 4: Wire into _deep_research — update quality dict after fetch**

In `_deep_research`, after a page is fetched and before it's added to `combined`, score its depth and merge into the result's quality dict:

```python
    if fetched:
        commentary = _chesterton_evaluate_sources(query, fetched, _qdomain)
        for idx, domain, content, r in fetched:
            depth = score_content_depth(content)
            # Merge content depth into quality dict
            if "_quality" in r:
                r["_quality"]["depth"] = depth
                r["_quality"]["credibility"] = round(0.6 * r["_quality"]["credibility"] + 0.4 * depth, 2)
                r["_quality"]["composite"] = round(0.45 * r["_quality"]["reliability"] + 0.55 * r["_quality"]["credibility"], 2)
            comment = commentary.get(idx, "")
            # ... rest unchanged
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestContentDepthScoring -v`
Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py
git commit -m "feat: content depth scoring — word count, structure, methodology, limitations"
```

---

### Task 4: Title-Content Alignment (Clickbait Detection)

**Files:**
- Modify: `surf.py` score_source_quality credibility axis
- Test: `tests/test_surf.py`

**Interfaces:**
- Consumes: `result["title"]` and `result["snippet"]` already available
- Produces: clickbait penalty integrated into credibility axis

- [ ] **Step 1: Write failing tests**

```python
class TestClickbaitDetection:
    def test_aligned_title_no_penalty(self):
        result = {"domain": "example.com", "url": "https://example.com/x", "title": "Intermittent fasting cardiovascular study results", "snippet": "A 2024 study of 20,000 adults examined cardiovascular outcomes of intermittent fasting protocols."}
        score = score_source_quality(result)
        base = score_source_quality({"domain": "example.com", "url": "https://example.com/y", "title": "Study results", "snippet": "A 2024 study of 20,000 adults examined cardiovascular outcomes."})
        assert score["credibility"] >= base["credibility"] - 0.05  # aligned, no penalty

    def test_clickbait_title_gets_penalty(self):
        result = {"domain": "example.com", "url": "https://example.com/x", "title": "You Won't BELIEVE What This Study Found About Fasting!!!", "snippet": "General information about intermittent fasting and health."}
        score = score_source_quality(result)
        assert score["credibility"] < 0.5

    def test_sensational_title_gets_penalty(self):
        result = {"domain": "example.com", "url": "https://example.com/x", "title": "SHOCKING: Doctors HATE This One Simple Trick", "snippet": "Some dietary advice about meal timing."}
        score = score_source_quality(result)
        assert score["credibility"] < 0.45
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestClickbaitDetection -v`
Expected: FAIL — clickbait signals not detected

- [ ] **Step 3: Add clickbait detection signals**

Add regex constant:

```python
_CLICKBAIT_RE = re.compile(
    r"you won't believe"
    r"|shocking"
    r"|doctors hate"
    r"|one (?:simple|weird) trick"
    r"|this (?:one|simple) (?:trick|hack)"
    r"|what happened next"
    r"|mind.?blowing"
    r"|!!!+"
    r"|[A-Z]{5,}"  # excessive caps
    r"|😱|🤯|💥",  # sensationalist emoji
    re.IGNORECASE,
)
```

Add to credibility axis in `score_source_quality`:

```python
    # Clickbait: sensationalist title patterns
    title = result.get("title", "")
    clickbait_hits = len(_CLICKBAIT_RE.findall(title))
    if clickbait_hits >= 1:
        cred -= 0.15
    if clickbait_hits >= 2:
        cred -= 0.10  # additional penalty for extreme clickbait
```

- [ ] **Step 4: Run tests**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestClickbaitDetection -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py
git commit -m "feat: clickbait detection in credibility axis"
```

---

### Task 5: Commentary Feedback Loop — Chesterton Assessment Feeds Quality Score

**Files:**
- Modify: `surf.py` `_chesterton_evaluate_sources` and `_deep_research`
- Test: `tests/test_surf.py`

**Interfaces:**
- Consumes: `_chesterton_evaluate_sources()` return value
- Produces: LLM quality assessment merged back into `_quality` dict per source

- [ ] **Step 1: Write failing test**

```python
class TestCommentaryFeedback:
    def test_chesterton_eval_returns_scores(self):
        # Mock the LLM call to return numbered commentary with quality signals
        with patch("surf.stream_groq") as mock_groq:
            mock_groq.return_value = iter(["1. Rigorous methodology with 500 participants — proper scholarship. [8/10]\n2. Marketing fluff with no data. [2/10]"])
            fetched = [
                (0, "pubmed.ncbi.nlm.nih.gov", "A study of 500...", {"domain": "pubmed.ncbi.nlm.nih.gov", "url": "https://pubmed.ncbi.nlm.nih.gov/123"}),
                (1, "healthblog.com", "Try these tips...", {"domain": "healthblog.com", "url": "https://healthblog.com/tips"}),
            ]
            commentary = _chesterton_evaluate_sources("test query", fetched)
            assert isinstance(commentary, dict)
            # Commentary should include scores when available
            assert 0 in commentary or 1 in commentary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestCommentaryFeedback -v`
Expected: FAIL

- [ ] **Step 3: Update _chesterton_evaluate_sources to extract quality scores**

Modify the `_CHESTERTON_EVAL_SYSTEM` prompt to request a score:

```python
_CHESTERTON_EVAL_SYSTEM = """You are G.K. Chesterton evaluating research sources. For each source, write ONE sentence (max 15 words) reacting to the ACTUAL CONTENT — not just the source name. End each line with a quality score in brackets: [N/10].

Voice: witty, direct, generous when deserved, devastating when not. React to what you SEE in the content.

Return ONLY numbered lines. Example:
1. A study of 560 participants with actual methodology — now this is scholarship. [9/10]
2. Three paragraphs without a single data point — remarkable avoidance of evidence. [2/10]
3. Buried in paragraph four is a finding that reframes the question entirely. [7/10]"""
```

Update the return type to include scores:

```python
def _chesterton_evaluate_sources(query: str, fetched: list[tuple], domain: str = "general") -> dict[int, dict]:
    """Returns {idx: {"comment": str, "llm_score": float}} for each source."""
```

Parse `[N/10]` from each line:

```python
        commentary = {}
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            match = re.match(r"^(\d+)[.\)]\s*(.+)", line)
            if match:
                idx = int(match.group(1)) - 1
                text = match.group(2).strip()
                score_match = re.search(r"\[(\d+)/10\]", text)
                llm_score = int(score_match.group(1)) / 10.0 if score_match else 0.5
                clean_text = re.sub(r"\s*\[\d+/10\]", "", text)
                commentary[idx] = {"comment": clean_text, "llm_score": llm_score}
        return commentary
```

- [ ] **Step 4: Update _deep_research to merge LLM scores into quality dict**

```python
    if fetched:
        commentary = _chesterton_evaluate_sources(query, fetched, _qdomain)
        for idx, domain, content, r in fetched:
            entry = commentary.get(idx, {})
            comment = entry.get("comment", "") if isinstance(entry, dict) else entry
            llm_score = entry.get("llm_score", 0.5) if isinstance(entry, dict) else 0.5

            depth = score_content_depth(content)
            if "_quality" in r:
                # Merge content depth + LLM assessment into credibility
                combined_content_score = 0.5 * depth + 0.5 * llm_score
                r["_quality"]["depth"] = depth
                r["_quality"]["llm_score"] = llm_score
                r["_quality"]["credibility"] = round(
                    0.4 * r["_quality"]["credibility"] + 0.6 * combined_content_score, 2
                )
                r["_quality"]["composite"] = round(
                    0.45 * r["_quality"]["reliability"] + 0.55 * r["_quality"]["credibility"], 2
                )

            if comment:
                print(f"\033[90m↳ [{idx + 1}] {domain} — {comment}\033[0m")
            else:
                print(f"\033[90m↳ [{idx + 1}] {domain}\033[0m")
            combined.append(f"[{idx + 1}] {domain}\n{content[:2000]}")
            sources_read.append(r)
```

- [ ] **Step 5: Run tests**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestCommentaryFeedback -v`
Expected: PASS

- [ ] **Step 6: Run full suite for regressions**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -k "not test_returns_results and not test_make_note_slug" -q`
Expected: 130+ pass

- [ ] **Step 7: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py
git commit -m "feat: Chesterton commentary feeds quality scores back into ranking"
```

---

### Task 6: Structural Clarity + E-E-A-T Signals

**Files:**
- Modify: `surf.py` score_source_quality credibility axis
- Test: `tests/test_surf.py`

**Interfaces:**
- Consumes: `result["snippet"]` and `result["title"]`
- Produces: author/expertise signals in credibility axis

- [ ] **Step 1: Write failing tests**

```python
class TestStructuralAndExpertiseSignals:
    def test_author_byline_boosts_credibility(self):
        with_author = {"domain": "example.com", "url": "https://example.com/a", "snippet": "By Dr. Jane Smith, MD. A randomized trial found 30% improvement.", "title": "Study results"}
        without_author = {"domain": "example.com", "url": "https://example.com/b", "snippet": "A randomized trial found 30% improvement.", "title": "Study results"}
        assert score_source_quality(with_author)["credibility"] > score_source_quality(without_author)["credibility"]

    def test_institutional_affiliation_boosts(self):
        result = {"domain": "example.com", "url": "https://example.com/a", "snippet": "Researchers at Harvard Medical School conducted a study of 1,000 patients.", "title": "Harvard study"}
        score = score_source_quality(result, domain="medical")
        assert score["credibility"] >= 0.65

    def test_vague_unsourced_claims_score_low(self):
        result = {"domain": "example.com", "url": "https://example.com/a", "snippet": "Many experts agree that this is important for your health and wellness journey.", "title": "Health tips"}
        score = score_source_quality(result)
        assert score["credibility"] < 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestStructuralAndExpertiseSignals -v`
Expected: FAIL

- [ ] **Step 3: Add expertise and structural clarity signals**

Add constants:

```python
_AUTHOR_RE = re.compile(r"\bby\s+(?:dr\.?\s+)?[A-Z][a-z]+\s+[A-Z][a-z]+|(?:MD|PhD|MPH|RN|JD|Professor)\b", re.IGNORECASE)
_INSTITUTION_SIGNALS = frozenset([
    "university", "institute", "hospital", "medical school", "college",
    "national academy", "harvard", "stanford", "mit", "oxford", "cambridge",
    "johns hopkins", "mayo clinic", "cdc", "who ", "nih", "fda",
])
_VAGUE_CLAIMS = frozenset([
    "many experts", "some people say", "it is believed", "studies show",
    "research suggests", "experts agree", "wellness journey", "holistic approach",
])
```

Add to credibility axis:

```python
    # E-E-A-T: Author expertise signals
    title_and_snippet = result.get("title", "") + " " + result.get("snippet", "")
    if _AUTHOR_RE.search(title_and_snippet):
        cred += 0.08
    inst_hits = sum(1 for s in _INSTITUTION_SIGNALS if s in snippet)
    cred += min(0.10, inst_hits * 0.05)
    # Vague unsourced claims demote
    vague_hits = sum(1 for s in _VAGUE_CLAIMS if s in snippet)
    if vague_hits >= 2:
        cred -= 0.10
```

- [ ] **Step 4: Run tests**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestStructuralAndExpertiseSignals -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py
git commit -m "feat: E-E-A-T expertise signals + vague claim detection in credibility axis"
```

---

### Task 7: Integration Test + Fix Pre-Existing Test Failures

**Files:**
- Modify: `tests/test_surf.py` (fix broken tests from earlier changes)
- Test: full suite

**Interfaces:**
- Consumes: all changes from Tasks 1-6
- Produces: clean test suite, integration test verifying end-to-end quality scoring

- [ ] **Step 1: Fix test_make_note_slug_sanitizes_query**

Read the test, update expected values to match the new stop-word-stripping behavior:

```python
    def test_make_note_slug_sanitizes_query(self):
        slug = _make_note_slug("What is the capital of France?")
        assert slug == "capital-france"  # stop words removed
        assert "/" not in slug
        assert " " not in slug
```

- [ ] **Step 2: Fix test_returns_results_and_response**

Update the mock to include the new `intent` and `fresh` parameters in `search_flow`:

```python
    def test_returns_results_and_response(self):
        # ... update mock call to include fresh=False, intent=None
```

- [ ] **Step 3: Write integration test**

```python
class TestQualityIntegration:
    def test_pubmed_outranks_seo_blog(self):
        pubmed = {"domain": "pubmed.ncbi.nlm.nih.gov", "url": "https://pubmed.ncbi.nlm.nih.gov/123", "snippet": "A randomized controlled trial of 500 participants found 40% reduction in symptoms according to researchers at NIH.", "title": "RCT results: symptom reduction"}
        seo_blog = {"domain": "healthtips.blog", "url": "https://healthtips.blog/fasting", "snippet": "Top 10 amazing health tips you won't believe! Get started on your wellness journey today.", "title": "10 Amazing Health Tips You Won't Believe"}
        pubmed_score = score_source_quality(pubmed, domain="medical", source_strategy="academic")
        seo_score = score_source_quality(seo_blog, domain="medical", source_strategy="academic")
        assert pubmed_score["composite"] > seo_score["composite"]
        assert pubmed_score["reliability"] > seo_score["reliability"]
        assert pubmed_score["credibility"] > seo_score["credibility"]

    def test_filter_and_rank_puts_quality_first(self):
        results = [
            {"domain": "healthtips.blog", "url": "https://healthtips.blog/1", "snippet": "Try these tips for wellness!", "title": "10 Tips"},
            {"domain": "pubmed.ncbi.nlm.nih.gov", "url": "https://pubmed.ncbi.nlm.nih.gov/123", "snippet": "A 2026 study of 1,000 patients found 30% improvement.", "title": "Clinical trial results"},
        ]
        intent = {"tier": "research", "domain": "medical", "source_strategy": "academic"}
        ranked = filter_and_rank_results("health study", results, intent=intent)
        assert ranked[0]["domain"] == "pubmed.ncbi.nlm.nih.gov"

    def test_snippet_tier_skips_quality(self):
        results = [
            {"domain": "healthtips.blog", "url": "https://healthtips.blog/1", "snippet": "The capital is Paris.", "title": "Capital of France"},
            {"domain": "example.com", "url": "https://example.com/2", "snippet": "Paris is the capital of France.", "title": "France capital"},
        ]
        intent = {"tier": "snippet", "domain": "general", "source_strategy": "any"}
        ranked = filter_and_rank_results("capital of France", results, intent=intent)
        # Should be BM25 ranked, not quality ranked
        assert "_quality" not in ranked[0] or ranked[0].get("_quality") is None
```

- [ ] **Step 4: Run full test suite**

Run: `cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -v`
Expected: ALL PASS

- [ ] **Step 5: Live test**

Run: `cd ~/termbrowser && .venv/bin/python3 surf.py --json "is intermittent fasting safe long-term" 2>&1 | head -20`
Expected: Research tier, quality-ranked sources, Chesterton commentary with scores

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py
git commit -m "test: fix pre-existing failures, add quality integration tests"
```

---

## Summary of Signals Addressed

| Signal | Task | Layer |
|--------|------|-------|
| NATO two-axis model (reliability × credibility) | Task 1 | Snippet |
| Publication recency | Task 2 | Snippet |
| Content depth (word count, headings, methodology, limitations) | Task 3 | Content |
| Clickbait detection (title patterns) | Task 4 | Snippet |
| Commentary feedback loop (LLM assessment → quality score) | Task 5 | Content |
| E-E-A-T expertise (author byline, institutional affiliation) | Task 6 | Snippet |
| "Shows its work" (methodology, caveats) | Task 3 | Content |
| Structural clarity (heading density) | Task 3 | Content |
| Vague claim detection | Task 6 | Snippet |

**Not implemented in this plan (deferred):**
- **Primary vs aggregated detection** — requires cross-result timestamp comparison, high complexity for marginal gain at current vault size. Revisit when surf processes 50+ results per query.
- **Full Perplexity-style 6-stage pipeline** — the two-phase approach (snippet heuristics + content scoring) captures the same signals without the infrastructure overhead.
