# CLI Watchtower

Web UI to observe running CLI tools on Windows.

## Architecture

```
Browser (xterm.js x N tabs)
    | WebSocket JSON frames
FastAPI server.py
    | subprocess stdout
attach_worker.py x N (one per CLI)
    | AttachConsole + ReadConsoleOutputW
target CLI process
```

## Quick start

```
pip install -r requirements.txt
python backend/server.py
# open http://127.0.0.1:8765
```
