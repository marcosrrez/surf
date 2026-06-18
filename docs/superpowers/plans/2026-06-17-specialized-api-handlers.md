# Specialized API Handlers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the financial, academic, and factual specialized handlers that are referenced but undefined in `_run_specialized_query`, fixing a live NameError bug in the process, then wire them all into `search_flow`.

**Architecture:** The routing infrastructure and weather handler already exist. `_run_specialized_query` (line 2283) has a dict referencing three undefined names — `_handle_academic`, `_handle_financial`, `_handle_factual` — which causes a NameError on any call. Task 0 adds stubs to fix the bug immediately. Tasks 1–3 replace each stub with the real handler. Task 4 wires the routing block into `search_flow` before `_classify_tier`.

**Tech Stack:** Python 3.10+, `requests` (already in requirements), `xml.etree.ElementTree` (stdlib), `concurrent.futures` (stdlib), existing surf.py design tokens. All APIs are free with no key required.

---

## Files

- **Modify:** `~/termbrowser/surf.py` — add handlers between line 2502 (`_handle_weather` end) and line 2505 (`_classify_tier`), update `search_flow` at line 2741
- **Modify:** `~/termbrowser/tests/test_surf.py` — add `TestFinancialHandler`, `TestAcademicHandler`, `TestFactualHandler`, `TestSpecializedIntegration`

---

## Task 0: Fix live NameError — add handler stubs

**Files:**
- Modify: `~/termbrowser/surf.py` — add three stub functions after line 2502

The `_run_specialized_query` function builds a dict `{"academic": _handle_academic, "financial": _handle_financial, "factual": _handle_factual}` when called. Since those names don't exist yet, calling the function with ANY source_type raises NameError — weather queries are broken too.

- [ ] **Step 1: Verify the bug**

```bash
cd ~/termbrowser && python3 -c "
import surf
print(surf._run_specialized_query('apple stock', 'financial', 0))
"
```

Expected: `NameError: name '_handle_financial' is not defined`

- [ ] **Step 2: Add stub functions**

In `surf.py`, immediately after line 2502 (the `return response, sources, False` at the end of `_handle_weather`) and before line 2505 (`def _classify_tier`), add:

```python

# ─── Handler stubs (replaced by real implementations in Tasks 1–3) ─────────────

def _handle_financial(query: str) -> "tuple[str, list[dict], bool] | None":
    return None


def _handle_academic(query: str) -> "tuple[str, list[dict], bool] | None":
    return None


def _handle_factual(query: str) -> "tuple[str, list[dict], bool] | None":
    return None
```

- [ ] **Step 3: Verify the fix**

```bash
cd ~/termbrowser && python3 -c "
import surf
result = surf._run_specialized_query('apple stock', 'financial', 0)
print('OK — returned:', result)
"
```

Expected: `OK — returned: None` (stub returns None, handler falls through to DDG)

- [ ] **Step 4: Run full test suite — no regressions**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q
```

Expected: all existing tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/termbrowser && git add surf.py && git commit -m "$(cat <<'EOF'
fix: add handler stubs to resolve NameError in _run_specialized_query

_handle_academic, _handle_financial, _handle_factual were referenced in the
dispatch dict but never defined. Any call to _run_specialized_query raised
NameError, breaking weather queries. Stubs return None (DDG fallback).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1: Financial handler (Yahoo Finance)

**Files:**
- Modify: `~/termbrowser/surf.py` — replace `_handle_financial` stub with real implementation + helpers
- Modify: `~/termbrowser/tests/test_surf.py` — add `TestFinancialHandler`

The financial handler fetches live price data from Yahoo Finance's unofficial chart API. It detects the ticker from the query, fetches 5-day history, and displays a price card with sparkline. Falls back to DDG on any API failure.

- [ ] **Step 1: Write failing tests**

Add this class to `tests/test_surf.py` (after the existing test classes):

```python
class TestFinancialHandler:
    def _yahoo_response(self, symbol="AAPL", price=193.42, prev_close=191.28):
        return {
            "chart": {"result": [{
                "meta": {
                    "symbol": symbol,
                    "regularMarketPrice": price,
                    "previousClose": prev_close,
                    "regularMarketDayHigh": price + 1,
                    "regularMarketDayLow": price - 2,
                    "fiftyTwoWeekHigh": 201.55,
                    "fiftyTwoWeekLow": 142.86,
                    "marketCap": 2980000000000,
                    "regularMarketVolume": 48300000,
                    "averageVolume": 54100000,
                    "longName": "Apple Inc.",
                    "exchangeName": "NYSE",
                },
                "indicators": {"quote": [{"close": [190.0, 191.0, 192.0, 191.5, price]}]},
                "timestamp": [1717000000, 1717086400, 1717172800, 1717259200, 1717345600],
            }]}
        }

    def test_detect_ticker_from_company_name(self):
        from surf import _detect_ticker
        assert _detect_ticker("Apple stock price") == "AAPL"

    def test_detect_ticker_from_explicit_ticker(self):
        from surf import _detect_ticker
        assert _detect_ticker("what is TSLA trading at") == "TSLA"

    def test_detect_ticker_from_crypto(self):
        from surf import _detect_ticker
        assert _detect_ticker("bitcoin price today") == "BTC-USD"

    def test_detect_ticker_returns_none_for_no_match(self):
        from surf import _detect_ticker
        assert _detect_ticker("what is the capital of France") is None

    def test_build_sparkline_ascending(self):
        from surf import _build_sparkline
        spark = _build_sparkline([100.0, 101.0, 102.0, 103.0, 104.0])
        assert len(spark) == 5
        assert ord(spark[-1]) > ord(spark[0])

    def test_build_sparkline_flat(self):
        from surf import _build_sparkline
        spark = _build_sparkline([100.0, 100.0, 100.0])
        assert set(spark) == {"─"}

    def test_handle_financial_returns_none_on_api_failure(self):
        from surf import _handle_financial
        with patch("surf.requests.get", side_effect=Exception("connection")):
            result = _handle_financial("Apple stock price")
        assert result is None

    def test_handle_financial_returns_none_when_no_ticker(self):
        from surf import _handle_financial
        result = _handle_financial("what is the capital of France")
        assert result is None

    def test_handle_financial_returns_tuple_on_success(self):
        from surf import _handle_financial
        mock_r = MagicMock()
        mock_r.json.return_value = self._yahoo_response()
        mock_r.raise_for_status = MagicMock()
        with patch("surf.requests.get", return_value=mock_r), \
             patch("surf.print_header"), patch("surf.print_status"), \
             patch("surf.clear_status"):
            result = _handle_financial("Apple stock price")
        assert result is not None
        response, sources, streaming = result
        assert "193.42" in response or "AAPL" in response
        assert not streaming
        assert len(sources) == 1
        assert "finance.yahoo.com" in sources[0]["url"]

    def test_handle_financial_down_day_uses_glyph_down(self):
        from surf import _handle_financial, GLYPH_DOWN
        mock_r = MagicMock()
        mock_r.json.return_value = self._yahoo_response(price=188.00, prev_close=193.42)
        mock_r.raise_for_status = MagicMock()
        with patch("surf.requests.get", return_value=mock_r), \
             patch("surf.print_header"), patch("surf.print_status"), \
             patch("surf.clear_status"):
            result = _handle_financial("Apple stock price")
        assert result is not None
        response, _, _ = result
        assert GLYPH_DOWN in response
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestFinancialHandler -v
```

Expected: `ImportError: cannot import name '_detect_ticker' from 'surf'` (functions not yet defined).

- [ ] **Step 3: Replace `_handle_financial` stub with full implementation**

In `surf.py`, find the stub block added in Task 0 and replace ONLY the `_handle_financial` function (leave the academic and factual stubs). Replace this:

```python
def _handle_financial(query: str) -> "tuple[str, list[dict], bool] | None":
    return None
