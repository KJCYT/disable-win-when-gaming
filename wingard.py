#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WinKeyGuard —— 打游戏时自动禁用 Windows 键，游戏结束后自动恢复。

原理
----
用 Windows 全局低级键盘钩子（WH_KEYBOARD_LL）在“游戏运行中”这段时间里吞掉
Win 键的按下/抬起事件，Windows 就永远不会看到它，开始菜单也就不会弹出来。

关键安全性：钩子属于本进程，进程一退出（正常退出、Ctrl+C、关窗口、任务管理器
结束、甚至崩溃），钩子自动被系统回收，Windows 键立刻恢复原状。
不写注册表、不改 Scancode Map、不需要重启，也不会给系统留下任何残留。

用法见 README.md；常用命令：
    python wingard.py               前台运行（看得到日志）
    python wingard.py --status      查看当前状态
    python wingard.py --stop        停止后台运行中的实例
    python wingard.py --diagnose    自检（环境、钩子能力、当前前台程序）
"""

from __future__ import annotations

import argparse
import atexit
import ctypes
import ctypes.wintypes as wt
import fnmatch
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from datetime import datetime

APP_NAME = "WinKeyGuard"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(APP_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "wingard.log")
STATE_FILE = os.path.join(LOG_DIR, "state.json")
PID_FILE = os.path.join(LOG_DIR, "wingard.pid")
STOP_FLAG = os.path.join(LOG_DIR, "stop.flag")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")

log = logging.getLogger(APP_NAME)

# ---------------------------------------------------------------- 默认配置 --
DEFAULT_CONFIG = {
    "_说明": [
        "games：游戏进程名（不带 .exe，不区分大小写；可用 * 通配，如 blackmyth*）。进程一出现就禁用 Win 键，进程消失就恢复。",
        "detect_fullscreen：是否额外把“全屏前台程序”当作游戏（没写在 games 里的游戏也能自动生效）。",
        "fullscreen_exclusions：全屏检测的排除名单，避免看全屏视频/浏览器全屏时被误判。",
        "require_foreground：true 时只有游戏窗口在前台才禁用；false（默认）游戏进程在后台也保持禁用。",
        "hotkeys：热键，格式 ctrl+alt+字母；改完重启程序生效。",
        "swallow_combo_keys：true 时 Win 键被按住期间的其他按键也一并吞掉（防止 Win+D 里的 D 进入游戏），默认关闭更稳妥。",
    ],
    "games": [
        "cs2", "csgo", "valorant", "league of legends", "dota2", "overwatch",
        "r5apex", "tslgame", "pubg", "narakabladepoint", "rainbowsix", "rainbowsix_dx11",
        "eldenring", "sekiro", "cyberpunk2077", "gta5", "gtav", "rdr2", "witcher3",
        "genshinimpact", "yuanshen", "starrail", "zenlesszonezero", "wutheringwaves",
        "blackmythwukong", "b1-win64-shipping", "dbd", "deadbydaylight-win64-shipping",
        "minecraft.windows", "minecraftlauncher", "wow", "wowclassic", "ffxiv_dx11",
        "lostark", "pathofexile", "diablo iv", "destiny2", "halo infinite",
        "forzahorizon5", "bf2042", "modernwarfare", "cod", "escapefromtarkov",
        "ark", "shootergame", "palworld", "helldivers2", "crossfire", "dnf",
    ],
    "detect_fullscreen": True,
    "fullscreen_exclusions": [
        "explorer", "chrome", "msedge", "firefox", "brave", "opera", "vivaldi",
        "360se", "360chrome", "qqbrowser", "sogouexplorer", "vlc", "mpv",
        "potplayermini64", "wmplayer", "zooomplayer", "winrar", "7zfm",
    ],
    "fullscreen_exclusion_classes": [
        "Progman", "WorkerW", "Shell_TrayWnd", "ApplicationFrameWindow",
        "Windows.UI.Core.CoreWindow", "Multimedia Class Scheduler",
    ],
    "require_foreground": False,
    "poll_interval": 1.0,
    "block_apps_key": True,
    "swallow_combo_keys": False,
    "pause_minutes": 0,
    "beep": True,
    "hotkeys": {
        "toggle_pause": "ctrl+alt+p",
        "toggle_lock": "ctrl+alt+l",
        "quit": "ctrl+alt+q",
    },
    "log_level": "info",
}

# ------------------------------------------------------------ Win32 常量 --
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_USER = 0x0400
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
PM_NOREMOVE = 0x0000
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_APPS = 0x5D

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
PROCESS_SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000

TH32CS_SNAPPROCESS = 0x00000002
MONITOR_DEFAULTTONEAREST = 2
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GWL_STYLE = -16
WS_MAXIMIZE = 0x01000000

# ------------------------------------------------------- ctypes 原型声明 --
if os.name != "nt":  # pragma: no cover - 仅为给出清晰错误
    sys.stderr.write("WinKeyGuard 只能在 Windows 上运行。\n")
    raise SystemExit(1)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

BOOL = ctypes.c_int
DWORD = ctypes.c_uint32
UINT = ctypes.c_uint
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
HANDLE = ctypes.c_void_p

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", DWORD),
        ("scanCode", DWORD),
        ("flags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD),
        ("cntUsage", DWORD),
        ("th32ProcessID", DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", DWORD),
        ("cntThreads", DWORD),
        ("th32ParentProcessID", DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", DWORD),
    ]


kernel32.GetModuleHandleW.argtypes = (ctypes.c_wchar_p,)
kernel32.GetModuleHandleW.restype = HANDLE
kernel32.GetCurrentThreadId.argtypes = ()
kernel32.GetCurrentThreadId.restype = DWORD
kernel32.CreateToolhelp32Snapshot.argtypes = (DWORD, DWORD)
kernel32.CreateToolhelp32Snapshot.restype = HANDLE
kernel32.Process32FirstW.argtypes = (HANDLE, ctypes.POINTER(PROCESSENTRY32W))
kernel32.Process32FirstW.restype = BOOL
kernel32.Process32NextW.argtypes = (HANDLE, ctypes.POINTER(PROCESSENTRY32W))
kernel32.Process32NextW.restype = BOOL
kernel32.CloseHandle.argtypes = (HANDLE,)
kernel32.CloseHandle.restype = BOOL
kernel32.OpenProcess.argtypes = (DWORD, BOOL, DWORD)
kernel32.OpenProcess.restype = HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    HANDLE, DWORD, ctypes.c_wchar_p, ctypes.POINTER(DWORD))
kernel32.QueryFullProcessImageNameW.restype = BOOL
kernel32.TerminateProcess.argtypes = (HANDLE, UINT)
kernel32.TerminateProcess.restype = BOOL
kernel32.WaitForSingleObject.argtypes = (HANDLE, DWORD)
kernel32.WaitForSingleObject.restype = DWORD
kernel32.GetConsoleWindow.restype = HANDLE
kernel32.SetConsoleCtrlHandler.argtypes = (ctypes.c_void_p, BOOL)
kernel32.SetConsoleCtrlHandler.restype = BOOL

user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, HANDLE, DWORD)
user32.SetWindowsHookExW.restype = HANDLE
user32.UnhookWindowsHookEx.argtypes = (HANDLE,)
user32.UnhookWindowsHookEx.restype = BOOL
user32.CallNextHookEx.argtypes = (HANDLE, ctypes.c_int, WPARAM, LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = (ctypes.POINTER(wt.MSG), HANDLE, UINT, UINT)
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = (
    ctypes.POINTER(wt.MSG), HANDLE, UINT, UINT, UINT)
user32.PeekMessageW.restype = BOOL
user32.TranslateMessage.argtypes = (ctypes.POINTER(wt.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wt.MSG),)
user32.DispatchMessageW.restype = LRESULT
user32.PostThreadMessageW.argtypes = (DWORD, UINT, WPARAM, LPARAM)
user32.PostThreadMessageW.restype = BOOL
user32.RegisterHotKey.argtypes = (HANDLE, ctypes.c_int, UINT, UINT)
user32.RegisterHotKey.restype = BOOL
user32.UnregisterHotKey.argtypes = (HANDLE, ctypes.c_int)
user32.UnregisterHotKey.restype = BOOL
user32.GetForegroundWindow.restype = HANDLE
user32.GetWindowThreadProcessId.argtypes = (HANDLE, ctypes.POINTER(DWORD))
user32.GetWindowThreadProcessId.restype = DWORD
user32.GetClassNameW.argtypes = (HANDLE, ctypes.c_wchar_p, ctypes.c_int)
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (HANDLE, ctypes.c_wchar_p, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = (HANDLE, ctypes.POINTER(wt.RECT))
user32.GetWindowRect.restype = BOOL
user32.IsIconic.argtypes = (HANDLE,)
user32.IsIconic.restype = BOOL
user32.GetWindowLongW.argtypes = (HANDLE, ctypes.c_int)
user32.GetWindowLongW.restype = ctypes.c_long
user32.MonitorFromWindow.argtypes = (HANDLE, DWORD)
user32.MonitorFromWindow.restype = HANDLE
user32.GetMonitorInfoW.argtypes = (HANDLE, ctypes.POINTER(MONITORINFO))
user32.GetMonitorInfoW.restype = BOOL


# ------------------------------------------------------------------ 工具 --
def ensure_dirs() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def setup_logging(level: str = "info", console: bool = True) -> None:
    ensure_dirs()
    log.setLevel(logging.DEBUG)
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    log.addHandler(fh)

    if console and sys.stderr is not None:
        ch = logging.StreamHandler()
        ch.setLevel(getattr(logging, str(level).upper(), logging.INFO))
        ch.setFormatter(fmt)
        log.addHandler(ch)

    log.propagate = False


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def beep(kind: str) -> None:
    """状态切换提示音（不阻塞钩子线程，只在主循环里调用）。"""
    try:
        import winsound
        if kind == "lock":
            winsound.Beep(760, 70)
            winsound.Beep(560, 90)
        elif kind == "unlock":
            winsound.Beep(560, 70)
            winsound.Beep(760, 90)
    except Exception:
        pass


# ------------------------------------------------------------ 进程 / 窗口 --
def snapshot_processes() -> dict[int, str]:
    """返回 {pid: exe 小写名称}。"""
    result: dict[int, str] = {}
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == HANDLE(-1).value:
        return result
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            try:
                result[int(entry.th32ProcessID)] = entry.szExeFile.lower()
            except Exception:
                break
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return result


_pid_name_cache: dict[int, tuple[str, float]] = {}


def process_name(pid: int) -> str:
    """由 PID 取进程名（小写，含 .exe）。

    不依赖进程快照：某些情况下窗口所属进程不在 Toolhelp 快照里
    （快照受限/进程刚启动），直接问内核更可靠。
    """
    try:
        pid = int(pid)
    except Exception:
        return ""
    if pid <= 0:
        return ""
    now = time.monotonic()
    hit = _pid_name_cache.get(pid)
    if hit and now - hit[1] < 5.0:
        return hit[0]
    name = ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(h)
    if len(_pid_name_cache) > 512:
        _pid_name_cache.clear()
    _pid_name_cache[pid] = (name, now)
    return name


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _is_fullscreen(hwnd) -> bool:
    """窗口是否独占了一整块显示器（游戏全屏/无边框全屏的典型特征）。

    只看几何：窗口与显示器矩形几乎完全重合，且不是“最大化”的普通窗口
    （最大化会避开任务栏，用它来判断容易把记事本也当成游戏）。
    """
    if not hwnd or user32.IsIconic(hwnd):
        return False
    if int(user32.GetWindowLongW(hwnd, GWL_STYLE)) & WS_MAXIMIZE:
        return False
    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    if rect.right - rect.left <= 0 or rect.bottom - rect.top <= 0:
        return False
    mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not mon:
        return False
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    mon_w = max(1, m.right - m.left)
    mon_h = max(1, m.bottom - m.top)
    inter_w = max(0, min(rect.right, m.right) - max(rect.left, m.left))
    inter_h = max(0, min(rect.bottom, m.bottom) - max(rect.top, m.top))
    ratio = (inter_w * inter_h) / float(mon_w * mon_h)
    return ratio >= 0.985


def foreground_window() -> dict | None:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return {
        "hwnd": int(hwnd),
        "pid": int(pid.value),
        "exe": process_name(int(pid.value)),
        "class": _class_name(hwnd),
        "title": _window_title(hwnd),
        "fullscreen": _is_fullscreen(hwnd),
    }


def match_any(exe: str, patterns) -> bool:
    """exe 为小写文件名（含 .exe）；patterns 允许带 .exe 或不带、允许 * 通配。"""
    if not exe:
        return False
    name = exe.lower().strip()
    base = name[:-4] if name.endswith(".exe") else name
    for raw in patterns or []:
        p = str(raw).lower().strip()
        if not p:
            continue
        pb = p[:-4] if p.endswith(".exe") else p
        if name == p or base == pb:
            return True
        if any(ch in pb for ch in "*?["):
            if fnmatch.fnmatch(name, p) or fnmatch.fnmatch(base, pb):
                return True
    return False


# ------------------------------------------------------- 键盘钩子拦截实现 --
class WinKeyBlocker:
    """全局低级键盘钩子，用于吞掉 Win 键。

    只在“需要禁用”的时段里安装；恢复时立刻卸载，平时对键盘零影响、
    零延迟。钩子随进程结束自动消失。
    """

    def __init__(self, block_apps_key: bool = True,
                 swallow_combo_keys: bool = False) -> None:
        self.block_apps_key = block_apps_key
        self.swallow_combo_keys = swallow_combo_keys
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._hhook = None
        self._proc = None
        self._blocking = False
        self._ready = threading.Event()
        self._ok = False
        self._err = 0
        self._win_down_at = 0.0
        self._swallowed: set[int] = set()
        self._guard = threading.Lock()

    # -- 对外接口 --------------------------------------------------------
    @property
    def active(self) -> bool:
        return bool(self._ok and self._thread and self._thread.is_alive())

    def start(self, timeout: float = 3.0) -> bool:
        with self._guard:
            if self.active:
                self._blocking = True
                return True
            self._blocking = True
            self._win_down_at = 0.0
            self._swallowed.clear()
            self._ready.clear()
            self._ok = False
            self._err = 0
            self._thread = threading.Thread(
                target=self._run, name="wingard-hook", daemon=True)
            self._thread.start()
            self._ready.wait(timeout)
            if not self._ok:
                self._blocking = False
                self._thread = None
            return self._ok

    def stop(self, timeout: float = 3.0) -> None:
        with self._guard:
            self._blocking = False
            th, tid = self._thread, self._tid
        if th is None:
            return
        if tid:
            user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
        th.join(timeout)
        with self._guard:
            self._thread = None
            self._tid = 0
            self._hhook = None
            self._ok = False
        if th.is_alive():
            log.warning("钩子线程未在 %.1fs 内退出（钩子仍会被系统回收）", timeout)

    # -- 内部实现 --------------------------------------------------------
    def _run(self) -> None:
        msg = wt.MSG()
        try:
            self._tid = int(kernel32.GetCurrentThreadId())
            # 先确保线程消息队列存在，才能被 PostThreadMessage 唤醒
            user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER,
                                PM_NOREMOVE)
            self._proc = HOOKPROC(self._hook_proc)
            hmod = kernel32.GetModuleHandleW(None)
            hhook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, hmod, 0)
            if not hhook:
                self._err = ctypes.get_last_error()
                log.error("安装键盘钩子失败（错误码 %s）。若游戏以管理员运行，"
                          "请同样以管理员身份运行本程序。", self._err)
                return
            self._hhook = hhook
            self._ok = True
            while True:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            log.exception("键盘钩子线程异常")
        finally:
            self._ok = False
            hhook = self._hhook
            if hhook:
                user32.UnhookWindowsHookEx(hhook)
                self._hhook = None
            self._ready.set()

    def _hook_proc(self, nCode, wParam, lParam):
        # 这个函数跑在每次按键的关键路径上，必须尽量短，且绝不能抛异常
        try:
            if nCode == HC_ACTION and self._blocking:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = int(kb.vkCode)
                if vk == VK_LWIN or vk == VK_RWIN:
                    if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        self._win_down_at = time.monotonic()
                    elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                        self._win_down_at = 0.0
                    return 1  # 吞掉，Windows 收不到
                if self.block_apps_key and vk == VK_APPS:
                    return 1
                if self.swallow_combo_keys and self._win_down_at:
                    if time.monotonic() - self._win_down_at > 2.0:
                        # 超时保护：绝不让键盘出现“按什么都没反应”
                        self._win_down_at = 0.0
                        self._swallowed.clear()
                    elif wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if len(self._swallowed) < 32:
                            self._swallowed.add(vk)
                        return 1
                    elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                        if vk in self._swallowed:
                            self._swallowed.discard(vk)
                            return 1
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)


# --------------------------------------------------------------- 热键 ----
_VK_NAMES = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}
_VK_PUNCT = {"`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
             "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF}
for _i in range(1, 25):
    _VK_NAMES["f%d" % _i] = 0x6F + _i


def parse_hotkey(text: str):
    if not text:
        return None
    mods = 0
    vk = None
    for part in str(text).lower().replace(" ", "").split("+"):
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "win键"):
            mods |= MOD_WIN
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part in _VK_NAMES:
            vk = _VK_NAMES[part]
        elif part in _VK_PUNCT:
            vk = _VK_PUNCT[part]
        else:
            return None
    if vk is None:
        return None
    return mods, vk


# --------------------------------------------------------------- 引擎 ----
class Engine:
    def __init__(self, cfg: dict, cfg_path: str) -> None:
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.blocker = WinKeyBlocker(
            block_apps_key=bool(cfg.get("block_apps_key", True)),
            swallow_combo_keys=bool(cfg.get("swallow_combo_keys", False)),
        )
        self.locked = False
        self.lock_reason = ""
        self.paused = False
        self.pause_until = 0.0
        self.manual_lock = False
        self.stop_event = threading.Event()
        self.stopping = False
        try:
            self._cfg_mtime = os.path.getmtime(cfg_path)
        except OSError:
            self._cfg_mtime = 0.0
        self._write_guard = threading.Lock()
        self._last_note = ""

    # -- 检测 ------------------------------------------------------------
    def evaluate(self) -> tuple[bool, str]:
        procs = set(snapshot_processes().values())
        fg = foreground_window()
        fg_exe = fg["exe"] if fg else ""
        if fg_exe:
            procs.add(fg_exe)

        game_hits = sorted(exe for exe in procs
                           if match_any(exe, self.cfg.get("games")))

        reason = ""
        if game_hits:
            if self.cfg.get("require_foreground") and fg_exe not in game_hits:
                self._note("游戏进程在后台（require_foreground=true，暂不禁用）：%s"
                           % game_hits[0])
            else:
                reason = "游戏运行中：%s" % game_hits[0]
        if not reason and self.cfg.get("detect_fullscreen", True) and fg:
            excluded = bool(fg_exe and match_any(
                fg_exe, self.cfg.get("fullscreen_exclusions")))
            excluded = excluded or (fg["class"] in
                                    (self.cfg.get("fullscreen_exclusion_classes") or []))
            # 只认“进程自己占满整块屏幕”的窗口；自己也是全屏的浏览器/播放器
            # 由排除名单过滤，避免看视频时被误禁用
            if fg["fullscreen"] and not excluded:
                reason = "全屏程序中：%s" % (fg_exe or fg["class"] or "未知")

        if self.paused:
            return False, "已暂停（Ctrl+Alt+P 恢复）"
        if self.manual_lock:
            return True, "手动锁定（Ctrl+Alt+L 解除）"
        return bool(reason), reason or "未检测到游戏"

    def _note(self, text: str) -> None:
        if text != self._last_note:
            self._last_note = text
            log.debug(text)

    # -- 状态切换 --------------------------------------------------------
    def set_locked(self, want: bool, reason: str) -> None:
        if want == self.locked:
            if want and reason and reason != self.lock_reason:
                self.lock_reason = reason
                self.write_state()
            return
        if want:
            if self.blocker.start():
                self.locked = True
                self.lock_reason = reason
                log.info("已禁用 Win 键 —— %s", reason)
                if self.cfg.get("beep", True):
                    beep("lock")
            else:
                self.locked = False
                log.error("禁用 Win 键失败，请查看上一条日志")
        else:
            self.blocker.stop()
            self.locked = False
            self.lock_reason = ""
            log.info("已恢复 Win 键 —— %s", reason)
            if self.cfg.get("beep", True):
                beep("unlock")
        self.write_state()

    def tick(self) -> None:
        self.reload_if_changed()
        if self.paused and self.pause_until:
            if time.time() >= self.pause_until:
                self.paused = False
                self.pause_until = 0.0
                log.info("暂停时间到，恢复自动检测")
        want, reason = self.evaluate()
        self.set_locked(want, reason)

    def reload_if_changed(self) -> None:
        """config.json 被改过就重新读，改名单不必重启守护。"""
        try:
            mtime = os.path.getmtime(self.cfg_path)
        except OSError:
            return
        if mtime <= self._cfg_mtime:
            return
        if not self._cfg_mtime:
            self._cfg_mtime = mtime
            return
        self._cfg_mtime = mtime
        try:
            fresh = load_config(self.cfg_path)
        except Exception:
            log.exception("重新读取配置失败，沿用旧配置")
            return
        # 运行期可改的项；热键改动需重启才生效
        for key in ("games", "detect_fullscreen", "fullscreen_exclusions",
                    "fullscreen_exclusion_classes", "require_foreground",
                    "block_apps_key", "swallow_combo_keys", "beep",
                    "pause_minutes"):
            if key in fresh:
                self.cfg[key] = fresh[key]
        self.blocker.block_apps_key = bool(self.cfg.get("block_apps_key", True))
        self.blocker.swallow_combo_keys = bool(
            self.cfg.get("swallow_combo_keys", False))
        log.info("配置已更新（游戏名单 %d 项）" % len(self.cfg.get("games") or []))

    # -- 热键回调 --------------------------------------------------------
    def on_hotkey(self, name: str) -> None:
        if name == "toggle_pause":
            self.paused = not self.paused
            minutes = float(self.cfg.get("pause_minutes") or 0)
            self.pause_until = (time.time() + minutes * 60) if (
                self.paused and minutes > 0) else 0.0
            if self.paused:
                log.info("已暂停守护，Win 键恢复正常（再按一次继续守护）")
                self.set_locked(False, "手动暂停")
            else:
                if self.pause_until:
                    self.pause_until = 0.0
                log.info("已恢复守护")
                self.tick()
            self.write_state()
        elif name == "toggle_lock":
            self.manual_lock = not self.manual_lock
            if self.manual_lock:
                self.paused = False
                self.pause_until = 0.0
                log.info("手动锁定 Win 键（再按一次解除）")
                self.set_locked(True, "手动锁定")
            else:
                log.info("手动锁定已解除")
                self.tick()
        elif name == "quit":
            log.info("收到退出热键，正在恢复 Win 键并退出")
            self.request_stop()

    def request_stop(self) -> None:
        self.stopping = True
        self.stop_event.set()

    # -- 状态文件 --------------------------------------------------------
    def write_state(self) -> None:
        with self._write_guard:
            data = {
                "running": not self.stopping,
                "locked": self.locked,
                "reason": self.lock_reason,
                "paused": self.paused,
                "manual_lock": self.manual_lock,
                "pid": os.getpid(),
                "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            try:
                os.makedirs(LOG_DIR, exist_ok=True)
                tmp = STATE_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, STATE_FILE)
            except Exception:
                log.debug("写入状态文件失败", exc_info=True)

    def shutdown(self, reason: str = "程序退出") -> None:
        self.stopping = True
        try:
            self.blocker.stop()
        finally:
            if self.locked:
                log.info("已恢复 Win 键 —— %s", reason)
                self.locked = False
                self.lock_reason = ""
            self.write_state()


# ------------------------------------------------------------- 热键线程 --
def hotkey_thread(engine: Engine) -> None:
    hk = (engine.cfg.get("hotkeys") or {})
    mapping: dict[int, str] = {}
    base = 0x5100
    idx = 0
    for name in ("toggle_pause", "toggle_lock", "quit"):
        parsed = parse_hotkey(hk.get(name, ""))
        if not parsed:
            continue
        mods, vk = parsed
        hid = base + idx
        idx += 1
        if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
            mapping[hid] = name
        else:
            log.warning("热键 %s 注册失败（可能被其他程序占用），请修改 config.json",
                        hk.get(name))

    tid = int(kernel32.GetCurrentThreadId())
    engine.hotkey_tid = tid  # type: ignore[attr-defined]
    msg = wt.MSG()
    user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, PM_NOREMOVE)
    try:
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            if msg.message == WM_HOTKEY:
                name = mapping.get(int(msg.wParam))
                if name:
                    try:
                        engine.on_hotkey(name)
                    except Exception:
                        log.exception("处理热键失败")
    finally:
        for hid in mapping:
            user32.UnregisterHotKey(None, hid)
    log.debug("热键线程退出")


# -------------------------------------------------------------- 子命令 ---
def load_config(path: str) -> dict:
    cfg: dict = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                raise ValueError("config.json 顶层必须是对象")
        except Exception as exc:
            log.error("读取 %s 失败（%s），改用默认配置", path, exc)
            cfg = {}
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in cfg.items():
        if k == "hotkeys" and isinstance(v, dict):
            merged["hotkeys"].update(v)
        elif k == "_说明":
            continue
        else:
            merged[k] = v
    return merged


def save_default_config(path: str) -> None:
    if os.path.exists(path):
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_pid_file() -> None:
    ensure_dirs()
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def read_pid_file() -> int | None:
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def read_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def describe_foreground() -> str:
    fg = foreground_window()
    if not fg:
        return "（没有拿到前台窗口）"
    return ("exe=%s  类名=%s  全屏=%s  标题=%s"
            % (fg["exe"] or "?", fg["class"],
               "是" if fg["fullscreen"] else "否", fg["title"][:60]))


def cmd_status() -> int:
    st = read_state()
    pid = st.get("pid") or read_pid_file()
    if not st:
        print("状态：没有找到状态文件，WinKeyGuard 应该没在运行。")
        print("（现在 Windows 键是正常可用的）")
        return 0
    alive = False
    if pid:
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if h:
            alive = True
            kernel32.CloseHandle(h)
    print("状态文件：%s" % STATE_FILE)
    print("进程 PID：%s（%s）" % (pid, "运行中" if alive else "已退出"))
    print("Win 键  ：%s" % ("已禁用" if st.get("locked") else "可用"))
    print("原因    ：%s" % (st.get("reason") or "—"))
    print("手动锁定：%s" % ("是" if st.get("manual_lock") else "否"))
    print("已暂停  ：%s" % ("是" if st.get("paused") else "否"))
    print("更新时间：%s" % (st.get("updated") or "—"))
    return 0


def cmd_stop() -> int:
    pid = read_pid_file() or (read_state().get("pid"))
    if not pid:
        print("没找到运行中的实例（PID 文件不存在）。")
        return 1
    pid = int(pid)
    h = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE
        | PROCESS_SYNCHRONIZE, False, pid)
    if not h:
        print("进程 %s 已经不在运行了。" % pid)
        return 1
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = DWORD(1024)
        image = ""
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            image = buf.value
        name = os.path.basename(image).lower()
        if not name.startswith(("python", "py")):
            print("PID %s 不是本程序（%s），为安全起见不结束它。" % (pid, image))
            return 1

        # 先请它自己退出（会正常恢复 Win 键、清理状态文件）；
        # 守候 3 秒仍不退出，才强制结束——钩子随进程消失，Win 键一样会恢复。
        ensure_dirs()
        with open(STOP_FLAG, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
        wait_ms = 3000
        if kernel32.WaitForSingleObject(h, wait_ms) == WAIT_OBJECT_0:
            cleanup_stop_flag()
            print("已停止 PID %s（%s），Win 键已恢复。" % (pid, name))
            return 0

        kernel32.TerminateProcess(h, 1)
        cleanup_stop_flag()
        print("已强制结束 PID %s（%s）。Win 键随进程退出已恢复。" % (pid, name))
        return 0
    finally:
        kernel32.CloseHandle(h)


def cleanup_stop_flag() -> None:
    for path in (STOP_FLAG, PID_FILE):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def cmd_diagnose(cfg: dict, cfg_path: str) -> int:
    print("=" * 62)
    print(" WinKeyGuard 自检")
    print("=" * 62)
    print("Python      : %s" % sys.version.split()[0])
    print("64 位       : %s" % (ctypes.sizeof(ctypes.c_void_p) == 8))
    print("脚本目录    : %s" % APP_DIR)
    print("配置文件    : %s%s" % (cfg_path, "" if os.path.exists(cfg_path) else "（不存在，将使用默认配置）"))
    print("管理员权限  : %s" % ("是" if is_admin() else "否（正常游戏不需要；"
                                        "若游戏以管理员运行，本程序也要管理员）"))
    print("日志文件    : %s" % LOG_FILE)
    print("-" * 62)

    procs = snapshot_processes()
    print("进程数量    : %d" % len(procs))
    hits = sorted({e for e in procs.values() if match_any(e, cfg.get("games"))})
    print("命中游戏名单: %s" % (", ".join(hits) if hits else "无"))
    print("前台窗口    : %s" % describe_foreground())
    print("-" * 62)

    blocker = WinKeyBlocker()
    ok = blocker.start()
    if ok:
        # 立刻进入“不拦截”状态再卸载：只验证钩子能力，不影响键盘
        blocker._blocking = False
        print("键盘钩子    : 可以安装（安装成功，随即卸载）")
        blocker.stop()
        print("Win 键状态  : 正常（钩子已卸载，未拦截任何按键）")
    else:
        print("键盘钩子    : 安装失败！错误码 %s" % blocker._err)
        print("              常见原因：安全软件拦截、进程权限不足。")
    print("-" * 62)
    print("想亲测效果：python wingard.py --test-hook 5")
    print("（这 5 秒内按 Win 键不会弹出开始菜单，5 秒后自动恢复）")
    print("=" * 62)
    return 0 if ok else 2


def cmd_add(cfg: dict, cfg_path: str, name: str | None,
            remove: bool = False) -> int:
    """把程序加入（或移出）游戏名单。name 为空时取当前前台程序。"""
    target = (name or "").strip()
    if not target:
        fg = foreground_window()
        if not fg or not fg["exe"]:
            print("没拿到当前前台程序，请改用：python wingard.py --add 进程名.exe")
            return 1
        target = fg["exe"]
        print("当前前台程序：%s（%s）" % (target, fg["title"][:50] or "无标题"))
    target = target.lower()
    if target.endswith(".exe"):
        target = target[:-4]
    if not target:
        print("进程名无效。")
        return 1

    try:
        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    games = list(raw.get("games") or [])
    lowered = [str(g).lower() for g in games]
    if remove:
        if target in lowered:
            games = [g for g, low in zip(games, lowered) if low != target]
            raw["games"] = games
            print("已从名单移除：%s" % target)
        else:
            print("名单里本来就没有：%s" % target)
            return 0
    else:
        if target in lowered:
            print("名单里已经有：%s（无需重复添加）" % target)
            return 0
        games.append(target)
        raw["games"] = games
        print("已加入游戏名单：%s" % target)
    tmp = cfg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cfg_path)
    print("配置已保存：%s（守护进程会在下一秒自动读到）" % cfg_path)
    return 0


def cmd_once(cfg: dict) -> int:
    procs = set(snapshot_processes().values())
    fg = foreground_window()
    fg_exe = fg["exe"] if fg else ""
    if fg_exe:
        procs.add(fg_exe)
    hits = sorted(exe for exe in procs if match_any(exe, cfg.get("games")))
    print("游戏名单命中: %s" % (", ".join(hits) if hits else "无"))
    print("前台窗口    : %s" % describe_foreground())
    excluded = bool(fg_exe and match_any(fg_exe, cfg.get("fullscreen_exclusions")))
    if fg:
        excluded = excluded or fg["class"] in (
            cfg.get("fullscreen_exclusion_classes") or [])
        print("全屏判定    : %s%s" % ("是" if fg["fullscreen"] else "否",
                                     "（在排除名单里）" if excluded else ""))
    if hits:
        print("结论        : 会禁用 Win 键（游戏进程 %s）" % hits[0])
    elif cfg.get("detect_fullscreen", True) and fg and fg["fullscreen"] and not excluded:
        print("结论        : 会禁用 Win 键（前台全屏程序）")
    else:
        print("结论        : Win 键保持可用")
    return 0


def cmd_test_hook(seconds: float) -> int:
    blocker = WinKeyBlocker()
    print("正在安装键盘钩子…")
    if not blocker.start():
        print("安装失败（错误码 %s）。" % blocker._err)
        return 2
    print("已拦截 Win 键 %d 秒，现在按 Win 键试试，应该没有反应。" % int(seconds))
    try:
        t0 = time.time()
        while time.time() - t0 < seconds:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        blocker.stop()
    print("钩子已卸载，Win 键已恢复正常。")
    return 0


# ------------------------------------------------------------- 主循环 ----
_console_handler_ref = None


def _install_console_handler(engine: Engine) -> None:
    global _console_handler_ref
    handler_type = ctypes.WINFUNCTYPE(BOOL, DWORD)

    def _handler(event: int) -> bool:
        try:
            engine.shutdown("窗口关闭/系统事件")
        except Exception:
            pass
        finally:
            os._exit(0)
        return True

    _console_handler_ref = handler_type(_handler)
    kernel32.SetConsoleCtrlHandler(_console_handler_ref, True)


def daemon_pid_alive() -> int | None:
    """已在运行的守护进程 PID；没有则 None。"""
    pid = read_pid_file() or read_state().get("pid")
    if not pid:
        return None
    try:
        pid = int(pid)
    except Exception:
        return None
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return pid
        name = os.path.basename(buf.value).lower()
        # PID 可能被系统复用，确认还是 Python 进程才认
        return pid if name.startswith(("python", "py")) else None
    finally:
        kernel32.CloseHandle(h)


def run_daemon(cfg: dict, cfg_path: str) -> int:
    ensure_dirs()
    running = daemon_pid_alive()
    if running:
        print("WinKeyGuard 已经在运行了（PID %s），不用重复启动。" % running)
        print("想重启请先运行：wingard.py --stop")
        return 3
    engine = Engine(cfg, cfg_path)
    write_pid_file()
    atexit.register(lambda: engine.shutdown("进程退出"))

    hk = threading.Thread(target=hotkey_thread, args=(engine,),
                          name="wingard-hotkey", daemon=True)
    hk.start()

    h = cfg.get("hotkeys") or {}
    print("-" * 62)
    print(" WinKeyGuard 已启动，正在守护。")
    print(" 热键：暂停/继续=%s   手动锁定=%s   退出=%s"
          % (h.get("toggle_pause"), h.get("toggle_lock"), h.get("quit")))
    print(" 游戏名单：%d 个进程名；全屏检测：%s"
          % (len(cfg.get("games") or []),
             "开" if cfg.get("detect_fullscreen", True) else "关"))
    print(" 关掉这个窗口或按 Ctrl+C 即可退出，Win 键立刻恢复。")
    print("-" * 62)
    log.info("守护启动 pid=%s", os.getpid())
    engine.write_state()
    # 交互式控制台事件（关窗/注销）只在打印完欢迎信息后接管
    _install_console_handler(engine)

    interval = float(cfg.get("poll_interval") or 1.0)
    interval = min(max(interval, 0.2), 10.0)
    try:
        engine.tick()
        while not engine.stop_event.is_set():
            engine.stop_event.wait(interval)
            if engine.stop_event.is_set():
                break
            if os.path.exists(STOP_FLAG):
                log.info("收到停止请求，正在退出")
                break
            try:
                engine.tick()
            except Exception:
                log.exception("检测循环出错")
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C")
    finally:
        engine.shutdown("程序退出")
        cleanup_stop_flag()
        log.info("WinKeyGuard 已停止")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wingard.py",
        description="打游戏时自动禁用 Windows 键，游戏结束后自动恢复。")
    p.add_argument("--config", help="配置文件路径（默认同目录 config.json）")
    p.add_argument("--status", action="store_true", help="显示当前状态")
    p.add_argument("--stop", action="store_true", help="停止后台运行中的实例")
    p.add_argument("--once", action="store_true", help="只检测一次并打印判定结果")
    p.add_argument("--add", nargs="?", const="", metavar="进程名",
                   help="把进程加入游戏名单（省略进程名则取当前前台程序）")
    p.add_argument("--remove", nargs="?", const="", metavar="进程名",
                   help="把进程移出游戏名单（省略进程名则取当前前台程序）")
    p.add_argument("--diagnose", action="store_true", help="环境自检")
    p.add_argument("--is-running", action="store_true",
                   help="仅检查是否已有实例在跑（退出码 3 = 在跑，0 = 没跑）")
    p.add_argument("--test-hook", nargs="?", type=float, const=5.0,
                   metavar="SECONDS",
                   help="临时拦截 Win 键 N 秒用于验证效果（默认 5 秒，自动恢复）")
    p.add_argument("--log-level", default=None,
                   choices=["debug", "info", "warning", "error"],
                   help="控制台日志级别（默认 info）")
    p.add_argument("--version", action="version", version="WinKeyGuard 1.0")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    ensure_dirs()

    cfg_path = os.path.abspath(args.config or CONFIG_FILE)
    save_default_config(cfg_path)
    cfg = load_config(cfg_path)
    level = args.log_level or cfg.get("log_level") or "info"
    setup_logging(level, console=True)

    if args.status:
        return cmd_status()
    if args.stop:
        return cmd_stop()
    if args.is_running:
        pid = daemon_pid_alive()
        if pid:
            print("WinKeyGuard 正在运行（PID %s）" % pid)
            return 3
        print("WinKeyGuard 没有在运行")
        return 0
    if args.diagnose:
        return cmd_diagnose(cfg, cfg_path)
    if args.once:
        return cmd_once(cfg)
    if args.add is not None:
        return cmd_add(cfg, cfg_path, args.add, remove=False)
    if args.remove is not None:
        return cmd_add(cfg, cfg_path, args.remove, remove=True)
    if args.test_hook is not None:
        return cmd_test_hook(args.test_hook)
    if args.log_level:
        cfg["log_level"] = args.log_level
    return run_daemon(cfg, cfg_path)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
