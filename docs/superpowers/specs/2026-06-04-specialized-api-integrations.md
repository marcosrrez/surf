# surf — Specialized API Integrations Design
**Date:** 2026-06-04
**Status:** Approved for implementation

---

## Problem

surf routes every query through DDG, which fundamentally cannot answer certain query categories well:
- **Weather**: DDG has no live hourly forecast data. "24-hour forecast for Siloam Springs" returns outdated cached pages.
- **Academic**: DDG finds *news articles about papers*, not the papers themselves. "What does the research say about X" surfaces Healthline, not PubMed.
- **Financial**: DDG can't return today's stock price. It returns yesterday's news articles about the price.
- **Factual entities**: DDG returns SEO content when Wikipedia has a definitive, authoritative answer.

The solution: a pre-routing layer that detects these query types and calls specialized APIs directly, bypassing DDG entirely for live/structured data.

---

## How leading AI search tools handle this

Perplexity searches weather.gov the same way surf does — their crawler has indexed those pages but can't pull live hourly data on demand. For weather, they give similar "see weather.gov" responses unless they have paid API partnerships.

The tools that handle this well (You.com, Wolfram Alpha, ChatGPT plugins) use **query-type routing**: a classifier detects "this needs live structured data" and calls a specific API directly. That's what surf is building.

---

## Architecture

### The routing layer

A new `_classify_data_source(query) -> str` function runs before `_classify_tier` for queries that could be specialized. Returns one of:

```
"weather"   — live forecast queries
"academic"  — peer-reviewed research queries
"financial" — stock/crypto/market price queries
"factual"   — known-entity definition queries
"web"       — everything else (existing DDG pipeline)
```

Integration point in `search_flow`:

```
Query
  ↓
_classify_data_source(query)
  ├── weather   → _handle_weather(query)   → (response, sources) | None
  ├── academic  → _handle_academic(query)  → (response, sources) | None
  ├── financial → _handle_financial(query) → (response, sources) | None
  ├── factual   → _handle_factual(query)   → (response, sources) | None
  └── web       → existing DDG pipeline (unchanged)
```

Each handler returns `(response_str, sources_list)` on success, or `None` on any failure. If `None`, `search_flow` falls through silently to DDG — users see a web search result, never an error.

### Hybrid display logic

The user asked for "C — hybrid": simple lookups display formatted data directly; complex queries feed structured data to Claude for natural language synthesis.

| Query type | Simple example | Complex example |
|---|---|---|
| Weather | "what's the weather in Siloam Springs" | "good weekend to hike?" |
| Academic | "when was the first mRNA vaccine paper" | "what does the research say about mRNA safety" |
| Financial | "Apple stock price" | "compare Apple vs Microsoft" |
| Factual | "what is the Eiffel Tower" | (always direct display) |

---

## Handler designs

### 1. Weather — Open-Meteo

**APIs:** Both completely free, no key required.
- Geocoding: `https://geocoding-api.open-meteo.com/v1/search?name=LOCATION&count=1`
- Forecast: `https://api.open-meteo.com/v1/forecast?latitude=LAT&longitude=LON&hourly=temperature_2m,precipitation_probability,wind_speed_10m,weathercode&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&forecast_days=3&wind_speed_unit=mph&temperature_unit=fahrenheit`

**Detection signals:**
```python
WEATHER_SIGNALS = {
    "forecast", "weather in", "weather for", "temperature",
    "rain today", "rain tomorrow", "humidity", "wind speed",
    "uv index", "hourly forecast", "24 hour", "weekend weather",
    "will it rain", "going to snow",
}
```

**Location extraction:** Strip weather-related words from the query, use remaining text as location string. Fall back to `_extract_specific_entities()` for proper nouns.

**Simple display (no LLM):**
```
━━ Siloam Springs, AR — Forecast ━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ↳ Open-Meteo · 36.19°N, -94.49°W · Jun 4, 2026 22:00

  Now     76°F  Partly cloudy    Wind 6mph SW    0% rain
  11pm    72°F  Clear            Wind 4mph S     0% rain
  2am     65°F  Clear            Wind 3mph S     0% rain
  5am     61°F  Clear            Wind 3mph SE    0% rain
  8am     68°F  Partly cloudy    Wind 5mph E     5% rain
  11am    78°F  Sunny            Wind 8mph SW    0% rain
  2pm     83°F  Sunny            Wind 10mph W    0% rain
  5pm     80°F  Partly cloudy    Wind 9mph SW   15% rain

  Tomorrow: High 79°F / Low 58°F  ·  20% rain chance
  Saturday: High 74°F / Low 55°F  ·  5% rain chance
```

