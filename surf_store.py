"""Persistence layer for surf — sessions, threads, snapshots, Obsidian vault, preferences."""
import os
import re
import json
import time
import surf_config


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


def _make_note_slug(query: str) -> str:
    """Convert query to a safe filename slug, max 60 chars."""
    slug = query.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if len(slug) > 60:
        slug = slug[:60].rsplit("-", 1)[0]
    return slug


def _make_frontmatter(query: str, sources: list[dict], tags: list[str]) -> str:
    """Generate YAML frontmatter for a surf vault note."""
    today = time.strftime("%Y-%m-%d")
    source_lines = "\n".join(
        f"  - {r.get('domain', '').removeprefix('www.')}" for r in sources[:5]
    ) or "  []"
    tag_str = "[" + ", ".join(tags) + "]" if tags else "[]"
    safe_query = query.replace('"', "'")
    return f'---\ndate: {today}\nquery: "{safe_query}"\nsources:\n{source_lines}\ntags: {tag_str}\n---'


def _obsidian_save(
    query: str,
    response: str,
    sources: list[dict],
    session_id: str,
    identify_entity_type_fn=None,
) -> str | None:
    """Save or append a surf response to the Obsidian vault."""
    vault = _obsidian_vault_path()
    if not vault:
        return None

    today = time.strftime("%Y-%m-%d")
    note_dir = os.path.join(vault, "surf", time.strftime("%Y"), time.strftime("%m"))
    os.makedirs(note_dir, exist_ok=True)

    note_path = os.path.join(note_dir, f"{today}-{session_id[:8]}.md")

    entity_type = ""
    if identify_entity_type_fn:
        entity_type = identify_entity_type_fn(query) or ""
    tags = [entity_type] if entity_type else []
    topic_signals = {
        "finance": ["stock", "market", "economy", "inflation", "fed", "rate"],
        "medical": ["health", "disease", "drug", "vaccine", "treatment"],
        "sports":  ["game", "match", "season", "league", "tournament"],
        "tech":    ["software", "ai", "model", "code", "programming"],
    }
    for topic, signals in topic_signals.items():
        if topic not in tags and any(s in query.lower() for s in signals):
            tags.append(topic)

    if os.path.exists(note_path):
        with open(note_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n## {query}\n\n{response}\n")
    else:
        fm = _make_frontmatter(query, sources, tags)
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(f"{fm}\n\n# {query}\n\n{response}\n")

    _obsidian_link_related(query, note_path, vault)
    return note_path


def _obsidian_find_related(query: str) -> str:
    """Scan vault for recent notes related to this query."""
    vault = _obsidian_vault_path()
    if not vault:
        return ""

    surf_dir = os.path.join(vault, "surf")
    if not os.path.isdir(surf_dir):
        return ""

    stop = {"the", "a", "an", "is", "are", "was", "were", "what", "how",
            "why", "who", "when", "does", "do", "did", "and", "or", "for",
            "of", "in", "on", "at", "to", "by", "it", "its"}
    q_words = {w for w in re.findall(r"\b[a-z]{4,}\b", query.lower()) if w not in stop}
    if not q_words:
        return ""

    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=30)
    best_score, best_excerpt, best_date = 0, "", ""

    for root, _dirs, files in os.walk(surf_dir):
        for fname in files:
            if not fname.endswith(".md"):
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
            except Exception:
                continue

    if best_excerpt:
        return f"[Prior research from {best_date}]\n{best_excerpt}\n[End prior research]"
    return ""


_ENTITY_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b")


def _obsidian_link_related(query: str, note_path: str, vault: str) -> None:
    """Add [[wiki links]] between notes that share capitalized entities."""
    entities = _ENTITY_RE.findall(query)
    if not entities:
        return
    surf_dir = os.path.join(vault, "surf")
    note_stem = os.path.splitext(os.path.basename(note_path))[0]
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=30)
    for root, _dirs, files in os.walk(surf_dir):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            if fpath == note_path:
                continue
            try:
                if date.fromtimestamp(os.path.getmtime(fpath)) < cutoff:
                    continue
                other_text = open(fpath, encoding="utf-8").read()
                if not any(e in other_text for e in entities):
                    continue
                other_stem = os.path.splitext(fname)[0]
                current_text = open(note_path, encoding="utf-8").read()
                if f"[[{other_stem}]]" not in current_text:
                    with open(note_path, "a", encoding="utf-8") as f:
                        f.write(f"\n\nRelated: [[{other_stem}]]\n")
                if f"[[{note_stem}]]" not in other_text:
                    with open(fpath, "a", encoding="utf-8") as f:
                        f.write(f"\n\nRelated: [[{note_stem}]]\n")
            except Exception:
                continue


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
