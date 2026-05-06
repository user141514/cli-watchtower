"""
discovery.py — 枚举主机上"持有 console 的进程"。

原理:
- Windows 每个有控制台的进程会附着到一个 conhost.exe (或 Win Terminal 的 OpenConsole.exe)
- 我们遍历所有进程, 对每个 pid 尝试 AttachConsole 探测 (代价高), 或用更便宜的启发式:
    启发式: 进程的父进程 / 子进程里有 conhost.exe, 则大概率是 console 程序
- 本文件只做"候选列表", 真正的 attach 交给 attach_worker.py
"""
from __future__ import annotations
import os
import sys
from dataclasses import dataclass, asdict
from typing import List, Optional

import psutil

CONSOLE_HOST_NAMES = {"conhost.exe", "openconsole.exe"}

# ── 精确 AI CLI 工具进程名白名单（全小写）──────────────────────────────────
# 只列 claude.exe；cmd/python/node/bun 等通用壳不在此列。
# cmdline 子串检测已移除，避免壳进程命令行恰好含 "claude" 而误入。
INTERESTING_NAMES = {
    "claude.exe",        # Anthropic Claude CLI — 唯一匹配项，精确进程名
}

# cmdline 子串检测已移除：避免 bun.exe / node.exe 等壳命令行恰好含
# "claude" 关键词而被误标。仅凭进程可执行文件名做最严格的一刀切筛选。


@dataclass
class CliCandidate:
    pid: int
    name: str
    cmdline: str
    cwd: Optional[str]
    conhost_pid: Optional[int]  # 关联的 conhost pid (若找到)
    interesting: bool           # 是否在白名单


def _find_conhost_for(proc: psutil.Process) -> Optional[int]:
    """找到这个进程关联的 conhost.exe。
    规则: conhost 通常是进程的子进程 (cmd.exe 启动时 Windows 会拉起子 conhost)。
    """
    try:
        for child in proc.children(recursive=False):
            if child.name().lower() in CONSOLE_HOST_NAMES:
                return child.pid
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    # 也可能 conhost 是父进程 (较少见)
    try:
        parent = proc.parent()
        if parent and parent.name().lower() in CONSOLE_HOST_NAMES:
            return parent.pid
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return None


def enumerate_candidates() -> List[CliCandidate]:
    """返回主机上所有"看起来是 CLI 程序"的候选。"""
    result: List[CliCandidate] = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            name = (p.info["name"] or "").lower()
            if name in CONSOLE_HOST_NAMES:
                continue  # conhost 本身不是目标
            conhost = _find_conhost_for(p)
            cmd = p.info.get("cmdline") or []
            cmd_str = " ".join(cmd)
            # interesting = 进程名精确匹配 claude.exe（不做 cmdline 检测，
            # 避免 bun.exe / node.exe 等壳的命令行恰好含 "claude" 而误入）
            interesting = name in INTERESTING_NAMES
            # 只收: 带 conhost 的, 或白名单里的 (后者兜底)
            if conhost is None and not interesting:
                continue
            result.append(CliCandidate(
                pid=p.info["pid"],
                name=p.info["name"] or "",
                cmdline=cmd_str,
                cwd=p.info.get("cwd"),
                conhost_pid=conhost,
                interesting=interesting,
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    # 优先白名单, 再按 pid
    result.sort(key=lambda c: (not c.interesting, c.pid))
    return result


if __name__ == "__main__":
    cands = enumerate_candidates()
    print(f"found {len(cands)} candidates:\n")
    for c in cands:
        tag = "*" if c.interesting else " "
        print(f"  {tag} pid={c.pid:>6}  conhost={c.conhost_pid}  {c.name:<20} {c.cmdline[:80]}")
