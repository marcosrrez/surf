"""Persistence layer for surf — sessions, threads, snapshots, Obsidian vault, preferences."""
import os
import re
import json
import time
import surf_config

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Truncate at the last sentence boundary before max_chars."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_period = max(truncated.rfind(". "), truncated.rfind(".\n"))
    return truncated[:last_period + 1] if last_period > max_chars // 2 else truncated


# ─── Session memory ─────────────────────────────────────────────────────────

def load_session() -> list[dict]:
    """Load session entries, returning empty list if expired or missing."""
    try:
        with open(surf_config.SESSION_FILE) as f:
            data = json.load(f)
        if time.time() > data.get("expires_at", 0):
            return []
        return data.get("entries", [])
    except Exception:
        return []


def save_session_entry(query: str, entry_type: str, summary: str) -> None:
    """Append a new entry to the session, creating or refreshing as needed."""
    entries = load_session()
    entries = [e for e in entries if e.get("query") != query]
    entries.append({
        "query": query,
        "type": entry_type,
        "summary": _truncate_at_sentence(summary, 500),
        "timestamp": int(time.time()),
    })
    entries = entries[-10:]
    try:
        os.makedirs(os.path.dirname(surf_config.SESSION_FILE), exist_ok=True)
        with open(surf_config.SESSION_FILE, "w") as f:
            json.dump({
                "expires_at": int(time.time()) + surf_config.SESSION_TTL,
                "entries": entries,
            }, f)
    except Exception:
        pass


def format_session_context() -> str:
    """Return session entries as a context string for prompts."""
    entries = load_session()
    if not entries:
        return ""
    lines = ["Earlier in this session:"]
    for e in entries[-5:]:
        lines.append(f"  [{e['type']}] {e['query']}: {e['summary']}")
    return "\n".join(lines)


# ─── Named threads ──────────────────────────────────────────────────────────

def _thread_path(name: str) -> str:
    """Return file path for a named thread."""
    safe_name = re.sub(r"[^a-z0-9-]", "", name.lower().strip().replace(" ", "-"))
    return os.path.join(surf_config.THREAD_DIR, f"{safe_name}.json")


def _load_thread(name: str) -> dict:
    """Load a named thread. Returns empty structure if thread doesn't exist."""
    path = _thread_path(name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"name": name, "entries": [], "created_at": 0, "updated_at": 0}


def _save_thread_entry(name: str, query: str, response: str, sources: list[dict]) -> None:
    """Append an entry to a named thread."""
    os.makedirs(surf_config.THREAD_DIR, exist_ok=True)
    thread = _load_thread(name)
    now = int(time.time())
    if not thread["created_at"]:
        thread["created_at"] = now
    thread["updated_at"] = now
    thread["name"] = name
    thread["entries"].append({
        "query": query,
        "response": _truncate_at_sentence(response, 2000),
        "sources": [{"domain": s.get("domain", ""), "url": s.get("url", "")} for s in sources[:5]],
        "timestamp": now,
    })
    path = _thread_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(thread, f, ensure_ascii=False, indent=2)


