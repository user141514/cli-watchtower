@echo off
chcp 65001 >nul
title CLI Watchtower

echo [Watchtower] Killing stale workers...
for /f "tokens=2" %%p in ('wmic process where "commandline like '%%cli-watchtower%%' and name like '%%python%%'" get processid /format:list 2^>nul ^| findstr "="') do (
    taskkill /F /PID %%p >nul 2>&1
)

echo [Watchtower] Starting server...
start "" /B "E:\Anaconda3\envs\rag-env\python.exe" "F:\cli-watchtower\backend\server.py"

timeout /t 2 /nobreak >nul

echo [Watchtower] Opening browser...
start "" "http://127.0.0.1:8765"

echo [Watchtower] Running. Press Ctrl+C to stop.
pause >nul