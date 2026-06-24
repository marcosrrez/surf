# Integrated Vault+Web Search for surf

## Context

Surf saves every search result to an Obsidian vault, building a personal knowledge base over time. Currently, `_obsidian_find_related` injects a single 300-char excerpt from one matching vault note into the AI prompt. This barely scratches the surface — the user's accumulated research is essentially invisible to future searches.

**The goal**: Make every surf search vault-aware. Surf searches both the user's knowledge base AND the web, then synthesizes across them — highlighting what's new, what contradicts prior findings, and what connections exist across topics. The more you use surf, the smarter it gets.

This is the highest-leverage feature not on the roadmap. It turns surf from "Perplexity in a terminal" into a search tool with compounding intelligence — a moat no competitor can replicate.

---

## Implementation Plan

### Step 1: `_extract_note_excerpt()` in `surf_store.py`

Helper to pull the useful content from a vault note body.

```python
def _extract_note_excerpt(text: str, max_chars: int = 1200) -> str:
    """Extract TL;DR + first substantive paragraph from a vault note body.
    Skips frontmatter, ## Related sections, and source lists."""
```

- Strip YAML frontmatter (everything between `---` markers)
- Strip `## Related` section and anything after it
- Strip `# heading` line
- Find `▸ TL;DR` line — if present, start from there
- If no TL;DR, take the first 2 substantive paragraphs
- Truncate at sentence boundary within `max_chars`

### Step 2: `_vault_retrieve()` in `surf_store.py`

Add alongside existing `_obsidian_find_related` (which stays for other uses).

```python
def _vault_retrieve(query: str, max_notes: int = 5, max_chars: int = 6000) -> tuple[list[dict], str]:
    """Retrieve relevant vault notes for integrated search.
    
    Returns (ranked_notes, best_predecessor_stem).
    Each note dict: {stem, date, query, tags, depth, excerpt, score}.
    best_predecessor_stem: stem of highest-scoring match for sparked_by threading.
    """
```

**Implementation details:**
1. Early return `([], "")` if `_obsidian_vault_path()` returns None or surf dir doesn't exist
2. Extract query keywords using `_STOP_WORDS`, same as current code. Early return if no keywords
3. Derive query-level tags via `_auto_tag(query)` for tag-based scoring
4. Scan all `.md` files under `surf/` (skip `_` prefixed dirs like `_topics/`), 30-day cutoff
5. For each note, parse frontmatter: `date`, `query`, `tags` (via `_extract_tags`), `depth` (default to `"lookup"` if missing — 21 existing notes lack this field)
6. Score using `_score_relatedness(query_tags, query_kw, note_tags, note_kw)`. Apply depth multiplier: `{"deep-dive": 1.5, "exploration": 1.2}.get(depth, 1.0)`. Minimum threshold: `_MIN_RELATED_SCORE` (1.5)
7. Sort descending, take top `max_notes`
8. For each qualifying note, extract excerpt via `_extract_note_excerpt`. Fill greedily until `max_chars` budget is reached
9. Return list of note dicts + stem of highest-scoring note

**Key difference from `_obsidian_find_related`:** Returns multiple notes with structured metadata instead of one 300-char excerpt. Uses same scoring infrastructure but with depth weighting and smarter excerpts.

### Step 3: `_format_vault_context()` in `surf_store.py`

```python
def _format_vault_context(notes: list[dict]) -> str:
    """Format retrieved vault notes as structured prompt context."""
```

Returns `""` if notes is empty. Otherwise:

```
You have 4 prior vault notes on related topics (2026-06-18–2026-06-22):

[Prior research: "anxiety in romantic relationships" (2026-06-22, exploration)]
Social anxiety undermines relationship satisfaction. Relationship anxiety erodes
intimacy, trust, and communication. Research acknowledges the field is underdeveloped.
[End note]

[Prior research: "depression and parenting" (2026-06-18)]
Maternal depression consistently predicts problematic parenting...
[End note]
```

Depth label omitted when empty/missing. Summary line includes count and date range.

### Step 4: Constants in `surf.py`

**Vault context instruction** (preamble injection, NOT system prompt modification — preserves cache):

