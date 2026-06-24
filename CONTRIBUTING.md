# Contributing to surf

Thanks for your interest in surf. Here's how to help.

## Quick start

```bash
git clone https://github.com/marcosrrez/surf ~/surf
cd ~/surf
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest tests/test_surf.py -q
```

## How to contribute

1. **Bug reports** — open an issue with steps to reproduce, your terminal, and OS
2. **Feature ideas** — open an issue describing the problem, not just the solution
3. **Pull requests** — fork, branch, make your changes, run tests, submit PR

## Code style

- No comments unless the WHY is non-obvious
- No abstractions unless there are three concrete uses
- Tests use pytest, classes group related tests
- One feature per PR

## Running tests

```bash
cd ~/surf
.venv/bin/pytest tests/test_surf.py -q
```

## Architecture

- `surf.py` — main search flow, intent engine, quality scoring, UI
- `surf_store.py` — persistence (sessions, threads, Obsidian vault, preferences)
- `surf_backends.py` — search backends (DDG, Brave, Tavily), page fetching
- `surf_config.py` — configuration loading
- `surf_mcp.py` — MCP server for Claude Code integration

## Questions?

Open an issue. We're friendly.
