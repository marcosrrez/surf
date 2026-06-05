# surf — Specialized API Integrations Design
**Date:** 2026-06-04
**Status:** Approved — updated with architecture + UI review findings

---

## Problem

surf routes every query through DDG, which fundamentally cannot answer certain query categories well:
- **Weather**: DDG has no live hourly forecast data. "24-hour forecast for Siloam Springs" returns outdated cached pages.
- **Academic**: DDG finds *news articles about papers*, not the papers themselves.
- **Financial**: DDG can't return today's stock price — it returns yesterday's news about it.
- **Factual entities**: DDG returns SEO content when Wikipedia has a definitive, authoritative answer.

The solution: a pre-routing layer that detects these query types and calls specialized APIs directly, bypassing DDG for live/structured data.

---

## Architecture

### Routing layer

`_classify_data_source(query) -> str` runs before `_classify_tier`. Returns: `weather` | `academic` | `financial` | `factual` | `web`.

**Detection requires strict AND conditions (prevents over-triggering):**

```python
# Weather: signal AND (location OR temporal word)
WEATHER_SIGNALS = {"forecast", "weather in", "weather for", "temperature today",
                   "rain today", "rain tomorrow", "humidity", "wind speed",
                   "uv index", "hourly forecast", "24 hour", "weekend weather",
                   "will it rain", "going to snow"}
TEMPORAL_WORDS = {"today", "tomorrow", "tonight", "this weekend", "right now",
                  "this morning", "this evening", "currently", "now"}
# Fires only if: any signal present AND (location extractable OR temporal word present)

# Academic: explicit research vocabulary required
ACADEMIC_SIGNALS = {"peer reviewed", "peer-reviewed", "clinical trial", "meta-analysis",
                    "systematic review", "research on", "published paper", "arxiv",
                    "pubmed", "what does the science say", "what does the research say",
                    "scientific consensus", "randomized controlled", "rct",
                    "evidence for", "evidence against"}
# Fires only if: any signal present (these are specific enough)

# Financial: ticker OR financial vocabulary (no standalone all-caps)
FINANCIAL_SIGNALS = {"stock price", "share price", "trading at", "market cap",
                     "stock today", "crypto price", "bitcoin price",
                     "dow jones", "s&p 500", "nasdaq"}
TICKER_RE = r'\b[A-Z]{2,5}\b'  # Only if adjacent to financial vocabulary
# Fires if: financial signal present OR recognized ticker in COMPANY_TICKER_MAP

# Factual: prefix AND length AND proper noun
FACTUAL_SIGNALS_PREFIX = {"what is ", "what are ", "who is ", "who was ",
                           "where is ", "when was ", "when did ", "define "}
# Fires only if: prefix match AND non-stopword count < 8 AND proper noun detected
```

**Priority ordering for mixed queries:**
`financial > weather > academic > factual > web`

Financial and weather return live data that DDG fundamentally cannot provide — they win. Academic and factual are lower priority. For genuinely ambiguous queries, route to `web`.

### Post-processing path (critical architectural decision)

All specialized handlers **return data only, never display**. A shared function `_display_specialized_result()` handles all post-processing:

```python
def _display_specialized_result(
    query: str,
    response: str,  # pre-formatted string (direct display) OR None (Claude handled it)
    sources: list[dict],
    handler_name: str,  # e.g. "Open-Meteo", "PubMed + arXiv"
    t0: float,
    streaming: bool = False,  # True if Claude already streamed during handler
) -> None:
    # Handles: elapsed time display, _print_linked_sources, print_results,
    # _obsidian_save, save_session_entry, record_feature_use
```

Handler return type: `(response_str | None, sources_list, streaming: bool) | None`
- `None` → API failed, fall through to DDG
- `(response_str, sources, False)` → display pre-formatted string, then post-process
- `(None, sources, True)` → Claude already streamed during handler, just post-process

### Integration point in search_flow

```python
def search_flow(query, ...):
    # NEW: Try specialized handlers first
    t0 = time.time()
    source_type = _classify_data_source(query)
    if source_type != "web":
        result = _run_specialized_query(query, source_type, t0)
        if result is not None:
            return result  # early return with (results, response)

    # Existing pipeline continues unchanged
    tier = _classify_tier(query)
    ...
```

`_run_specialized_query` dispatches to the appropriate handler and calls `_display_specialized_result` before returning.

---

## Design tokens (additions to design system)

New glyphs for financial displays:

