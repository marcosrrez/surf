"""surf web UI — FastAPI app over the unified search pipeline (surf_engine)."""
from __future__ import annotations

import os
import sys
import json

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="surf", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "web", "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "web", "templates"))

_engine_loaded = False
_search_events = None


def _load_engine():
    """Lazy import — surf pulls in the full pipeline; keep startup instant."""
    global _engine_loaded, _search_events
    if _engine_loaded:
        return
    from surf_engine import search_events
    _search_events = search_events
    _engine_loaded = True


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/vault/topics")
async def vault_topics_api():
    """Vault tags with note counts — powers the sidebar's topic list."""
    try:
        from surf_store import vault_topics
        return {"topics": vault_topics()}
    except Exception:
        return {"topics": []}


@app.get("/api/search")
async def search(q: str, fresh: bool = False, context: str = ""):
    """Stream the unified pipeline as SSE events.

    context: recent conversation turns from the client ("Q: ...\nA: ..." lines),
    capped server-side — lets follow-ups resolve pronouns without accounts or
    server-side conversation state.
    """
    _load_engine()
    context = (context or "")[:2000]

    def generate():
        try:
            for event in _search_events(q, fresh=fresh, context=context):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # never leave the stream hanging without a terminal event
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3939)