**Complex display (Claude synthesis):** Raw forecast data as context, system prompt asks Claude to answer the specific question ("Saturday looks excellent for hiking — sunny, 74°F, winds under 8mph, negligible rain chance").

**Weather code mapping:** Open-Meteo returns WMO codes (0=clear, 1=mainly clear, 2=partly cloudy, 3=overcast, 45/48=fog, 51-67=drizzle/rain, 71-77=snow, 80-82=showers, 95=thunderstorm). Map to emoji: ☀ 🌤 ⛅ ☁ 🌧 🌨 ⛈

---

### 2. Academic — PubMed + arXiv

**APIs:** Both completely free, no key required.
- PubMed search: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=QUERY&retmode=json&retmax=5&sort=relevance`
- PubMed abstracts: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=IDS&rettype=abstract&retmode=xml`
- arXiv search: `https://export.arxiv.org/api/query?search_query=all:QUERY&start=0&max_results=5&sortBy=relevance`

**Detection signals:**
```python
ACADEMIC_SIGNALS = {
    "studies show", "peer reviewed", "peer-reviewed", "clinical trial",
    "meta-analysis", "systematic review", "research on", "published paper",
    "arxiv", "pubmed", "evidence for", "evidence against",
    "what does the science say", "what does the research say",
    "scientific consensus", "randomized controlled", "rct",
}
```

**Flow:**
1. Run PubMed and arXiv searches in sequence (arXiv for CS/physics, PubMed for medicine/biology)
2. Parse results into `{title, authors, year, abstract, source, link}`
3. Detect query complexity: simple lookup vs synthesis question
4. Simple: display top 2-3 paper cards with abstracts
5. Complex: feed all abstracts to Claude with `SEARCH_SYSTEM_ACADEMIC` prompt

**`SEARCH_SYSTEM_ACADEMIC`** (new prompt constant):
```
You are a precise research assistant synthesizing peer-reviewed literature.

Format rules:
- First line: "▸ TL;DR  " followed by the key finding with confidence level
- Cite inline as [Author et al., YEAR] — never fabricate citations
- Note study types: RCT, meta-analysis, observational, in vitro
- Note sample sizes when given
- Distinguish correlation from causation explicitly
- If consensus is contested, say so clearly
- End with "Limitations:" noting what these studies don't cover
```

**Result sources format:** Papers listed with DOI links (OSC 8 clickable), author, journal, year.

---

### 3. Financial — Yahoo Finance (unofficial)

**API:** No key required.
- Quote: `https://query2.finance.yahoo.com/v8/finance/chart/{SYMBOL}?interval=1d&range=5d`
- Returns: current price, previous close, day range, 52-week range, market cap, volume, 5-day history

**Ticker detection:**
- All-caps 1-5 letter sequences: `AAPL`, `MSFT`, `TSLA`, `BTC`
- Crypto: bitcoin→`BTC-USD`, ethereum→`ETH-USD`, dogecoin→`DOGE-USD`
- Indices: "S&P 500"→`^GSPC`, "Dow Jones"→`^DJI`, "Nasdaq"→`^IXIC`
- Company name lookup (top 100 companies): Apple→AAPL, Tesla→TSLA, Google→GOOGL, etc.

**Detection signals:**
```python
FINANCIAL_SIGNALS = {
    "stock price", "share price", "trading at", "market cap",
    "stock today", "crypto price", "bitcoin price", "coin price",
    "52 week", "market is", "dow jones", "s&p 500", "nasdaq",
}
```
Plus: all-caps 2-5 letter words that look like tickers (AAPL, TSLA, BTC).

**Simple display (no LLM):**
```
━━ AAPL — Apple Inc. ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ↳ Yahoo Finance · Jun 4, 2026 22:00 ET

  Price     $193.42     ▲ +2.14  (+1.12% today)
  Range     $191.50 – $194.30  (today)
  52-week   $142.86 – $201.55
  Mkt cap   $2.98T     Volume  48.3M
```

