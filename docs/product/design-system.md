# surf Design System
*Version 1.0 · May 2026*

---

## Philosophy

Every visual decision in surf resolves to a named token. No raw ANSI codes scattered through code. No hardcoded spacing numbers. No ad-hoc decisions about whether to add a blank line or not.

The result: surf looks intentional because it *is* intentional. Every space, every color, every character is the same token applied consistently — not a judgment call made in the moment.

---

## 1. The Grid

**Base unit: 1 terminal line.**

All vertical spacing is a multiple of 1. Four values exist. No others.

| Token | Lines | Meaning |
|---|---|---|
| `SPACE_NONE` | 0 | Elements that belong together. No visual gap. |
| `SPACE_XS` | 1 | Within a zone. Nearby related elements. |
| `SPACE_SM` | 2 | Between zones. Clear transition. **Answer begins here.** |
| `SPACE_MD` | 3 | Major section break. Reserved for dramatic transitions. |

The 2-line gap (`SPACE_SM`) is the most important decision in the system. It is the moment the eye understands: *searching is done, the answer begins.* It corresponds to the visual beat that Claude chat achieves between a user message bubble and the AI response.

---

## 2. Zones and Spacing Rules

Every element in surf belongs to one of five zones. Zone transitions are governed by the grid.

```
┌─────────────────────────────────┐
│ QUERY ZONE                      │  What you asked
│ ━━ question ━━━━━━━━━━━━━━━━━━  │
│ source · source · source        │
└────────────── SPACE_NONE ───────┘
┌─────────────────────────────────┐
│ CONTEXT ZONE                    │  When/where the answer came from
│ ↳ session · timestamp · tier   │
└────────────── SPACE_SM ─────────┘
                                       ← The key beat. Two blank lines.
┌─────────────────────────────────┐
│ ANSWER ZONE                     │  The response
│ ▸ TL;DR  One sentence answer.   │
│                                 │
│ Body paragraph.                 │
│                                 │
│ **Bold section** with facts.    │
└────────────── SPACE_XS ─────────┘
                                       ← One blank line. Metadata is a caption.
┌─────────────────────────────────┐
│ METADATA ZONE                   │  How long it took, what it cost
│ ↳ 7.5s · claude $0.11/$1.00    │
│ Sources: source · source        │
└────────────── SPACE_NONE ───────┘  (divider handles the visual break)
                                   ─────────────────────────────────
┌─────────────────────────────────┐
│ ACTION ZONE                     │  What you can do next
│  1  Result title                │
│     domain.com                  │
│  2  Result title                │
│                                 │
│  read in terminal: 1–N ...      │
│  tip: ...                       │
└────────────── SPACE_XS ─────────┘
                                       ← One blank line before interaction.
› ask a follow-up or type a new search
```

**Zone spacing rules — the complete rulebook:**

| From zone | To zone | Token | Lines |
|---|---|---|---|
| Query | Context | `SPACE_NONE` | 0 |
| Context | Answer | `SPACE_SM` | 2 |
| Answer | Metadata | `SPACE_XS` | 1 |
| Metadata | Action | `SPACE_NONE` | 0 (divider) |
| Action | Prompt | `SPACE_XS` | 1 |

---

## 3. Color Palette

Six semantic roles. One ANSI code per role. Never two elements of different meaning sharing a color.

| Token | ANSI | Color | Role |
|---|---|---|---|
| `C_BRAND` | `\033[35m` | Purple | Headers, the surf header bar. Brand identity. |
| `C_INTERACTIVE` | `\033[33m` | Amber | Numbers, shortcuts, tips, anything you can act on. |
| `C_ANSWER_MARK` | `\033[36m` | Cyan | The `▸` TL;DR marker only. Marks where the answer begins. |
| `C_ANSWER_TEXT` | `\033[1;97m` | Bold white | TL;DR sentence text only. The highest-contrast element. |
| `C_META` | `\033[90m` | Dim gray | All secondary information: timing, sources, domains, status, tips, dividers. |
| `C_BODY` | `""` | Default | Body text. Inherits terminal foreground. No ANSI code applied. |
| `C_BOLD` | `\033[1m` | Bold | **Bold** key terms mid-body. Paired with `C_BOLD_END`. |
| `C_BOLD_END` | `\033[22m` | Intensity reset | Ends bold without resetting color. Use instead of `C_RESET` mid-sentence. |
| `C_ERROR` | `\033[31m` | Red | Errors only. Fatal failures. |
| `C_RESET` | `\033[0m` | Reset | Full color reset. End of any colored span. |