```

With:

```python
def _detect_ticker(query: str) -> "str | None":
    """Return Yahoo Finance ticker from query text, or None if not recognized."""
    q = query.lower()
    for name, ticker in COMPANY_TICKER_MAP.items():
        if name in q:
            return ticker
    financial_words = {"stock", "price", "shares", "trading", "ticker", "market", "nasdaq", "nyse", "etf"}
    if any(w in q for w in financial_words):
        import re as _re2
        matches = _re2.findall(r'\b([A-Z]{2,5})\b', query)
        skip = {"US", "EU", "UK", "AI", "ML", "API", "CEO", "GDP", "IPO", "CIA", "FBI", "NASA", "NYT"}
        for m in matches:
            if m not in skip:
                return m
    return None


def _build_sparkline(prices: "list[float]") -> str:
    """Build single-row block sparkline from a list of prices."""
    blocks = "▁▂▃▄▅▆▇█"
    if len(prices) < 2:
        return "─" * len(prices)
    lo, hi = min(prices), max(prices)
    if hi == lo:
        return "─" * len(prices)
    return "".join(blocks[min(7, int((p - lo) / (hi - lo) * 7.99))] for p in prices)


def _fmt_large(n: float) -> str:
    """Format large number as human-readable: 2980000000000 → $2.98T"""
    if n >= 1e12:
        return f"${n/1e12:.2f}T"
    if n >= 1e9:
        return f"${n/1e9:.2f}B"
    if n >= 1e6:
        return f"${n/1e6:.1f}M"
    return f"${n:,.0f}"


