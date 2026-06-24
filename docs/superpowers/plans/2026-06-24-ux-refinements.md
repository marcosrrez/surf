# UX Refinements — Ephemeral Commentary + Rendering Fixes

## Context

Based on UX/design and content quality reviews of surf's research rendering:
1. Chesterton commentary should be **ephemeral** — streams live while waiting, then clears before the answer appears (like a loading animation with personality)
2. TL;DR is too long and buried under commentary — needs to be shorter and first
3. Line width is uncapped, making text hard to scan
4. Academic queries need better source retrieval (site-specific search for pubmed/arxiv)

## Changes

### 1. Ephemeral Chesterton Commentary

**Current:** Commentary prints as permanent `print()` lines that stay in the terminal output forever.

**Target:** Commentary appears line-by-line as sources are evaluated (using `print_status` style overwriting or sequential lines), then ALL commentary is cleared before the synthesis streams. The user sees Chesterton reacting in real-time, then the screen clears to show just the answer.

**Implementation in `_deep_research` (~line 3383):**
- Print each commentary line with `sys.stdout.write` + `\n`
- After all sources evaluated, use ANSI escape `\033[{N}A\033[J` to move cursor up N lines and clear everything below (where N = number of commentary lines printed)
- Then the synthesis streams cleanly below the header

**Fallback:** If terminal doesn't support ANSI cursor movement, commentary simply scrolls off as synthesis streams.

```python
# After commentary loop, clear all commentary lines:
if commentary_lines_printed > 0:
    sys.stdout.write(f"\033[{commentary_lines_printed}A\033[J")
    sys.stdout.flush()
```

### 2. TL;DR Length Enforcement

**Current:** System prompts say "one concise sentence" but the model often produces 3-5 lines.

**Target:** Add explicit character/word limits to the TL;DR instruction across all system prompts.

**In SEARCH_SYSTEM, SEARCH_SYSTEM_CURRENT, SEARCH_SYSTEM_RESEARCH, SEARCH_SYSTEM_CONTESTED, SEARCH_SYSTEM_EVALUATIVE:**
Change `"▸ TL;DR  " followed by one concise sentence` to:
`"▸ TL;DR  " followed by ONE sentence, maximum 30 words. This is a headline, not a paragraph.`

### 3. Academic Source Retrieval

**Current:** When `assess_intent` returns `source_strategy: "academic"`, the reformulated query gets general search terms.

**Target:** When source_strategy is `academic`, inject site-specific operators into the search query to prioritize peer-reviewed sources.

**In `search_flow` (~line 3380), after intent is available:**
```python
if intent and intent.get("source_strategy") == "academic":
    ddg_query += " site:pubmed.ncbi.nlm.nih.gov OR site:pmc.ncbi.nlm.nih.gov OR site:arxiv.org"
```

### 4. Line Width Capping (deferred)

Line width capping requires changes to `stream_to_terminal` which already does word-aware wrapping based on `_term_width()`. The issue is it uses the FULL terminal width. Changing this affects every search, not just research tier. **Defer** to a separate pass after the higher-priority items are tested.
