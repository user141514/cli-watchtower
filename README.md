# CLI Watchtower

Web UI to observe running CLI tools on Windows.

[中文版本](README.zh-CN.md)

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

## Process filtering

The left sidebar defaults to showing only `interesting` processes.
In the current code, that whitelist is defined in `backend/discovery.py`:

```python
INTERESTING_NAMES = {
    "claude.exe",
}
```

If you want to monitor something other than Claude, there are two ways:

1. Temporary: check `all processes` in the UI to show every detected console process.
2. Permanent: edit `INTERESTING_NAMES` in `backend/discovery.py` and add the executable name you care about in lowercase.

Example:

```python
INTERESTING_NAMES = {
    "claude.exe",
    "codex.exe",
    "python.exe",
}
```

Notes:

- Matching is by exact process name, not by command-line substring.
- Adding generic shells like `python.exe`, `node.exe`, `cmd.exe`, or `powershell.exe` will usually show many more unrelated processes.
- After changing the whitelist, restart `python backend/server.py`.