def _handle_financial(query: str) -> "tuple[str, list[dict], bool] | None":
    """Fetch stock/crypto price from Yahoo Finance. Returns (response, sources, streaming) or None."""
    ticker = _detect_ticker(query)
    if not ticker:
        return None

    print_status(f"↳ fetching {ticker} · Yahoo Finance...")

    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = requests.get(url, params={"interval": "1d", "range": "5d"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=3.0)
        r.raise_for_status()
        data = r.json()
    except Exception:
        clear_status()
        return None

    clear_status()

    try:
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice", 0)
        prev = meta.get("previousClose", price)
        change = price - prev
        pct = (change / prev * 100) if prev else 0
        day_hi = meta.get("regularMarketDayHigh", price)
        day_lo = meta.get("regularMarketDayLow", price)
        wk52_hi = meta.get("fiftyTwoWeekHigh", 0)
        wk52_lo = meta.get("fiftyTwoWeekLow", 0)
        mkt_cap = meta.get("marketCap", 0)
        volume = meta.get("regularMarketVolume", 0)
        avg_vol = meta.get("averageVolume", 0)
        name = meta.get("longName", meta.get("shortName", ticker))
        exchange = meta.get("exchangeName", "")

        if change > 0:
            dir_glyph, dir_color = GLYPH_UP, C_SPEED_FAST
        elif change < 0:
            dir_glyph, dir_color = GLYPH_DOWN, C_ERROR
        else:
            dir_glyph, dir_color = GLYPH_FLAT, C_META

        pct_color = C_INTERACTIVE if abs(pct) > 2 else dir_color

        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c is not None]
        spark = _build_sparkline(closes) if len(closes) >= 2 else ""
        if spark:
            spark = f"{C_META}{spark[:-1]}{dir_color}{spark[-1]}{C_RESET}"

        direction_word = "up" if change > 0 else ("down" if change < 0 else "flat")
        tldr = (f"{GLYPH_TLDR} TL;DR  {name} {direction_word} "
                f"{abs(pct):.2f}% today at ${price:,.2f}.")

        lines = [
            f"{C_ANSWER_MARK}{tldr}{C_RESET}",
            "",
            f"  {'Price':<11}${price:>8,.2f}   "
            f"{dir_color}{dir_glyph} {'+' if change >= 0 else ''}{change:+.2f}{C_RESET}  "
            f"{pct_color}{pct:+.2f}%{C_RESET}",
            f"  {'Day range':<11}${day_lo:,.2f} – ${day_hi:,.2f}",
            f"  {'52-week':<11}${wk52_lo:,.2f} – ${wk52_hi:,.2f}",
        ]
        if mkt_cap:
            lines.append(f"  {'Mkt cap':<11}{_fmt_large(mkt_cap)}")
        if volume:
            vol_str = f"{volume/1e6:.1f}M"
            avg_str = f"  (avg {avg_vol/1e6:.1f}M)" if avg_vol else ""
            lines.append(f"  {'Volume':<11}{vol_str}{avg_str}")
        if spark:
            lines.append("")
            lines.append(f"  {C_META}5d:{C_RESET}  {spark}")

        response = "\n".join(lines)

        import datetime as _dt
        ts = _dt.datetime.now().strftime("%b %-d %H:%M")
        tz_note = " ET" if exchange in ("NYSE", "NASDAQ") else ""
        print_header(f"{ticker} — {name}",
                     f"{C_META}{GLYPH_META} Yahoo Finance · {ts}{tz_note} · {exchange}{C_RESET}",
                     zone_after=SPACE_SM)

        sources = [{
            "title": f"{ticker} — {name} · Yahoo Finance",
            "url": f"https://finance.yahoo.com/quote/{ticker}",
            "domain": "finance.yahoo.com",
            "snippet": f"${price:,.2f} {dir_glyph} {pct:+.2f}% · not financial advice",
        }]
        return response, sources, False

    except (KeyError, IndexError, TypeError):
        return None
```

- [ ] **Step 4: Run TestFinancialHandler**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestFinancialHandler -v
```

Expected: 10 passed.

- [ ] **Step 5: Run full suite — no regressions**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q
```

Expected: all existing tests + 10 new, all pass.

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "$(cat <<'EOF'
feat: financial handler — Yahoo Finance price card with sparkline

Stock/crypto price display with 5-day sparkline, direction glyphs, and
market cap. Detects ticker from company name, explicit symbol, or crypto
alias. Falls back to DDG on any API failure.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Academic handler (PubMed + arXiv)

**Files:**
- Modify: `~/termbrowser/surf.py` — replace `_handle_academic` stub with full implementation + helpers
- Modify: `~/termbrowser/tests/test_surf.py` — add `TestAcademicHandler`

Searches PubMed and arXiv in parallel. Simple queries show paper cards; complex "what does the research say" queries synthesize via Claude using `SEARCH_SYSTEM_ACADEMIC`. LaTeX is stripped from arXiv abstracts.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_surf.py`:

```python
class TestAcademicHandler:
    def _pubmed_search_json(self):
        return {"esearchresult": {"idlist": ["12345678"]}}

    def _pubmed_summary_json(self):
        return {"result": {
            "12345678": {
                "uid": "12345678",
                "title": "Safety of mRNA COVID-19 Vaccines",
                "authors": [{"name": "Polack F"}, {"name": "Thomas S"}],
                "pubdate": "2023",
                "fulljournalname": "New England Journal of Medicine",
                "elocationid": "10.1056/test",
            }
        }}

    def _arxiv_xml(self):
        return """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <title>mRNA Vaccine Mechanism Study</title>
  <author><name>Smith J</name></author>
  <published>2023-01-15T00:00:00Z</published>
  <summary>We present findings on mRNA vaccine mechanisms. Strong immune response observed.</summary>
  <id>http://arxiv.org/abs/2301.00001v1</id>
</entry>
</feed>"""

    def _mock_all_requests(self, mock_get):
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if "esearch" in url:
                r.json.return_value = self._pubmed_search_json()
            elif "esummary" in url:
                r.json.return_value = self._pubmed_summary_json()
            elif "efetch" in url:
                r.text = "Abstract text. In this randomized trial, the vaccine showed 95% efficacy."
            else:
                r.text = self._arxiv_xml()
            return r
        mock_get.side_effect = side_effect

    def test_strip_latex_removes_inline_math(self):
        from surf import _strip_latex
        assert "$" not in _strip_latex("We prove $\\mathcal{O}(n^2)$ is tight.")
        assert "\\mathcal" not in _strip_latex("We prove $\\mathcal{O}(n^2)$ is tight.")

    def test_strip_latex_removes_commands(self):
        from surf import _strip_latex
        cleaned = _strip_latex("Using \\text{Theorem 1} we show")
        assert "\\text" not in cleaned

    def test_handle_academic_returns_none_on_failure(self):
        from surf import _handle_academic
        with patch("surf.requests.get", side_effect=Exception("timeout")), \
             patch("surf.print_status"), patch("surf.clear_status"):
            result = _handle_academic("what does the research say about mRNA vaccines")
        assert result is None

    def test_handle_academic_returns_tuple_on_success(self):
        from surf import _handle_academic
        with patch("surf.requests.get") as mock_get, \
             patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.print_header"):
            self._mock_all_requests(mock_get)
            result = _handle_academic("peer reviewed studies on aspirin")
        assert result is not None
        response, sources, streaming = result
        assert isinstance(response, str)
        assert len(sources) > 0

    def test_handle_academic_returns_none_when_no_results(self):
        from surf import _handle_academic
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if "esearch" in url:
                r.json.return_value = {"esearchresult": {"idlist": []}}
            else:
                r.text = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>"""
            return r
        with patch("surf.requests.get", side_effect=side_effect), \
             patch("surf.print_status"), patch("surf.clear_status"):
            result = _handle_academic("zzz nonexistent topic xyzxyz")
        assert result is None

    def test_source_tag_in_paper_card(self):
        from surf import _handle_academic
        with patch("surf.requests.get") as mock_get, \
             patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.print_header"):
            self._mock_all_requests(mock_get)
            result = _handle_academic("peer reviewed studies on aspirin")
        assert result is not None
        response, sources, _ = result
        source_domains = [s["domain"] for s in sources]
        assert any("pubmed" in d or "arxiv" in d for d in source_domains)
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestAcademicHandler -v
```

