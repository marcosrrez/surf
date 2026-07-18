# Web UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix sidebar visual hierarchy, add a cache/freshness indicator to research cards, switch input typography from mono to sans, and add a search filter over sidebar history — per `docs/superpowers/specs/2026-07-17-web-ui-polish-design.md`.

**Architecture:** All changes are in two files: `web/templates/index.html` (markup + inline vanilla JS) and `web/static/style.css`. No backend changes — every fix here is client-side (contrast/typography tokens, DOM rendering logic, `localStorage`-backed cache lookups). No new files, no new dependencies.

**Tech Stack:** FastAPI backend (untouched by this plan), server-rendered single-file frontend, vanilla JS (no framework, no build step), CSS custom properties for theming.

## Global Constraints

- No build step, no bundler, no new dependencies — this codebase is deliberately vanilla JS with a system-font stack (`--mono`/`--sans` in `style.css:21-22`, no `@font-face` anywhere). Do not introduce one.
- **No JS test framework exists in this codebase** (confirmed: no `package.json`, no test runner for `web/`; Python tests in `tests/` cover the backend only, via `pytest`). TDD's red/green cycle is adapted here to: write the code, then verify by hand — either a small throwaway Python script for pure CSS-math claims (contrast ratios), or the actual running app in a browser for anything involving DOM/interaction. This is a deliberate substitution, not a skipped step — every task still has a concrete, checkable "done" condition.
- Dev server: `cd ~/surf && source .venv/bin/activate && python3 surf_web.py` → serves at `http://localhost:3939`. `Ctrl+C` to stop. The server has no autoreload; restart it after editing `surf_web.py` (not needed for this plan — only static/template files change, and FastAPI serves `index.html`/`style.css` fresh on each request with no caching beyond the browser's own, which the `?v=` query param busts).
- CSS is served with a manual cache-bust query param on the `<link>` tag: `web/templates/index.html:7`, currently `/static/style.css?v=8`. Bump to `?v=9` once, in Task 1 — it covers every CSS-only change in this plan; no need to bump again per task.
- Every task's commit message must be a real, specific description of that task's change — no generic "update UI" messages.

---

### Task 1: Fix sidebar label contrast (`--faint` token)

**Files:**
- Modify: `web/static/style.css:5-40` (`:root` and `html.light` custom properties)
- Modify: `web/templates/index.html:7` (cache-bust bump)

**Interfaces:**
- Produces: corrected `--faint` values consumed by every existing selector that already references `var(--faint)` (`.rail-section-label`, `.rail-group-label`, `.rail-footer-text`, `.followup-label`, `.intent-badge`, `.source-num`, etc. — no selector changes needed, only the token value).

**Context:** `--faint` (dark: `#5a554f`, `html.light:32`: `#8a8075`) is the color used for every muted label in the app, including the sidebar's `THE VAULT`/`HISTORY` section labels and `today`/`earlier` group labels. Measured against `--surface`, it computes to ~2.43:1 contrast in dark mode and ~3.31:1 in light mode — both fail WCAG AA's 4.5:1 minimum for normal-size text. `--dim` (dark: `#8a8075`, light: `#5c5550`) already passes (~4.63:1 dark) and doesn't need changing.

- [ ] **Step 1: Write a throwaway contrast-check script**

Create `/tmp/contrast_check.py` (not part of the repo — delete after use):

```python
def relative_luminance(hex_color):
    hex_color = hex_color.lstrip('#')
    r, g, b = (int(hex_color[i:i+2], 16) / 255 for i in (0, 2, 4))
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = lin(r), lin(g), lin(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def contrast(hex1, hex2):
    l1, l2 = relative_luminance(hex1), relative_luminance(hex2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)

# Current values (for reference — confirm the failure before fixing)
print("dark  --faint (#5a554f) vs --surface (#181715):", round(contrast('#5a554f', '#181715'), 2))
print("light --faint (#8a8075) vs --surface (#f0ede8):", round(contrast('#8a8075', '#f0ede8'), 2))

# Proposed new values
print("dark  new --faint (#948a7d) vs --surface (#181715):", round(contrast('#948a7d', '#181715'), 2))
print("light new --faint (#635a4f) vs --surface (#f0ede8):", round(contrast('#635a4f', '#f0ede8'), 2))

# Confirm --dim is untouched and still passes
print("dark  --dim (#8a8075) vs --surface (#181715):", round(contrast('#8a8075', '#181715'), 2))
print("light --dim (#5c5550) vs --surface (#f0ede8):", round(contrast('#5c5550', '#f0ede8'), 2))
```

- [ ] **Step 2: Run it and confirm the fix passes AA**

Run: `python3 /tmp/contrast_check.py`
Expected output (approximately):
```
dark  --faint (#5a554f) vs --surface (#181715): 2.43
light --faint (#8a8075) vs --surface (#f0ede8): 3.32
dark  new --faint (#948a7d) vs --surface (#181715): 5.28
light new --faint (#635a4f) vs --surface (#f0ede8): 5.79
dark  --dim (#8a8075) vs --surface (#181715): 4.63
light --dim (#5c5550) vs --surface (#f0ede8): 4.63
```
The two "new" lines must both read ≥ 4.5. If either doesn't, adjust the hex value (lighter for dark mode, darker for light mode) and re-run before continuing.

- [ ] **Step 3: Apply the new token values**

In `web/static/style.css`, change line 12 (inside `:root`):
```css
  --faint: #5a554f;
```
to:
```css
  --faint: #948a7d;
```

And change line 32 (inside `html.light`):
```css
  --faint: #8a8075;
```
to:
```css
  --faint: #635a4f;
```

- [ ] **Step 4: Bump the CSS cache-bust version**

In `web/templates/index.html:7`, change:
```html
<link rel="stylesheet" href="/static/style.css?v=8">
```
to:
```html
<link rel="stylesheet" href="/static/style.css?v=9">
```

- [ ] **Step 5: Verify in the browser**

Run: `cd ~/surf && source .venv/bin/activate && python3 surf_web.py`
Open `http://localhost:3939` in a browser. Confirm:
- Sidebar section labels (`THE VAULT`, `HISTORY`) and group labels (`today`/`earlier`) are visibly lighter/more legible than before, in both dark and light mode (toggle via the `☀ light` / `🌙 dark` button in the rail footer).
- No layout shift or broken rendering elsewhere `--faint` is used (footer text, intent badges on a search result, source card metadata).

Stop the server (`Ctrl+C`) when done. Delete `/tmp/contrast_check.py`.

- [ ] **Step 6: Commit**

```bash
cd ~/surf
git add web/static/style.css web/templates/index.html
git commit -m "Fix --faint contrast to meet WCAG AA (2.43:1 dark, 3.31:1 light -> both >4.5:1)"
```

---

### Task 2: Sidebar structural hierarchy fix

**Files:**
- Modify: `web/templates/index.html:34` (vault section label markup)
- Modify: `web/templates/index.html:337-353` (`loadSidebarTags`)
- Modify: `web/static/style.css:154-226` (`#rail-scroll`, `.rail-thread`, `.rail-thread-text`)

**Interfaces:**
- Consumes: `.hidden` utility class (`style.css:974`, `display: none !important`) — already used throughout the codebase for this exact toggle pattern.
- No new functions produced; `loadSidebarTags()` keeps its existing signature (called with no arguments from page-load init code elsewhere in the file — do not change call sites).

**Context:** `THE VAULT` label (`index.html:34`) is static markup that always renders, even with zero vault tags — it currently sits empty, crammed against `HISTORY` right below. Separately, `.rail-thread-text` (the actual history item text) uses `--dim`, making the sidebar's primary clickable content less prominent than it should be. And `.rail-thread` rows have only 5px vertical padding, so items run together.

- [ ] **Step 1: Give the vault label an id and hide it by default**

In `web/templates/index.html`, change line 34:
```html
      <div class="rail-section-label">THE VAULT</div>
```
to:
```html
      <div class="rail-section-label hidden" id="vault-section-label">THE VAULT</div>
```

- [ ] **Step 2: Toggle the label's visibility based on tag count**

In `web/templates/index.html`, find `loadSidebarTags()` (currently lines 337-353):
```javascript
async function loadSidebarTags() {
  try {
    var resp = await fetch('/api/tags');
    var data = await resp.json();
    var list = document.getElementById('tag-list');
    list.innerHTML = '';
    var tags = data.tags || {};
    Object.entries(tags).slice(0, 15).forEach(function(entry) {
      var name = entry[0], count = entry[1];
      var btn = document.createElement('button');
      btn.className = 'rail-tag' + (activeTag === name ? ' active' : '');
      btn.addEventListener('click', function() { filterByTag(name); });
      btn.innerHTML = '<span class="rail-tag-name"># ' + escapeHtml(name) + '</span><span class="rail-tag-count">' + count + '</span>';
      list.appendChild(btn);
    });
  } catch(e) {}
}
```

Replace with:
```javascript
async function loadSidebarTags() {
  try {
    var resp = await fetch('/api/tags');
    var data = await resp.json();
    var list = document.getElementById('tag-list');
    list.innerHTML = '';
    var tags = data.tags || {};
    var tagEntries = Object.entries(tags).slice(0, 15);
    document.getElementById('vault-section-label').classList.toggle('hidden', tagEntries.length === 0);
    tagEntries.forEach(function(entry) {
      var name = entry[0], count = entry[1];
      var btn = document.createElement('button');
      btn.className = 'rail-tag' + (activeTag === name ? ' active' : '');
      btn.addEventListener('click', function() { filterByTag(name); });
      btn.innerHTML = '<span class="rail-tag-name"># ' + escapeHtml(name) + '</span><span class="rail-tag-count">' + count + '</span>';
      list.appendChild(btn);
    });
  } catch(e) {}
}
```

- [ ] **Step 3: Raise history item text to full contrast and add row breathing room**

In `web/static/style.css`, find (currently lines 203-225):
```css
.rail-thread {
  width: 100%;
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 5px 14px;
  border-radius: 7px;
  text-align: left;
  transition: background .15s ease;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
}
.rail-thread:hover { background: var(--hi); }
.rail-thread-text {
  font: 400 13px var(--sans);
  color: var(--dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}
```

Replace with:
```css
.rail-thread {
  width: 100%;
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 8px 14px;
  border-radius: 7px;
  text-align: left;
  transition: background .15s ease;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
}
.rail-thread:hover { background: var(--hi); }
.rail-thread-text {
  font: 400 13px var(--sans);
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}
```

- [ ] **Step 4: Add a single divider between static chrome and the dynamic list**

In `web/static/style.css`, find (currently lines 154-160):
```css
#rail-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 0 6px;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}
```

Replace with:
```css
#rail-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 12px 6px 0;
  border-top: 1px solid var(--border);
  margin-top: 4px;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}
```

- [ ] **Step 5: Verify in the browser**

Run: `cd ~/surf && source .venv/bin/activate && python3 surf_web.py`
Open `http://localhost:3939`. Confirm:
- With no vault tags (fresh install / no Obsidian vault configured), `THE VAULT` label does not appear at all — only `HISTORY` and its items show.
- If you have vault tags configured (`/api/tags` returns entries), clicking a tag still filters correctly and the label reappears.
- History item text (past search queries in the sidebar) reads clearly, close to full brightness — no longer visibly dimmer than the surrounding chrome.
- Rows have visible breathing room between them, not crammed together.
- A visible horizontal line separates the `+ new search` button area from the list below it, in both themes.
- Run at least one real search first (so `HISTORY` has content) to check all of the above with actual items present, not just the empty state.

- [ ] **Step 6: Commit**

```bash
cd ~/surf
git add web/templates/index.html web/static/style.css
git commit -m "Sidebar: hide empty vault label, raise item-text contrast, add row spacing + divider"
```

---

### Task 3: Distinguish stale-cache-rerun from never-cached in lookup logic

**Files:**
- Modify: `web/templates/index.html:449-457` (`getCachedResult`)
- Modify: `web/templates/index.html:747-761` (`doSearch`, cache-check block)

**Interfaces:**
- Produces: `findHistoryEntry(query)` — new function, returns the raw `searchHistory` entry for an exact query match (regardless of freshness), or `null`. Consumed by `getCachedResult` (refactored to use it) and by `doSearch` (to compute `wasStale`).
- Produces: local variable `wasStale` inside `doSearch`, consumed by Task 4's `renderCardIntent` call.

**Context:** `getCachedResult()` currently does its own `searchHistory.find()` and returns `null` both when no entry ever existed for a query *and* when an entry existed but failed the freshness check ("current"-tier queries >10min old, or any entry >24h old). `doSearch()` can't currently tell these two cases apart, which Task 4 needs to render the right badge (or no badge).

- [ ] **Step 1: Extract the raw lookup into its own function**

In `web/templates/index.html`, find (currently lines 449-457):
```javascript
function getCachedResult(query) {
  var entry = searchHistory.find(function(h) { return h.query === query; });
  if (!entry || !entry.answer) return null;
  var age = Date.now() - entry.time;
  var tier = (entry.intent && entry.intent.tier) || 'snippet';
  if (tier === 'current' && age > 10 * 60 * 1000) return null;
  if (age > 24 * 60 * 60 * 1000) return null;
  return entry;
}
```

Replace with:
```javascript
function findHistoryEntry(query) {
  return searchHistory.find(function(h) { return h.query === query; }) || null;
}

function getCachedResult(query) {
  var entry = findHistoryEntry(query);
  if (!entry || !entry.answer) return null;
  var age = Date.now() - entry.time;
  var tier = (entry.intent && entry.intent.tier) || 'snippet';
  if (tier === 'current' && age > 10 * 60 * 1000) return null;
  if (age > 24 * 60 * 60 * 1000) return null;
  return entry;
}
```

- [ ] **Step 2: Compute `wasStale` in `doSearch`**

In `web/templates/index.html`, find the start of `doSearch` (currently lines 747-761):
```javascript
function doSearch(e, forceFresh) {
  if (e) e.preventDefault();
  var q = document.getElementById('query').value.trim();
  if (!q) return;

  hideSuggestions();
  showResultsState();
  document.getElementById('followup-bar').classList.add('hidden');

  if (!forceFresh) {
    var cached = getCachedResult(q);
    if (cached) { renderCachedCard(cached); return; }
  }

  if (currentEvtSource) { currentEvtSource.close(); currentEvtSource = null; }
```

Replace with:
```javascript
function doSearch(e, forceFresh) {
  if (e) e.preventDefault();
  var q = document.getElementById('query').value.trim();
  if (!q) return;

  hideSuggestions();
  showResultsState();
  document.getElementById('followup-bar').classList.add('hidden');

  if (!forceFresh) {
    var cached = getCachedResult(q);
    if (cached) { renderCachedCard(cached); return; }
  }

  var wasStale = !forceFresh && !!findHistoryEntry(q);

  if (currentEvtSource) { currentEvtSource.close(); currentEvtSource = null; }
```

- [ ] **Step 3: Verify with the browser devtools console**

Run: `cd ~/surf && source .venv/bin/activate && python3 surf_web.py`
Open `http://localhost:3939`, open devtools console. Run a real search (e.g. "what is a vector database"), let it complete. Then in the console:

```javascript
findHistoryEntry("what is a vector database")   // should return the entry object, not null
findHistoryEntry("some query never searched")     // should return null
```

Then manually age an entry to force the stale path:
```javascript
var h = JSON.parse(localStorage.getItem('surf_history'));
h[0].time = Date.now() - 25 * 60 * 60 * 1000;  // 25 hours ago, past the 24h ceiling
localStorage.setItem('surf_history', JSON.stringify(h));
searchHistory = h;  // sync the in-memory copy the running page is using
getCachedResult(h[0].query)   // should now return null (stale)
findHistoryEntry(h[0].query)  // should still return the entry (proves the distinction works)
```

- [ ] **Step 4: Commit**

```bash
cd ~/surf
git add web/templates/index.html
git commit -m "Distinguish stale-cache-rerun from never-cached in doSearch lookup"
```

---

### Task 4: Render cache/freshness badge on research cards

**Files:**
- Modify: `web/templates/index.html:556-567` (`renderCardIntent`)
- Modify: `web/templates/index.html:780-784` (SSE `'intent'` handler in `doSearch`)

**Interfaces:**
- Consumes: `wasStale` from Task 3, `findHistoryEntry`/`getCachedResult` from Task 3.
- Modifies signature: `renderCardIntent(cardId, intent)` → `renderCardIntent(cardId, intent, freshness)`. `freshness` is optional; existing call in `renderCachedCard` (`index.html:724`, `renderCardIntent(cardId, entry.intent)`) is a 2-arg call and needs no change — `freshness` will be `undefined` there, which is correct (a live cache hit shows no badge).

**Context:** Only one state needs a visible badge: a query that *was* cached but expired, silently re-ran. A fresh cache hit and a genuinely first-time query both render identically today (no badge) — that's correct and unchanged.

- [ ] **Step 1: Add the freshness parameter and badge render**

In `web/templates/index.html`, find (currently lines 556-567):
```javascript
function renderCardIntent(cardId, intent) {
  var div = document.getElementById(cardId + '-intent');
  if (!div) return;
  div.classList.remove('hidden');
  div.innerHTML = '';
  [intent.tier, intent.domain, intent.answer_depth].filter(Boolean).forEach(function(label) {
    var badge = document.createElement('span');
    badge.className = 'intent-badge';
    badge.textContent = label;
    div.appendChild(badge);
  });
}
```

Replace with:
```javascript
function renderCardIntent(cardId, intent, freshness) {
  var div = document.getElementById(cardId + '-intent');
  if (!div) return;
  div.classList.remove('hidden');
  div.innerHTML = '';
  [intent.tier, intent.domain, intent.answer_depth].filter(Boolean).forEach(function(label) {
    var badge = document.createElement('span');
    badge.className = 'intent-badge';
    badge.textContent = label;
    div.appendChild(badge);
  });
  if (freshness === 'refreshed') {
    var fbadge = document.createElement('span');
    fbadge.className = 'intent-badge';
    fbadge.textContent = 'refreshed';
    div.appendChild(fbadge);
  }
}
```

- [ ] **Step 2: Pass `wasStale` through at the SSE call site**

In `web/templates/index.html`, find (currently lines 780-784):
```javascript
      case 'intent':
        currentIntent = data.content;
        renderCardIntent(cardId, currentIntent);
        if (statusEl) statusEl.textContent = currentIntent.tier + ' · ' + currentIntent.domain;
        break;
```

Replace with:
```javascript
      case 'intent':
        currentIntent = data.content;
        renderCardIntent(cardId, currentIntent, wasStale ? 'refreshed' : null);
        if (statusEl) statusEl.textContent = currentIntent.tier + ' · ' + currentIntent.domain;
        break;
```

- [ ] **Step 3: Verify in the browser**

Run: `cd ~/surf && source .venv/bin/activate && python3 surf_web.py`
Open `http://localhost:3939`.
1. Run a new search, e.g. "what is a vector database". Confirm the intent badge row shows tier/domain/depth with **no** "refreshed" badge (first-time query).
2. Click that same query again from the sidebar history immediately. Confirm it loads instantly (cached) with still no "refreshed" badge, and no new network request fires in the Network tab (filter by `EventSource`/`search`).
3. In devtools console, force staleness the same way as Task 3 Step 3 (`h[0].time = Date.now() - 25*60*60*1000`, update `localStorage` and `searchHistory`), then click that query again from the sidebar. Confirm: a new SSE request fires (visible in Network tab) **and** the resulting card's intent badge row now includes "refreshed" at the end, separated by the existing `·` separator (`.intent-badge + .intent-badge::before`, `style.css:550`).

- [ ] **Step 4: Commit**

```bash
cd ~/surf
git add web/templates/index.html
git commit -m "Add 'refreshed' badge when a stale cache entry silently re-runs"
```

---

### Task 5: Switch input typography from mono to sans, soften input shape

**Files:**
- Modify: `web/static/style.css:322-350` (`.input-box`, `.input-box.focused`, `.input-box textarea`)
- Modify: `web/static/style.css:355-372` (`#suggest-drop` radius)
- Modify: `web/static/style.css:391` (`.suggest-row span`)
- Modify: `web/static/style.css:514-519` (`.followup-query`)

**Interfaces:** None — pure CSS, no JS/markup changes, no new selectors.

**Context:** Per the design spec, the search/follow-up input's monospace font is genuinely harder to read as flowing prose than sans — this is the core typography fix. Two things must move with it in the same pass so nothing looks like an oversight: the autocomplete dropdown (`.suggest-row span`) shows the same class of content (raw query text) directly under the now-sans input, and `.followup-query` (currently mono) needs to match `.card-query-headline` (already sans, `style.css:448`) so a typed query renders the same way whether it's the first query or a follow-up. Also bumping `.input-box` border-radius and softening the focus ring, per the separate shape/premium-feel observation made while comparing input designs.

- [ ] **Step 1: Switch the textarea font and soften the box shape**

In `web/static/style.css`, find (currently lines 322-350):
```css
.input-box {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 13px;
  padding: 16px 20px;
  transition: border-color .15s ease, box-shadow .15s ease;
}
.input-box.focused {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--soft);
}

.input-box textarea {
  display: block;
  width: 100%;
  background: transparent;
  border: none;
  outline: none;
  resize: none;
  color: var(--text);
  font: 400 17px/1.5 var(--mono);
  padding: 0;
  min-height: 28px;
  max-height: 200px;
  overflow-y: hidden;
  field-sizing: content;
}
```

Replace with:
```css
.input-box {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 22px;
  padding: 16px 20px;
  transition: border-color .15s ease, box-shadow .15s ease;
}
.input-box.focused {
  border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
  box-shadow: 0 0 0 4px var(--soft);
}

.input-box textarea {
  display: block;
  width: 100%;
  background: transparent;
  border: none;
  outline: none;
  resize: none;
  color: var(--text);
  font: 400 17px/1.5 var(--sans);
  padding: 0;
  min-height: 28px;
  max-height: 200px;
  overflow-y: hidden;
  field-sizing: content;
}
```

- [ ] **Step 2: Match the suggestion dropdown's corner radius**

In `web/static/style.css`, find (currently within lines 355-372):
```css
#suggest-drop {
  position: absolute;
  left: 0;
  right: 0;
  top: calc(100% + 8px);
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 13px;
  padding: 6px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, .28);
  z-index: 20;
  animation: fadeSlideIn .15s ease both;
}
```

Change `border-radius: 13px;` to `border-radius: 16px;` (leave every other line unchanged).

- [ ] **Step 3: Switch the suggestion row text to sans**

In `web/static/style.css`, find line 391:
```css
.suggest-row span { font: 400 14px var(--mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

Replace with:
```css
.suggest-row span { font: 400 14px var(--sans); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 4: Switch the follow-up query echo to sans**

In `web/static/style.css`, find (currently lines 514-519):
```css
.followup-query {
  font: 500 18px var(--mono);
  color: var(--text);
  letter-spacing: -.01em;
  line-height: 1.45;
}
```

Replace with:
```css
.followup-query {
  font: 500 18px var(--sans);
  color: var(--text);
  letter-spacing: -.01em;
  line-height: 1.45;
}
```

- [ ] **Step 5: Verify in the browser**

Run: `cd ~/surf && source .venv/bin/activate && python3 surf_web.py`
Open `http://localhost:3939`. Confirm:
- The empty-state search box and the "ask a follow-up…" box both render typed/placeholder text in the sans font, not mono — compare against the still-mono `surf` hero wordmark above it, which should look clearly like a distinct logo element, not inconsistent.
- Type at least 2 characters to trigger the autocomplete dropdown — its rows should also read in sans, matching the input above it.
- Run a search, then submit a follow-up. The follow-up's echoed query text (`↳ follow-up` line) should render in the same sans style as the original query headline at the top of the thread — both should look like the same kind of content now.
- The input box's corners should read visibly more rounded than before (compare against the copy button or other 8-9px-radius elements elsewhere on the page — the input should look distinctly softer). Click into the box and confirm the focus state is a soft glow rather than a hard bright blue line.

- [ ] **Step 6: Commit**

```bash
cd ~/surf
git add web/static/style.css
git commit -m "Switch input/suggestion/follow-up query typography from mono to sans; soften input shape"
```

---

### Task 6: Sidebar search over history

**Files:**
- Modify: `web/templates/index.html:33-38` (rail markup, add search toggle + input)
- Modify: `web/templates/index.html:376-413` (`renderThreadList`)
- Modify: `web/static/style.css` (new rules appended after the `.rail-thread`/`.vault-dot` block, currently ending at line 226)

**Interfaces:**
- Produces: `toggleRailSearch()`, `onRailSearchInput()`, `clearRailSearch()` — new functions, wired to new markup via `onclick`/`oninput`.
- Produces: module-level vars `railSearchQuery` (string, default `''`) and `railSearchOpen` (bool, default `false`).
- Consumes: `renderThreadList()` (modified, not renamed — same call sites elsewhere in the file keep working unchanged), `activeTag`, `vaultNotesCache`, `searchHistory`, `getDateGroup`, `addThreadItem`, `loadCachedOrSearch`, `escapeHtml` — all pre-existing, unchanged.

**Context:** Filters the sidebar's rendered history/vault list by substring match on the query text, reusing the same `.toLowerCase().includes()` idiom `getSuggestions()` already uses (`index.html:245-255`) — but applied without debouncing, since it runs over an in-memory array capped at 50 items, not a network call. ANDs with the existing `activeTag` tag filter rather than replacing it, and filters both the vault-notes group and the history groups (the same list the user is looking at, not two separate search behaviors).

- [ ] **Step 1: Add the search toggle and input to the rail markup**

In `web/templates/index.html`, find (currently lines 33-38):
```html
    <div id="rail-scroll">
      <div class="rail-section-label hidden" id="vault-section-label">THE VAULT</div>
      <div id="tag-list"></div>
      <div class="rail-section-label">HISTORY</div>
      <div id="thread-list"></div>
    </div>
```

Replace with:
```html
    <div id="rail-scroll">
      <div id="rail-search-wrap">
        <button id="rail-search-toggle" onclick="toggleRailSearch()" aria-label="Search history" title="Search history">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="6" cy="6" r="4.5" stroke="currentColor" stroke-width="1.3"/><line x1="9.5" y1="9.5" x2="13" y2="13" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
          <span>search history</span>
        </button>
        <div id="rail-search-input-wrap" class="hidden">
          <input type="text" id="rail-search-input" placeholder="search history…" oninput="onRailSearchInput()">
          <button id="rail-search-clear" onclick="clearRailSearch()" aria-label="Clear search">×</button>
        </div>
      </div>
      <div class="rail-section-label hidden" id="vault-section-label">THE VAULT</div>
      <div id="tag-list"></div>
      <div class="rail-section-label">HISTORY</div>
      <div id="thread-list"></div>
    </div>
```

- [ ] **Step 2: Add the toggle/input/clear functions**

In `web/templates/index.html`, find `renderThreadList()` (currently starts at line 376, right after the `/* ── Thread list ────────────────────────────────────────── */` comment). Add these three functions immediately **before** that comment block:

```javascript
/* ── Sidebar search ─────────────────────────────────────── */

var railSearchQuery = '';
var railSearchOpen = false;

function toggleRailSearch() {
  railSearchOpen = !railSearchOpen;
  document.getElementById('rail-search-toggle').classList.toggle('hidden', railSearchOpen);
  document.getElementById('rail-search-input-wrap').classList.toggle('hidden', !railSearchOpen);
  if (railSearchOpen) document.getElementById('rail-search-input').focus();
}

function onRailSearchInput() {
  railSearchQuery = document.getElementById('rail-search-input').value.trim().toLowerCase();
  renderThreadList();
}

function clearRailSearch() {
  railSearchQuery = '';
  document.getElementById('rail-search-input').value = '';
  toggleRailSearch();
  renderThreadList();
}

```

- [ ] **Step 3: Apply the filter in `renderThreadList`, AND'd with `activeTag`, across both groups**

In `web/templates/index.html`, find `renderThreadList()` (currently lines 376-413):
```javascript
function renderThreadList() {
  var container = document.getElementById('thread-list');
  container.innerHTML = '';

  if (activeTag && vaultNotesCache[activeTag] && vaultNotesCache[activeTag].length) {
    var lbl = document.createElement('div');
    lbl.className = 'rail-group-label';
    lbl.textContent = 'vault · #' + activeTag;
    container.appendChild(lbl);
    vaultNotesCache[activeTag].forEach(function(note) {
      addThreadItem(container, note.query, true, function() {
        document.getElementById('query').value = note.query;
        doSearch(new Event('submit'));
        if (window.innerWidth < 760) closeRail();
      });
    });
  }

  var items = searchHistory;
  if (activeTag) items = items.filter(function(h) { return h.query && h.query.toLowerCase().includes(activeTag.toLowerCase()); });

  var groups = { today: [], yesterday: [], earlier: [] };
  items.forEach(function(h) { var g = getDateGroup(h.time); (groups[g] || groups.earlier).push(h); });

  ['today', 'yesterday', 'earlier'].forEach(function(group) {
    if (!groups[group].length) return;
    var lbl = document.createElement('div');
    lbl.className = 'rail-group-label';
    lbl.textContent = group;
    container.appendChild(lbl);
    groups[group].forEach(function(h) {
      addThreadItem(container, h.query, false, function() {
        loadCachedOrSearch(h.query);
        if (window.innerWidth < 760) closeRail();
      });
    });
  });
}
```

Replace with:
```javascript
function renderThreadList() {
  var container = document.getElementById('thread-list');
  container.innerHTML = '';

  var vaultNotes = (activeTag && vaultNotesCache[activeTag]) || [];
  if (railSearchQuery) vaultNotes = vaultNotes.filter(function(n) { return n.query && n.query.toLowerCase().includes(railSearchQuery); });

  if (vaultNotes.length) {
    var lbl = document.createElement('div');
    lbl.className = 'rail-group-label';
    lbl.textContent = 'vault · #' + activeTag;
    container.appendChild(lbl);
    vaultNotes.forEach(function(note) {
      addThreadItem(container, note.query, true, function() {
        document.getElementById('query').value = note.query;
        doSearch(new Event('submit'));
        if (window.innerWidth < 760) closeRail();
      });
    });
  }

  var items = searchHistory;
  if (activeTag) items = items.filter(function(h) { return h.query && h.query.toLowerCase().includes(activeTag.toLowerCase()); });
  if (railSearchQuery) items = items.filter(function(h) { return h.query && h.query.toLowerCase().includes(railSearchQuery); });

  var groups = { today: [], yesterday: [], earlier: [] };
  items.forEach(function(h) { var g = getDateGroup(h.time); (groups[g] || groups.earlier).push(h); });

  ['today', 'yesterday', 'earlier'].forEach(function(group) {
    if (!groups[group].length) return;
    var lbl = document.createElement('div');
    lbl.className = 'rail-group-label';
    lbl.textContent = group;
    container.appendChild(lbl);
    groups[group].forEach(function(h) {
      addThreadItem(container, h.query, false, function() {
        loadCachedOrSearch(h.query);
        if (window.innerWidth < 760) closeRail();
      });
    });
  });
}
```

- [ ] **Step 4: Add CSS for the new elements**

In `web/static/style.css`, append immediately after the existing `.vault-dot` rule (currently line 226: `.vault-dot { color: var(--accent); font-size: 8px; flex-shrink: 0; }`):

```css

/* Rail search */

#rail-search-wrap {
  padding: 0 14px;
  margin-bottom: 12px;
}

#rail-search-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 6px 0;
  color: var(--faint);
  font: 400 12px var(--mono);
  transition: color .15s ease;
}
#rail-search-toggle:hover { color: var(--dim); }

#rail-search-input-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
}

#rail-search-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font: 400 13px var(--sans);
  min-width: 0;
}
#rail-search-input::placeholder { color: var(--faint); }

#rail-search-clear {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--faint);
  font: 400 15px var(--mono);
  line-height: 1;
  padding: 0 2px;
  flex-shrink: 0;
}
#rail-search-clear:hover { color: var(--text); }
```

- [ ] **Step 5: Verify in the browser**

Run: `cd ~/surf && source .venv/bin/activate && python3 surf_web.py`
Open `http://localhost:3939`, run 2-3 different searches so `HISTORY` has multiple entries. Confirm:
- A "search history" icon/label sits at the top of the rail's scrollable list, above `THE VAULT`/`HISTORY`.
- Clicking it reveals a text input and focuses it; the toggle button itself disappears while the input is open.
- Typing a substring of one of your past queries live-filters the `HISTORY` list to only matching items (no page reload, no network request).
- Typing something that matches nothing empties the list (no error, no crash).
- Clicking the `×` clears the input, restores the full list, and collapses back to the icon-only toggle.
- If you have vault tags: click a tag to set `activeTag`, then type in the sidebar search — confirm the list narrows *within* the tag-filtered set (both filters apply together), rather than the tag filter being dropped.

- [ ] **Step 6: Commit**

```bash
cd ~/surf
git add web/templates/index.html web/static/style.css
git commit -m "Add sidebar search filter over history, AND'd with active vault-tag filter"
```