```python
VAULT_CONTEXT_INSTRUCTION = """When prior vault research is provided above:
- Build on it — don't repeat what the user already knows
- Highlight what's NEW in today's web results compared to prior research
- Flag any CONTRADICTIONS between vault findings and current sources
- Surface CONNECTIONS across topics the user may not have noticed
- If prior research is comprehensive and web adds nothing new, say so"""
```

**Vault-only system prompt** (for `vault:` prefix searches):

```python
VAULT_ONLY_SYSTEM = """You synthesize a user's accumulated research on a topic.
Same voice as always — sharp, direct, opinionated when the evidence warrants it.

Format rules (use exactly):
- First line: "▸ TL;DR  " followed by one sentence synthesizing what they know
- Blank line
- 2-4 paragraphs connecting findings across their notes
- Highlight patterns, contradictions, and knowledge gaps
- Use "•" for bullet points, never dashes or asterisks
- Use **bold** for key terms

Do not fabricate findings not present in their notes. If they've only
scratched the surface, say so."""
```

### Step 5: Wire into main search flow in `surf.py`

**5a. Standard search flow (~line 3378)**

Replace current vault injection:
```python
# CURRENT:
vault_ctx, _sparked_by_stem = _obsidian_find_related(query)
if vault_ctx:
    base_prompt = f"{vault_ctx}\n\n{base_prompt}"
    ...

# NEW:
if not fresh:
    vault_notes, _sparked_by_stem = _vault_retrieve(query)
    vault_ctx = _format_vault_context(vault_notes)
else:
    vault_notes, _sparked_by_stem = [], ""
    vault_ctx = ""
if vault_ctx:
    base_prompt = f"{vault_ctx}\n\n{VAULT_CONTEXT_INSTRUCTION}\n\n{base_prompt}"
    _dates = [n["date"] for n in vault_notes]
    _range = f"{min(_dates)}–{max(_dates)}" if len(_dates) > 1 else _dates[0]
    print(f"{C_META}{GLYPH_META} drawing from {len(vault_notes)} vault note{'s' if len(vault_notes) != 1 else ''} ({_range}){C_RESET}")
```

Prompt injection order: `preferences → vault context + instruction → session context → web results`

Update depth assignment: `_depth = "exploration" if vault_notes else "lookup"`

**5b. Deep search loop (~line 3133)**

Same replacement pattern. `_deep_sparked_by` captured from `_vault_retrieve` return.

**5c. Follow-up handler (`_handle_followup`, ~line 3720)**

Currently does NOT inject vault context — this is a gap. Add vault retrieval after session context injection.

**5d. Academic handler (`_handle_academic`, ~line 2718)**

Add vault context before academic synthesis prompt.

**5e. Specialized handlers (weather, financial, factual)**

Skip — these are time-sensitive queries that don't benefit from vault context.

### Step 6: `--fresh` / `-f` flag

Add to argparser (~line 5338):
```python
parser.add_argument("-f", "--fresh", action="store_true",
                    help="Skip vault context — search only the web")
```

Add `fresh: bool = False` parameter to `search_flow` signature. Propagate through `_watch_loop` and `_diff_search`.

When `fresh=True`, skip `_vault_retrieve` call, set `vault_notes = []`, `_sparked_by_stem = ""`.

### Step 7: `vault:` prefix for vault-only search

**Detection point:** In `main()`, after `query = " ".join(args.input)` (~line 5434), before URL detection (~line 5532):

```python
if query.lower().startswith("vault:"):
    vault_query = query[6:].strip()
    if not vault_query:
        _display_vault_summary()  # optional: show stats
        return
    _vault_only_search(vault_query)
    return
```

**`_vault_only_search(query)` function:**
- Call `_vault_retrieve(query, max_notes=10, max_chars=12000)` — higher budget, no web competing
- Skip web search entirely
- Use `VAULT_ONLY_SYSTEM` as system prompt
- Same `stream_ai → stream_to_terminal` pipeline
- Save to session with type `"vault"`

### Step 8: Update imports

Add `_vault_retrieve` and `_format_vault_context` to the import block in `surf.py` (~line 208).

---

## Edge Cases