Expected: `ImportError: cannot import name '_strip_latex' from 'surf'`.

- [ ] **Step 3: Replace `_handle_academic` stub with full implementation**

In `surf.py`, replace:

```python
def _handle_academic(query: str) -> "tuple[str, list[dict], bool] | None":
    return None
```

With:

```python
def _strip_latex(text: str) -> str:
    """Remove LaTeX math and commands from arXiv abstract text."""
    text = re.sub(r'\$\$[^$]+\$\$', '[math]', text)
    text = re.sub(r'\$[^$]+\$', '[math]', text)
    text = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', text)
    text = re.sub(r'\\[a-zA-Z]+', '', text)
    text = re.sub(r'[{}]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def _search_pubmed(query: str, max_results: int = 4) -> "list[dict]":
    """Search PubMed; return list of {title, authors, year, journal, abstract, link, source}."""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    search_r = requests.get(f"{base}/esearch.fcgi",
                            params={"db": "pubmed", "term": query, "retmode": "json",
                                    "retmax": max_results, "sort": "relevance"},
                            headers=HEADERS, timeout=3.0)
    search_r.raise_for_status()
    ids = search_r.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    summary_r = requests.get(f"{base}/esummary.fcgi",
                             params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
                             headers=HEADERS, timeout=3.0)
    summary_r.raise_for_status()
    summaries = summary_r.json().get("result", {})

    abstract_r = requests.get(f"{base}/efetch.fcgi",
                              params={"db": "pubmed", "id": ",".join(ids),
                                      "rettype": "abstract", "retmode": "text"},
                              headers=HEADERS, timeout=3.0)
    abstract_r.raise_for_status()
    abstract_text = abstract_r.text

    paper_blocks = [b.strip() for b in abstract_text.split("\n\n\n") if b.strip()]
    papers = []
    for pmid in ids:
        s = summaries.get(pmid, {})
        if not s or s.get("error"):
            continue
        authors = s.get("authors", [])
        author_str = authors[0]["name"] if authors else "Unknown"
        if len(authors) > 1:
            author_str += " et al."
        papers.append({
            "title": s.get("title", "").rstrip("."),
            "authors": author_str,
            "year": s.get("pubdate", "")[:4],
            "journal": s.get("fulljournalname", s.get("source", "")),
            "abstract": "",
            "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "source": "PubMed",
            "pmid": pmid,
        })

    for paper in papers:
        for block in paper_blocks:
            if paper["pmid"] in block:
                lines = block.split("\n")
                abstract_lines = []
                in_abstract = False
                for line in lines:
                    if line.startswith("Abstract") or line.startswith("ABSTRACT"):
                        in_abstract = True
                        continue
                    if in_abstract and line.strip():
                        abstract_lines.append(line.strip())
                if abstract_lines:
                    paper["abstract"] = " ".join(abstract_lines)[:500]
                break

    return papers


def _search_arxiv(query: str, max_results: int = 3) -> "list[dict]":
    """Search arXiv; strips LaTeX from abstracts."""
    import xml.etree.ElementTree as ET
    r = requests.get(
        "https://export.arxiv.org/api/query",
        params={"search_query": f"all:{query}", "start": 0,
                "max_results": max_results, "sortBy": "relevance"},
        headers=HEADERS, timeout=3.0,
    )
    r.raise_for_status()
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)
    papers = []
    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        published_el = entry.find("atom:published", ns)
        id_el = entry.find("atom:id", ns)
        authors = entry.findall("atom:author", ns)

        title = title_el.text.strip() if title_el is not None else ""
        abstract = _strip_latex(summary_el.text.strip() if summary_el is not None else "")
        year = published_el.text[:4] if published_el is not None else ""
        link = id_el.text.strip() if id_el is not None else ""
        author_names = [a.find("atom:name", ns).text for a in authors
                        if a.find("atom:name", ns) is not None]
        author_str = author_names[0] if author_names else "Unknown"
        if len(author_names) > 1:
            author_str += " et al."
        if title:
            papers.append({
                "title": title, "authors": author_str, "year": year,
                "journal": "arXiv preprint", "abstract": abstract[:500],
                "link": link, "source": "arXiv",
            })
    return papers


def _handle_academic(query: str) -> "tuple[str, list[dict], bool] | None":
    """Search PubMed + arXiv in parallel; return paper cards or Claude synthesis."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print_status("↳ searching PubMed + arXiv...")

    with ThreadPoolExecutor(max_workers=2) as executor:
        pubmed_future = executor.submit(_search_pubmed, query)
        arxiv_future = executor.submit(_search_arxiv, query)
        papers = []
        for future in as_completed([pubmed_future, arxiv_future], timeout=4.0):
            try:
                papers.extend(future.result())
            except Exception:
                pass

    clear_status()

    if not papers:
        return None

    seen_titles: set = set()
    unique_papers = []
    for p in papers:
        key = p["title"].lower()[:50]
        if key not in seen_titles:
            seen_titles.add(key)
            unique_papers.append(p)
    papers = unique_papers[:5]

    synthesis_signals = {"what does", "summarize", "explain", "overview",
                         "what is known", "evidence", "consensus"}
    is_complex = any(s in query.lower() for s in synthesis_signals)

    sources = [{
        "title": f"{p['title'][:60]} — {p['authors']}, {p['year']}",
        "url": p["link"],
        "domain": "pubmed.ncbi.nlm.nih.gov" if p["source"] == "PubMed" else "arxiv.org",
        "snippet": p.get("abstract", "")[:120],
    } for p in papers]

    header_q = f"{query[:50]}… — Academic" if len(query) > 50 else f"{query} — Academic"
    print_header(header_q,
                 f"{C_META}{GLYPH_META} PubMed · arXiv · {len(papers)} result{'s' if len(papers) != 1 else ''}{C_RESET}",
                 zone_after=SPACE_SM)

    if is_complex:
        abstracts_text = "\n\n".join(
            f"[{p['authors']}, {p['year']}] {p['title']}\n{p.get('abstract', '')}"
            for p in papers
        )
        prefs = _read_preferences()
        prompt = f"Query: {query}\n\nPeer-reviewed papers:\n{abstracts_text}"
        if prefs:
            prompt = f"[User preferences]\n{prefs}\n[End preferences]\n\n{prompt}"
        stream = stream_ai(prompt, SEARCH_SYSTEM_ACADEMIC)
        stream_to_terminal(stream, results=sources)
        return None, sources, True

    lines = [
        f"{C_ANSWER_MARK}{GLYPH_TLDR} TL;DR  Found {len(papers)} peer-reviewed result{'s' if len(papers) != 1 else ''}.{C_RESET}",
        "",
    ]
    for i, p in enumerate(papers, 1):
        abstract_preview = p.get("abstract", "")
        if abstract_preview:
            words = abstract_preview.split()
            if len(words) > 40:
                abstract_preview = " ".join(words[:40]) + f"{GLYPH_ELLIPSIS}"
            lines.append(f" {C_INTERACTIVE}{i}{C_RESET}  {p['title'][:_term_width()-6]}")
            lines.append(f"     {C_META}{p['authors']}, {p['year']} · {p['journal']}  [{p['source']}]{C_RESET}")
            lines.append(f"     {C_META}{abstract_preview}{C_RESET}")
        else:
            lines.append(f" {C_INTERACTIVE}{i}{C_RESET}  {p['title'][:_term_width()-6]}")
            lines.append(f"     {C_META}{p['authors']}, {p['year']} · {p['journal']}  [{p['source']}]{C_RESET}")
        lines.append("")

    return "\n".join(lines), sources, False
```

