# surf Web UI — Design Specification

## Layout Wireframe

```
┌─────────────────────────────────────────────────────┐
│                                                     │
│  STATE: EMPTY (home)                                │
│                                                     │
│              surf                                   │
│              search that learns                     │
│              ~~~~~~~~~~~~~~~~~~~~~~~~~~             │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │ ask anything...                          ⏎  │    │
│  │                                              │    │
│  │ (auto-growing textarea, 1-5 lines)           │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  recent                                             │
│  ┌─ query one ─────────── tldr preview... ─────┐    │
│  ┌─ query two ─────────── tldr preview... ─────┐    │
│                                                     │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│                                                     │
│  STATE: RESULTS (after search)                      │
│                                                     │
│  surf  + new                                        │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │ original query here                      ⏎  │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  ↳ drawing from 4 vault notes                       │
│                                                     │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐      │
│  │src 1 │ │src 2 │ │src 3 │ │src 4 │ │src 5 │      │
│  │██░░░░│ │████░░│ │██████│ │███░░░│ │█░░░░░│      │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘      │
│                                                     │
│  ┃ ↳ domain.com — Chesterton comment...             │
│  ┃ ↳ other.org — another comment...                 │
│  ┃                                                  │
│  ┃ ▸ TL;DR  One sentence answer headline.           │
│  ┃                                                  │
│  ┃ **Section header**                               │
│  ┃                                                  │
│  ┃ Body text with [source] citations inline.        │
│  ┃ More analysis...                                 │
│  ┃                                                  │
│  ┃                               ┌──────┐           │
│  ┃                               │ copy │           │
│  ┃                               └──────┘           │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │ ask a follow-up...                       ⏎  │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
└─────────────────────────────────────────────────────┘
```

Key change from current: **commentary and answer are one continuous panel** with the blue left border, not separate elements. The commentary is the preamble to the answer — they belong together.

## Component Inventory

| Component | Element | Purpose | Behavior |
|-----------|---------|---------|----------|
| **Brand header** | `h1` + tagline + wave | Identity, reset trigger | Centered on home, left-aligned collapsed. Click resets. |
| **Search input** | `<textarea>` | Query entry | Auto-grows 1→5 lines via `field-sizing: content`. Shift+Enter for newline, Enter submits. |
| **History list** | Div list | Recent searches | Shows on home below input. Click re-searches. Max 5 visible. |
| **Source cards** | Horizontal scroll | Source quality at a glance | Cards with domain, title, quality bar. Clickable → opens source. |
| **Vault line** | Text line | Knowledge base context | Blue accent. "drawing from N vault notes" |
| **Commentary trail** | Lines inside answer panel | Chesterton reactions | Appears line by line. Part of the answer block, not separate. |
| **Answer body** | Streaming text | The synthesized answer | Markdown rendered. TL;DR pill, bold headers, citation chips, bullets. |
| **Answer actions** | Button row | Copy, share | Bottom of answer panel. Appears on completion. |
| **Follow-up input** | `<textarea>` | Conversational continuation | Same auto-grow as main input. Appears on completion. |
| **"+ new" link** | Text button | Start fresh search | Next to collapsed logo. Resets everything. |

## State Machine

| State | What's visible | What's hidden | Primary focus |
|-------|---------------|---------------|---------------|
| **EMPTY** | Hero (centered, large), search input, history | Results, sources, answer, follow-up | Search input (autofocus) |
| **SEARCHING** | Hero (collapsed), search input (with query), status line (pulsing) | History, sources, answer | Status line |
| **SOURCES_LOADED** | Collapsed hero, input, source cards (fade in), vault line | Answer, follow-up | Source cards |
| **READING** | Above + commentary trail (lines appear one by one) | Answer body | Commentary trail |
| **STREAMING** | Above + answer panel (tokens streaming) | — | Answer body (auto-scroll) |
| **COMPLETE** | Above + answer actions + follow-up input | Status line | Follow-up input (autofocus) |

Transitions use `fadeSlideIn` animation (0.3s ease). Status line uses `pulse` animation during waiting states.

## Typography Scale

| Element | Font | Size | Weight | Color |
|---------|------|------|--------|-------|
| Hero h1 (home) | Monospace | 3rem / 48px | 700 | `--text` |
| Hero h1 (collapsed) | Monospace | 1.4rem / 22px | 700 | `--text` |
| Tagline | Monospace | 1rem / 16px | 400 | `--text-secondary` |
| Search input | Monospace | 15px | 400 | `--text` |
| Source domain | Monospace | 11px | 400 | `--text-secondary` |
| Source title | Sans-serif | 12px | 400 | `--text` |
| Status line | Monospace | 13px | 400 | `--text-secondary` |
| Vault line | Monospace | 13px | 400 | `--accent` |
| Commentary | Monospace | 13px | 400 | `--text-secondary` |
| TL;DR marker | Monospace | 14px | 700 | `--accent` on `--accent-dim` bg |
| Answer body | Sans-serif | 15px | 400 | `--text` |
| Answer headers | Sans-serif | 15px | 600 | `#fff` |
| Citation chip | Monospace | 11px | 400 | `--text-secondary` |
| Follow-up input | Monospace | 14px | 400 | `--text` |
| History query | Sans-serif | 13px | 400 | `--text` |
| History tldr | Sans-serif | 12px | 400 | `--text-dim` |
| Action buttons | Monospace | 12px | 400 | `--text-secondary` |

