# -*- coding: utf-8 -*-
"""隐藏/显示桌面图标：只切换显示状态，一个文件都不碰。

原理：桌面「查看 → 显示桌面图标」的菜单命令 ID = 0x7402，
      发给真正的 SHELLDLL_DefView 窗口；它挂在 Progman 或某个 WorkerW 下面。
控制台改完后必须回读注册表 HideIcons 复核，SendMessage 返回成功但状态没变的情况确实存在。
"""
import ctypes
import sys
import time
from ctypes import wintypes

WM_COMMAND = 0x0111
CMD_DESKTOP_ICONS = 0x7402          # 0x7402 = 「显示桌面图标」开关
SMTO_ABORTIFHUNG = 0x0002
SMTO_TIMEOUT_MS = 2000
SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST = 0x0000
ADV_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
HIDE_VALUE = "HideIcons"
_POLL_STEP = 0.05                   # 轮询复核间隔（秒）
_POLL_TRIES = 40                    # 2000ms / 50ms

_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _user32():
    """按需加载 user32，加载不到返回 None。"""
    if not sys.platform.startswith("win"):
        return None
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowExW.restype = wintypes.HWND
        u.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                    wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]
        u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        u.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        return u
    except Exception:
        return None


def _shell32():
    if not sys.platform.startswith("win"):
        return None
    try:
        s = ctypes.WinDLL("shell32", use_last_error=True)
        s.SHChangeNotify.argtypes = [wintypes.LONG, wintypes.UINT,
                                     ctypes.c_void_p, ctypes.c_void_p]
        s.SHChangeNotify.restype = None
        return s
    except Exception:
        return None


# ---------------------------------------------------------------- 窗口定位
def defview_hwnd():
    """返回 SHELLDLL_DefView 的句柄；找不到返回 0。"""
    u = _user32()
    if not u:
        return 0
    try:
        prog = u.FindWindowW("Progman", None)      # 有的机器 DefView 直接在 Progman 下
        if prog:
            dv = u.FindWindowExW(prog, None, "SHELLDLL_DefView", None)
            if dv:
                return int(dv)
    except Exception:
        pass
    hits = []
    try:
        def cb(h, lp):                              # 其余机器挂在某个 WorkerW 下
            try:
                dv = u.FindWindowExW(h, None, "SHELLDLL_DefView", None)
                if dv:
                    hits.append(int(dv))
            except Exception:
                pass
            return True
        u.EnumWindows(_ENUMPROC(cb), 0)
    except Exception:
        return 0
    return hits[0] if hits else 0


# ---------------------------------------------------------------- 注册表状态
def _read_hideicons():
    """HideIcons DWORD：1=已隐藏，0=显示，读不到当 0。"""
    if not sys.platform.startswith("win"):
        return 0
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ADV_KEY) as k:
            return int(winreg.QueryValueEx(k, HIDE_VALUE)[0])
    except Exception:
        return 0


def _write_hideicons(value):
    """写 HideIcons；键或值不存在就建。成功返回 True。"""
    if not sys.platform.startswith("win"):
        return False
    try:
        import winreg
        k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, ADV_KEY, 0,
                               winreg.KEY_SET_VALUE)
        try:
            winreg.SetValueEx(k, HIDE_VALUE, 0, winreg.REG_DWORD, int(value))
        finally:
            winreg.CloseKey(k)
        return True
    except Exception:
        return False


def _notify():
    """通知资源管理器刷新。"""
    s = _shell32()
    if not s:
        return False
    try:
        s.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
        return True
    except Exception:
        return False


def is_hidden():
    """桌面图标现在是不是隐藏着。"""
    return _read_hideicons() != 0


# ---------------------------------------------------------------- 切换内核
def _send_cmd(dv):
    """给 DefView 发 0x7402；返回 (是否送达, 错误说明)。"""
    u = _user32()
    if not u:
        return False, "非 Windows 环境"
    if not dv:
        return False, "没找到桌面图标层"
    res = ctypes.c_size_t(0)
    try:
        ret = u.SendMessageTimeoutW(dv, WM_COMMAND, CMD_DESKTOP_ICONS, 0,
                                    SMTO_ABORTIFHUNG, SMTO_TIMEOUT_MS,
                                    ctypes.byref(res))
    except Exception as e:
        return False, "发送桌面命令失败: %s" % e
    if not ret:
        return False, "桌面图标层无响应"
    return True, ""


def _wait_state(want, tries=_POLL_TRIES):
    """等注册表状态变成 want（True=隐藏）；等到返回 True。"""
    for _ in range(max(1, tries)):
        if is_hidden() == bool(want):
            return True
        time.sleep(_POLL_STEP)
    return is_hidden() == bool(want)


def _set_hidden(want):
    """把桌面图标切到 want 状态，返回 (成功?, 错误说明)。"""
    want = bool(want)
    if not sys.platform.startswith("win"):
        return False, "非 Windows 环境"
    if is_hidden() == want:
        return True, ""                         # 幂等：已经是目标状态
    err = ""
    ok, e = _send_cmd(defview_hwnd())
    if not ok:
        err = e
    if _wait_state(want):
        return True, ""
    # 兜底：直接改注册表 + 通知刷新（SendMessage 报成功但状态没变时走这条）
    if not _write_hideicons(1 if want else 0):
        return False, (err + "；" if err else "") + "写注册表失败"
    _notify()
    if _wait_state(want):
        return True, ""
    return False, (err + "；" if err else "") + "桌面图标状态未变化"


def hide():
    """隐藏桌面图标。返回 (成功?, 错误说明)。"""
    try:
        return _set_hidden(True)
    except Exception as e:
        return False, "隐藏桌面图标失败: %s" % e


def show():
    """显示桌面图标。返回 (成功?, 错误说明)。"""
    try:
        return _set_hidden(False)
    except Exception as e:
        return False, "显示桌面图标失败: %s" % e


def toggle():
    """切换。返回 (成功?, 错误说明, 切换后是否隐藏)。"""
    try:
        if is_hidden():
            ok, err = _set_hidden(False)
        else:
            ok, err = _set_hidden(True)
        return ok, err, is_hidden()
    except Exception as e:
        return False, "切换桌面图标失败: %s" % e, False


def describe():
    """给 UI 当 tooltip：桌面图标：显示中 / 已隐藏。"""
    try:
        state = "已隐藏" if is_hidden() else "显示中"
        if not defview_hwnd():
            return "桌面图标：%s（暂未找到桌面图标层）" % state
        return "桌面图标：%s" % state
    except Exception:
        return "桌面图标：状态未知"