- [ ] **Step 4: Run TestAcademicHandler**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestAcademicHandler -v
```

Expected: 6 passed.

- [ ] **Step 5: Run full suite**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q
```

Expected: all existing tests + 16 new, all pass.

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "$(cat <<'EOF'
feat: academic handler — PubMed + arXiv parallel search with LaTeX stripping

Parallel search via ThreadPoolExecutor. Simple queries show paper cards
with [PubMed]/[arXiv] tags; synthesis queries route to Claude with
SEARCH_SYSTEM_ACADEMIC. Strips LaTeX from arXiv abstracts.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Factual handler (Wikipedia)

**Files:**
- Modify: `~/termbrowser/surf.py` — replace `_handle_factual` stub with full implementation
- Modify: `~/termbrowser/tests/test_surf.py` — add `TestFactualHandler`

Looks up named entities on Wikipedia's REST API. Disambiguation pages show an inline choice menu (NOT a DDG fallback) — this is the key design decision for this handler.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_surf.py`:

```python
class TestFactualHandler:
    def _wiki_summary(self, title="Eiffel Tower"):
        return {
            "title": title,
            "description": "Iron lattice tower in Paris, France",
            "extract": ("The Eiffel Tower is a wrought-iron lattice tower on the Champ de Mars "
                        "in Paris, France. It was named after the engineer Gustave Eiffel, "
                        "whose company designed and built the tower from 1887 to 1889."),
            "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}},
            "type": "standard",
        }

    def _wiki_disambiguation(self):
        return {
            "title": "Mercury",
            "description": "disambiguation page",
            "extract": "Mercury may refer to:",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Mercury"}},
            "type": "disambiguation",
        }

    def _mock_search_success(self, mock_get, title="Eiffel Tower"):
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if "opensearch" in url:
                r.json.return_value = [title, [title], ["description"], [f"https://en.wikipedia.org/wiki/{title}"]]
            else:
                r.json.return_value = self._wiki_summary(title)
            return r
        mock_get.side_effect = side_effect

    def test_handle_factual_returns_none_on_network_failure(self):
        from surf import _handle_factual
        with patch("surf.requests.get", side_effect=Exception("timeout")), \
             patch("surf.print_status"), patch("surf.clear_status"):
            result = _handle_factual("what is the Eiffel Tower")
        assert result is None

    def test_handle_factual_returns_none_when_no_results(self):
        from surf import _handle_factual
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = ["query", [], [], []]
            return r
        with patch("surf.requests.get", side_effect=side_effect), \
             patch("surf.print_status"), patch("surf.clear_status"):
            result = _handle_factual("what is zzz xyzabc")
        assert result is None

    def test_handle_factual_returns_entity_response(self):
        from surf import _handle_factual
        with patch("surf.requests.get") as mock_get, \
             patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.print_header"):
            self._mock_search_success(mock_get)
            result = _handle_factual("what is the Eiffel Tower")
        assert result is not None
        response, sources, streaming = result
        assert "Eiffel" in response
        assert not streaming
        assert sources[0]["domain"] == "en.wikipedia.org"

    def test_handle_factual_disambiguation_shows_choice_menu(self):
        from surf import _handle_factual
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if "opensearch" in url:
                r.json.return_value = [
                    "Mercury",
                    ["Mercury (planet)", "Mercury (element)", "Freddie Mercury"],
                    ["desc1", "desc2", "desc3"],
                    ["url1", "url2", "url3"]
                ]
            else:
                r.json.return_value = self._wiki_disambiguation()
            return r
        with patch("surf.requests.get", side_effect=side_effect), \
             patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.print_header"):
            result = _handle_factual("what is Mercury")
        assert result is not None
        response, sources, streaming = result
        assert "Mercury (planet)" in response or "choose" in response.lower()
        assert len(sources) > 1

    def test_handle_factual_tldr_is_first_sentence(self):
        from surf import _handle_factual
        with patch("surf.requests.get") as mock_get, \
             patch("surf.print_status"), patch("surf.clear_status"), \
             patch("surf.print_header"):
            self._mock_search_success(mock_get)
            result = _handle_factual("what is the Eiffel Tower")
        assert result is not None
        response, _, _ = result
        assert "TL;DR" in response
        assert "wrought-iron" in response.lower() or "eiffel" in response.lower()
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestFactualHandler -v
```