def _list_threads() -> list[dict]:
    """List all named threads with metadata."""
    if not os.path.isdir(surf_config.THREAD_DIR):
        return []
    threads = []
    for fname in os.listdir(surf_config.THREAD_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(surf_config.THREAD_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            threads.append({
                "name": data.get("name", fname.replace(".json", "")),
                "entries": len(data.get("entries", [])),
                "updated_at": data.get("updated_at", 0),
            })
        except Exception:
            continue
    return sorted(threads, key=lambda t: t["updated_at"], reverse=True)


# ─── Export ──────────────────────────────────────────────────────────────────

def _export_thread(name: str) -> str:
    """Export a named thread as a markdown document."""
    thread = _load_thread(name)
    if not thread["entries"]:
        return ""

    from datetime import datetime
    lines = [f"# {name}\n"]
    created = datetime.fromtimestamp(thread["created_at"]).strftime("%Y-%m-%d")
    updated = datetime.fromtimestamp(thread["updated_at"]).strftime("%Y-%m-%d")
    lines.append(f"*Research thread · {len(thread['entries'])} entries · {created} to {updated}*\n")
    lines.append("---\n")

    for entry in thread["entries"]:
        ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%Y-%m-%d %H:%M")
        lines.append(f"## {entry['query']}\n")
        lines.append(f"*{ts}*\n")
        lines.append(f"{entry['response']}\n")
        if entry.get("sources"):
            lines.append("\n**Sources:**")
            for s in entry["sources"]:
                url = s.get("url", "")
                domain = s.get("domain", "")
                if url:
                    lines.append(f"- [{domain}]({url})")
                elif domain:
                    lines.append(f"- {domain}")
            lines.append("")
        lines.append("---\n")

    return "\n".join(lines)


def _export_session() -> str:
    """Export current session as a markdown document."""
    entries = load_session()
    if not entries:
        return ""

    from datetime import datetime
    lines = ["# Surf Session\n"]
    first_ts = datetime.fromtimestamp(entries[0]["timestamp"]).strftime("%Y-%m-%d %H:%M")
    lines.append(f"*{len(entries)} searches starting {first_ts}*\n")
    lines.append("---\n")

    for entry in entries:
        ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%H:%M")
        lines.append(f"## {entry['query']}\n")
        lines.append(f"*{ts} · {entry['type']}*\n")
        lines.append(f"{entry['summary']}\n")
        lines.append("---\n")

    return "\n".join(lines)


# ─── Search snapshots ────────────────────────────────────────────────────────

def _snapshot_path(query: str) -> str:
    """Return path for a search snapshot file."""
    slug = re.sub(r"[^a-z0-9-]", "", query.lower().strip().replace(" ", "-"))
    slug = re.sub(r"-{2,}", "-", slug).strip("-")[:60]
    return os.path.join(surf_config.SNAPSHOT_DIR, f"{slug}.json")


def _save_search_snapshot(query: str, response: str, sources: list[dict]) -> None:
    """Save a search result as a snapshot for later diff comparison."""
    os.makedirs(surf_config.SNAPSHOT_DIR, exist_ok=True)
    path = _snapshot_path(query)
    data = {
        "query": query,
        "response": response,
        "sources": [{"domain": s.get("domain", ""), "url": s.get("url", "")} for s in sources[:10]],
        "timestamp": int(time.time()),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _load_search_snapshot(query: str) -> dict | None:
    """Load a previous search snapshot. Returns None if no snapshot exists."""
    path = _snapshot_path(query)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ─── Obsidian vault ──────────────────────────────────────────────────────────

def _obsidian_vault_path() -> str | None:
    """Return configured Obsidian vault path, or None."""
    return surf_config.load_config().get("OBSIDIAN_VAULT") or None


_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "what", "how",
    "why", "who", "when", "does", "do", "did", "and", "or", "for",
    "of", "in", "on", "at", "to", "by", "it", "its", "me", "my",
    "find", "get", "show", "tell", "give", "about", "just", "some",
    "that", "this", "with", "from", "been", "have", "has", "had",
    "there", "their", "they", "them", "most", "more", "very",
})

_TOPIC_SIGNALS = {
    "finance":       ["stock", "market", "economy", "inflation", "fed", "rate", "ipo", "invest",
                      "earnings", "dividend", "trading", "crypto", "bitcoin", "nasdaq", "s&p"],
    "health":        ["health", "disease", "drug", "vaccine", "treatment", "symptom", "diagnosis",
                      "medical", "clinical", "patient", "chronic", "sleep deprivation", "fasting",
                      "nutrition", "diet", "exercise"],
    "psychology":    ["anxiety", "depression", "mental health", "cognitive", "emotional", "trauma",
                      "attachment style", "rumination", "disorder", "coping",
                      "mindfulness", "self-esteem", "burnout", "psychological"],
    "relationships": ["romantic", "relationship", "dating", "marriage", "breakup",
                      "divorce", "intimacy", "parenting", "couples therapy"],
    "sports":        ["game", "match", "season", "league", "tournament", "world cup", "goal",
                      "championship", "player", "coach", "fifa", "nba", "nfl", "scored"],
    "tech":          ["software", "machine learning", "neural", "programming", "algorithm", "api",
                      "transformer", "python", "javascript",
                      "database", "cloud computing", "startup", "artificial intelligence"],
    "science":       ["experiment", "physics", "biology", "chemistry",
                      "quantum", "evolution", "astronomy", "black hole",
                      "particle", "dna", "genome"],
    "politics":      ["election", "vote", "president", "congress", "legislation",
                      "democrat", "republican", "government", "senate", "campaign", "trump",
                      "biden", "geopolitics", "sanction"],
    "world-events":  ["war ", "conflict", "protest", "crisis", "disaster", "refugee",
                      "united nations", "nato", "military"],
    "academic":      ["peer review", "journal", "citation", "meta-analysis", "systematic review",
                      "longitudinal", "qualitative", "quantitative", "methodology", "sample size",
                      "p-value", "statistical significance", "hypothesis", "literature review"],
    "news":          ["news", "headline", "breaking", "current events"],
    "weather":       ["forecast", "temperature", "rain", "storm", "weather", "humidity",
                      "snow"],
    "history":       ["history", "historical", "century", "ancient", "civilization", "empire",
                      "revolution", "colonial", "medieval"],
    "philosophy":    ["philosophy", "ethics", "moral", "existential", "consciousness",
                      "free will", "epistemology", "metaphysics", "stoic"],
    "arts-culture":  ["music", "film", "movie", "novel", "culture",
                      "literature", "poetry", "theater", "album"],
}


def _make_note_slug(query: str) -> str:
    """Convert query to a topic-focused filename slug, max 50 chars."""
    slug = query.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    words = slug.split()
    words = [w for w in words if w not in _STOP_WORDS and len(w) > 1]
    slug = "-".join(words)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if len(slug) > 50:
        slug = slug[:50].rsplit("-", 1)[0]
    return slug or "note"


def _auto_tag(query: str, response: str = "") -> list[str]:
    """Derive tags from query and response using word-boundary matching."""
    combined = (query + " " + response[:500]).lower()
    tags = []
    for topic, signals in _TOPIC_SIGNALS.items():
        for signal in signals:
            if " " in signal.strip():
                if signal in combined:
                    tags.append(topic)
                    break
            else:
                if re.search(r"\b" + re.escape(signal.strip()) + r"\b", combined):
                    tags.append(topic)
                    break
    return tags


def _make_frontmatter(
    query: str,
    sources: list[dict],
    tags: list[str],
    depth: str = "lookup",
    sparked_by: str = "",
    deep_dive_of: str = "",
) -> str:
    """Generate YAML frontmatter for a surf vault note."""
    today = time.strftime("%Y-%m-%d")
    source_lines = "\n".join(
        f"  - {r.get('domain', '').removeprefix('www.')}" for r in sources[:5]
    ) or "  []"
    tag_str = "[" + ", ".join(tags) + "]" if tags else "[]"
    safe_query = query.replace('"', "'")
    lines = [
        "---",
        f"date: {today}",
        f'query: "{safe_query}"',
        f"depth: {depth}",
        f"sources:\n{source_lines}",
        f"tags: {tag_str}",
    ]
    if sparked_by:
        lines.append(f'sparked_by: "[[{sparked_by}]]"')
    if deep_dive_of:
        lines.append(f'deep_dive_of: "[[{deep_dive_of}]]"')
    lines.append("---")
    return "\n".join(lines)


def _append_followed_by(predecessor_path: str, new_stem: str) -> None:
    """Append a 'Followed by' link to a predecessor note."""
    try:
        text = open(predecessor_path, encoding="utf-8").read()
        if f"[[{new_stem}]]" in text:
            return
        with open(predecessor_path, "a", encoding="utf-8") as f:
            f.write(f"\nFollowed by: [[{new_stem}]]\n")
    except Exception:
        pass


def _obsidian_save(
    query: str,
    response: str,
    sources: list[dict],
    session_id: str,
    identify_entity_type_fn=None,
    sparked_by: str = "",
    deep_dive_of: str = "",
    depth: str = "",
) -> str | None:
    """Save or append a surf response to the Obsidian vault."""
    vault = _obsidian_vault_path()
    if not vault:
        return None

    response = _ANSI_RE.sub("", response)

    today = time.strftime("%Y-%m-%d")
    note_dir = os.path.join(vault, "surf", time.strftime("%Y"), time.strftime("%m"))
    os.makedirs(note_dir, exist_ok=True)

    slug = _make_note_slug(query)
    note_path = os.path.join(note_dir, f"{today}-{slug}.md")

    # Deduplicate filename if slug collides on same day
    if os.path.exists(note_path):
        existing_text = open(note_path, encoding="utf-8").read()
        existing_query = re.search(r'^query:\s*"(.+)"', existing_text, re.MULTILINE)
        if existing_query and existing_query.group(1).lower().strip() != query.lower().strip():
            note_path = os.path.join(note_dir, f"{today}-{slug}-{session_id[:6]}.md")

    entity_type = ""
    if identify_entity_type_fn:
        entity_type = identify_entity_type_fn(query) or ""
    tags = [entity_type] if entity_type else []
    tags.extend(t for t in _auto_tag(query, response) if t not in tags)

    if not depth:
        depth = "deep-dive" if deep_dive_of else "lookup"

    if os.path.exists(note_path):
        with open(note_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n## {query}\n\n{response}\n")
    else:
        fm = _make_frontmatter(query, sources, tags, depth, sparked_by, deep_dive_of)
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(f"{fm}\n\n# {query}\n\n{response}\n")

    note_stem = os.path.splitext(os.path.basename(note_path))[0]

    # Bidirectional threading: add "Followed by" on predecessor
    if sparked_by:
        sparked_path = _find_note_by_stem(vault, sparked_by)
        if sparked_path:
            _append_followed_by(sparked_path, note_stem)

    # Bidirectional threading: add "Deep dive" on parent
    if deep_dive_of:
        parent_path = _find_note_by_stem(vault, deep_dive_of)
        if parent_path:
            _append_followed_by(parent_path, note_stem)

    _obsidian_link_related(note_path, vault)
    _update_topic_mocs(vault)
    return note_path


def _find_note_by_stem(vault: str, stem: str) -> str | None:
    """Find a note file by its stem name anywhere under the surf dir."""
    surf_dir = os.path.join(vault, "surf")
    target = stem + ".md"
    for root, _dirs, files in os.walk(surf_dir):
        if target in files:
            return os.path.join(root, target)
    return None


def _obsidian_find_related(query: str) -> tuple[str, str]:
    """Scan vault for recent notes related to this query.

    Returns (context_string, predecessor_stem) — predecessor_stem is empty
    if no related note was found.
    """
    vault = _obsidian_vault_path()
    if not vault:
        return "", ""

    surf_dir = os.path.join(vault, "surf")
    if not os.path.isdir(surf_dir):
        return "", ""

    q_words = {w for w in re.findall(r"\b[a-z]{4,}\b", query.lower()) if w not in _STOP_WORDS}
    if not q_words:
        return "", ""

    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=30)
    best_score, best_excerpt, best_date, best_stem = 0, "", "", ""

    for root, _dirs, files in os.walk(surf_dir):
        for fname in files:
            if not fname.endswith(".md") or fname.startswith("_"):
                continue
            fpath = os.path.join(root, fname)
            try:
                if date.fromtimestamp(os.path.getmtime(fpath)) < cutoff:
                    continue
                text = open(fpath, encoding="utf-8").read()
                date_m = re.search(r"^date:\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
                note_date = date_m.group(1) if date_m else ""
                note_words = set(re.findall(r"\b[a-z]{4,}\b", text.lower()))
                score = len(q_words & note_words)
                if score > best_score and score >= 3:
                    best_score = score
                    body_start = text.find("---", 3)
                    excerpt = text[body_start + 3:].strip()[:300] if body_start != -1 else text[:300]
                    best_excerpt = excerpt.strip()
                    best_date = note_date
                    best_stem = os.path.splitext(fname)[0]
            except Exception:
                continue

    if best_excerpt:
        context = f"[Prior research from {best_date}]\n{best_excerpt}\n[End prior research]"
        return context, best_stem
    return "", ""


def _extract_note_excerpt(text: str, max_chars: int = 1200) -> str:
    """Extract TL;DR + first substantive paragraph from a vault note body."""
    body_start = text.find("---", 3)
    body = text[body_start + 3:].strip() if body_start != -1 else text.strip()
    body = re.sub(r"^#[^\n]*\n+", "", body)
    body = re.sub(r"\n+## Related\n.*", "", body, flags=re.DOTALL)
    body = re.sub(r"\nFollowed by: \[\[[^\]]+\]\]\n?", "", body)
    body = body.strip()
    if not body:
        return ""
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    last_period = max(truncated.rfind(". "), truncated.rfind(".\n"))
    return truncated[:last_period + 1] if last_period > max_chars // 2 else truncated


_DEPTH_WEIGHT = {"deep-dive": 1.5, "exploration": 1.2}


def _vault_retrieve(query: str, max_notes: int = 5, max_chars: int = 6000) -> tuple[list[dict], str]:
    """Retrieve relevant vault notes for integrated search.

    Returns (ranked_notes, best_predecessor_stem).
    Each note: {stem, date, query, tags, depth, excerpt, score}.
    """
    vault = _obsidian_vault_path()
    if not vault:
        return [], ""
    surf_dir = os.path.join(vault, "surf")
    if not os.path.isdir(surf_dir):
        return [], ""

    q_words = {w for w in re.findall(r"\b[a-z]{4,}\b", query.lower()) if w not in _STOP_WORDS}
    if not q_words:
        return [], ""

    q_tags = set(_auto_tag(query))
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=30)
    scored: list[tuple[float, dict]] = []

    for root, _dirs, files in os.walk(surf_dir):
        if "_topics" in root:
            continue
        for fname in files:
            if not fname.endswith(".md") or fname.startswith("_"):
                continue
            fpath = os.path.join(root, fname)
            try:
                if date.fromtimestamp(os.path.getmtime(fpath)) < cutoff:
                    continue
                text = open(fpath, encoding="utf-8").read()
                note_tags = _extract_tags(text)
                note_kw = _content_keywords(text)
                score = _score_relatedness(q_tags, q_words, note_tags, note_kw)
                if score < _MIN_RELATED_SCORE:
                    continue
                dm = re.search(r"^depth:\s*(\S+)", text, re.MULTILINE)
                depth = dm.group(1) if dm else "lookup"
                score *= _DEPTH_WEIGHT.get(depth, 1.0)
                qm = re.search(r'^query:\s*"(.+)"', text, re.MULTILINE)
                note_query = qm.group(1) if qm else ""
                ddm = re.search(r"^date:\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
                note_date = ddm.group(1) if ddm else ""
                stem = os.path.splitext(fname)[0]
                scored.append((score, {
                    "stem": stem, "date": note_date, "query": note_query,
                    "tags": list(note_tags), "depth": depth,
                    "excerpt": "", "score": score, "_text": text,
                }))
            except Exception:
                continue

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_notes]

    result = []
    chars_used = 0
    for _, note in top:
        remaining = max_chars - chars_used
        if remaining <= 100:
            break
        excerpt = _extract_note_excerpt(note.pop("_text"), max_chars=min(1500, remaining))
        note["excerpt"] = excerpt
        chars_used += len(excerpt)
        result.append(note)

    best_stem = result[0]["stem"] if result else ""
    return result, best_stem


def _format_vault_context(notes: list[dict]) -> str:
    """Format retrieved vault notes as structured prompt context."""
    if not notes:
        return ""
    dates = [n["date"] for n in notes if n.get("date")]
    date_range = f"{min(dates)}–{max(dates)}" if len(dates) > 1 else (dates[0] if dates else "")
    lines = [f"You have {len(notes)} prior vault note{'s' if len(notes) != 1 else ''} on related topics ({date_range}):\n"]
    for n in notes:
        depth_label = f", {n['depth']}" if n.get("depth") and n["depth"] != "lookup" else ""
        lines.append(f'[Prior research: "{n["query"]}" ({n["date"]}{depth_label})]')
        lines.append(n["excerpt"])
        lines.append("[End note]\n")
    return "\n".join(lines)


def _extract_tags(text: str) -> set[str]:
    """Pull tags from a note's frontmatter."""
    m = re.search(r"^tags:\s*\[([^\]]*)\]", text, re.MULTILINE)
    if not m:
        return set()
    return {t.strip() for t in m.group(1).split(",") if t.strip()}


def _content_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from note body (below frontmatter)."""
    body_start = text.find("---", 3)
    body = text[body_start + 3:] if body_start != -1 else text
    return {w for w in re.findall(r"\b[a-z]{4,}\b", body.lower()) if w not in _STOP_WORDS}


_MAX_RELATED = 3
_MIN_RELATED_SCORE = 1.5
_GENERIC_TAGS = frozenset({"news", "tech"})


def _score_relatedness(tags_a: set, kw_a: set, tags_b: set, kw_b: set) -> float:
    """Score how related two notes are. Higher = more related."""
    shared_tags = tags_a & tags_b
    meaningful_tags = shared_tags - _GENERIC_TAGS
    tag_score = len(meaningful_tags) * 3 + len(shared_tags & _GENERIC_TAGS) * 0.5
    overlap = len(kw_a & kw_b)
    min_size = min(len(kw_a), len(kw_b)) or 1
    kw_score = overlap / min_size if overlap >= 3 else 0
    return tag_score + kw_score


def _rebuild_related_section(note_path: str, related_stems: list[str]) -> None:
    """Rebuild the ## Related section at the bottom of a note."""
    text = open(note_path, encoding="utf-8").read()
    text = re.sub(r"\n## Related\n(?:- \[\[[^\]]+\]\]\n)*", "", text)
    text = re.sub(r"\n*Related: \[\[[^\]]+\]\]\n*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"
    if related_stems:
        text += "\n## Related\n"
        for stem in related_stems:
            text += f"- [[{stem}]]\n"
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(text)


def _obsidian_link_related(note_path: str, vault: str) -> None:
    """Rebuild ## Related sections for a note and its neighbors, capped at top 3."""
    surf_dir = os.path.join(vault, "surf")
    note_stem = os.path.splitext(os.path.basename(note_path))[0]
    current_text = open(note_path, encoding="utf-8").read()
    current_tags = _extract_tags(current_text)
    current_kw = _content_keywords(current_text)
    if not current_tags and len(current_kw) < 5:
        return

    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=60)
    scored: list[tuple[float, str, str]] = []

    for root, _dirs, files in os.walk(surf_dir):
        for fname in files:
            if not fname.endswith(".md") or fname.startswith("_"):
                continue
            fpath = os.path.join(root, fname)
            if fpath == note_path:
                continue
            try:
                if date.fromtimestamp(os.path.getmtime(fpath)) < cutoff:
                    continue
                other_text = open(fpath, encoding="utf-8").read()
                other_tags = _extract_tags(other_text)
                other_kw = _content_keywords(other_text)
                score = _score_relatedness(current_tags, current_kw, other_tags, other_kw)
                if score >= _MIN_RELATED_SCORE:
                    other_stem = os.path.splitext(fname)[0]
                    scored.append((score, other_stem, fpath))
            except Exception:
                continue

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:_MAX_RELATED]

    top_stems = [stem for _, stem, _ in top]
    _rebuild_related_section(note_path, top_stems)

    for _, other_stem, other_path in top:
        other_text = open(other_path, encoding="utf-8").read()
        if f"[[{note_stem}]]" not in other_text:
            other_tags = _extract_tags(other_text)
            other_kw = _content_keywords(other_text)
            other_scored: list[tuple[float, str]] = []
            for root2, _, files2 in os.walk(surf_dir):
                for fname2 in files2:
                    if not fname2.endswith(".md") or fname2.startswith("_"):
                        continue
                    fpath2 = os.path.join(root2, fname2)
                    if fpath2 == other_path:
                        continue
                    try:
                        t2 = open(fpath2, encoding="utf-8").read()
                        s = _score_relatedness(other_tags, other_kw, _extract_tags(t2), _content_keywords(t2))
                        if s >= _MIN_RELATED_SCORE:
                            other_scored.append((s, os.path.splitext(fname2)[0]))
                    except Exception:
                        continue
            other_scored.sort(key=lambda x: x[0], reverse=True)
            neighbor_stems = [s for _, s in other_scored[:_MAX_RELATED]]
            _rebuild_related_section(other_path, neighbor_stems)


