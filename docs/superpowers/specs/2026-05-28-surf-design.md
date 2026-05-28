# surf — Design Spec
**Date:** 2026-05-28
**Status:** Approved

## What It Is

A terminal tool that works like Perplexity AI: search or read any URL, get a clean AI-generated answer with sources, streamed word-by-word. Built for slow Wi-Fi — fetches only what's necessary, renders beautifully in the terminal using `rich`.

## Invocation

Single command. Auto-detects input type:

```bash
surf what is a black hole        # query → search flow
surf latest news on Iran         # query → search flow (current events)
surf nasa.gov/black-holes        # URL → read flow
surf https://wikipedia.org/...   # URL → read flow
```

Detection rule: if input contains a dot with no spaces and resembles a hostname/URL, treat as URL. Otherwise treat as search query.

## Search Flow

```
surf <query>
  1. Search DuckDuckGo → collect top 5 result snippets (title + description + url)
  2. Send query + all 5 snippets to Groq
  3. Stream Groq's response to terminal via `rich`
  4. After response: show numbered results list
  5. User types 1-9 → triggers Read Flow on that URL
```

**Groq system prompt (search mode):**
You are a precise research assistant. Given a query and search result snippets, write a direct answer. Start with a one-sentence TL;DR prefixed with "▸ TL;DR". Then write 2-4 short paragraphs of detail. Use **bold** for key terms. End with "Sources:" and list the domains used. Be concise. No filler. No "Great question!".

## Read Flow

```
surf <url>
  1. Fetch URL with requests (plain HTML, no JS execution)
  2. Strip HTML tags → extract readable text (BeautifulSoup)
  3. Truncate to first 6000 words (avoids context overflow)
  4. Send page title + text to Groq
  5. Stream Groq's response to terminal via `rich`
  6. After response: show 3-5 related topic suggestions (Groq-generated)
  7. User types a number → surf searches that related topic
```

**Groq system prompt (read mode):**
You are a precise content extractor. Given a webpage's text, write a clean summary. Start with a one-sentence TL;DR prefixed with "▸ TL;DR". Then write the key content in 3-6 paragraphs, preserving important facts and structure. Use **bold** for key terms, bullet lists where appropriate. End with "Related:" and suggest 3 related topics the user might want to explore next (numbered 1-3). No filler.

## Rendering

Uses Python `rich` library throughout:

- Header: `━━ [title] ━━━━━━━━━━━━━` in purple
- Source/metadata line: gray, below header
- TL;DR panel: yellow `▸` prefix, slightly indented
- Body: Markdown rendered (bold, bullets, headers)
- Divider between TL;DR and body: gray `────────────`
- Sources/Related: gray, at bottom
- Status messages (`↳ searching…`, `↳ asking Groq…`): gray, overwritten in place
- All output streams word-by-word via Groq's streaming API

Terminal width auto-detected; content wraps at `min(terminal_width, 100)` columns.

## Output Example

```
↳ searching DuckDuckGo...
↳ asking Groq...

━━ Latest News: Iran ━━━━━━━━━━━━━━━━━━━━━━━━━━━
reuters.com · bbc.com · apnews.com

▸ TL;DR  Iran's nuclear talks resumed in Geneva amid
  rising regional tensions following drone incidents.

Negotiations between Iran and Western powers entered
a new phase this week as both sides agreed to...

**Key developments:**
• Ceasefire talks extended through June
• Sanctions relief remains the central sticking point
• Regional allies watching Strait of Hormuz closely

────────────────────────────────────────────────
 1  Iran nuclear deal — Wikipedia
 2  Iran sanctions explained — BBC
 3  Latest from Tehran — Reuters
 4  Gulf tensions overview — AP News
 5  Iran economy under sanctions — FT

[ 1-5 ] read full article   [ q ] quit
```

## Architecture

Single Python script at `/usr/local/bin/surf`. No package structure needed.

```
surf (Python 3, ~250 lines)
├── classify_intent(query) → {intent, sub_type, open_url, tip, fetch_snippets}
├── detect_input_type(text) → "url" | "query"
├── open_in_browser(url) → None          [macOS: subprocess "open"]
├── search_flow(query)
│   ├── ddg_search(query) → list of {title, snippet, url}
│   ├── build_search_prompt(query, snippets) → str
│   └── stream_groq(prompt, system) → generator
├── read_flow(url)
│   ├── fetch_page(url) → raw_html
│   ├── extract_text(html) → clean_text
│   ├── build_read_prompt(title, text) → str
│   └── stream_groq(prompt, system) → generator
├── render_streaming(stream) → None
└── show_results(results) → user_choice
```

## Intent Classification

Before any search or fetch, a fast Groq call (`llama-3.1-8b-instant`) classifies the query and returns JSON:

```json
{
  "intent": "transactional",
  "sub_type": "flights",
  "open_url": "https://google.com/flights?f=JFK&t=LAX&d=2026-06-15",
  "tip": "Tue/Wed departures are cheapest for this route",
  "fetch_snippets": false
}
```

**Intent types:**
- `informational` → DDG snippets + streamed answer
- `current_events` → DDG snippets focused on news sources
- `how_to` → fetch best tutorial, format as numbered steps
- `transactional` → construct smart URL, open in browser, show tip
- `comparison` → multi-source fetch, comparison prose
- `instant` → answer directly (translate, calculate, define), no search
- `navigation` → open site directly

## Open in Browser

Any result can be opened in Safari/default browser via `open_in_browser(url)` which calls macOS `open`. Available as:
- `[ o ]` shortcut in results footer
- Automatic for `transactional` intent (with user confirmation)

## Dependencies

```
pip install groq rich requests beautifulsoup4
```

Four packages. All installable once pip is fixed on this system (bootstrap with `python3 -m ensurepip --upgrade`).

## Configuration

Loaded from `~/.config/surf/config`:
```
GROQ_API_KEY=...
```

## Groq Model

`llama-3.3-70b-versatile` — best quality on Groq's free tier, 128k context window (handles any article length), fast enough to stream visibly.

## Error Handling

- Network failure fetching page → show error, suggest trying again
- DDG returns no results → tell user, exit cleanly
- Groq API error → show message, include raw error for debugging
- Page too large → truncate to 6000 words, note truncation in output
- Non-article URL (login page, homepage with no content) → Groq will note "this page has limited readable content" naturally

## What's Out of Scope

- History / bookmarks
- Multiple tabs
- Image rendering
- JavaScript execution
- Saving articles offline
- User accounts or sync
