# surf

**Search that learns.** Terminal-native AI search with a memory.

One command. Clean answer. Sources evaluated for quality, not just listed. The more you search, the smarter it gets.

```
Google finds pages. Perplexity summarizes them. surf finds truth.
```

---

## See it in action

```bash
$ surf is intermittent fasting safe long-term

↳ reading 5 sources...
↳ [1] pmc.ncbi.nlm.nih.gov — a systematic review, the gold standard of evidence
↳ [3] nature.com — Nature: one does not argue with the venue, only the findings
↳ [5] healthblog.com — remarkable restraint in avoiding evidence

▸ TL;DR  Long-term safety remains unproven — a Cochrane review finds no
  advantage over standard calorie restriction, and observational data links
  extreme time restriction to 91% higher cardiovascular mortality risk.

  Your prior vault research covered weight loss mechanisms. What's new:
  the refeeding phase — not the fast itself — may drive any health benefits...

  Sources: pmc.ncbi.nlm.nih.gov · nature.com · utsouthwestern.edu
  ────────────────────────────────────────────────────
  1  Cochrane systematic review: IF vs calorie restriction
  2  Nature Communications: refeeding metabolism study
  3  AHA Newsroom: cardiovascular risk data

  read in terminal: 1–3   open in browser: o1–o3   summary: s1–s3
```

Notice: surf evaluated each source's quality live, cited a Cochrane review over SEO blogs, and built on research you did last week.

---

## What makes surf different

**Search that remembers.** Every search saves to a personal knowledge base. When you search a topic again, surf draws from your accumulated research — highlighting what's new, flagging contradictions, surfacing connections across topics you've explored.

**Sources scored, not just listed.** surf evaluates source quality on two axes — reliability (is this source trustworthy?) and credibility (does this specific piece show evidence?). PubMed outranks health blogs. Cochrane reviews outrank listicles. SEO farms get ignored.

**Semantic intent understanding.** surf understands *what you want*, not just the words you typed. "Is this safe?" triggers contested-tier evaluation with authoritative sources. "What happened today?" triggers current-events mode. "Translate hello to Japanese" gets an instant answer with no web search.

**Terminal-native.** Lives in your shell. Composes with pipes and scripts. `surf "CVEs for nginx 1.24" --json >> digest.txt`. No browser, no login, no context switch.

**Nearly free.** Claude Haiku at $1/month covers ~2,500 searches. Free fallbacks (Groq, Cerebras, Gemini) kick in automatically when the budget runs out.

**Private.** Your knowledge base stays on your machine. No tracking. No account required.

---

## Install

```bash
git clone https://github.com/marcosrrez/surf ~/surf
cd ~/surf
bash install.sh
```

Then:

```bash
surf what is a black hole
```

**Manual setup:**

```bash
git clone https://github.com/marcosrrez/surf ~/surf
cd ~/surf
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
alias surf='~/surf/.venv/bin/python3 ~/surf/surf.py'  # add to ~/.zshrc
```

---

## Configure

surf needs at least one API key. Claude is recommended — $1/month covers ~2,500 queries.

```bash
surf setup
```

Or manually:

```bash
mkdir -p ~/.config/surf
cat > ~/.config/surf/config << 'EOF'
ANTHROPIC_API_KEY=your-key-here    # claude.ai/settings — $1/mo for ~2500 queries

# Optional free fallbacks
# GROQ_API_KEY=your-key-here       # console.groq.com — free
# CEREBRAS_API_KEY=your-key-here   # inference.cerebras.ai — free
# GEMINI_API_KEY=your-key-here     # aistudio.google.com — free
EOF
```

**Provider chain:** Claude → Groq → Cerebras → Gemini → local Ollama. surf falls back automatically.

---

## Usage

```bash
# Search anything
surf what causes inflation
surf latest AI news
surf who won the game last night

# Specialized queries — routed to dedicated APIs
surf weather in Denver tomorrow          # Open-Meteo live forecast
surf NVDA stock price                    # Yahoo Finance real-time data
surf peer reviewed studies on anxiety    # PubMed + arXiv search
surf what is the Higgs boson             # Wikipedia entity lookup

# Deep research mode
surf --deep effects of social media on adolescent mental health

# Search your personal knowledge base
surf vault: what do I know about attachment theory?

# Fresh search (skip vault context)
surf --fresh anxiety in relationships

# Read any URL
surf https://arstechnica.com/science/2026/...

# Automation
surf "CVEs for nginx 1.24" --json | jq .tldr
```

### Interactive commands

After a search result:

| Key | Action |
|-----|--------|
| `1–9` | Read full article in terminal |
| `s1–s9` | Quick AI summary |
| `o1–o9` | Open in browser |
| Type a question | Follow-up with full context |
| `n` | New search |
| `q` | Quit |

### Conversational inputs

| What you type | What surf does |
|---------------|----------------|
| "why did that happen?" | Follow-up using prior context |
| "no, I meant 2022" | Fresh search with correction |
| "try harder" | Broadens search, retries |
| "what about the others?" | Parallel searches, streamed live |

---

## How it works

### Intent engine

surf classifies every query semantically — not with keyword matching, but with an 8B model that understands what you actually want:

- Simple facts → instant answer, no web search
- Current events → real-time sources, freshness-weighted
- Research questions → deep reading, academic sources prioritized
- Contested topics → multiple perspectives, evaluative voice
- Transactional → booking deep-links, price comparisons

### Source quality (the Chesterton brain)

Every source is scored on two axes (NATO Admiralty model):

- **Reliability** — is this source generally trustworthy? (.gov, PubMed, Nature → high. SEO blog → low)
- **Credibility** — does this specific piece show evidence? (data, methodology, citations → high. Marketing copy, listicles → low)

For research queries, surf reads the actual content and evaluates depth: word count, heading structure, methodology signals, limitation acknowledgment, factual density. Sources that show their work rank above those that don't.

### Knowledge base (vault)

Every search result saves to an Obsidian vault with:
- Topic-based filenames and 15-category auto-tagging
- Conversation threading (`sparked_by` links between related searches)
- Auto-generated topic maps (psychology, finance, sports, etc.)
- Cross-topic connection detection

When you search a topic you've researched before, surf injects your prior findings into the prompt — the AI builds on what you already know instead of repeating it.

---

## Claude Code integration (MCP)

surf runs as an MCP server inside Claude Code:

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

**Tools:** `search`, `weather`, `stock`, `academic`, `factual`, `read_url`. No extra API keys — Claude Code synthesizes using its own subscription.

---

## Cost

| Usage | Monthly cost |
|-------|-------------|
| ~10 searches/day | ~$0.12 |
| ~30 searches/day | ~$0.36 |
| ~80 searches/day | ~$0.96 |
| Over budget | Falls to free providers automatically |

---

## Requirements

- Python 3.10+
- macOS or Linux
- API key from [claude.ai](https://claude.ai/settings) (recommended) or any free provider

---

## License

[MIT](LICENSE)