Expected: `ImportError: cannot import name '_handle_factual'` (still a stub returning None, tests expect real behavior).

- [ ] **Step 3: Replace `_handle_factual` stub with full implementation**

In `surf.py`, replace:

```python
def _handle_factual(query: str) -> "tuple[str, list[dict], bool] | None":
    return None
```

With:

```python
def _handle_factual(query: str) -> "tuple[str, list[dict], bool] | None":
    """Look up named entity on Wikipedia. Disambiguation → inline menu, not DDG fallback."""
    print_status("↳ looking up Wikipedia...")

    try:
        search_r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": query, "limit": 4, "format": "json"},
            headers=HEADERS, timeout=2.0,
        )
        search_r.raise_for_status()
        search_data = search_r.json()
        titles = search_data[1] if len(search_data) > 1 else []
        descriptions = search_data[2] if len(search_data) > 2 else []
        urls = search_data[3] if len(search_data) > 3 else []
        if not titles:
            clear_status()
            return None
    except Exception:
        clear_status()
        return None

    try:
        title_encoded = titles[0].replace(" ", "_")
        summary_r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{title_encoded}",
            headers=HEADERS, timeout=2.0,
        )
        summary_r.raise_for_status()
        summary = summary_r.json()
    except Exception:
        clear_status()
        return None

    clear_status()

    extract = summary.get("extract", "")
    description = summary.get("description", "")
    page_url = summary.get("content_urls", {}).get("desktop", {}).get("page", urls[0] if urls else "")

    is_disambiguation = (
        len(extract) < 100
        or "may refer to" in extract.lower()
        or "disambiguation" in description.lower()
        or summary.get("type", "") == "disambiguation"
    )

    if is_disambiguation and len(titles) > 1:
        choice_lines = [
            f"{C_ANSWER_MARK}{GLYPH_TLDR} TL;DR  Multiple Wikipedia articles match \"{titles[0]}\" — choose one.{C_RESET}",
            "",
        ]
        max_title_len = max(len(t) for t in titles[:4]) + 2
        descs = descriptions[:4] if descriptions else [""] * 4
        for i, (t, d) in enumerate(zip(titles[:4], descs), 1):
            desc_str = f"  {C_META}{d}{C_RESET}" if d else ""
            choice_lines.append(f" {C_INTERACTIVE}{i}{C_RESET}  {t:<{max_title_len}}{desc_str}")
        response = "\n".join(choice_lines)
        sources = [
            {"title": t, "url": u, "domain": "en.wikipedia.org", "snippet": d}
            for t, d, u in zip(
                titles[:4],
                descs,
                urls[:4] if urls else [""] * 4,
            )
        ]
        print_header(titles[0], f"{C_META}{GLYPH_META} Wikipedia · disambiguation{C_RESET}", zone_after=SPACE_SM)
        return response, sources, False

    paragraphs = [p.strip() for p in extract.split("\n") if p.strip()]
    if paragraphs:
        display_text = paragraphs[0]
        if len(display_text.split()) < 80 and len(paragraphs) > 1:
            display_text += "\n\n" + paragraphs[1]
    else:
        display_text = extract[:400]

    first_sentence = display_text.split(".")[0] + "."
    if len(first_sentence) > 120:
        first_sentence = first_sentence[:117] + GLYPH_ELLIPSIS

    lines = [
        f"{C_ANSWER_MARK}{GLYPH_TLDR} TL;DR  {first_sentence}{C_RESET}",
        "",
        display_text,
    ]
    response = "\n".join(lines)

    print_header(titles[0], f"{C_META}{GLYPH_META} Wikipedia · {description}{C_RESET}", zone_after=SPACE_SM)

    sources = [{
        "title": f"{titles[0]} — Wikipedia",
        "url": page_url,
        "domain": "en.wikipedia.org",
        "snippet": description,
    }]
    return response, sources, False
```

