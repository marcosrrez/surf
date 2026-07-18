# Fulfilling "search that learns": reconnect the real engine, thread the real score, find the real voice

Three related fixes to close the gap the 2026-07-18 audit found between what surf's web deployment claims and what it does. All three are grounded in code already read line-by-line during the audit and the brainstorming that followed — no new exploration assumed here.

## Context: what the audit established

- **Three independent search pipelines exist.** `surf.py` (CLI), `surf_engine.py` ("the unified search pipeline," fully built and tested, called from nowhere in the running app), and `surf_web.py`'s own hand-rolled reimplementation of the same flow, which is what every web visitor actually gets. `surf_engine.search_events()` (`surf_engine.py:174`) already has fan-out, specialized data sources, citation verification, concurrent fetching, and the correctly-blended Chesterton score — everything the audit found missing from the web path.
- **The Chesterton score is computed and thrown away in the web path.** `_chesterton_evaluate_sources()` returns a real `llm_score` per source; the CLI blends it into the composite (`surf.py:3550-3559`); `surf_web.py` discards it and recomputes a stale pre-fetch score instead (`surf_web.py:361-365`).
- **The vault path bug found while verifying the audit is now fixed** (`OBSIDIAN_VAULT` on the iMac now points at a real, writable path) — that work is done and out of scope here.
- **Vault scoping is intentionally single-tenant per deployment**, not a session/multi-user problem — confirmed out of scope for this spec (see "Out of scope" below).
- **Only one Chesterton-named prompt exists** (`_CHESTERTON_EVAL_SYSTEM`, `surf.py:1602`), confined to one-line source commentary in a side panel. The actual answer text — what every user reads — runs on `SEARCH_SYSTEM` and its tier siblings (`surf.py:482` and five others), a generic "sharp analyst" voice with no Chesterton fingerprint at all.

## 1. Reconnect `/api/search` to the real engine

**Problem.** `surf_web.py`'s `/api/search` route (`surf_web.py:189-409`) reimplements search, ranking, quality retry, deep reading, and synthesis independently of `surf_engine.search_events()` — the tested, more capable version of the exact same flow (`surf_engine.py:174-377`). `search_events()` already:
- Does fan-out sub-query search for research/contested tiers (`surf_engine.py:248-256`)
- Classifies and handles specialized data sources — weather/finance/academic/wiki (`surf_engine.py:218-223`)
- Runs citation verification (`surf_engine.py:362-366`)
- Generates related searches (`surf_engine.py:369-371`)
- Accepts a `context` parameter for cross-surface pronoun resolution (`surf_engine.py:174`)
- **Already saves to the vault correctly** — `_save()` (`surf_engine.py:98-108`) calls the same `surf._obsidian_save()` `surf_web.py` calls directly today, with the same `sparked_by` threading.
- Emits a **richer, differently-shaped event vocabulary** than `surf_web.py`'s own implementation (full list in `surf_engine.py:9-20`).

