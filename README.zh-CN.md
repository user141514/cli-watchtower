# CLI Watchtower

用于在 Windows 上观察正在运行的 CLI 工具的 Web UI。背后的开发理念和故事记录在微信公众号：AI..whatever，小弟在此也是希望大家多多地关注和点赞

[Engli，sh README](README.md)

## 架构

```
Browser (xterm.js x N tabs)
    | WebSocket JSON frames
FastAPI server.py
    | subprocess stdout
attach_worker.py x N (one per CLI)
    | AttachConsole + ReadConsoleOutputW
target CLI process
```

## 快速开始

```bash
pip install -r requirements.txt
python backend/server.py
# 打开 http://127.0.0.1:8765
```

## 进程筛选

左侧边栏默认只显示被标记为 `interesting` 的进程。
当前代码里的白名单定义在 `backend/discovery.py`：

```python
INTERESTING_NAMES = {
    "claude.exe",
}
```

如果你不只是想监控 Claude，有两种方式：

1. 临时查看：在界面里勾选 `all processes`，显示所有检测到的控制台进程。
2. 永久修改：编辑 `backend/discovery.py` 里的 `INTERESTING_NAMES`，把你关心的可执行文件名按小写加入白名单。

示例：

```python
INTERESTING_NAMES = {
    "claude.exe",
    "codex.exe",
    "python.exe",
}
```

说明：

- 这里按进程名精确匹配，不按命令行子串匹配。
- 如果把 `python.exe`、`node.exe`、`cmd.exe`、`powershell.exe` 这类通用进程加进去，通常会看到更多无关进程。
- 修改白名单后，重启 `python backend/server.py`。