**Rule**: Monospace for brand/interface chrome (inputs, labels, metadata). Sans-serif for content (answers, history previews, source titles).

## Color Usage Rules

| Color | Variable | Use for | Never for |
|-------|----------|---------|-----------|
| `#58a6ff` | `--accent` | Wave, vault line, TL;DR pill, cite chips hover, focus rings, quality-high | Body text, backgrounds |
| `#1f3a5f` | `--accent-dim` | TL;DR pill bg, source card hover bg | Borders, text |
| `#e6edf3` | `--text` | Primary content, headings, input text | Metadata, labels |
| `#8b949e` | `--text-secondary` | Source domains, commentary, status, action buttons | Primary content |
| `#6e7681` | `--text-dim` | Placeholders, history tldrs, wave | Anything users must read |
| `#3fb950` | `--green` | Quality bar high (≥0.6) | Text, backgrounds |
| `#d29922` | `--yellow` | Quality bar mid (0.4–0.6) | Text, backgrounds |
| `#f85149` | `--red` | Quality bar low (<0.4) | Text, backgrounds |
| `#0d1117` | `--bg` | Page background | Cards, surfaces |
| `#161b22` | `--bg-surface` | Answer panel, input bg, history items | Page bg |
| `#1c2128` | `--bg-card` | Source cards, citation chips | Surfaces |
| `#30363d` | `--border` | All borders, dividers, commentary left border | Backgrounds |

**Rule**: The accent blue appears in exactly 3 structural places: wave motif, vault context, and the answer panel left border. Everything else uses it only on hover/focus.

## Animation Spec

| Element | Trigger | Animation | Duration | Easing |
|---------|---------|-----------|----------|--------|
| Hero collapse | Search submitted | `padding-top`, `font-size`, `text-align` | 0.4s | ease |
| Source cards | Sources loaded | `fadeSlideIn` (opacity 0→1, translateY 8→0) | 0.3s | ease |
| Commentary trail | Reading event | `fadeSlideIn` per line | 0.3s | ease |
| Answer panel | First token | `fadeSlideIn` | 0.3s | ease |
| Follow-up bar | Done event | `fadeSlideIn` | 0.3s | ease |
| Status line | Waiting | `pulse` (opacity 1→0.5→1) | 1.5s | ease-in-out, infinite |
| Source card hover | Mouse enter | `border-color`, `background` | 0.2s | ease |
| Citation chip hover | Mouse enter | `border-color`, `color` | 0.2s | ease |

**Rule**: No animation exceeds 0.4s. No bounce or spring — ease only. Animations serve clarity (showing where content appeared), never decoration.

## Mobile Breakpoints

| Breakpoint | Changes |
|------------|---------|
| `≤600px` | Padding 24→16px. Hero font 3→2.2rem. Source cards 200→160px. Input/follow-up font 16px + min-height 48px (prevents iOS zoom). Answer panel padding 24→16px. |
| `≤400px` | Source cards 160→140px. Hero font 2.2→1.8rem. Max 3 source cards visible. |

**Rule**: Mobile-first means content-first. No sidebar, no split layout. Single column always.

## Interaction Patterns

| Action | Input | Result |
|--------|-------|--------|
| Submit search | Enter in main input | Collapses hero, starts search |
| New line in input | Shift+Enter | Adds newline (textarea grows) |
| Submit follow-up | Enter in follow-up input | Runs new search with follow-up text |
| Reset | Click "surf" logo or "+ new" | Returns to home state |
| Copy answer | Click copy button | Copies plain text to clipboard, shows "copied" for 2s |
| Open source | Click source card | Opens URL in new tab |
| Open cited source | Click citation chip | Opens source URL in new tab |
| Re-search history | Click history item | Populates input and submits |
| Keyboard escape | Esc key | Focus main search input |

### Key implementation detail: auto-growing textarea

Replace `<input type="text">` with `<textarea>`:

```css
#query, #followup-input {
  field-sizing: content;  /* modern CSS — auto-grows */
  resize: none;
  min-height: 44px;       /* 1 line */
  max-height: 160px;      /* ~5 lines */
  overflow-y: auto;
}
```

```js
// Enter submits, Shift+Enter adds newline
textarea.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    doSearch(e);
  }
});
```

### Key implementation detail: unified commentary+answer panel

Remove the separate `#commentary-trail` div. Instead, render commentary lines inside the `#answer-panel` div, above the `#answer` div:

```html
<div id="answer-panel">
  <div id="commentary-trail"></div>  <!-- inside, not sibling -->
  <div id="answer"></div>
  <div id="answer-actions">...</div>
</div>
```

The blue left border (`border-left: 3px solid var(--accent)`) on the answer panel then visually connects commentary and answer as one continuous analysis block.
