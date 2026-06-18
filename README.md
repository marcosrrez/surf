# surf

**AI-powered search in your terminal.** One command, clean answer, sources cited. No browser, no login, no subscription.

```
surf what is the UEFA Champions League final venue 2026
surf latest news on AI
surf AAPL stock price
surf peer reviewed studies on omega-3 and cognition
surf weather in Austin tomorrow
```

```
$ surf who won the 2026 Champions League final

▸ TL;DR  Real Madrid defeated Manchester City 2–1 in the 2026 Champions League
  final, held at the Metropolitano Stadium in Madrid on May 30, 2026.
  Vinicius Jr. scored the winner in the 87th minute.

  The match was settled in the final minutes after City leveled through
  Erling Haaland's 71st minute header. Madrid's victory marks their 16th
  European Cup title...

  ─────────────────────────────────────────────────────
  1  Champions League 2026 Final — UEFA.com
  2  Real Madrid vs Man City match report — BBC Sport
  3  Vinicius Jr. winner — The Athletic

  read in terminal: 1–3   open in browser: o1–o3   summary: s1–s3

› who scored first?
▸ TL;DR  Rodrygo opened the scoring for Real Madrid in the 34th minute...
```

---

## Why

Every AI search tool today asks you to stop and switch contexts — open a browser, navigate, wait, copy, paste back.

Developers live in terminals. surf brings the answer to where you already are. It searches the web, synthesizes a clean TL;DR, and streams it live — then shows numbered sources you can read in-terminal or open in a browser with `cmd+click`.

**What makes surf different:**
- **Terminal-native** — lives in your shell, composes with pipes and scripts
- **Conversational** — follow-up questions carry full context; push back ("try harder"), correct ("no, I meant 2022"), or expand scope ("what about the other groups?") and surf adapts
- **Specialized APIs** — live weather forecasts, real-time stock prices, PubMed/arXiv academic papers, and Wikipedia facts bypass DDG and go straight to authoritative sources
- **No account or subscription** — bring your own API key; free fallbacks (Groq, Gemini) kick in automatically
- **Privacy** — nothing stored except your session context; no tracking
- **Automation-ready** — `surf "CVEs for nginx 1.24" --json >> digest.txt`
- **Open source**

---

## Install

```bash
git clone https://github.com/marcosrrez/surf ~/surf
cd ~/surf
bash install.sh
```

`install.sh` creates the virtualenv, installs dependencies, and puts `surf` in `/usr/local/bin`. Then:

```bash
surf what is a black hole
```

**Manual setup (if you prefer):**

```bash
git clone https://github.com/marcosrrez/surf ~/surf
cd ~/surf
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
alias surf='~/surf/.venv/bin/python3 ~/surf/surf.py'  # add to ~/.zshrc
```

---

## Configure

surf needs at least one API key. Claude is the recommended starting point — $1/month covers ~2,500 queries.

```bash
mkdir -p ~/.config/surf
cat > ~/.config/surf/config << 'EOF'
# Required: at least one of these
ANTHROPIC_API_KEY=your-key-here    # claude.ai/settings — $1/mo for ~2500 queries

# Optional free fallbacks (used when Claude budget runs out)
GROQ_API_KEY=your-key-here        # console.groq.com — free, fast
GEMINI_API_KEY=your-key-here      # aistudio.google.com — free
CEREBRAS_API_KEY=your-key-here    # inference.cerebras.ai — free
EOF
```

**Provider chain:** Claude → Groq → Cerebras → Gemini → local Ollama. surf automatically falls back through the chain if a provider hits its rate limit or your Claude budget runs out.

---

## Usage

```bash
# Search anything
surf what causes inflation
surf latest news on Iran
surf who invented the telephone

# Specialized queries — surf routes these to dedicated APIs
surf weather in Denver tomorrow          # Open-Meteo live forecast
surf NVDA stock price                    # Yahoo Finance real-time data
surf peer reviewed studies on statins    # PubMed + arXiv search
surf what is the Coriolis effect         # Wikipedia entity lookup

# Read any URL (full article, stripped to text)
surf https://arstechnica.com/science/2026/...
surf en.wikipedia.org/wiki/Black_hole

# Automation
surf "CVEs for nginx 1.24" --json | jq .tldr
surf "current EURUSD rate" --json >> fx_log.jsonl

# Check Claude spend
surf --usage
```

### Interactive commands

After a search result appears:

| Key | Action |
|-----|--------|
| `1–9` | Read full article in terminal (reader mode) |
| `s1–s9` | Quick AI summary |
| `o1–o9` | Open in browser |
| Type a question | Follow-up with full session context |
| `n` | New search |
| `q` | Quit |