def _update_topic_mocs(vault: str) -> None:
    """Create or update _topics/{tag}.md index notes for tags with 3+ notes."""
    surf_dir = os.path.join(vault, "surf")
    topics_dir = os.path.join(surf_dir, "_topics")
    if not os.path.isdir(surf_dir):
        return

    tag_notes: dict[str, list[tuple[str, str, str]]] = {}
    for root, _dirs, files in os.walk(surf_dir):
        if "_topics" in root:
            continue
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                text = open(fpath, encoding="utf-8").read()
                tags = _extract_tags(text)
                note_date = ""
                dm = re.search(r"^date:\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
                if dm:
                    note_date = dm.group(1)
                query_m = re.search(r'^query:\s*"(.+)"', text, re.MULTILINE)
                title = query_m.group(1) if query_m else fname.replace(".md", "")
                stem = os.path.splitext(fname)[0]
                for tag in tags:
                    tag_notes.setdefault(tag, []).append((note_date, title, stem))
            except Exception:
                continue

    for tag, notes in tag_notes.items():
        if len(notes) < 3:
            continue
        os.makedirs(topics_dir, exist_ok=True)
        notes.sort(reverse=True)
        moc_path = os.path.join(topics_dir, f"{tag}.md")
        lines = [
            "---",
            f"type: topic-moc",
            f"tag: {tag}",
            f"updated: {time.strftime('%Y-%m-%d')}",
            "---",
            f"",
            f"# {tag.replace('-', ' ').title()}",
            f"",
        ]
        for note_date, title, stem in notes:
            lines.append(f"- {note_date} · [[{stem}|{title}]]")
        lines.append("")
        with open(moc_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


def _obsidian_session_id() -> str:
    """Stable 8-char hex ID from session file mtime."""
    try:
        mtime = int(os.path.getmtime(surf_config.SESSION_FILE))
        return format(mtime % (16 ** 8), "08x")
    except Exception:
        return format(int(time.time()) % (16 ** 8), "08x")


# ─── Preferences ─────────────────────────────────────────────────────────────

def _preferences_path() -> str | None:
    """Return path to preferences.md — in vault if configured, else local fallback."""
    vault = _obsidian_vault_path()
    if vault:
        return os.path.join(vault, "surf", "preferences.md")
    config_dir = os.path.dirname(os.path.expanduser("~/.config/surf/config"))
    return os.path.join(config_dir, "preferences.md")


def _read_preferences() -> str:
    """Read user's preferences.md. Returns empty string if not set up yet."""
    path = _preferences_path()
    if not path or not os.path.exists(path):
        return ""
    try:
        return open(path, encoding="utf-8").read().strip()
    except Exception:
        return ""


def _write_preferences(text: str, append: bool = False) -> str | None:
    """Write or append to preferences.md. Returns path or None."""
    path = _preferences_path()
    if not path:
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8") as f:
        f.write(text if not append else f"\n{text}\n")
    return path