| Token | Glyph | Unicode | Role |
|---|---|---|---|
| `GLYPH_UP` | `▲` | U+25B2 | Price/value increase. Financial zone only. |
| `GLYPH_DOWN` | `▼` | U+25BC | Price/value decrease. Financial zone only. |
| `GLYPH_FLAT` | `→` | U+2192 | No meaningful change. Financial zone only. |

New helper function:
```python
def print_section_break(label: str) -> None:
    """Section divider within answer zone. Used in 48h weather view."""
    width = _term_width()
    label_str = f" {label} "
    dashes = GLYPH_DIVIDER * max(0, width - len(label_str) - INDENT_SM)
    print(f"{' ' * INDENT_SM}{C_META}{label_str}{dashes}{C_RESET}")
```

---

## Handler 1: Weather (Open-Meteo)

**APIs** (both free, no key):
- Geocoding: `https://geocoding-api.open-meteo.com/v1/search?name=LOCATION&count=1`
  Returns: lat, lon, country_code, timezone
- Forecast: `https://api.open-meteo.com/v1/forecast?latitude=LAT&longitude=LON&hourly=temperature_2m,precipitation_probability,wind_speed_10m,weathercode&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&forecast_days=3&wind_speed_unit=mph&temperature_unit=UNIT`

**Unit detection:** Use `fahrenheit` if geocoded `country_code` is US, otherwise `celsius`.

**Timeouts:** 2 calls sequential. Budget: 1.5s geocoding + 2.0s forecast = 3.5s total.

**Location extraction:** Strip weather signals from query, extract remaining text as location. Fall back to `_extract_specific_entities()`.

**WMO code mapping (no emoji — use 8-char descriptions):**
```python
WMO_CODES = {
    0: "Sunny   ", 1: "Clear   ", 2: "P.Cloudy", 3: "Overcast",
    45: "Fog     ", 48: "Fog     ",
    51: "Drizzle ", 53: "Drizzle ", 55: "Drizzle ",
    61: "Rain    ", 63: "Rain    ", 65: "Hvy Rain",
    71: "Snow    ", 73: "Snow    ", 75: "Hvy Snow",
    80: "Showers ", 81: "Showers ", 82: "Showers ",
    95: "Tstorm  ", 96: "Tstorm  ", 99: "Tstorm  ",
}
```

**Temperature coloring:** `≥86°F` → `C_INTERACTIVE` (amber). `≤44°F` → `C_META` (dim). Mid-range → `C_BODY` (default).

**Display — simple query:**
```
━━ Siloam Springs, AR — Forecast ━━━━━━━━━━━━━━━━━━━━━━━━━━   C_BRAND
↳ Open-Meteo · 36.19°N 94.49°W · Wed Jun 4 22:05              C_META
[SPACE_SM — 2 blank lines]
▸ TL;DR  Now 76°F partly cloudy, clear overnight, sunny tomorrow high 83°F.
[SPACE_XS]
  Now      76°F  P.Cloudy  Wind  6mph SW   0% rain
  11pm     72°F  Clear     Wind  4mph S    0% rain
  2am      65°F  Clear     Wind  3mph S    0% rain
  8am      68°F  P.Cloudy  Wind  5mph E    5% rain
  11am     78°F  Sunny     Wind  8mph SW   0% rain
  2pm      83°F  Sunny     Wind 10mph W    0% rain   ← 83°F in C_INTERACTIVE
  5pm      80°F  P.Cloudy  Wind  9mph SW  15% rain
[SPACE_XS]
  Tomorrow  High 79°F · Low 58°F · 20% rain
  Saturday  High 74°F · Low 55°F ·  5% rain
```

**Column widths:** TIME 8 · TEMP 6 · COND 10 · WIND 14 · RAIN 9. All at INDENT_SM.

**48-hour view:** Insert `print_section_break("Thursday, Jun 5")` between day blocks.

**Complex query (Claude synthesis):** Raw forecast JSON → Claude with existing `SEARCH_SYSTEM_CURRENT`. Daily summary rows appear after Claude response as supporting data. Hourly table omitted.

---

## Handler 2: Academic (PubMed + arXiv)

**APIs** (both free, no key):

PubMed (use text endpoint, not XML — more reliable):
- Search: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=QUERY&retmode=json&retmax=5&sort=relevance`
- Abstract text: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=IDS&rettype=abstract&retmode=text`
  (Returns plain text with no XML parsing required)
- Summary (for title/authors/year): `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=IDS&retmode=json`