**Speed-indexed timing color** (same amber/gray palette, semantically named):

| Token | ANSI | When |
|---|---|---|
| `C_SPEED_FAST` | `\033[32m` | Green — response ≤ 3s |
| `C_SPEED_MED` | `\033[33m` | Amber — response ≤ 8s (= `C_INTERACTIVE`) |
| `C_SPEED_SLOW` | `\033[90m` | Dim gray — response > 8s (= `C_META`) |

---

## 4. Glyph Vocabulary

Nine characters. One role per character. Never reused for a different purpose.

| Token | Glyph | Unicode | Role |
|---|---|---|---|
| `GLYPH_HEADER_FILL` | `━` | U+2501 | Thick horizontal bar. Header zone only. |
| `GLYPH_DIVIDER` | `─` | U+2500 | Thin horizontal rule. Separates action zone from metadata. |
| `GLYPH_TLDR` | `▸` | U+25B8 | TL;DR marker. Answer zone only, first line only. |
| `GLYPH_META` | `↳` | U+21B3 | Metadata lines: timing, status, session, tips. Never in body. |
| `GLYPH_PROMPT` | `›` | U+203A | Input prompt. Interaction point only. |
| `GLYPH_SEPARATOR` | `·` | U+00B7 | Inline separator: sources, domains, footer items. |
| `GLYPH_ELLIPSIS` | `…` | U+2026 | Truncation. Never three dots `...`. |
| `GLYPH_RANGE` | `–` | U+2013 | Ranges: `1–5`, `o1–o5`. En-dash, not hyphen. |
| `GLYPH_BULLET` | `•` | U+2022 | List bullets. Never `-` or `*`. |

---

## 5. Indent Tokens

Horizontal spacing is as deliberate as vertical.

| Token | Characters | Use |
|---|---|---|
| `INDENT_NONE` | 0 | Full-width elements: header bar, divider, body text |
| `INDENT_SM` | 2 | Result numbers prefix (` 1  title`), footer lines |
| `INDENT_MD` | 5 | Domain under result title (`     domain.com`) |

---

## 6. Applying the System

When adding a new element, answer three questions:

1. **Which zone does this belong to?** Query, Context, Answer, Metadata, or Action?
2. **What token governs the space before it?** Look up the zone transition table.
3. **Which color and glyph tokens apply?** Look up the palette and vocabulary.

Never introduce a spacing value, color, or character not in this document without adding it here first.

---

## 7. Reference Layout

Full annotated example of a search response:

```
                                         ← [before header: terminal newline]
━━ What all should I know about Claude ━━━━━━━━   C_BRAND + GLYPH_HEADER_FILL
anthropic.com · platform.claude.com               C_META + GLYPH_SEPARATOR
                                         ← SPACE_SM (2 blank lines — ANSWER BEGINS)
▸ TL;DR  Claude is Anthropic's AI...              C_ANSWER_MARK + C_ANSWER_TEXT
                                         ← SPACE_XS (1 blank line — within answer)
**Core strengths**                                C_BOLD
                                         ← SPACE_XS
• 1M token context window [1]                     C_BODY + GLYPH_BULLET
• Strong coding: 80.8% SWE-bench [2]
                                         ← SPACE_XS (1 blank line — answer → metadata)
↳ 9.5s · claude $0.11/$1.00                      C_SPEED_MED + C_META + GLYPH_META
Sources: anthropic.com · platform.claude.com      C_META + GLYPH_SEPARATOR
─────────────────────────────────────────         C_META + GLYPH_DIVIDER
 1  Introduction to Claude — Anthropic            INDENT_SM + C_INTERACTIVE + C_BODY
     anthropic.com                                INDENT_MD + C_META
 2  Models overview — Claude API Docs
     platform.claude.com
                                         ← SPACE_XS (1 blank line — action → prompt)
  read in terminal: 1–5   browser: o1–o5         C_META + C_INTERACTIVE + GLYPH_RANGE
  tip: press 1 to read in terminal...            C_META + GLYPH_META
                                         ← SPACE_XS
› ask a follow-up or type a new search           GLYPH_PROMPT + placeholder
```
