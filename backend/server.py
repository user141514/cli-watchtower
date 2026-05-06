"""
server.py - FastAPI + WebSocket 聚合层。
"""
from __future__ import annotations
import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from discovery import enumerate_candidates, asdict  # noqa: E402

app = FastAPI(title="CLI Watchtower")
WORKER_PATH = Path(__file__).parent / "attach_worker.py"
sessions: Dict[str, dict] = {}


@app.get("/api/candidates")
async def get_candidates():
    return [asdict(c) for c in enumerate_candidates()]


@app.post("/api/sessions")
async def create_session(body: dict):
    pid = int(body["pid"])
    interval = float(body.get("interval", 0.5))
    sid = str(uuid.uuid4())[:8]
    proc = subprocess.Popen(
        [sys.executable, str(WORKER_PATH), str(pid), str(interval)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE,
        text=True, bufsize=1, encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    sessions[sid] = {"proc": proc, "pid": pid}
    return {"sid": sid, "pid": pid}


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    s = sessions.pop(sid, None)
    if s:
        try:
            s["proc"].terminate()
        except Exception:
            pass
    return {"ok": True}


@app.websocket("/ws/{sid}")
async def ws_endpoint(ws: WebSocket, sid: str):
    await ws.accept()
    s = sessions.get(sid)
    if not s:
        await ws.send_json({"type": "error", "msg": f"session {sid} not found"})
        await ws.close()
        return
    proc = s["proc"]
    loop = asyncio.get_running_loop()

    async def relay_stdout():
        """Worker stdout → WebSocket frames."""
        try:
            while True:
                line = await loop.run_in_executor(None, proc.stdout.readline)
                if not line:
                    break
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    frame = {"type": "raw", "data": line.strip()}
                await ws.send_json(frame)
        except Exception:
            pass

    async def relay_stdin():
        """WebSocket input messages → Worker stdin JSON lines."""
        try:
            while True:
                data = await ws.receive_json()
                if data.get("type") == "input":
                    text = data["data"]
                    msg = json.dumps({"type": "input", "data": text}) + "\n"
                    await loop.run_in_executor(
                        None,
                        lambda m=msg: (proc.stdin.write(m), proc.stdin.flush()),
                    )
        except (WebSocketDisconnect, Exception):
            pass

    tasks: list[asyncio.Task] = []
    try:
        tasks = [
            asyncio.create_task(relay_stdout()),
            asyncio.create_task(relay_stdin()),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        # 浏览器关闭/切换时确保 worker 子进程被终止，避免僵尸进程
        s2 = sessions.pop(sid, None)
        if s2:
            try:
                s2["proc"].terminate()
            except Exception:
                pass


@app.get("/api/browse-folder")
async def browse_folder():
    """打开系统原生文件夹选择对话框（tkinter，运行在线程池避免阻塞事件循环）。"""
    import asyncio
    def _pick():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory(title="Select Skills Folder")
            root.destroy()
            return {"path": path or ""}
        except Exception as e:
            return {"path": "", "error": str(e)}
    return await asyncio.to_thread(_pick)


@app.post("/api/skills")
async def list_skills(body: dict):
    """列出指定目录下的子文件夹名（跳过隐藏目录）。"""
    path = body.get("path", "").strip()
    if not path:
        return {"skills": [], "error": "path is required"}
    try:
        p = Path(path)
        if not p.is_dir():
            return {"skills": [], "error": f"not a directory: {path}"}
        skills = sorted(
            f.name for f in p.iterdir()
            if f.is_dir() and not f.name.startswith(".")
        )
        return {"skills": skills}
    except Exception as e:
        return {"skills": [], "error": str(e)}


FRONTEND = ROOT / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="static")


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", "8765"))
    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=False)
