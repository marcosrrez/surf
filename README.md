# surf

**AI-powered search in your terminal.** One command, clean answer, sources cited. No browser, no login, no subscription.

```
surf what is the UEFA Champions League final venue 2026
surf latest news on AI
surf marcosgutierrezcounseling.com
surf how do black holes form
```

![surf demo](docs/product/demo.gif)

---

## Why

Every AI search tool today asks you to stop and switch contexts — open a browser, navigate, wait, copy, go back.

Developers live in terminals. surf brings the answer to where you already are. It searches the web, synthesizes a clean TL;DR, and streams it live — then shows numbered sources you can read in-terminal or open in a browser with `cmd+click`.

**What makes surf different from Claude Code, Perplexity, or Google:**
- Terminal-native — lives in your shell, composes with pipes and scripts
- No account or subscription required (bring your own API key)
- Privacy — nothing stored, nothing sent except your query
- Automation-ready — `surf "CVEs for nginx 1.24" >> digest.txt`
- Open source

---

## Install

```bash
git clone https://github.com/marcosrrez/surf ~/surf
cd ~/surf
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then add surf to your PATH — add this to `~/.zshrc` or `~/.bashrc`:

```bash
alias surf='~/surf/.venv/bin/python3 ~/surf/surf.py'
```

Or install the shell wrapper:

```bash
sudo tee /usr/local/bin/surf > /dev/null << 'EOF'
#!/bin/zsh
exec ~/surf/.venv/bin/python3 ~/surf/surf.py "$@"
EOF
sudo chmod +x /usr/local/bin/surf
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

**Provider chain:** Claude → Groq → Cerebras → Gemini. surf automatically falls back through the chain if a provider hits its rate limit or your Claude budget runs out.

---

## Usage

```bash
# Search
surf what causes inflation
surf latest news on Iran
surf who invented the telephone

# Read any URL (full article, stripped to text — like Brave reader mode)
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
| `s1–s9` | AI summary of article |
| `o1–o9` | Open in browser |
| Type a question | Follow-up search with session context |
| `n` | New search |
| `q` | Quit |

Source links and domain names are **cmd+clickable** in any modern terminal (iTerm2, Terminal.app, Warp).

---

## Examples

```bash
# Research session with follow-ups
$ surf Pam Bondi attorney general
▸ TL;DR  Pam Bondi served as the 37th Attorney General of the United States...
[sources]
› Who replaced her?
▸ TL;DR  Todd Blanche was named Acting Attorney General after Bondi's removal...

# Pipe output to a file
$ surf "WWDC 2026 announcements" --json > wwdc.json

# Check your Claude budget
$ surf --usage
Claude usage — 2026-05
  ███░░░░░░░░░░░░░░░░░  $0.18 / $1.00
  450 queries  ·  $0.82 remaining
  ≈ 2,050 queries left this month
```

---

## How it works

1. Your query goes to DuckDuckGo (no tracking)
2. Top 5 results (titles + snippets) are sent to Claude Haiku
3. Claude streams a structured answer: TL;DR → detail → sources
4. The numbered source list lets you dig deeper without leaving the terminal

For URL reads: the page is fetched, HTML stripped, and the clean text is summarized by Claude in "full article" or "reader summary" mode.

Session memory: surf remembers your last 4 hours of searches, so follow-up questions like "who replaced her?" work with full context.

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

## Requirements

- Python 3.10+
- macOS (Linux works, Windows untested)
- API key from [claude.ai](https://claude.ai/settings) (and optionally Groq, Gemini, Cerebras)

---

## License

MIT
