# Fulfilling "Search That Learns" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconnect `surf_web.py`'s `/api/search` to the already-built `surf_engine.search_events()` pipeline, thread the real (already-blended) Chesterton quality score into the live source cards instead of discarding it, and give the answer text Chesterton's actual voice instead of a generic analyst persona — per `docs/superpowers/specs/2026-07-18-fulfill-the-promise-design.md`.

**Architecture:** Three changes across two files. `surf_web.py`'s `/api/search` route shrinks from ~220 lines of duplicated pipeline logic to a thin SSE adapter around `surf_engine.search_events()`. `web/templates/index.html` gains one new SSE event handler and a small live-update helper for source quality. `surf.py`'s six answer-voice system prompts (plus `VAULT_ONLY_SYSTEM`) get a voice rewrite, format rules and tier gates untouched.

**Tech Stack:** FastAPI backend, vanilla JS frontend (no build step, no JS test framework — verification is real end-to-end search via the running dev server), `tests/test_engine.py` (existing, covers `surf_engine.py` directly).

## Global Constraints

- No build step, no bundler, no new dependencies.
- `surf.py` is shared by the CLI and (after Task 1) the web — Task 3's prompt changes reach both surfaces from one edit. Do not add web-specific or CLI-specific branches to these prompts.
- `surf_engine.search_events()` already saves to the vault internally (`surf_engine.py:98-108`, calls `surf._obsidian_save()`) and already saves session context (`save_session_entry`). After Task 1, `surf_web.py` must **not** also call these — that would double-save every search.
- Existing pre-existing test failures unrelated to this work: `tests/test_engine.py::TestSnippetFlow::test_citemap_matches_sources` and `tests/test_engine.py::TestEnginePureHelpers::test_merged_search_single_engine_when_only_ddg` — both fail identically on `main` before this plan's changes. Confirm the baseline (`21 passed, 2 failed` in `tests/test_engine.py`) before starting, and expect the same 2 failures throughout — do not attempt to fix them, they are out of scope.
- Dev server: `cd ~/surf && source .venv/bin/activate && python3 surf_web.py` → `http://localhost:3939`. No autoreload — restart after editing `surf_web.py` or `surf.py`.
- This is a materially higher-stakes change than prior UI-only rounds: Task 1 replaces the entire live search route. Every task in this plan requires a real, live, end-to-end search through the running dev server as part of verification — not just a syntax check.
- Do not deploy to the iMac as part of this plan. That happens after the whole branch is reviewed and merged, as a separate, explicit step the human will request.

---

### Task 1: Rewire `/api/search` to `surf_engine.search_events()`

**Files:**
- Modify: `surf_web.py:1-67` (imports, lazy-load globals, `_load_surf()` — all removed)
- Modify: `surf_web.py:189-409` (the `/api/search` route — replaced with a thin adapter)

**Interfaces:**
- Consumes: `surf_engine.search_events(query: str, fresh: bool = False) -> Iterator[dict]` (`surf_engine.py:174`) — already exists, do not modify it in this task.
- Produces: the same `/api/search` route signature (`GET /api/search?q=...&fresh=...`), same SSE wire format (`data: {json}\n\n` per event) — the frontend's `EventSource` connection in `index.html` needs no URL/protocol changes, only the event-handling changes in Task 2.

**Context:** `surf_web.py`'s current route independently reimplements intent assessment, search, ranking, quality retry, deep reading, and synthesis — all of which `surf_engine.search_events()` already does, better (fan-out, specialized data sources, citation verification, concurrent fetching, and the correctly-blended Chesterton score). This task deletes the duplicate implementation and its now-dead supporting code.

- [ ] **Step 1: Confirm the pre-existing test baseline**

Run: `cd ~/surf && source .venv/bin/activate && python3 -m pytest tests/test_engine.py -q`
Expected: `2 failed, 21 passed` — the two named pre-existing failures (`test_citemap_matches_sources`, `test_merged_search_single_engine_when_only_ddg`). If the numbers differ, stop and report — something else has changed and needs investigation before proceeding.

- [ ] **Step 2: Replace the file header — imports, dead globals, `_load_surf()`**

In `surf_web.py`, find (currently lines 1-67, everything from the top of the file through the end of `_load_surf()`, immediately before `@app.get("/", response_class=HTMLResponse)`):