arXiv (Atom XML — strip LaTeX):
- `https://export.arxiv.org/api/query?search_query=all:QUERY&start=0&max_results=5&sortBy=relevance`
- Strip LaTeX inline math: `re.sub(r'\$[^$]+\$', '[math]', text)` and `re.sub(r'\\[a-z]+\{[^}]*\}', '', text)`
- Use `published` field (not `updated`) for the year

**Parallelization:** Run PubMed and arXiv searches concurrently with `ThreadPoolExecutor(max_workers=2)`. Total timeout budget: 4 seconds.

**Display — direct (simple queries):**
```
━━ mRNA vaccine safety — Academic ━━━━━━━━━━━━━━━━━━━━━━━━   C_BRAND
↳ PubMed · arXiv · 2 results                                   C_META
[SPACE_SM]
▸ TL;DR  Strong safety profile confirmed across multiple large-cohort studies.
[SPACE_XS]
 1  Safety and Efficacy of mRNA COVID-19 Vaccines               C_INTERACTIVE
     Polack F et al., 2023 · New England Journal of Medicine  [PubMed]  C_META
     In a randomized trial of 43,548 participants, BNT162b2
     showed 95% efficacy with no serious safety concerns
     attributable to the vaccine…                              GLYPH_ELLIPSIS
[SPACE_XS]
 2  Myocarditis After mRNA COVID-19 Vaccination                 C_INTERACTIVE
     Oster M et al., 2022 · JAMA                             [PubMed]  C_META
     Among 192 million vaccine doses, 1,626 myocarditis cases
     were reported — predominantly males aged 12–39 after
     second dose. Most cases resolved within days…            GLYPH_ELLIPSIS
```

Paper cards: `INDENT_SM` for number + title, `INDENT_MD` for attribution + abstract.
Abstract: 3 lines truncated. `[PubMed]` or `[arXiv]` tag at end of attribution line.
Action zone: `expand abstract: 1–N   open DOI: o1–oN`

**Display — Claude synthesis (complex queries):**
```
▸ TL;DR  Evidence strongly supports mRNA vaccine safety...

Body with [Author et al., YEAR] inline citations...

**Limitations:** ...

 1  Safety and Efficacy... — Polack F et al., 2023  [PubMed]  ← title only, no abstract
 2  Myocarditis After... — Oster M et al., 2022    [PubMed]
```

**`SEARCH_SYSTEM_ACADEMIC` (new prompt constant):**
```
You are synthesizing peer-reviewed literature.
- First line: "▸ TL;DR  " followed by key finding + confidence level
- Cite as [Author et al., YEAR] — never fabricate citations
- Note study types (RCT, meta-analysis, observational, in vitro)
- Note sample sizes when given; distinguish correlation from causation
- End with "**Limitations:**" noting gaps in the evidence
- No filler phrases
```

**Inject preferences:** For Claude synthesis path, inject `_read_preferences()` into the prompt.

---

## Handler 3: Financial (Yahoo Finance)

**API** (unofficial, no key):
`https://query2.finance.yahoo.com/v8/finance/chart/{SYMBOL}?interval=1d&range=5d`

**Ticker detection:**
1. Check `COMPANY_TICKER_MAP` first (handles aliases: Google→GOOGL, Facebook→META)
2. All-caps 2-5 letter words ONLY if adjacent to financial vocabulary
3. Crypto names: bitcoin→BTC-USD, ethereum→ETH-USD
4. Indices: "S&P 500"→^GSPC, "Dow"→^DJI, "Nasdaq"→^IXIC

**Fallback on API failure:** Show `↳ live price unavailable — showing recent news` header, then run DDG pipeline for the same query. Do NOT silently fall through — users need to know the live data failed.

**Display:**
```
━━ AAPL — Apple Inc. ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   C_BRAND
↳ Yahoo Finance · Jun 4, 2026 22:05 ET · NYSE                 C_META
[SPACE_SM]
▸ TL;DR  Apple up 1.12% today at $193.42, near 52-week high.
[SPACE_XS]
  Price      $193.42   ▲ +$2.14  +1.12%    ← ▲ C_SPEED_FAST, % C_SPEED_FAST
  Day range  $191.50 – $194.30
  52-week    $142.86 – $201.55
  Mkt cap    $2.98T
  Volume     48.3M  (avg 54.1M)
[SPACE_XS]
  5d:  ▃▄▄▅▆▅▆█                            ← C_META prefix, last bar C_SPEED_FAST
[SPACE_XS]
↳ 0.3s · finance.yahoo.com · not financial advice            C_META
```