- [ ] **Step 4: Run TestFactualHandler**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestFactualHandler -v
```

Expected: 5 passed.

- [ ] **Step 5: Run full suite**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q
```

Expected: all existing tests + 21 new, all pass.

- [ ] **Step 6: Commit**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "$(cat <<'EOF'
feat: factual handler — Wikipedia entity lookup with disambiguation menu

Fetches Wikipedia summaries for entity queries. Disambiguation pages show
an inline choice menu instead of falling through to DDG — the user can
pick the right Mercury (planet, element, or Freddie).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire into search_flow + integration tests

**Files:**
- Modify: `~/termbrowser/surf.py` — add specialized routing block at start of `search_flow` (line 2736)
- Modify: `~/termbrowser/tests/test_surf.py` — add `TestSpecializedIntegration`

Currently `search_flow` jumps straight to `tier = _classify_tier(query)` at line 2741. This task adds a pre-routing block before that line that calls `_classify_data_source` and dispatches to specialized handlers.

- [ ] **Step 1: Write failing integration tests**

Add to `tests/test_surf.py`:

```python
class TestSpecializedIntegration:
    def _ddg_patches(self):
        """Common patches needed for DDG fallthrough tests."""
        fake_results = [{"title": "T", "url": "https://example.com",
                         "domain": "example.com", "snippet": "Content about the topic."}]
        return [
            patch("surf.ddg_search", return_value=fake_results),
            patch("surf.stream_ai", return_value=iter(["▸ TL;DR  Answer."])),
            patch("surf.stream_to_terminal", return_value="▸ TL;DR  Answer."),
            patch("surf.print_header"), patch("surf.print_status"),
            patch("surf.clear_status"), patch("surf._print_linked_sources"),
            patch("surf.print_results"), patch("surf.save_session_entry"),
            patch("surf.format_session_context", return_value=""),
            patch("surf._read_preferences", return_value=""),
            patch("surf._obsidian_find_related", return_value=""),
            patch("surf._obsidian_save", return_value=None),
            patch("surf._classify_tier", return_value="snippet"),
            patch("surf._confidence_gate", return_value="snippet"),
            patch("surf._enrich_ddg_query", return_value="query"),
            patch("surf._fix_entity_mismatch", side_effect=lambda q, r, d, **kw: (r, d)),
            patch("surf._bm25_rank", side_effect=lambda q, r: r),
            patch("surf._snippets_are_diverse", return_value=True),
            patch("surf._sources_are_substantive", return_value=True),
            patch("surf._filter_results", side_effect=lambda r, **kw: r),
        ]

    def test_weather_query_bypasses_ddg(self):
        from surf import search_flow
        with patch("surf._classify_data_source", return_value="weather"), \
             patch("surf._run_specialized_query", return_value=([], "weather response")) as mock_specialized, \
             patch("surf.ddg_search") as mock_ddg:
            search_flow("weather in Chicago", interactive=False)
        mock_specialized.assert_called_once()
        mock_ddg.assert_not_called()

    def test_financial_query_bypasses_ddg(self):
        from surf import search_flow
        with patch("surf._classify_data_source", return_value="financial"), \
             patch("surf._run_specialized_query", return_value=([], "price response")) as mock_specialized, \
             patch("surf.ddg_search") as mock_ddg:
            search_flow("Apple stock price", interactive=False)
        mock_specialized.assert_called_once()
        mock_ddg.assert_not_called()

    def test_web_query_skips_specialized_goes_to_ddg(self):
        from surf import search_flow
        patches = self._ddg_patches()
        with patch("surf._classify_data_source", return_value="web"), \
             patch("surf._run_specialized_query") as mock_specialized, \
             *patches:
            search_flow("who wrote Pride and Prejudice", interactive=False)
        mock_specialized.assert_not_called()

    def test_handler_failure_falls_through_to_ddg(self):
        from surf import search_flow
        patches = self._ddg_patches()
        with patch("surf._classify_data_source", return_value="weather"), \
             patch("surf._run_specialized_query", return_value=None), \
             patch("surf.ddg_search") as mock_ddg, \
             *patches:
            search_flow("weather in Chicago", interactive=False)
        mock_ddg.assert_called()

    def test_json_output_skips_specialized(self):
        from surf import search_flow
        patches = self._ddg_patches()
        with patch("surf._run_specialized_query") as mock_specialized, \
             *patches:
            search_flow("Apple stock price", interactive=False, json_output=True)
        mock_specialized.assert_not_called()
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSpecializedIntegration -v
```