```python
"""surf web UI — FastAPI app that wraps surf's search pipeline."""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import surf_config

app = FastAPI(title="surf", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "web", "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "web", "templates"))

# Import surf's core pipeline
from surf_backends import searxng_search, tavily_search, ddg_search, fetch_page, _is_spa_shell, _fetch_with_jina
from surf_backends import _get_search_backend
from surf_store import _vault_retrieve, _format_vault_context, _obsidian_save, _obsidian_session_id

# These imports need the full surf module loaded
_surf_loaded = False
_assess_intent = None
_score_source_quality = None
_filter_and_rank_results = None
_score_content_depth = None
_build_search_prompt = None
_stream_ai = None
_VAULT_CONTEXT_INSTRUCTION = ""
_SEARCH_SYSTEM = ""
_SEARCH_SYSTEM_RESEARCH = ""
_SEARCH_SYSTEM_CURRENT = ""
_SEARCH_SYSTEM_CONTESTED = ""


_quality_retry_search = None
_confidence_gate = None
_QUALITY_RETRY_THRESHOLD = 0.45


def _load_surf():
    global _surf_loaded, _assess_intent, _score_source_quality, _filter_and_rank_results
    global _score_content_depth, _build_search_prompt, _stream_ai
    global _VAULT_CONTEXT_INSTRUCTION, _SEARCH_SYSTEM, _SEARCH_SYSTEM_RESEARCH
    global _SEARCH_SYSTEM_CURRENT, _SEARCH_SYSTEM_CONTESTED
    global _quality_retry_search, _confidence_gate
    if _surf_loaded:
        return
    import surf
    _assess_intent = surf.assess_intent
    _score_source_quality = surf.score_source_quality
    _filter_and_rank_results = surf.filter_and_rank_results
    _score_content_depth = surf.score_content_depth
    _build_search_prompt = surf.build_search_prompt
    _stream_ai = surf.stream_ai
    _quality_retry_search = surf._quality_retry_search
    _confidence_gate = surf._confidence_gate
    _VAULT_CONTEXT_INSTRUCTION = surf.VAULT_CONTEXT_INSTRUCTION
    _SEARCH_SYSTEM = surf.SEARCH_SYSTEM
    _SEARCH_SYSTEM_RESEARCH = surf.SEARCH_SYSTEM_RESEARCH
    _SEARCH_SYSTEM_CURRENT = surf.SEARCH_SYSTEM_CURRENT
    _SEARCH_SYSTEM_CONTESTED = surf.SEARCH_SYSTEM_CONTESTED
    _surf_loaded = True
```

Replace with:

```python
"""surf web UI — FastAPI app that wraps surf's search pipeline."""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import surf_config

app = FastAPI(title="surf", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "web", "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "web", "templates"))

# Import surf's core pipeline
from surf_backends import _is_spa_shell, _fetch_with_jina
```

(`_is_spa_shell`/`_fetch_with_jina` are still used by `/api/article`, further down in the file — confirmed by `grep -n "_is_spa_shell\|_fetch_with_jina" surf_web.py` before this edit: both appear again at what are currently lines 126, 138, 147, all inside `/api/article`, none inside the route being replaced in Step 3.)

- [ ] **Step 3: Replace the `/api/search` route body**

In `surf_web.py`, find the entire route (currently lines 189-409, from `@app.get("/api/search")` through the `return StreamingResponse(...)` line immediately before the blank lines and `if __name__ == "__main__":`):

```python
@app.get("/api/search")
async def search(q: str, fresh: bool = False):
    """Stream search results as SSE events."""
    _load_surf()

    def generate():
        query = q.strip()
        if not query:
            yield f"data: {json.dumps({'type': 'error', 'content': 'Empty query'})}\n\n"
            return
        # ... (the entire ~220-line body: intent, instant route, search, retry,
        # ranking, quality retry, deep reading loop, synthesis, vault save)

    return StreamingResponse(generate(), media_type="text/event-stream")
```

Replace the entire route with:

```python
@app.get("/api/search")
async def search(q: str, fresh: bool = False):
    """Stream search results as SSE events via the shared surf_engine pipeline."""
    from surf_engine import search_events

    def generate():
        for event in search_events(q, fresh=fresh):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 4: Verify nothing else in the file references the removed names**

Now that both the header (Step 2) and the route body (Step 3) are replaced, run:
`cd ~/surf && grep -n "_load_surf\|_surf_loaded\|_assess_intent\|_score_source_quality\|_filter_and_rank_results\|_score_content_depth\|_build_search_prompt\|_stream_ai\b\|_VAULT_CONTEXT_INSTRUCTION\|_SEARCH_SYSTEM\b\|_SEARCH_SYSTEM_RESEARCH\|_SEARCH_SYSTEM_CURRENT\|_SEARCH_SYSTEM_CONTESTED\|_quality_retry_search\|_confidence_gate\|_QUALITY_RETRY_THRESHOLD\|_get_search_backend\|tavily_search\|ddg_search\|searxng_search\|fetch_page\b\|_vault_retrieve\|_format_vault_context\|_obsidian_save\|_obsidian_session_id" surf_web.py`
Expected: no matches at all. If anything matches, stop and report — something still depends on a name this task removed, and needs investigation before proceeding (do not just re-add the removed code without understanding why it's still referenced).

- [ ] **Step 5: Syntax check**

Run: `cd ~/surf && source .venv/bin/activate && python3 -c "import py_compile; py_compile.compile('surf_web.py', doraise=True); print('SYNTAX OK')"`
Expected: `SYNTAX OK`

- [ ] **Step 6: Re-run the engine test suite (confirms `surf_engine.py` itself is untouched and still healthy)**

Run: `cd ~/surf && source .venv/bin/activate && python3 -m pytest tests/test_engine.py -q`
Expected: same baseline as Step 1 — `2 failed, 21 passed`, same two named failures. Any *new* failure means something broke.

- [ ] **Step 7: Real end-to-end verification against the running server**

Run: `cd ~/surf && source .venv/bin/activate && python3 surf_web.py &` (background it, note the PID), then:

```bash
curl -s -N "http://localhost:3939/api/search?q=what+are+the+health+benefits+of+intermittent+fasting" --max-time 45 > /tmp/task1_verify.txt
cat /tmp/task1_verify.txt
```

Expected, checking the captured output by hand:
- An `intent` event with a real `tier`/`domain` classification.
- A `sources` event with a non-empty array of real domains (not an error).
- One or more `token` events whose concatenated `content` forms a real, readable answer (not empty, not an error message).
- A final `done` event.
- No `error` event type anywhere in the stream.

Then confirm the vault got exactly **one** note from this search (not zero, not two) — this machine's `OBSIDIAN_VAULT` is already configured to `/Users/marcos/Documents/SurfResearch` (confirmed present and correctly owned before starting this plan):
```bash
find /Users/marcos/Documents/SurfResearch -name "*intermittent*"
```
Confirm exactly one new `.md` file was created for this query, with real frontmatter (query, date, tags, sources) and the same answer text as the SSE stream. Delete this test note afterward so the vault stays clean for real use — this is a throwaway verification note, not real user research.

Kill the background server (`kill <PID>`) when done.

- [ ] **Step 8: Self-review**

Confirm: does the new route produce the exact same SSE wire format (`data: {json}\n\n` per line) the frontend already expects? Did you check that no other route in `surf_web.py` (`/api/tags`, `/api/suggest`, `/api/article`, `/api/vault-notes`) references anything you removed in Step 2? Is `surf_web.py` meaningfully shorter (should drop from ~410 lines to roughly 190)?

- [ ] **Step 9: Commit**

```bash
cd ~/surf
git add surf_web.py
git commit -m "Reconnect /api/search to surf_engine.search_events(), remove duplicate pipeline"
```

---

### Task 2: Frontend — commentary event, live quality-bar update

**Files:**
- Modify: `web/templates/index.html` (`renderCardSources`, the SSE `onmessage` switch statement — add one new shared helper function)

**Interfaces:**
- Consumes: the `'commentary'` SSE event (`{domain, comment, num, quality}`, `surf_engine.py:10, 301-302, 560-561` in the underlying `_deep_research`) — new to the frontend, carries the **corrected**, content-aware quality score. The `'reading'` event (`{domain, quality}`, `surf_engine.py:9, 542-543`) still exists but its `quality` is the **pre-blend, stale** value (fired as each source is *fetched*, before Chesterton evaluation runs) — do not use `'reading'`'s quality for the visible bar.
- Produces: `qualityMeta(q)` — new shared helper, returns `{label: string, color: string}`. Both `renderCardSources` and the new commentary handler call it, so the label/color mapping exists in exactly one place.

**Context:** Task 1 makes the corrected Chesterton score exist and stream to the browser. This task is what actually gets it in front of the user: source cards currently render once (pre-fetch score) and never update. The `'commentary'` event fires once per source, after the real read and Chesterton evaluation — that's the moment to update the specific card in place.

- [ ] **Step 1: Add a shared quality→label/color helper, used by both the initial render and the live update**

In `web/templates/index.html`, find `renderCardSources` (currently lines 639-671):

```javascript
function renderCardSources(cardId, sources) {
  var panel = document.getElementById(cardId + '-sources');
  if (!panel) return;
  panel.classList.remove('hidden');
  panel.innerHTML = '';
  sources.forEach(function(s, i) {
    var q = s.composite || 0.5;
    var qLabel = q >= 0.85 ? 'high' : q >= 0.65 ? 'solid' : 'fair';
    var qColor = q >= 0.8 ? 'var(--green)' : q >= 0.6 ? 'var(--yellow)' : 'var(--red)';
    var card = document.createElement('div');
    card.className = 'source-card';
    card.innerHTML =
      '<div class="source-header">' +
        '<span class="source-num">' + String(i + 1).padStart(2, '0') + '</span>' +
        '<span class="source-domain">' + escapeHtml(s.domain) + '</span>' +
        '<a class="source-ext" href="' + escapeHtml(s.url) + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">↗</a>' +
      '</div>' +
      '<div class="source-title">' + escapeHtml(s.title) + '</div>' +
      '<div class="source-bar"><div class="source-bar-fill" style="width:' + Math.round(q * 100) + '%;background:' + qColor + '"></div></div>' +
      '<div class="source-foot">' +
        '<span class="source-meta" style="color:' + qColor + '">' + qLabel + '</span>' +
        '<span class="source-read">read →</span>' +
      '</div>';
    var _px, _py;
    card.addEventListener('pointerdown', function(e) { _px = e.clientX; _py = e.clientY; });
    card.addEventListener('pointerup', function(e) {
      if (Math.abs(e.clientX - _px) < 8 && Math.abs(e.clientY - _py) < 8) {
        openReader(s.url, s.title, s.domain);
      }
    });
    panel.appendChild(card);
  });
}
```

Replace with:

```javascript
function qualityMeta(q) {
  return {
    label: q >= 0.85 ? 'high' : q >= 0.65 ? 'solid' : 'fair',
    color: q >= 0.8 ? 'var(--green)' : q >= 0.6 ? 'var(--yellow)' : 'var(--red)'
  };
}

