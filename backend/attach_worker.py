"""
attach_worker.py - AttachConsole + ReadConsoleOutputW 读取目标 CLI 当前屏幕。
                   支持 WriteConsoleInputW 向目标注入键盘输入。

限制:
- 一个进程同一时刻只能 attach 一个 console
- 必须以独立子进程运行, 每个被观察的 CLI 对应一个 worker
- 与 server.py 通过 stdout 行 JSON 通信 (screen 帧)
- 接收来自 server.py 的 stdin JSON 行 (input 帧)
"""
from __future__ import annotations
import ctypes
import ctypes.wintypes as wt
import json
import sys
import threading
import time

# 控制台屏幕里有CJK/盒绘/进度条等非ASCII字符；Windows中文终端默认GBK
# 编码不下而崩溃。强制stdout为UTF-8，无法编码的字符以替换符兜底。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ATTACH_PARENT_PROCESS = 0xFFFFFFFF
GENERIC_READ          = 0x80000000
GENERIC_WRITE         = 0x40000000
OPEN_EXISTING         = 3
FILE_SHARE_READ       = 1
FILE_SHARE_WRITE      = 2
INVALID_HANDLE_VALUE  = ctypes.c_void_p(-1).value
COMMON_LVB_TRAILING_BYTE = 0x0200   # Attributes bit: trailing half of a CJK wide char
KEY_EVENT             = 0x0001       # INPUT_RECORD.EventType for keyboard

kernel32 = ctypes.windll.kernel32
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.WriteConsoleInputW.restype = wt.BOOL


# ── Console screen-buffer structures ─────────────────────────────────────────