**Fix:**
- Replace the entire body of `surf_web.py`'s `/api/search` route with a thin wrapper: iterate `surf_engine.search_events(query, fresh=fresh)` and re-yield each event as an SSE `data:` line, unchanged. Delete `surf_web.py`'s own reimplementation of intent/search/rank/retry/deep-reading/synthesis (everything currently between the intent-classification call and the final `done` yield, `surf_web.py:189-409`).
- **Remove `surf_web.py`'s own vault-save block entirely** (`surf_web.py:394-405`) — `search_events()` already calls `_save()` internally (`surf_engine.py:374`) before yielding `done`. Leaving both in place would double-save every search.
- **Event-shape migration, frontend side** (`web/templates/index.html`):
  - `'reading'` changes shape from `{domain, comment, quality}` (today's `surf_web.py:365`) to `{domain, quality}` only (`surf_engine.py:9,301-302` — comment moves to a separate event). The existing handler (`index.html:871-876`) degrades safely (empty string where `comment` used to be) but **the commentary trail would silently stop showing text** unless a new handler is added.
  - Add a `case 'commentary':` handler for the new `{domain, comment, num, quality}` event (`surf_engine.py:10`), calling the same `addReadLine(cardId, ...)` function the old `'reading'` handler used, so "The Read" panel's behavior is preserved exactly, just sourced from the correct event.
  - `citemap`, `answer_card`, `verification`, `related` are new event types with no handler today. The frontend's `switch` statement has no `default` case, so unhandled types are silently ignored — **no frontend crash risk**, and no UI work is required for this task; these are real signals now available for future rounds, not wired to any display in this one.
- **`context` parameter:** accept it in the route signature (`context: str = ""`) and pass it straight through to `search_events()`, but do not build any frontend logic to populate it from conversation history in this task — that is a distinct design decision (how much history, what format) left for a future round. Passing an always-empty string preserves exactly today's behavior while leaving the plumbing in place.

## 2. Thread the corrected Chesterton score into the visible quality bar

**Problem, precisely located.** Even after the reconnect in §1, the `'sources'` event (which drives each source card's quality bar, `renderCardSources`, `index.html`) is yielded *before* deep reading happens (`surf_engine.py:280` vs. the deep-reading loop at `surf_engine.py:293-304`) — same ordering as today. The corrected, content-aware quality value only appears afterward, per-source, inside the `'reading'` event's `quality` field (`surf_engine.py:301-302`, sourced from `_deep_research`'s blended composite, the same one the CLI already uses correctly). Reconnecting to the engine makes the *correct number exist and stream to the client* — it does not, by itself, make the already-rendered quality bar reflect it.

**Fix (frontend only):**
- In `renderCardSources` (`index.html`, source-card creation loop), add `card.dataset.domain = s.domain;` when building each `.source-card` element, so a card can be located by domain after the fact.
- Add a live-update step inside the (new, from §1) `'reading'` event handler: look up the source card in the current card's `.card-sources` panel by `[data-domain="..."]` matching `ev.content.domain`, and if found, update its `.source-bar-fill` width/background and `.source-meta` label/color using the same quality→label/color mapping `renderCardSources` already uses (`q >= 0.85 ? 'high' : ...`, `q >= 0.8 ? 'var(--green)' : ...`) — do not duplicate the mapping as a second copy; factor it into one small shared function both call.
- No change needed to `renderCardSources` itself beyond the `dataset.domain` addition — the initial pre-fetch render stays as the starting point; the update only fires for sources that were actually deep-read (current/research/contested tiers), which is exactly the set that has a corrected number to show.

## 3. Chesterton's voice as the answer, not just the commentary

**Problem.** `SEARCH_SYSTEM` and its tier siblings (`SEARCH_SYSTEM_CURRENT`, `SEARCH_SYSTEM_RESEARCH`, `SEARCH_SYSTEM_CONTESTED`, `SEARCH_SYSTEM_ACADEMIC`, `SEARCH_SYSTEM_EVALUATIVE`, `VAULT_ONLY_SYSTEM` — all in `surf.py`) write in a generic "sharp analyst with opinions" register. `_CHESTERTON_EVAL_SYSTEM` (`surf.py:1602`) is the only prompt that actually names him and asks for his fingerprints — paradox, aphorism, wit that's "generous when deserved, devastating when not" — and it's confined to one-line source reactions.

**Direction, confirmed in brainstorming:** adopt his epistemology and rhetorical moves — paradox-framing, aphoristic clarity, delight in the concrete, common-sense wit that cuts through cant — not his period sentence-length or vocabulary. The existing `_CHESTERTON_EVAL_SYSTEM` is the right calibration model to scale up (short, punchy, unmistakably him), not a license to write essays.

**Fix — same format discipline, different fingerprint:**
- Every format rule currently in `SEARCH_SYSTEM` stays **exactly as-is**: the `▸ TL;DR` line, 2-4 short paragraphs, `•` bullets, `**bold**` for terms, inline `[1][2]` citations, no Sources line.
- The `TIER GATE` rule stays **exactly as-is** — factual queries (a score, a date, a definition) remain plain, fast, no voice at all. Chesterton's fingerprint applies only where the existing rule already reserves "the opinionated voice," i.e., analytical/multi-faceted questions.
- What changes is the **Voice rules** section: replace "sharp, well-read research assistant with genuine opinions" framing with instructions that explicitly invoke Chesterton's actual moves — spotting the paradox in what the evidence shows, aphoristic one-line verdicts, wit calibrated to the evidence (generous when it's earned, unsparing when it isn't), delight in the specific concrete fact over the vague abstraction. Reuse `_CHESTERTON_EVAL_SYSTEM`'s own phrasing as the anchor voice description, adapted from "evaluating one source in 15 words" to "writing a full multi-paragraph answer."
- Apply the equivalent adaptation to `SEARCH_SYSTEM_CURRENT`, `SEARCH_SYSTEM_RESEARCH`, `SEARCH_SYSTEM_CONTESTED`, `SEARCH_SYSTEM_ACADEMIC`, `SEARCH_SYSTEM_EVALUATIVE`, and `VAULT_ONLY_SYSTEM` — each keeps its own tier-specific framing (academic literature, evaluative comparison, contested-views steelmanning, etc.), gaining the same voice change, not a rewrite of what each tier is *for*.
- These prompts are shared, unconditionally, between the CLI and (after §1) the web — one change reaches both surfaces. No surf_web.py-specific work needed here.
- **Full exact replacement text for each constant is a plan-writing task, not a design task** — the design commitment here is the *direction and constraint*, not the final prose, which needs iteration once written.

## Out of scope

- Vault per-visitor scoping (confirmed: correctly single-tenant-per-deployment already, per brainstorming).
- Adaptive feedback loop / domain reputation over time (confirmed deferred — real new engineering, not a reconnection).
- Building UI for `citemap`, `answer_card`, `verification`, or `related` events — received safely, not displayed, this round.
- Populating the new `context` parameter from actual conversation history — plumbing only, not the history-construction logic.
- Any change to `_CHESTERTON_EVAL_SYSTEM` itself — it's already correctly calibrated and stays as the anchor reference for the new voice work.

## Risk notes for the implementation plan

- This is a materially higher-stakes change than the earlier UI-polish round: §1 replaces the entire live search route, and §3 edits prompts shared with the CLI. Both need real end-to-end verification (a live search producing a real answer, not just a syntax check) before this is considered done — `tests/test_engine.py` already exists and covers `surf_engine.py` directly, and should be the first thing confirmed green, but is not a substitute for exercising the actual `/api/search` route once rewired.
- Do not deploy to the iMac until the full branch has passed review, given the live site is in active use.