function renderCardSources(cardId, sources) {
  var panel = document.getElementById(cardId + '-sources');
  if (!panel) return;
  panel.classList.remove('hidden');
  panel.innerHTML = '';
  sources.forEach(function(s, i) {
    var q = s.composite || 0.5;
    var meta = qualityMeta(q);
    var card = document.createElement('div');
    card.className = 'source-card';
    card.dataset.domain = s.domain;
    card.innerHTML =
      '<div class="source-header">' +
        '<span class="source-num">' + String(i + 1).padStart(2, '0') + '</span>' +
        '<span class="source-domain">' + escapeHtml(s.domain) + '</span>' +
        '<a class="source-ext" href="' + escapeHtml(s.url) + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">↗</a>' +
      '</div>' +
      '<div class="source-title">' + escapeHtml(s.title) + '</div>' +
      '<div class="source-bar"><div class="source-bar-fill" style="width:' + Math.round(q * 100) + '%;background:' + meta.color + '"></div></div>' +
      '<div class="source-foot">' +
        '<span class="source-meta" style="color:' + meta.color + '">' + meta.label + '</span>' +
        '<span class="source-read">read →</span>' +
      '</div>';
    var _px, _py;
    card.addEventListener('pointerdown', function(e) { _px = e.clientX; _py = e.clientY; });
    card.addEventListener('pointerup', function(e) {
      if (Math.abs(e.clientX - _px) < 8 && Math.abs(e.clientY - _py) < 8) {
        openReader(s.url, s.title, s.domain);
      }
    });
    panel.appendChild(card);
  });
}

