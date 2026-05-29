# surf Terminal UI Design Spec
**Date:** 2026-05-29
**Status:** Approved for implementation

Seven concrete decisions with implementation code.

---

## 1. Header system — truncate, never wrap

Long titles get clipped at `width - 7` chars with a trailing `…`. No trailing bar when truncated. The absence of a bar is the visual signal that the title was trimmed.

```python
def print_header(title: str, meta: str = "") -> None:
    width = _term_width()
    max_title = width - 4
    if len(title) <= max_title - 1:
        bar = "━" * max(0, width - len(title) - 4)
        line = f"━━ {title} {bar}" if bar else f"━━ {title}"
    else:
        truncated = title[:max_title - 4] + "…"
        line = f"━━ {truncated}"
    print(f"\n\033[35m{line}\033[0m")
    if meta:
        print(f"\033[90m{meta}\033[0m")
    print()
```

**Before:** `━━ What are the arkansas board of examiners in counseling requirements for ceu cr` (wraps mid-word)
**After:** `━━ What are the arkansas board of examiners in counseling requirements for CEU…`

---

## 2. TL;DR card — cyan callout, bold white text

The `▸` glyph becomes cyan. The TL;DR sentence becomes bold bright-white. The block is indented 2 spaces to visually float above body text.

```
  ▸ The Arkansas Board requires 24 hours of CE every 2 years from NBCC or APA providers.
```

ANSI: `  \033[36m▸\033[0m \033[1;97m{sentence}\033[0m`

**Why cyan:** Purple is headers, yellow is numbers, gray is metadata. Cyan is the unused channel — reads as "system callout," the same register as a terminal prompt color. Signals "answer" without competing with anything else on screen.

---

## 3. Answer body typography — three targeted fixes

**Bold markdown:** Apply per-chunk during streaming.
```python
_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
# Replace with:
_BOLD_RE.sub(r'\033[1m\1\033[22m', chunk)
```
Use `\033[22m` (intensity reset only), not `\033[0m` (full reset), to preserve active colors mid-stream.

**Bullet indent:** Lines starting with `•` get 2 spaces prepended: `  • point text`.

**Paragraph spacing:** `re.sub(r'\n{3,}', '\n\n', text)` collapses triple-blank-line runs before output.

---

## 4. Error messages — categorized, never raw exceptions

Replace raw exception propagation in `stream_cerebras` (and any provider fallback):

```python
except requests.exceptions.HTTPError as e:
    code = e.response.status_code if e.response is not None else "?"
    if code == 429:
        yield "\n\033[33m↳ Cerebras rate limit hit. Try again in a minute.\033[0m"
    elif code in (401, 403):
        yield "\n\033[33m↳ Cerebras auth failed — check CEREBRAS_API_KEY\033[0m"
    elif code >= 500:
        yield f"\n\033[33m↳ Cerebras server error ({code}). Try again shortly.\033[0m"
    else:
        yield f"\n\033[33m↳ Cerebras error ({code}).\033[0m"
except Exception:
    yield "\n\033[33m↳ Cerebras unavailable.\033[0m"
```

Use amber `\033[33m` for recoverable conditions, red `\033[31m` for fatal (no API key configured).

---

## 5. Results footer — two-row semantic grouping

```
  read: 1–5   summary: s1–s5   browser: o1–o5
  new: n      quit: q
```

Row 1: actions on a result. Row 2: navigation. Removes bracket-noise from the current `[ 1-5 ] full article   [ s1-s5 ] summary   [ o1-o5 ] browser` layout. En-dash `–` for ranges (not hyphen).

---

## 6. Color palette and character vocabulary

| Role | ANSI | Character |
|---|---|---|
| Header bar | `\033[35m` | `━` U+2501 |
| TL;DR `▸` | `\033[36m` | `▸` U+25B8 |
| TL;DR text | `\033[1;97m` | — |
| Result numbers | `\033[33m` | — |
| Warnings / fallback | `\033[33m` | `↳` U+21B3 |
| Fatal errors | `\033[31m` | — |
| Metadata / status | `\033[90m` | `─` U+2500, `·` U+00B7 |
| Truncation | — | `…` U+2026 (not `...`) |
| Footer ranges | — | `–` U+2013 en-dash (not `-`) |

---

## 7. Delight feature — elapsed time on the meta line

After streaming completes, append response time to the source domain line:

```
agentsofchangeprep.com · netce.com · clearlyclinical.com  (5 results · 1.4s)
```

Two lines: `t0 = time.time()` before the Groq call, `elapsed = time.time() - t0` after. Developer users track latency instinctively. When Cerebras is faster on a given day, the elapsed time makes the fallback feel like a win rather than a warning. Zero extra API calls.

---

## Implementation priority

1. Header truncation — 10 lines, fixes the most visually jarring bug
2. Bold markdown rendering — 3 lines, high visual impact per effort
3. TL;DR cyan/bold styling — streaming state machine, most complex
4. Error message cleanup — exception handler rewrite
5. Paragraph spacing normalization — 3 lines
6. Bullet indent — 2 lines
7. Results footer two-row layout — 5 lines
8. Elapsed time annotation — 2 lines