| Case | Behavior |
|------|----------|
| 0 matching vault notes | `_vault_retrieve` returns `([], "")`. No vault preamble injected. Identical to current behavior. |
| Vault not configured | `_obsidian_vault_path()` returns None. Early return `([], "")`. Graceful degradation. |
| Query is a URL | URL detection happens after `vault:` check but before `search_flow`. `read_flow` doesn't use vault context — correct for page reads. |
| Notes missing `depth` field | All 21 existing notes lack it. Default to `"lookup"`, depth multiplier = 1.0. |
| Notes missing TL;DR | 2 of 21 notes. `_extract_note_excerpt` falls back to first 2 substantive paragraphs. |
| `--fresh` + `vault:` | `vault:` is detected before `--fresh` is checked (different code path). `--fresh` is ignored — correct behavior. |
| `--fresh` + `--deep` | Both independent. `--fresh` prevents vault retrieval; `--deep` triggers multi-step search. |

---

## Token Budget Math

- Claude Haiku 4.5 / Sonnet 4.6: 200K context window
- Groq Llama 3.3 70B: 128K context window
- Average note body: ~2000 chars (~500 tokens). Largest: 3336 chars (~834 tokens)
- 5 notes at ~600 chars excerpt each = ~3000 chars (~750 tokens) vault context
- Vault instruction: ~400 chars (~100 tokens)
- **Total vault overhead: ~850 tokens per search** — negligible
- Full prompt with vault: ~2650-4150 tokens total. Well within all context windows
- **Cost impact**: ~$0.0016 per search at $1.00/MTok. Negligible against $1.00/month budget

---

## Test Scenarios

1. **Standard search + vault match**: `surf anxiety in romantic relationships`
   - Shows "drawing from N vault notes (date range)"
   - AI references prior findings, highlights what's new
   - Note saved with `sparked_by` + `depth: exploration`

2. **Standard search + no match**: `surf quantum computing breakthroughs`
   - No vault status message. Normal search behavior.

3. **Deep search + vault**: `surf --deep effects of rumination on mental health`
   - Vault context in final synthesis. `sparked_by` set correctly.

4. **`--fresh` flag**: `surf --fresh anxiety in romantic relationships`
   - No vault output. Searches as if vault doesn't exist.

5. **Vault-only search**: `surf vault: what do I know about psychology?`
   - Synthesizes across psychology-tagged notes. No web search.

6. **Vault-only + no match**: `surf vault: kubernetes deployment strategies`
   - "No matching vault notes" message + suggestion to try regular search.

7. **Vault not configured**: Remove `OBSIDIAN_VAULT` from config
   - No errors. Search works normally without vault.

8. **Import/syntax verification**:
   - `python3 -c "from surf_store import _vault_retrieve, _format_vault_context; print('OK')"`
   - `python3 -m py_compile surf.py && python3 -m py_compile surf_store.py`

---

## Implementation Sequence

1. Add `_extract_note_excerpt()` to `surf_store.py`
2. Add `_vault_retrieve()` to `surf_store.py`
3. Add `_format_vault_context()` to `surf_store.py`
4. Verify: `python3 -c "from surf_store import _vault_retrieve; notes, stem = _vault_retrieve('anxiety'); print(len(notes), stem)"`
5. Add `VAULT_CONTEXT_INSTRUCTION` + `VAULT_ONLY_SYSTEM` constants to `surf.py`
6. Replace vault injection at standard search call site
7. Replace vault injection at deep search call site
8. Add vault injection to follow-up handler
9. Add `--fresh` flag to argparser + propagate through search_flow
10. Add `vault:` prefix detection + `_vault_only_search()`
11. Update import block in `surf.py`
12. Run all test scenarios

Steps 1-4 can be one commit. Steps 5-8 another. Steps 9-10 each separate.

---

## Files to Modify

| File | Changes |
|------|---------|
| `surf_store.py` | Add `_extract_note_excerpt()`, `_vault_retrieve()`, `_format_vault_context()` |
| `surf.py` | Replace vault injection at 2-3 call sites, add 2 constants, add `--fresh` flag, add `vault:` prefix routing, update imports |
