"""
Tanishi API Server v2 — REST + WebSocket + Dashboard.

Serves the web dashboard and provides API endpoints for:
- Chat (with tool use)
- Memory (core + recall)
- Tasks (autonomy engine)
- Notifications
- Screenshot
- Status
"""

import os
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional

from tanishi.core import get_config
from tanishi.core.brain import TanishiBrain
from tanishi.tools import register_all_tools
from tanishi.tools.registry import ToolRegistry
from tanishi.memory.manager import MemoryManager
from tanishi.core.autonomy import AutonomyEngine


# ============================================================
# State
# ============================================================

brain: TanishiBrain = None
memory: MemoryManager = None
autonomy: AutonomyEngine = None
config = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global brain, memory, autonomy, config
    config = get_config()

    # Tools
    registry = ToolRegistry()
    register_all_tools(None, registry)

    brain = TanishiBrain(tool_registry=registry)
    memory = MemoryManager(config.db_path)
    autonomy = AutonomyEngine(config.tanishi_home)

    # Start autonomy background loop
    async def brain_callback(command: str) -> str:
        resp = await brain.think(command)
        return resp.content

    autonomy.set_brain_callback(brain_callback)
    bg_task = asyncio.create_task(autonomy.run_background())

    status = brain.get_status()
    print(f"\n⚡ Tanishi API + Dashboard online")
    print(f"   Claude: {status['claude']}")
    print(f"   Tools: {status['tools']}")
    print(f"   Dashboard: http://localhost:{config.port}\n")

    yield

    autonomy.stop()
    bg_task.cancel()
    print("\n💤 Tanishi going to sleep...")


app = FastAPI(title="Project Tanishi", version="0.4.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Models
# ============================================================

class ChatRequest(BaseModel):
    message: str
    mood: str = "casual"
    session_id: str = ""

class ChatResponse(BaseModel):
    response: str
    model_used: str
    tokens_in: int = 0
    tokens_out: int = 0
    tools_used: list = []
    session_id: str = ""
    timestamp: str = ""

class TaskToggle(BaseModel):
    enabled: bool


# ============================================================
# Dashboard
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the web dashboard."""
    dashboard_path = Path(__file__).parent.parent / "dashboard" / "index.html"
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Tanishi</h1><p>Dashboard not found.</p>")


# ============================================================
# Core Endpoints
# ============================================================

@app.get("/status")
async def status():
    return {
        "brain": brain.get_status() if brain else {},
        "autonomy": autonomy.get_status() if autonomy else {},
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/health")
async def health():
    brain_status = brain.get_status() if brain else {}
    return {
        "ok": bool(brain and memory and autonomy),
        "tools": brain_status.get("tools", 0),
        "deps": {
            "brain": brain is not None,
            "memory": memory is not None,
            "autonomy": autonomy is not None,
            "llm_available": brain_status.get("claude") == "online"
            or brain_status.get("ollama") == "online",
        },
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if not brain:
        raise HTTPException(status_code=503, detail="Brain not initialized")

    session_id = request.session_id or str(uuid.uuid4())
    extra_context = memory.build_core_context() if memory else ""

    response = await brain.think(
        user_input=request.message,
        mood=request.mood,
        extra_context=extra_context,
    )

    # Auto-learn from conversation
    try:
        from tanishi.memory.auto_learn import AutoMemory
        auto = AutoMemory(memory, brain.claude_client)
        await auto.extract_and_store(request.message, response.content)
    except Exception:
        pass

    return ChatResponse(
        response=response.content,
        model_used=response.model_used,
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        tools_used=response.tools_used,
        session_id=session_id,
        timestamp=datetime.now().isoformat(),
    )


@app.websocket("/ws")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            async for chunk in brain.stream_think(data):
                await websocket.send_text(chunk)
            await websocket.send_text("[END]")
    except WebSocketDisconnect:
        pass


# ============================================================
# Memory Endpoints
# ============================================================

@app.get("/memory")
async def get_memory():
    if not memory:
        return {}
    return {
        "stats": memory.get_stats(),
        "core": memory.get_all_core(),
        "recent": [
            {"content": m.content, "category": m.category, "importance": m.importance}
            for m in memory.get_recent_memories(10)
        ],
    }


@app.post("/memory/remember")
async def remember(body: dict):
    fact = body.get("fact", "")
    if not fact:
        raise HTTPException(status_code=400, detail="No fact provided")
    from tanishi.memory.manager import MemoryEntry
    entry_id = f"api_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    entry = MemoryEntry(id=entry_id, content=fact, category="fact", importance=0.7, source="api")
    memory.remember(entry)
    return {"status": "remembered", "id": entry_id}


@app.get("/memory/recall/{query}")
async def recall(query: str):
    results = memory.recall(query)
    return [
        {"content": m.content, "category": m.category, "importance": m.importance}
        for m in results
    ]


# ============================================================
# Task Endpoints (Autonomy)
# ============================================================

@app.get("/tasks")
async def list_tasks():
    if not autonomy:
        return []
    return [
        {
            "id": t.id, "name": t.name, "description": t.description,
            "interval_minutes": t.interval_minutes, "enabled": t.enabled,
            "last_run": t.last_run, "run_count": t.run_count,
        }
        for t in autonomy.list_tasks()
    ]


@app.post("/tasks/{task_id}/toggle")
async def toggle_task(task_id: str, body: TaskToggle):
    if body.enabled:
        task = autonomy.enable_task(task_id)
    else:
        task = autonomy.disable_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "updated", "enabled": task.enabled}


# ============================================================
# Notification Endpoints
# ============================================================

@app.get("/notifications")
async def get_notifications():
    if not autonomy:
        return []
    return [
        {
            "id": n.id, "message": n.message, "priority": n.priority,
            "source": n.source, "timestamp": n.timestamp, "read": n.read,
        }
        for n in autonomy.notifications[-20:]
    ]


@app.post("/notifications/read")
async def mark_read():
    if autonomy:
        autonomy.mark_all_read()
    return {"status": "done"}


# ============================================================
# Screenshot Endpoint
# ============================================================

@app.post("/screenshot")
async def screenshot(body: dict = {}):
    try:
        from tanishi.tools.screenshot import take_screenshot
        result = await take_screenshot(save=True)
        return {"status": "captured", "result": result[:200]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Run
# ============================================================

def main():
    import uvicorn
    config = get_config()
    print(f"\n🧠 Starting Tanishi Dashboard on http://localhost:{config.port}")
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