Expected: `FAILED test_weather_query_bypasses_ddg` — `_run_specialized_query` gets called because `_classify_data_source` fires but `search_flow` doesn't check it yet.

- [ ] **Step 3: Add specialized routing block to search_flow**

In `surf.py`, find `search_flow` at line 2736. The function body currently starts:

```python
def search_flow(query: str, interactive: bool = True, json_output: bool = False) -> tuple[list[dict], str]:
    """
    Run the search flow: DDG → Groq → display results.
    Returns (results, groq_response_text).
    """
    tier = _classify_tier(query)
```

Replace those last two lines (`    tier = _classify_tier(query)` onwards — just insert BEFORE it):

```python
def search_flow(query: str, interactive: bool = True, json_output: bool = False) -> tuple[list[dict], str]:
    """
    Run the search flow: DDG → Groq → display results.
    Returns (results, groq_response_text).
    """
    _t_start = time.time()

    # Specialized routing: try dedicated APIs before DDG
    if not json_output:
        _source_type = _classify_data_source(query)
        if _source_type != "web":
            _specialized = _run_specialized_query(query, _source_type, _t_start, interactive)
            if _specialized is not None:
                return _specialized

    tier = _classify_tier(query)
```

**Important:** There is likely an existing `_t0 = time.time()` line inside `search_flow`'s body. Leave it in place — `_t_start` is used only for the specialized path, and `_t0` continues to be used by the existing DDG path. The two are independent.

- [ ] **Step 4: Run integration tests**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py::TestSpecializedIntegration -v
```

Expected: 5 passed.

- [ ] **Step 5: Run full test suite**

```bash
cd ~/termbrowser && .venv/bin/pytest tests/test_surf.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Smoke test — financial**

```bash
cd ~/termbrowser && python3 surf.py "Apple stock price"
```

Expected: `━━ AAPL — Apple Inc. ━━━━` header with price card, direction glyph (▲/▼/→), and 5-day sparkline. No DDG results list at the bottom.

- [ ] **Step 7: Smoke test — factual**

```bash
cd ~/termbrowser && python3 surf.py "what is the Eiffel Tower"
```

Expected: `━━ Eiffel Tower ━━━━` header, TL;DR line, Wikipedia paragraph. No DDG results.

- [ ] **Step 8: Smoke test — factual disambiguation**

```bash
cd ~/termbrowser && python3 surf.py "what is Mercury"
```

Expected: disambiguation menu with 3–4 numbered choices (Mercury planet, element, Freddie Mercury, etc.).

- [ ] **Step 9: Smoke test — academic**

```bash
cd ~/termbrowser && python3 surf.py "peer reviewed studies on aspirin and heart disease"
```

Expected: `━━ peer reviewed studies... — Academic ━━━━` header with numbered paper cards showing `[PubMed]` or `[arXiv]` tags.

- [ ] **Step 10: Smoke test — DDG fallthrough still works**

```bash
cd ~/termbrowser && python3 surf.py "best practices for Python error handling"
```

Expected: normal DDG + research-tier response (no specialized handler fires).

- [ ] **Step 11: Smoke test — weather still works**

```bash
cd ~/termbrowser && python3 surf.py "weather in Chicago"
```

Expected: Open-Meteo forecast table (this worked before but confirms nothing regressed).

- [ ] **Step 12: Commit and push**

```bash
cd ~/termbrowser && git add surf.py tests/test_surf.py && git commit -m "$(cat <<'EOF'
feat: wire specialized handlers into search_flow

Financial, academic, and factual queries now route to dedicated APIs before
DDG. Handler failures and json_output mode fall through to DDG unchanged.
Full post-processing (session save, Obsidian, feature tracking) preserved.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)" && git push
```

---

## Self-Review

**Spec coverage check:**
- ✅ Task 0: NameError bug fixed — stubs for all three undefined handlers
- ✅ Task 1: Financial — `_detect_ticker` (company map + all-caps), `_build_sparkline` (▁▂▃▄▅▆▇█), `_fmt_large`, `_handle_financial`, direction glyphs (GLYPH_UP/DOWN/FLAT), >2% amber threshold, "not financial advice" in snippet
- ✅ Task 2: Academic — `_strip_latex`, `_search_pubmed` (text endpoint not XML), `_search_arxiv` (parallel via ThreadPoolExecutor), `_handle_academic` (simple → cards, complex → Claude + SEARCH_SYSTEM_ACADEMIC), `[PubMed]`/`[arXiv]` source tags
- ✅ Task 3: Factual — `_handle_factual`, disambiguation inline menu (NOT DDG fallback), first paragraph display, TL;DR from first sentence
- ✅ Task 4: `search_flow` routing block before `_classify_tier`, `json_output` guard, `_run_specialized_query` returns None → DDG fallthrough, integration tests cover all branches
- ✅ All handlers: return None on API failure → DDG fallthrough (tested)
- ✅ `_display_specialized_result` already handles session save, Obsidian, feature tracking for all handlers

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:** All handlers return `tuple[str | None, list[dict], bool] | None`. `_run_specialized_query` unpacks as `response, sources, streaming = result`. `_display_specialized_result` receives all three. Consistent throughout.

**One note for implementers:** The `_handle_academic` complex synthesis path returns `(None, sources, True)` — `streaming=True` means Claude already printed to terminal during `stream_to_terminal`. `_display_specialized_result` checks `if not streaming and response:` before printing, so this is handled correctly.