function updateSourceQuality(cardId, domain, quality) {
  var panel = document.getElementById(cardId + '-sources');
  if (!panel) return;
  var cards = panel.querySelectorAll('.source-card[data-domain="' + CSS.escape(domain) + '"]');
  var meta = qualityMeta(quality);
  cards.forEach(function(card) {
    var fill = card.querySelector('.source-bar-fill');
    var metaEl = card.querySelector('.source-meta');
    if (fill) { fill.style.width = Math.round(quality * 100) + '%'; fill.style.background = meta.color; }
    if (metaEl) { metaEl.textContent = meta.label; metaEl.style.color = meta.color; }
  });
}
```

(Note: if two sources share a domain — e.g. two different articles both from the same site — `updateSourceQuality` updates all matching cards together, since the `'commentary'` event only carries `domain`, not a per-URL key. This is an accepted, documented simplification, not a bug to fix in this task.)

- [ ] **Step 2: Split the `'reading'` handler and add `'commentary'`**

In `web/templates/index.html`, find (currently lines 871-876):

```javascript
      case 'reading':
        if (statusEl) statusEl.classList.add('hidden');
        var r = data.content;
        addReadLine(cardId, r.domain + (r.comment ? ' — ' + r.comment : ''));
        readCount++;
        break;
```

Replace with:

```javascript
      case 'reading':
        if (statusEl) statusEl.classList.add('hidden');
        break;

      case 'commentary':
        var c = data.content;
        addReadLine(cardId, c.domain + (c.comment ? ' — ' + c.comment : ''));
        readCount++;
        updateSourceQuality(cardId, c.domain, c.quality);
        break;
