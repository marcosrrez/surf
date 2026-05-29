# surf — Product VT/O
*Draft v1 · May 28, 2026*

---

## Vision

Make the intelligence of AI search feel as natural as typing a command — so that getting answers never requires leaving where you work.

---

## The Problem

Every AI search tool today asks you to stop and switch. Open a browser. Log in. Start a conversation. Wait. Copy the answer. Go back to what you were doing.

For the people who live in terminals — developers, engineers, researchers, data scientists, power users — this is the wrong model. They don't want a conversation. They want an answer. Fast. In context. And then gone.

Claude Code is extraordinary. But it's a coding assistant that happens to search, costs $20/month, requires an account, and sends your queries to Anthropic. Perplexity is excellent. But it's a browser tab, not a tool.

Nobody has built the search layer for people who work in terminals.

---

## What surf Is

A single command that understands what you mean, searches the web intelligently, and returns a clean, sourced answer — streamed live, formatted beautifully, in your terminal.

```bash
surf latest news on Iran
surf flights XNA to LAX June 15
surf marcosgutierrezcounseling.com
surf who was the most important player in Arsenal's title win?
```

No browser. No login. No subscription. No waiting for a page to load.

---

## Target Users

**Primary: Terminal-native professionals**
Developers, DevOps engineers, data scientists, researchers, students. People for whom the terminal is the primary work environment, not a fallback. Estimated 30M+ developers worldwide. Growing fast.

**Secondary: Privacy-first users**
People who don't want their searches stored, their queries training AI models, or their data tied to an account. surf runs your API keys, your config, your code. Nothing leaves your machine except the query.

**Tertiary: Automation builders**
Engineers who need search as infrastructure — in cron jobs, scripts, CI pipelines, data workflows. `surf "CVEs for nginx v1.24" >> security_digest.txt` scheduled nightly. This is impossible with Claude or Perplexity. It's trivial with surf.

---

## Differentiators

| | surf | Claude Code | Perplexity | Google |
|---|---|---|---|---|
| Terminal-native | ✓ | ✓ | ✗ | ✗ |
| Truly pipeable/scriptable | **✓** | ✗ | ✗ | ✗ |
| Free at scale | **✓** | ✗ | ✗ | ✓ |
| No account required | **✓** | ✗ | ✗ | ✓ |
| Privacy (no data stored) | **✓** | ✗ | ✗ | ✗ |
| Open source / hackable | **✓** | ✗ | ✗ | ✗ |
| Multi-page deep crawl | ✓ | ✓ | ✓ | ✗ |
| AI-synthesized answers | ✓ | ✓ | ✓ | partial |
| Works in automation/cron | **✓** | ✗ | ✗ | via API |

surf's moat is the intersection of the first four rows. No competitor owns all of them simultaneously.

---

## Why Now

Three things converged in 2025-26:

1. **Free inference at scale.** Groq and Cerebras offer frontier-quality models at zero cost. A year ago, building a free AI search tool required VC money. Today it requires an API key.

2. **The terminal is having a moment.** Claude Code, Cursor, Warp, Fig. Developer tooling is moving back to the command line. The generation of developers coming up learned AI-first. They want AI in their terminal, not just in their browser.

3. **Search is broken and people know it.** Google results are SEO spam. Perplexity costs money. People are actively looking for better. The window to establish a new default is open.

---

## The Opportunity

**Short term:** A sharp, free tool that terminal users reach for daily. Grows through word of mouth and GitHub. Becomes the default answer for "how do I search the web from the terminal?"

**Medium term:** An open-source project with a community. Plugins for specific domains (legal research, medical, financial). Self-hostable enterprise version with private data sources.

**Long term:** The search intelligence layer. Not just a CLI tool — an API that any tool, script, or workflow can call. The thing you embed when you need AI-powered web intelligence without a browser.

---

## The One-Line Pitch

**surf is the search tool for people who don't want to leave the terminal to find answers — and the automation layer for workflows that need web intelligence without a browser.**

---

## On Perplexity

Perplexity is good. It is not inevitable. It lives in a browser tab — which means it competes with every other browser tab for attention. It requires an account. It costs money. It stores your searches. It has no automation story.

The terminal has none of those problems. A command you run is part of your workflow. It disappears when it's done. It composes with other tools. It runs at 3am in a cron job without you.

Perplexity has not run away with AI search. The space is early. The terminal approach is genuinely underserved. The window is open.

---

*Built in a single session. Already faster than opening a tab.*