**Complex display (Claude synthesis):** Price data + DDG search for recent news, fed to Claude with SEARCH_SYSTEM_CONTESTED and required disclaimer: "This is market data, not financial advice."

**Rate limiting:** Yahoo Finance unofficial API may throttle. On failure: fall through to DDG silently.

---

### 4. Factual — Wikipedia REST API

**API:** Free, no key.
- Search: `https://en.wikipedia.org/w/api.php?action=opensearch&search=QUERY&limit=3&format=json`
- Summary: `https://en.wikipedia.org/api/rest_v1/page/summary/{TITLE}`
- Returns: extract (1-3 paragraphs), description, thumbnail URL, canonical link

**Detection signals:**
```python
FACTUAL_SIGNALS_PREFIX = {
    "what is ", "what are ", "who is ", "who was ", "where is ",
    "when was ", "when did ", "define ", "what does ", "what was ",
}
```
Plus: query contains a known proper noun (person, place, organization) and is under 8 words.

**Always direct display (no LLM needed):**
```
━━ Eiffel Tower ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ↳ Wikipedia

The Eiffel Tower is a wrought-iron lattice tower on the Champ de Mars
in Paris, France. It is named after the engineer Gustave Eiffel, whose
company designed and built the tower from 1887 to 1889 as the entrance
arch for the 1889 World's Fair.

  ↳ Full article: en.wikipedia.org/wiki/Eiffel_Tower  [cmd+click]
```

**Disambiguation:** If the search returns multiple results, pick the first match. If the extract is under 50 words (disambiguation page), fall through to DDG.

---

## Error handling

Each handler follows a three-layer fallback:

1. **API timeout** (3 seconds max per handler) → fall through to DDG
2. **Parse failure** (unexpected schema, bad JSON) → fall through to DDG
3. **No results** (geocode miss, no papers, unknown ticker) → fall through to DDG

All failures are silent. Users never see an error — they see a web search result. The only exception: rate limiting on Yahoo Finance, which shows `↳ financial data temporarily unavailable` once, then falls through.

**Timeout rationale:** 3 seconds. These APIs (especially Open-Meteo and Wikipedia) respond in under 500ms. A 3-second timeout catches genuine unavailability without impacting UX.

---

## Status indicators

Using existing design system tokens (`C_META`, `GLYPH_META`):

```
↳ fetching weather for Siloam Springs, AR...
↳ searching PubMed + arXiv for "mRNA vaccine safety"...
↳ fetching AAPL · Yahoo Finance...
↳ looking up "Eiffel Tower" · Wikipedia...
```

Status lines overwrite in place using `\r`, cleared before response displays.

---

## New constants and functions

**New constants:**
- `WEATHER_SIGNALS`, `ACADEMIC_SIGNALS`, `FINANCIAL_SIGNALS`, `FACTUAL_SIGNALS_PREFIX`
- `SEARCH_SYSTEM_ACADEMIC` — synthesis prompt for peer-reviewed queries
- `COMPANY_TICKER_MAP` — top 100 company name → ticker symbol

**New functions:**
- `_classify_data_source(query) -> str`
- `_handle_weather(query) -> tuple[str, list] | None`
- `_handle_academic(query) -> tuple[str, list] | None`
- `_handle_financial(query) -> tuple[str, list] | None`
- `_handle_factual(query) -> tuple[str, list] | None`
- `_extract_weather_location(query) -> str`
- `_format_weather_response(geocode_data, forecast_data, query) -> str`
- `_parse_pubmed_results(xml) -> list[dict]`
- `_parse_arxiv_results(xml) -> list[dict]`
- `_detect_ticker(query) -> str | None`

**Modified functions:**
- `search_flow()` — add data source routing before existing tier classification

---

## Testing

Unit tests mock all HTTP calls. Tests cover:
- Classification: each signal set correctly routes to the right handler
- Each handler: successful API response → formatted output
- Each handler: API failure → returns None → falls through
- Weather location extraction edge cases
- Ticker detection edge cases (AAPL vs lowercase, company names)
- Wikipedia disambiguation page detection