```

- [ ] **Step 3: Syntax check the extracted script**

Run:
```bash
cd ~/surf && source .venv/bin/activate && python3 surf_web.py &
sleep 2
curl -s http://localhost:3939/ -o /tmp/index_check.html
python3 -c "
import re
html = open('/tmp/index_check.html').read()
scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
open('/tmp/task2_check.js', 'w').write('\n'.join(scripts))
"
node --check /tmp/task2_check.js && echo "JS SYNTAX OK"
```
Kill the background server afterward.

- [ ] **Step 4: Real end-to-end verification — confirm `'commentary'` events carry quality and the card structure supports the update**

Run the dev server again, then:
```bash
curl -s -N "http://localhost:3939/api/search?q=what+is+the+evidence+for+intermittent+fasting+improving+metabolic+health" --max-time 45 | grep '"type": "commentary"'
```
Expected: one or more `commentary` events, each with a `domain`, a `comment` string, and a `quality` number between 0 and 1. This confirms the data this task's frontend code depends on is really flowing — full DOM/visual confirmation (the bar actually animating in a browser) isn't possible without browser automation in this environment; note in the report that this was verified at the data level, not visually, and say so rather than claiming full visual confirmation.

- [ ] **Step 5: Self-review**

Confirm: does `renderCardSources` still render correctly with `qualityMeta` extracted (no leftover duplicate label/color logic anywhere)? Does the old `'reading'` case still safely hide the status element (no dropped functionality)? Is `card.dataset.domain` set on every card `renderCardSources` creates?

- [ ] **Step 6: Commit**

```bash
cd ~/surf
git add web/templates/index.html
git commit -m "Frontend: handle 'commentary' event, live-update source quality with the real Chesterton score"
```

---

### Task 3: Chesterton's voice in the answer prompts

**Files:**
- Modify: `surf.py` (`SEARCH_SYSTEM`, `SEARCH_SYSTEM_ACADEMIC`, `SEARCH_SYSTEM_EVALUATIVE`, `SEARCH_SYSTEM_CURRENT`, `SEARCH_SYSTEM_RESEARCH`, `SEARCH_SYSTEM_CONTESTED`, `VAULT_ONLY_SYSTEM` — seven string constants)

**Interfaces:** None — pure prompt-text changes, no function signatures change. Shared verbatim by the CLI and (after Task 1) the web.

**Context:** Every format rule and the `TIER GATE` line in each prompt stays **exactly as written** — only the persona-establishing opening sentences (the "Voice rules" framing) change, to actually invoke Chesterton's paradox/aphorism/wit-calibrated-to-evidence fingerprint instead of a generic "sharp analyst" persona. `_CHESTERTON_EVAL_SYSTEM` (`surf.py:1602`, unchanged in this task) is the calibration anchor — short, punchy, unmistakably him — not a license for long florid prose.

- [ ] **Step 1: `SEARCH_SYSTEM`**

Find (currently `surf.py:482`):
```python
SEARCH_SYSTEM = """You are a sharp, well-read research assistant with genuine opinions. You find topics interesting and it shows. You lead with the most surprising or counterintuitive finding, not the most obvious one. You state your read clearly — not "sources suggest" but what you actually think the evidence shows. You are honest about what you don't know, and you say so with wit rather than disclaimers.
```

Replace only this opening paragraph (everything from `Format rules (use exactly):` onward, currently lines 484-507, stays byte-for-byte identical):
```python
SEARCH_SYSTEM = """You are Chesterton reading the evidence — delighted by the concrete fact, impatient with the vague abstraction. You lead with the paradox: the thing that looks backwards until you look closer, or the plain point everyone's been dancing around. You state your read as a verdict, not a hedge — not "sources suggest" but what you actually think the evidence shows. Your wit is calibrated to what's earned: generous when a source has done real work, unsparing when it hasn't. You are honest about what you don't know, and you say so with the same directness, not disclaimers.
```

- [ ] **Step 2: `SEARCH_SYSTEM_ACADEMIC`**

Find (currently `surf.py:1991`):
```python
SEARCH_SYSTEM_ACADEMIC = """You are synthesizing peer-reviewed literature.

Format rules:
- First line: "▸ TL;DR  " followed by key finding + confidence level
- Cite inline as [Author et al., YEAR] — never fabricate citations
- Note study types (RCT, meta-analysis, observational, in vitro)
- Note sample sizes when given; distinguish correlation from causation explicitly
- End with "**Limitations:**" section noting gaps in the evidence
- No filler phrases"""
```

Replace with (only the opening line changes; every format rule is untouched):
```python
SEARCH_SYSTEM_ACADEMIC = """You are Chesterton synthesizing peer-reviewed literature — delighted by a well-designed study, out of patience with a poorly-designed one, and equally honest about both.

Format rules:
- First line: "▸ TL;DR  " followed by key finding + confidence level
- Cite inline as [Author et al., YEAR] — never fabricate citations
- Note study types (RCT, meta-analysis, observational, in vitro)
- Note sample sizes when given; distinguish correlation from causation explicitly
- End with "**Limitations:**" section noting gaps in the evidence
- No filler phrases"""
```

- [ ] **Step 3: `SEARCH_SYSTEM_EVALUATIVE`**

Find (currently `surf.py:2067`):
```python
SEARCH_SYSTEM_EVALUATIVE = """You are a precise research assistant evaluating a company, product, or service based on independent third-party sources.
```

Replace only this opening line (`Format rules:` through the end, currently lines 2069-2083, stays byte-for-byte identical):
```python
SEARCH_SYSTEM_EVALUATIVE = """You are Chesterton evaluating a company, product, or service — delighted by an independent number, allergic to a company's own adjectives about itself.
```

- [ ] **Step 4: `SEARCH_SYSTEM_CURRENT`**

Find (currently `surf.py:2234`):
```python
SEARCH_SYSTEM_CURRENT = """You are a sharp analyst synthesizing today's news with genuine opinions. You lead with what's actually surprising or significant — not just what happened, but what it means. You state your read clearly. When coverage is thin or contradictory, you say so in one sentence and explain why.
```

Replace only this opening paragraph (`Format rules:` through the end, currently lines 2236-2255, stays byte-for-byte identical):
```python
SEARCH_SYSTEM_CURRENT = """You are Chesterton reading today's news — quick to spot the paradox in what's being reported, out of patience with anyone burying the plain point under caveats. You lead with what's actually surprising or significant — not just what happened, but what it means. You state your read as a verdict. When coverage is thin or contradictory, you say so in one sentence and explain why.
```

- [ ] **Step 5: `SEARCH_SYSTEM_RESEARCH`**

Find (currently `surf.py:2257`):
```python
SEARCH_SYSTEM_RESEARCH = """You are a knowledgeable analyst explaining complex topics with genuine intellectual engagement. You make the interesting parts interesting. You synthesize across sources and state where you land — not "scholars debate" but what the evidence actually shows and where real uncertainty remains.
```

Replace only this opening paragraph (`Format rules:` through the end, currently lines 2259-2275, stays byte-for-byte identical):
```python
SEARCH_SYSTEM_RESEARCH = """You are Chesterton explaining a complex topic — delighted the moment the mechanism clicks into place, out of patience with anyone who makes it sound more complicated than it is. You synthesize across sources and state where you land — not "scholars debate" but what the evidence actually shows and where real uncertainty remains.
```

- [ ] **Step 6: `SEARCH_SYSTEM_CONTESTED`**

Find (currently `surf.py:2277`):
```python
SEARCH_SYSTEM_CONTESTED = """You are an intellectually honest analyst presenting competing views with genuine engagement. You steelman each side before offering your honest read. You are not a pushover — when evidence favors one side clearly, you say so. When it genuinely doesn't, you say that too, and explain why the disagreement persists.
```

Replace only this opening paragraph (`Format rules:` through the end, currently lines 2279-2293, stays byte-for-byte identical):
```python
SEARCH_SYSTEM_CONTESTED = """You are Chesterton weighing competing views — you steelman each side properly before you say what you actually think, because a paradox worth taking seriously deserves its best version first. You are not a pushover — when the evidence favors one side clearly, you say so. When it genuinely doesn't, you say that too, and explain why the disagreement persists.
```

- [ ] **Step 7: `VAULT_ONLY_SYSTEM`**

Find (currently `surf.py:516`):
```python
VAULT_ONLY_SYSTEM = """You synthesize a user's accumulated research on a topic. Same voice as always — sharp, direct, opinionated when the evidence warrants it.
```

Replace only this opening line (`Format rules (use exactly):` through the end, currently lines 518-526, stays byte-for-byte identical):
```python
VAULT_ONLY_SYSTEM = """You synthesize a user's accumulated research on a topic. Same voice as always — Chesterton's: direct, delighted by a real pattern, opinionated when the evidence warrants it.
```

- [ ] **Step 8: Syntax check**

Run: `cd ~/surf && source .venv/bin/activate && python3 -c "import py_compile; py_compile.compile('surf.py', doraise=True); print('SYNTAX OK')"`

- [ ] **Step 9: Confirm the CLI still runs**

Run: `cd ~/surf && source .venv/bin/activate && python3 surf.py what is the capital of France 2>&1 | head -30` (the query is a positional argument, per `surf.py`'s `argparse` setup — `parser.add_argument("input", nargs="*", ...)`). Expected: a real answer, no traceback. This is a smoke test that the shared prompt constants still import and format correctly — not a content judgment.

- [ ] **Step 10: Real end-to-end verification — read the actual voice, across two tiers**

Start the dev server, then run two real searches and read the full answer text:
```bash
curl -s -N "http://localhost:3939/api/search?q=is+it+better+to+rent+or+buy+a+home+right+now" --max-time 45 > /tmp/task3_contested.txt
curl -s -N "http://localhost:3939/api/search?q=what+is+2+plus+2" --max-time 20 > /tmp/task3_instant.txt
```
For the first (contested tier — should get the new voice): extract the `token` events' concatenated content and read it. In your report, quote the actual TL;DR line and at least one full paragraph, and assess against the design intent: does it read like a verdict, not a hedge? Is there an identifiable paradox-framing or aphoristic turn, not just confident-sounding prose? This is a subjective call — say so plainly rather than asserting certainty, and quote enough of the real output that the reviewer can independently judge.

For the second (simple/instant query — should NOT get the new voice, per the unchanged `TIER GATE`/instant-route behavior): confirm the answer is plain and short, with no Chesterton flourish. This confirms the tier gate still works exactly as before.

- [ ] **Step 11: Self-review**

Confirm: did every format rule and every `TIER GATE` line in all seven prompts survive completely unchanged — check with a diff, not memory. Does each new opening paragraph read as *him* — paradox, aphorism, evidence-calibrated wit — rather than just swapping in the word "Chesterton" without changing the substance?

- [ ] **Step 12: Commit**

```bash
cd ~/surf
git add surf.py
git commit -m "Give the answer prompts Chesterton's actual voice, not a generic analyst persona"
```