**Conversational inputs surf understands:**

| What you type | What surf does |
|---------------|----------------|
| "why did that happen?" | Follow-up using previous context |
| "no, I meant 2022" | Discards prior context, fresh search |
| "try harder" / "you missed some" | Broadens the search, tries again |
| "what about groups A through G?" | Parallel searches, streamed as they land |
| "cool" / "thanks" | Brief acknowledgment, stays in session |

Source links and domain names are **cmd+clickable** in any modern terminal (iTerm2, Terminal.app, Warp).

---

## Examples

```bash
# Deep research session
$ surf CRISPR off-target effects clinical trials
▸ TL;DR  Off-target edits remain the central safety concern in CRISPR clinical
  trials. A 2025 systematic review of 14 trials found off-target rates below 0.1%
  in therapeutic doses, but longitudinal data beyond 36 months is still sparse...
[sources from PubMed + arXiv]

› what do critics say about the safety data?
▸ TL;DR  The main critique is that current detection methods (GUIDE-seq, CIRCLE-seq)
  only catch edits at known off-target sites — novel sites may go undetected...

# Scope expansion (runs 6 searches in parallel, streams results as they land)
› what about the other major gene editing approaches?
↳ searching: "zinc finger nucleases clinical safety 2025"...
↳ searching: "TALENs off-target clinical trial data"...
↳ searching: "base editing prime editing safety comparison"...

# Pipe output
$ surf "WWDC 2026 announcements" --json > wwdc.json

# Check your Claude budget
$ surf --usage
Claude usage — 2026-06
  ████░░░░░░░░░░░░░░░░  $0.22 / $1.00
  540 queries  ·  $0.78 remaining
  ≈ 1,900 queries left this month
```

---

## How it works

**Query routing:** surf classifies each query before searching. Specialized queries skip DDG entirely:
- `weather in Austin tomorrow` → Open-Meteo live hourly forecast
- `AAPL stock price` → Yahoo Finance real-time price card with sparkline
- `peer reviewed studies on X` → PubMed + arXiv parallel search
- `what is the Higgs boson` → Wikipedia entity lookup with disambiguation

**Web search:** Everything else goes to DuckDuckGo (no tracking). Top results (titles + snippets) go to Claude Haiku for synthesis. For research/current-events queries, surf fetches full article content before synthesizing.

**Conversational engine:** surf classifies every input — follow-up, correction, redirect, scope expansion, or casual — and responds accordingly. "Try harder" triggers a broader retry; "what about all the others?" fans out into parallel searches.

**Session memory:** surf remembers your last 4 hours of searches, so follow-ups like "who replaced her?" resolve correctly without re-stating context.

**Article reader:** `1` reads the full article in-terminal with AI formatting. Full article mode, not a summary — Claude Haiku formats it as clean prose with a TL;DR header.

---

## Cost

With Claude Haiku 4.5 as the primary provider:

| Usage | Monthly cost |
|-------|-------------|
| ~10 searches/day | ~$0.12 |
| ~30 searches/day | ~$0.36 |
| ~80 searches/day | ~$0.96 |
| Over budget | Falls to free providers (Groq, Gemini) |

Add a free Groq key and you effectively get unlimited searches — Groq handles overflow when Claude's monthly $1 runs out.

---

## Claude Code integration (MCP)

surf can run as an MCP server inside Claude Code, giving Claude access to live search, weather, stocks, academic papers, Wikipedia, and URL reading — with no extra API keys. Claude Code synthesizes answers using its own subscription.

### Setup

Add this to `~/.claude/settings.json` (adjust the path to wherever you cloned surf):

```json
{
  "mcpServers": {
    "surf": {
      "command": "/Users/you/surf/.venv/bin/python3",
      "args": ["/Users/you/surf/surf_mcp.py"]
    }
  }
}
```

Restart Claude Code. You'll see surf listed under MCP servers. Claude will automatically use it when you ask questions that benefit from live data.

### Available tools

| Tool | What it does |
|------|-------------|
| `search` | DuckDuckGo web search, returns top 10 results |
| `weather` | Live forecast from Open-Meteo |
| `stock` | Real-time price from Yahoo Finance |
| `academic` | PubMed + arXiv paper search |
| `factual` | Wikipedia entity lookup |
| `read_url` | Fetch and extract any web page |

**No extra API keys needed.** surf does the data gathering; Claude Code synthesizes the answer.

---

## Requirements

- Python 3.10+
- macOS (Linux works, Windows untested)
- API key from [claude.ai](https://claude.ai/settings) (and optionally Groq, Gemini, Cerebras)

---

## License

MIT