**Field alignment:** Field names in 11-char left-aligned column. Values flush after. INDENT_SM (2 spaces).

**Sparkline:** `▁▂▃▄▅▆▇█` (U+2581–U+2588). 5 daily close prices normalized to 0-7. Last bar colored with direction token.

**>2% move threshold:** If abs(pct_change) > 2%, show percentage in `C_INTERACTIVE` (amber) regardless of direction — signals notable volatility.

**Complex query:** Price data + DDG news → Claude with SEARCH_SYSTEM_CONTESTED. Required: "This is market data, not financial advice" appended to source attribution line.

---

## Handler 4: Factual (Wikipedia REST)

**API** (free, no key):
- Disambiguation: `https://en.wikipedia.org/w/api.php?action=opensearch&search=QUERY&limit=3&format=json`
- Summary: `https://en.wikipedia.org/api/rest_v1/page/summary/{TITLE}`
  Returns: `extract` (1-3 paragraphs), `description` (short string), `content_urls`

**Detection:** Prefix match AND non-stopword count < 12 AND proper noun detectable via `_extract_specific_entities()`.

**Display — standard entity:**
```
━━ Eiffel Tower ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   C_BRAND
↳ Wikipedia · Iron lattice tower, Paris, France               C_META  ← description field
[SPACE_SM]
▸ TL;DR  Wrought-iron lattice tower on the Champ de Mars,
         built 1887–1889 as the entrance arch for the World's Fair.
[SPACE_XS]
The Eiffel Tower is a wrought-iron lattice tower on the Champ de
Mars in Paris, France. It is named after the engineer Gustave
Eiffel, whose company designed and built the tower from 1887 to
1889 as the entrance arch for the 1889 World's Fair.
```

Show first paragraph in full. If under 80 words, show second paragraph too. Never three.

**Disambiguation — inline choice menu (NOT DDG fallback):**
```
━━ Mercury ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   C_BRAND
↳ Wikipedia · disambiguation                                   C_META
[SPACE_SM]
▸ TL;DR  Multiple Wikipedia articles match "Mercury" — choose one.
[SPACE_XS]
 1  Mercury (planet)    innermost planet in the Solar System
 2  Mercury (element)   toxic heavy metal, atomic number 80
 3  Freddie Mercury     lead vocalist of the rock band Queen
```

Action zone: `choose article: 1–3   open all: o1–o3`

---

## Shared post-processing

All handlers route through `_display_specialized_result()` which ensures:
1. `print()` elapsed time + attribution line  
2. `_print_linked_sources(sources)`
3. `print_results(sources)` with action zone
4. `_obsidian_save(query, response, sources, _obsidian_session_id())`
5. `save_session_entry(query, "search", summary)`
6. `record_feature_use("search")`

This keeps session continuity intact — weather and academic queries appear in vault notes and session context just like web searches.

---

## Error handling

| Condition | Behavior |
|---|---|
| API timeout (3.5s weather, 4s academic, 3s financial, 2s factual) | Return `None` → fall through to DDG |
| Parse failure | Return `None` → fall through to DDG |
| No results / geocode miss | Return `None` → fall through to DDG |
| Yahoo Finance rate limited | Show "live price unavailable" banner → DDG fallback |
| Wikipedia disambiguation | Show inline choice menu — do NOT fall through to DDG |

---

## New constants and functions

**Constants:** `WEATHER_SIGNALS`, `TEMPORAL_WORDS`, `ACADEMIC_SIGNALS`, `FINANCIAL_SIGNALS`, `TICKER_RE`, `FACTUAL_SIGNALS_PREFIX`, `WMO_CODES`, `COMPANY_TICKER_MAP`, `SEARCH_SYSTEM_ACADEMIC`, `GLYPH_UP`, `GLYPH_DOWN`, `GLYPH_FLAT`

**New functions:**
- `_classify_data_source(query) -> str`
- `_run_specialized_query(query, source_type, t0) -> tuple | None`
- `_display_specialized_result(query, response, sources, handler_name, t0, streaming)`
- `_handle_weather(query) -> tuple | None`
- `_handle_academic(query) -> tuple | None`
- `_handle_financial(query) -> tuple | None`
- `_handle_factual(query) -> tuple | None`
- `_extract_weather_location(query) -> str`
- `_format_weather_table(forecast_data, unit) -> str`
- `_build_sparkline(prices: list[float]) -> str`
- `print_section_break(label: str) -> None`

**Modified:** `search_flow()` — add specialized routing before tier classification