class COORD(ctypes.Structure):
    _fields_ = [("X", wt.SHORT), ("Y", wt.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", wt.SHORT), ("Top", wt.SHORT),
                ("Right", wt.SHORT), ("Bottom", wt.SHORT)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize",              COORD),
        ("dwCursorPosition",    COORD),
        ("wAttributes",         wt.WORD),
        ("srWindow",            SMALL_RECT),
        ("dwMaximumWindowSize", COORD),
    ]


class _CharUnion(ctypes.Union):
    _fields_ = [("UnicodeChar", wt.WCHAR), ("AsciiChar", ctypes.c_char)]


class CHAR_INFO(ctypes.Structure):
    _anonymous_ = ("Char",)
    _fields_ = [("Char", _CharUnion), ("Attributes", wt.WORD)]


# ── Console input structures (for WriteConsoleInputW) ────────────────────────

class _InputCharUnion(ctypes.Union):
    _fields_ = [("UnicodeChar", wt.WCHAR), ("AsciiChar", ctypes.c_char)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _anonymous_ = ("uChar",)
    _fields_ = [
        ("bKeyDown",         wt.BOOL),
        ("wRepeatCount",     wt.WORD),
        ("wVirtualKeyCode",  wt.WORD),
        ("wVirtualScanCode", wt.WORD),
        ("uChar",            _InputCharUnion),
        ("dwControlKeyState", wt.DWORD),
    ]


class _EventUnion(ctypes.Union):
    # Only need KeyEvent; pad to max event size (16 bytes) so the union is correct.
    _fields_ = [
        ("KeyEvent", KEY_EVENT_RECORD),
        ("_pad",     ctypes.c_byte * 16),
    ]


class INPUT_RECORD(ctypes.Structure):
    _anonymous_ = ("Event",)
    _fields_ = [
        ("EventType", wt.WORD),
        ("Event",     _EventUnion),
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _emit(obj: dict):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def write_keystrokes(hIn, text: str) -> int:
    """Inject `text` as keyboard events into the attached console.

    When text ends with \\r, the text and the Enter key are injected as
    TWO separate WriteConsoleInputW calls with a sleep in between.
    This prevents the CLI from processing \\r before the full text has
    been buffered (especially important for long text, where a single
    batch would cause \\r to be treated as a literal newline rather
    than a submit command).
    """
    if not text:
        return 0

    # Detect trailing Enter — split into two phases
    enter_suffix = text.endswith("\r")
    body = text[:-1] if enter_suffix else text

    total = _inject_raw(hIn, body)
    if enter_suffix:
        # Sleep proportional to text length so the CLI has time to
        # process all characters before receiving Enter.
        delay = min(0.08 + len(body) * 0.005, 1.2)
        time.sleep(delay)
        total += _inject_raw(hIn, "\r")
    return total


def _inject_raw(hIn, text: str) -> int:
    """Inject characters as keyboard events (single WriteConsoleInputW call)."""
    if not text:
        return 0
    records: list[INPUT_RECORD] = []
    for ch in text:
        is_enter = (ch == "\r")
        for key_down in (True, False):
            r = INPUT_RECORD()
            r.EventType = KEY_EVENT
            r.KeyEvent.bKeyDown = bool(key_down)
            r.KeyEvent.wRepeatCount = 1
            r.KeyEvent.wVirtualKeyCode = 0x0D if is_enter else 0
            r.KeyEvent.wVirtualScanCode = 0
            r.KeyEvent.UnicodeChar = ch
            r.KeyEvent.dwControlKeyState = 0
            records.append(r)
    arr = (INPUT_RECORD * len(records))(*records)
    written = wt.DWORD(0)
    kernel32.WriteConsoleInputW(hIn, arr, wt.DWORD(len(records)), ctypes.byref(written))
    return written.value


def _stdin_listener(hIn):
    """Background thread: reads JSON lines from stdin, injects keystrokes.
    Exits when stdin closes (EOF) or hIn becomes invalid."""
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "input":
            text = msg.get("data", "")
            if text:
                write_keystrokes(hIn, text)


SCROLLBACK_ROWS = 500   # 每帧最多携带的行数（从 cursor 往上）


def read_screen(hOut) -> dict | None:
    info = CONSOLE_SCREEN_BUFFER_INFO()
    if not kernel32.GetConsoleScreenBufferInfo(hOut, ctypes.byref(info)):
        return None

    cursor_y  = info.dwCursorPosition.Y
    buf_cols  = info.dwSize.X
    win_bottom = info.srWindow.Bottom   # 终端可见窗口的最底行

    # 取 cursor 行与可见窗口底行的较大值，确保光标以下的内容（状态栏等）也被捕获
    effective_bottom = max(cursor_y, win_bottom)

    # 读取 full console buffer 中 effective_bottom 往上最多 SCROLLBACK_ROWS 行，
    # 而非仅读可见窗口，从而实现历史滚动。
    top_row   = max(0, effective_bottom - SCROLLBACK_ROWS + 1)
    row_count = effective_bottom - top_row + 1   # inclusive

    buf_size  = COORD(buf_cols, row_count)
    buf_coord = COORD(0, 0)
    read_rect = SMALL_RECT(0, top_row, buf_cols - 1, effective_bottom)
    buf = (CHAR_INFO * (buf_cols * row_count))()
    ok = kernel32.ReadConsoleOutputW(
        hOut, buf, buf_size, buf_coord, ctypes.byref(read_rect)
    )
    if not ok:
        return None

    lines = []
    for r in range(row_count):
        runs = []
        col = 0
        while col < buf_cols:
            cell = buf[r * buf_cols + col]
            # Skip the trailing half-cell of a CJK double-width character
            if cell.Attributes & COMMON_LVB_TRAILING_BYTE:
                col += 1
                continue

            fg = cell.Attributes & 0x0F
            bg = (cell.Attributes >> 4) & 0x0F
            run_start = col
            chars = []

            while col < buf_cols:
                c2 = buf[r * buf_cols + col]
                if c2.Attributes & COMMON_LVB_TRAILING_BYTE:
                    col += 1
                    continue
                f2 = c2.Attributes & 0x0F
                b2 = (c2.Attributes >> 4) & 0x0F
                if (f2, b2) != (fg, bg):
                    break
                ch = c2.UnicodeChar
                chars.append(ch if ch else " ")
                col += 1

            if chars:
                runs.append({"x": run_start, "t": "".join(chars), "f": fg, "b": bg})

        lines.append(runs)

    return {
        "cols": buf_cols, "rows": row_count,
        "cursor": {"x": info.dwCursorPosition.X, "y": info.dwCursorPosition.Y},
        "lines": lines,
    }


def run(target_pid: int, interval: float = 0.5):
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(target_pid):
        _emit({"type": "error", "msg": f"AttachConsole({target_pid}) failed err={kernel32.GetLastError()}"})
        return

    # Open CONOUT$ for screen reading
    hOut = kernel32.CreateFileW(
        "CONOUT$", GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if not hOut or hOut == INVALID_HANDLE_VALUE:
        _emit({"type": "error", "msg": f"CreateFile(CONOUT$) failed err={kernel32.GetLastError()}"})
        kernel32.FreeConsole()
        return

    # Open CONIN$ for keystroke injection
    hIn = kernel32.CreateFileW(
        "CONIN$", GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if hIn and hIn != INVALID_HANDLE_VALUE:
        t = threading.Thread(target=_stdin_listener, args=(hIn,), daemon=True)
        t.start()
    else:
        hIn = None  # input injection unavailable, but screen reading still works

    _emit({"type": "attached", "pid": target_pid, "input": hIn is not None})
    try:
        last = None
        while True:
            snap = read_screen(hOut)
            if snap is None:
                _emit({"type": "error", "msg": "read_screen None"})
                break
            # Build plain-text key for change detection (lines is now RLE runs)
            key = "\n".join(
                "".join(run["t"] for run in row).rstrip()
                for row in snap["lines"]
            )
            if key != last:
                snap["type"] = "screen"
                _emit(snap)
                last = key
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        kernel32.CloseHandle(hOut)
        if hIn:
            kernel32.CloseHandle(hIn)
        kernel32.FreeConsole()
        _emit({"type": "detached"})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python attach_worker.py <target_pid> [interval]")
        sys.exit(1)
    run(int(sys.argv[1]), float(sys.argv[2]) if len(sys.argv) > 2 else 0.5)