# Web UI polish: sidebar hierarchy, cache visibility, input typography, sidebar search

Four related fixes to the surf web UI (`web/templates/index.html`, `web/static/style.css`), scoped after the 2026-06-25 spec's article-reader and tab-architecture work landed. Reviewed independently by a fresh agent against the actual code before being finalized here; corrections from that review are folded in below.

## 1. Sidebar hierarchy

**Problem.** `THE VAULT` section label (`index.html:34`) is unconditional static markup — it renders even with zero vault tags, sitting empty and crammed against `HISTORY` right below it. Separately, the sidebar's primary content (history item text, `.rail-thread-text`) uses `--dim`, while section/group labels use `--faint` — but `--faint` measures **2.43:1** contrast against `--surface` in dark mode (3.31:1 in light mode), both well under WCAG AA's 4.5:1 minimum. (`--dim` itself measures 4.63:1, which passes — it doesn't need a contrast fix, but it's still the wrong emphasis level for what should be the sidebar's most prominent content.)

**Fix:**
- Hide the `THE VAULT` label when `#tag-list` has zero children (mirror the emptiness check `loadSidebarTags()` already has the data for, `index.html:337-353`).
- Raise history item text (`.rail-thread-text`) to near-`--text` (full) contrast — it's the primary clickable content and should read as such. Labels stay on `--faint`, but `--faint`'s actual value needs correcting for contrast (new dark/light values in the same warm palette family, targeting ≥4.5:1).
- Add more vertical spacing/padding on `.rail-thread` rows so items don't run together.
- One clean divider between static chrome (`+ new search`) and the dynamic vault/history list, rather than multiple same-weight stacked labels.

Reference point (not a template to copy): Claude.ai's own sidebar keeps only the section label dim, renders recent-item text at near-full contrast with generous row spacing, and uses a single divider between static nav and the dynamic list — same pattern this fix converges on independently.

## 2. Cache / freshness indicator

**Problem.** History entries save to `localStorage` with full answer/sources/commentary (`addToHistory()`, `index.html:434`). Clicking a history item calls `loadCachedOrSearch()` (`index.html:459`) → `getCachedResult()` (`index.html:449`): "current"-tier (time-sensitive) queries serve from cache only under 10 minutes old; everything else serves from cache up to 24 hours old; past that, it silently reruns a full new search with no indication this happened.

**Correction from review:** the original plan anchored this near "the read-time display" — that element doesn't exist on research cards; `#reader-read-time` (`index.html:948`) belongs only to the separate article-reader panel and is unrelated to search-result caching.

**Fix:**
- Anchor the indicator on the existing `.card-intent` badge row (`index.html:556-567`, already renders tier/domain/answer_depth per card) — add a small tag alongside those.
- Three states need distinguishing, not two: **fresh cache hit** (loaded instantly, no new calls) → no badge needed, this is the default/expected path; **expired cache, silently reran** → show `refreshed`; genuinely **first-time query** → no badge (same as fresh hit, visually — nothing to flag).
- `getCachedResult()` currently loses the distinction between "no entry ever existed" and "entry existed but failed the freshness check" — both just return `null`. Fix: look up the raw entry by query first (existing `searchHistory.find()`), *then* apply the freshness checks separately, so `loadCachedOrSearch()` can tell "stale entry existed → reran → refreshed" apart from "no entry existed → first search → no badge." This is a small logic change in `getCachedResult`/`loadCachedOrSearch`, not new plumbing — `renderCachedCard()` and the SSE search path in `doSearch()` are already distinct functions, so the render-time branch is free.

## 3. Input typography

**Problem.** `.input-box textarea` (search input + follow-up input) renders at `font: 400 17px/1.5 var(--mono)` (`style.css:336`). Typed query text is hard to read while typing — text color is already full `--text` contrast, so this is a pure letterform/rhythm issue: monospace fixed-pitch letterforms and unnatural inter-word spacing slow down reading flowing prose, which is what a search query is, regardless of the app's "mono chrome, sans answers" branding rule.

**Fix — switch to sans, full scope (corrected from initial proposal):**
- `.input-box textarea` (both `#query` and `#followup-input`): `var(--mono)` → `var(--sans)`.
- `.suggest-row span` (`style.css:391`) — the autocomplete dropdown that appears directly under/over the input, showing the same class of content (raw query text, including past queries via `fromHistory`). Left mono, this would look like an oversight sitting immediately adjacent to a now-sans input.
- `.followup-query` (`style.css:514`, currently mono) — reconcile against `.card-query-headline` (`style.css:448`, already sans) so a typed query renders the same way whether it's the first query in a thread or a follow-up. This inconsistency predates this spec but sits in the same content class being touched here.
- Everything else (logo, buttons, section labels, source metadata) stays mono — brand chrome untouched.
- Not pursuing: a "friendlier" monospace variant — the codebase has zero `@font-face` declarations, everything is system-font-stack (`style.css:21-22`), and introducing a custom webfont means a loading pipeline in a codebase whose stated design is "no build step, vanilla JS." Disproportionate for this problem.
- Not pursuing: keeping mono and bumping weight/letter-spacing — doesn't address the actual letterform-ambiguity complaint.

**Confirmed non-issue:** the 58px mono hero wordmark sitting above the search box on the empty state was flagged as a possible inconsistency risk — it isn't. The wordmark is a display/logo element (58px, bold, tight letter-spacing) already differentiated from the input's content-weight text (17px, regular) by size and weight; a family switch on top of that won't read as "the same thing done two ways."

**Small addition from visual comparison:** `.input-box` currently uses a 13px border-radius (`style.css:322`) that reads tight relative to the box's height, and the focused state relies on a loud 2px solid `--accent` border. Since this section already touches the input box's styling, bump the corner radius to something more generous (closer to the box's height, "squircle" territory rather than "rectangle with rounded corners") and soften the focus treatment — reference point being a noticeably more restrained, elevation-based focus style seen in a comparison screenshot, not a specific value to copy verbatim.

## 4. Sidebar search

**Problem.** No way to search/filter the sidebar's own history list — only a substring-match autocomplete exists today (`getSuggestions()`, `index.html:245-255`), and it only fires while composing a *new* query, not for finding something already in the list.

**Fix:**
- Reuse the exact `.toLowerCase().includes()` substring-match idiom from `getSuggestions()`, applied to `renderThreadList()`'s rendering instead of the autocomplete dropdown. **No debounce** — `searchHistory` is capped at 50 items (`index.html:444`) and lives in memory; cheap enough to filter on every keystroke. (The 180ms debounce on `fetchSmartSuggestions()`, `index.html:267-268`, exists only because that path hits the network — not applicable here.)
- **Icon-triggered, not always-visible.** `#rail` is a fixed 248px (`style.css:80-81`), and the mobile rail is a full-height fixed overlay (`style.css:95-103`) — a permanently pinned search field on top of the header, `+ new search` button, and section labels is too much stacked chrome in that width. A search icon (placed in `#rail-header` or above the section list) expands into an input on click/tap.
- **Interaction with `activeTag`, resolved:** typing in the sidebar search does **not** clear the active vault-tag filter (`filterByTag()`, `index.html:355-372`) — the two filters **AND together**, narrowing within whatever `activeTag` currently shows. This matches ordinary filter-composition expectations (each filter narrows further, doesn't reset the others).
- **Scope, resolved:** the search filters everything currently rendered by `renderThreadList()` — both the vault-notes group (`vaultNotesCache[activeTag]`, `index.html:380-392`) and the history groups. One search box, one list, filters what's visible in it — not two separate search behaviors for two sub-sections of the same container.
- Collapse-when-empty: closing/clearing the search (via an ✕ or re-tapping the icon) collapses it back to icon-only, same visual footprint as today when not in use.

## Out of scope

- Article reader edge cases (code blocks, tables, images in Jina-sourced markdown) — separate, already-identified work, paused when this UI-polish thread started; not part of this spec.
- Any change to the mono/sans split outside the input textarea, suggestion dropdown, and follow-up-query echo named above.
- Custom webfonts / build pipeline changes.
