# -*- coding: utf-8 -*-
"""Windows 原生右键菜单：和桌面右键完全一样的菜单（含第三方扩展）。

配方照抄 docs/NATIVE_MENU.md：SHCreateDefaultContextMenu + IContextMenu::QueryContextMenu。
本模块 import 时不做任何 COM / Win32 调用，全部延迟到 popup 或 _build。
"""
import ctypes
import os
import sys
from ctypes import wintypes

WM_COMMAND = 0x0111
WM_INITMENUPOPUP = 0x0117
WM_DRAWITEM = 0x002B
WM_MEASUREITEM = 0x002C
WM_MENUCHAR = 0x0120
WM_MENUSELECT = 0x011F

TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
MF_BYPOSITION = 0x0400
CMF_EXPLORE = 0x00000004
CMF_CANRENAME = 0x00000010
CMF_EXTENDEDVERBS = 0x00000100
SVGIO_BACKGROUND = 0
COINIT_APARTMENTTHREADED = 0x2
S_OK = 0
S_FALSE = 1                 # 线程已经是 STA，本次调用没加引用计数
RPC_E_CHANGED_MODE = 0x80010106

# vtable 索引（docs/NATIVE_MENU.md）
VT_QUERY_INTERFACE = 0
VT_RELEASE = 2
VT_QUERY_CONTEXT_MENU = 3
VT_INVOKE_COMMAND = 4
VT_HANDLE_MENU_MSG = 6
VT_HANDLE_MENU_MSG2 = 7

# IShellFolder
VT_SF_BIND_TO_OBJECT = 5
VT_SF_CREATE_VIEW_OBJECT = 8
# IShellView
VT_SV_GET_ITEM_OBJECT = 15

IID_ISHELLFOLDER = "{000214E6-0000-0000-C000-000000000046}"
IID_ISHELLVIEW = "{000214E3-0000-0000-C000-000000000046}"

_MENU_MSGS = (WM_INITMENUPOPUP, WM_DRAWITEM, WM_MEASUREITEM, WM_MENUCHAR)

# 宿主窗口收到菜单消息时从这里取当前 IContextMenu2/3
_CUR = {"pcm3": None, "pcm2": None, "sel_menu": 0, "sel_id": 0}
_LIBS = {}
_WNDPROC_TYPE = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM)


# ---------------------------------------------------------------- 结构体
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class DEFCONTEXTMENU(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("pcmcb", ctypes.c_void_p),
                ("pidlFolder", ctypes.c_void_p), ("psf", ctypes.c_void_p),
                ("cidl", wintypes.UINT),
                ("apidl", ctypes.POINTER(ctypes.c_void_p)),
                ("punkAssociationInfo", ctypes.c_void_p),
                ("cKeys", wintypes.UINT), ("aKeys", ctypes.c_void_p)]


class CMINVOKECOMMANDINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.DWORD),
                ("hwnd", wintypes.HWND), ("lpVerb", ctypes.c_void_p),
                ("lpParameters", ctypes.c_void_p),
                ("lpDirectory", ctypes.c_void_p), ("nShow", ctypes.c_int),
                ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName",
                                                     wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]


def _co_start(ole32):
    """调 CoInitializeEx，返回 (ok, need_uninit, hr)。

    need_uninit 表示「本次调用要不要配对 CoUninitialize」：
      S_OK               这次真的初始化了 -> True
      S_FALSE            线程本来就是 STA，本次没加引用计数 -> False
      RPC_E_CHANGED_MODE 别的单元模式，同样没加计数       -> False
    后两种都不能判失败。Qt 的 QApplication 已把主线程设成 STA，再调一次拿到
    的是 S_FALSE(1)；把它当失败或照样 Uninitialize，都会破坏 Qt 的 STA。
    hr 按 32 位无符号比：ctypes 未声明 restype 时返回有符号 c_int，负数与
    0x80010106 直接比永远不成立。

    与 store._co_start 同语义，就地重写而不 import store —— 避免循环依赖。
    """
    hr = int(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)) & 0xFFFFFFFF
    if hr == S_OK:
        return True, True, hr
    if hr in (S_FALSE, RPC_E_CHANGED_MODE):
        return True, False, hr
    return False, False, hr


# ---------------------------------------------------------------- 惰性加载
def _libs():
    """按需加载 DLL 并声明原型；非 Windows 返回 None。"""
    if _LIBS:
        return _LIBS
    if not sys.platform.startswith("win"):
        return None
    try:
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        ole32.CoInitializeEx.restype = ctypes.c_long
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        ole32.CoUninitialize.argtypes = []
        ole32.CLSIDFromString.restype = ctypes.c_long
        ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR,
                                          ctypes.POINTER(GUID)]
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

        shell32.SHParseDisplayName.restype = ctypes.c_long
        shell32.SHParseDisplayName.argtypes = [
            wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
            wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        shell32.SHBindToParent.restype = ctypes.c_long
        shell32.SHBindToParent.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
        shell32.SHCreateDefaultContextMenu.restype = ctypes.c_long
        shell32.SHCreateDefaultContextMenu.argtypes = [
            ctypes.POINTER(DEFCONTEXTMENU), ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p)]
        shell32.SHGetDesktopFolder.restype = ctypes.c_long
        shell32.SHGetDesktopFolder.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        # 背景菜单要显式补发 WM_INITMENUPOPUP，见 _prefill_submenus
        user32.SendMessageW.restype = ctypes.c_ssize_t
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]

        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.CreatePopupMenu.argtypes = []
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.GetMenuItemCount.restype = ctypes.c_int
        user32.GetMenuItemCount.argtypes = [wintypes.HMENU]
        user32.GetMenuItemID.restype = ctypes.c_int
        user32.GetMenuItemID.argtypes = [wintypes.HMENU, ctypes.c_int]
        user32.GetMenuStringW.restype = ctypes.c_int
        user32.GetMenuStringW.argtypes = [wintypes.HMENU, wintypes.UINT,
                                          wintypes.LPWSTR, ctypes.c_int,
                                          wintypes.UINT]
        user32.GetSubMenu.restype = wintypes.HMENU
        user32.GetSubMenu.argtypes = [wintypes.HMENU, ctypes.c_int]
        user32.TrackPopupMenuEx.restype = wintypes.BOOL
        user32.TrackPopupMenuEx.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, ctypes.c_void_p]
        user32.RegisterClassExW.restype = wintypes.ATOM
        user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]

        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetProcessHandleCount.restype = wintypes.BOOL
        kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE,
                                                   ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    except Exception:
        return None
    _LIBS.update({"ole32": ole32, "shell32": shell32, "user32": user32,
                  "kernel32": kernel32})
    return _LIBS


def _guid(s):
    libs = _libs()
    g = GUID()
    libs["ole32"].CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
    return g


def _vfn(ptr, idx, restype, *argtypes):
    """取 COM 虚表第 idx 项并包成可调用函数。"""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p,
                              *argtypes)(vtbl[idx])


def _release(ptr):
    """IUnknown::Release，ptr 是 c_void_p 或裸值。"""
    if not ptr:
        return
    try:
        _vfn(ptr, VT_RELEASE, ctypes.c_ulong)(ptr)
    except Exception:
        pass


def _qi(ptr, iid):
    """QueryInterface，拿不到返回 None。"""
    if not ptr:
        return None
    try:
        out = ctypes.c_void_p()
        hr = _vfn(ptr, VT_QUERY_INTERFACE, ctypes.c_long, ctypes.POINTER(GUID),
                  ctypes.POINTER(ctypes.c_void_p))(ptr, ctypes.byref(iid),
                                                   ctypes.byref(out))
        return out if hr == 0 and out.value else None
    except Exception:
        return None


def available():
    """(能不能用, 原因)。"""
    if not sys.platform.startswith("win"):
        return False, "非 Windows 环境"
    libs = _libs()
    if not libs:
        return False, "系统 shell/ole32 组件加载失败"
    try:
        ok, need_uninit, hr = _co_start(libs["ole32"])
        if not ok:
            return False, "COM 初始化失败: hr=0x%08X" % hr
        if need_uninit:
            # 只有本次真的初始化过才配对释放；S_FALSE / CHANGED_MODE 都别动
            libs["ole32"].CoUninitialize()
    except Exception as e:
        return False, "COM 初始化失败: %s" % e
    return True, "SHCreateDefaultContextMenu 可用（含第三方扩展）"


# ---------------------------------------------------------------- 宿主窗口
def _wndproc(hwnd, msg, wparam, lparam):
    """菜单消息转发给 IContextMenu3/2，其余交给 DefWindowProc。"""
    libs = _LIBS
    try:
        if msg == WM_MENUSELECT:
            _CUR["sel_menu"] = int(wparam or 0)
            _CUR["sel_id"] = int(lparam or 0) & 0xFFFF
        if msg in _MENU_MSGS:
            p3 = _CUR.get("pcm3")
            p2 = _CUR.get("pcm2")
            if p3:
                res = wintypes.LPARAM(0)
                _vfn(p3, VT_HANDLE_MENU_MSG2, ctypes.c_long, wintypes.UINT,
                     wintypes.WPARAM, wintypes.LPARAM,
                     ctypes.POINTER(wintypes.LPARAM))(
                    p3, msg, wparam, lparam, ctypes.byref(res))
                return res.value
            if p2:
                _vfn(p2, VT_HANDLE_MENU_MSG, ctypes.c_long, wintypes.UINT,
                     wintypes.WPARAM, wintypes.LPARAM)(p2, msg, wparam, lparam)
                return 0
    except Exception:
        pass
    try:
        return libs["user32"].DefWindowProcW(hwnd, msg, wparam, lparam)
    except Exception:
        return 0


_HOST = {"hwnd": 0, "proc": None, "class": None}


def host_hwnd():
    """建（或复用）不可见宿主窗口，返回 hwnd；失败返回 0。"""
    if _HOST["hwnd"]:
        return _HOST["hwnd"]
    libs = _libs()
    if not libs:
        return 0
    try:
        user32 = libs["user32"]
        hinst = libs["kernel32"].GetModuleHandleW(None)
        cls = "DeskBoxMenuHost"
        # 回调对象必须全进程唯一且始终被引用：窗口类一旦注册就长期记住第一次
        # 传进去的 lpfnWndProc 地址，若每次都新建 _WNDPROC_TYPE，旧对象被 GC 后
        # 销毁窗口就会跳进已释放的 thunk（实测 Fatal exception code 0xc0000096）。
        proc = _HOST["proc"] or _WNDPROC_TYPE(_wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = cls
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            pass                        # 已注册过也算可用
        hwnd = user32.CreateWindowExW(0, cls, "DeskBoxMenuHost", 0x80000000,
                                      0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            return 0
        _HOST.update({"hwnd": int(hwnd), "proc": proc, "class": cls})
        return _HOST["hwnd"]
    except Exception:
        return 0


# ---------------------------------------------------------------- 建菜单
class _BuiltMenu(object):
    """一次建菜单的全部资源。可解包成 (pcm, hmenu, n_items)。"""

    def __init__(self, pcm, hmenu, n_items):
        self.pcm = pcm
        self.hmenu = hmenu
        self.n_items = n_items
        self.pcm2 = None
        self.pcm3 = None
        self._pidls = []
        self._coms = []          # 需要 Release 的 COM 指针（IShellFolder / IShellView / 桌面根）
        self.prefilled = 0       # 背景菜单已补发过 INITMENUPOPUP 的子菜单个数
        self._error = ""         # 建菜单失败原因，给 last_error 用
        self._done = False

    def __iter__(self):
        return iter((self.pcm, self.hmenu, self.n_items))

    def release(self):
        """DestroyMenu + 逐个 Release + 释放 pidl。幂等。"""
        if self._done:
            return
        self._done = True
        libs = _libs()
        if self.hmenu and libs:
            try:
                libs["user32"].DestroyMenu(self.hmenu)
            except Exception:
                pass
            self.hmenu = None
        for p in (self.pcm3, self.pcm2):
            _release(p)
        self.pcm3 = self.pcm2 = None
        _release(self.pcm)
        self.pcm = None
        if libs:
            for p in self._pidls:
                try:
                    libs["ole32"].CoTaskMemFree(p)
                except Exception:
                    pass
        self._pidls = []
        for c in self._coms:
            _release(c)
        self._coms = []


def _is_virtual(p):
    """::{CLSID} 这种系统项，不是磁盘文件。"""
    try:
        return isinstance(p, str) and p.strip().startswith("::")
    except Exception:
        return False


def _menu_from(dcm, hwnd=0):
    """SHCreateDefaultContextMenu + QueryContextMenu，返回 _BuiltMenu。"""
    libs = _libs()
    shell32, user32 = libs["shell32"], libs["user32"]
    iid_cm = _guid("{000214e4-0000-0000-c000-000000000046}")
    men = _BuiltMenu(None, None, 0)
    pcm = ctypes.c_void_p()
    hr = shell32.SHCreateDefaultContextMenu(ctypes.byref(dcm),
                                            ctypes.byref(iid_cm),
                                            ctypes.byref(pcm))
    if hr != 0 or not pcm.value:
        return men
    men.pcm = pcm
    hmenu = user32.CreatePopupMenu()
    if not hmenu:
        return men
    men.hmenu = hmenu
    # idCmdFirst 必须是 1，lpVerb 才能用 cmd - 1
    _vfn(pcm, VT_QUERY_CONTEXT_MENU, ctypes.c_long, wintypes.HMENU,
         wintypes.UINT, wintypes.UINT, wintypes.UINT,
         wintypes.UINT)(pcm, hmenu, 0, 1, 0x7FFF,
                        CMF_EXPLORE | CMF_EXTENDEDVERBS)
    try:
        men.n_items = int(user32.GetMenuItemCount(hmenu))
    except Exception:
        men.n_items = 0
    men.pcm2 = _qi(pcm, _guid("{000214f4-0000-0000-c000-000000000046}"))
    men.pcm3 = _qi(pcm, _guid("{bcfce0a0-ec17-11d0-8d10-00a0c90f2719}"))
    return men


def _build_virtual(clsid, hwnd=0):
    """给 ::{CLSID} 系统项建菜单。

    内存规则（探针实测）：full_pidl 与 child_pidl 都不单独 CoTaskMemFree
    —— child 是 full 内部的子 pidl，由菜单借用；只 Release 拿到的 IShellFolder。
    """
    libs = _libs()
    if not libs:
        return _BuiltMenu(None, None, 0)
    shell32 = libs["shell32"]
    iid_sf = _guid("{000214e6-0000-0000-c000-000000000046}")
    men = _BuiltMenu(None, None, 0)
    full = ctypes.c_void_p()
    hr = shell32.SHParseDisplayName(ctypes.c_wchar_p(clsid), None,
                                    ctypes.byref(full), 0,
                                    ctypes.byref(wintypes.ULONG()))
    if hr != 0 or not full.value:
        return men
    psf = ctypes.c_void_p()
    child = ctypes.c_void_p()
    if shell32.SHBindToParent(full, ctypes.byref(iid_sf),
                              ctypes.byref(psf), ctypes.byref(child)) != 0:
        return men
    if not psf.value or not child.value:
        return men
    men._coms.append(psf.value)
    arr = (ctypes.c_void_p * 1)(child.value)
    dcm = DEFCONTEXTMENU()
    dcm.hwnd = hwnd or None
    dcm.pidlFolder = None
    dcm.psf = psf.value
    dcm.cidl = 1
    dcm.apidl = arr
    out = _menu_from(dcm, hwnd)
    out._coms.extend(men._coms)
    return out


def _prefill_submenus(hmenu, hwnd=0):
    """显式给每个带子菜单的项补发 WM_INITMENUPOPUP。

    wParam 必须是「那个子菜单自己的 HMENU」（GetSubMenu 拿到的），lParam 低 16 位
    是它在父菜单里的位置。传顶层 hmenu 会让 IContextMenu3::HandleMenuMsg2 返回
    0x80004005 且什么都不填 —— 桌面背景菜单的「新建 ▸」会只剩一个占位项。
    重复发送幂等，不发也能用（TrackPopupMenuEx 的模态循环通常自己会发），
    但发完更稳。返回补发成功的子菜单个数。
    """
    libs = _libs()
    if not libs or not hmenu:
        return 0
    user32 = libs["user32"]
    host = int(hwnd or 0) or host_hwnd()
    if not host:
        return 0
    n = 0
    try:
        count = int(user32.GetMenuItemCount(hmenu))
    except Exception:
        return 0
    for i in range(count):
        try:
            sub = user32.GetSubMenu(hmenu, i)
        except Exception:
            continue
        if not sub:
            continue
        try:
            user32.SendMessageW(host, WM_INITMENUPOPUP, sub, i & 0xFFFF)
            n += 1
        except Exception:
            pass
    return n


def _view_menu(psf, hwnd=0):
    """IShellFolder -> CreateViewObject(IID_IShellView) -> GetItemObject。"""
    libs = _libs()
    user32 = libs["user32"]
    iid_sv = _guid(IID_ISHELLVIEW)
    iid_cm = _guid("{000214e4-0000-0000-c000-000000000046}")
    men = _BuiltMenu(None, None, 0)
    psv = ctypes.c_void_p()
    try:
        hr = _vfn(psf, VT_SF_CREATE_VIEW_OBJECT, ctypes.c_long, ctypes.c_void_p,
                  ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            psf, None, ctypes.byref(iid_sv), ctypes.byref(psv))
    except Exception:
        hr = -1
    if hr != 0 or not psv.value:
        men._error = "CreateViewObject hr=0x%08X" % ((hr or 0) & 0xFFFFFFFF)
        return men
    men._coms.append(psv.value)          # IShellView 必须 Release
    pcm = ctypes.c_void_p()
    try:
        hr = _vfn(psv, VT_SV_GET_ITEM_OBJECT, ctypes.c_long, ctypes.c_uint,
                  ctypes.POINTER(GUID),
                  ctypes.POINTER(ctypes.c_void_p))(psv, SVGIO_BACKGROUND,
                                                   ctypes.byref(iid_cm),
                                                   ctypes.byref(pcm))
    except Exception:
        hr = -1
    if hr != 0 or not pcm.value:
        men._error = "GetItemObject hr=0x%08X" % ((hr or 0) & 0xFFFFFFFF)
        return men
    men.pcm = pcm
    hmenu = user32.CreatePopupMenu()
    if not hmenu:
        men._error = "CreatePopupMenu 失败"
        return men
    men.hmenu = hmenu
    # idCmdFirst 必须是 1（_invoke 用 cmd-1 当 lpVerb 低字）
    _vfn(pcm, VT_QUERY_CONTEXT_MENU, ctypes.c_long, wintypes.HMENU,
         wintypes.UINT, wintypes.UINT, wintypes.UINT,
         wintypes.UINT)(pcm, hmenu, 0, 1, 0x7FFF,
                        CMF_EXPLORE | CMF_CANRENAME)
    try:
        men.n_items = int(user32.GetMenuItemCount(hmenu))
    except Exception:
        men.n_items = 0
    men.pcm2 = _qi(pcm, _guid("{000214f4-0000-0000-c000-000000000046}"))
    men.pcm3 = _qi(pcm, _guid("{bcfce0a0-ec17-11d0-8d10-00a0c90f2719}"))
    # 补发 WM_INITMENUPOPUP，把「新建」子菜单填满
    if men.pcm3 or men.pcm2:
        _CUR["pcm3"] = men.pcm3
        _CUR["pcm2"] = men.pcm2
        men.prefilled = _prefill_submenus(hmenu, hwnd)
    return men


def _build_background(folder=None, hwnd=0):
    """桌面背景菜单（= 真桌面空白处右键）。

    folder=None -> 桌面根命名空间；folder=<路径> -> 该文件夹的视图背景菜单。
    """
    libs = _libs()
    if not libs:
        return _BuiltMenu(None, None, 0)
    shell32 = libs["shell32"]
    men = _BuiltMenu(None, None, 0)
    pidl = ctypes.c_void_p()
    psf_extra = None
    try:
        pdesc = ctypes.c_void_p()
        if shell32.SHGetDesktopFolder(ctypes.byref(pdesc)) != 0 or not pdesc.value:
            men._error = "SHGetDesktopFolder 失败"
            return men
        men._coms.append(pdesc.value)
        psf = pdesc
        if folder:
            hr = shell32.SHParseDisplayName(ctypes.c_wchar_p(str(folder)), None,
                                            ctypes.byref(pidl), 0,
                                            ctypes.byref(wintypes.ULONG()))
            if hr != 0 or not pidl.value:
                men._error = "SHParseDisplayName hr=0x%08X" % (hr & 0xFFFFFFFF)
                return men
            men._pidls.append(pidl.value)
            psf_extra = ctypes.c_void_p()
            iid_sf = _guid(IID_ISHELLFOLDER)
            hr = _vfn(pdesc, VT_SF_BIND_TO_OBJECT, ctypes.c_long,
                      ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
                      ctypes.POINTER(ctypes.c_void_p))(
                pdesc, pidl.value, None, ctypes.byref(iid_sf),
                ctypes.byref(psf_extra))
            if hr != 0 or not psf_extra.value:
                men._error = "BindToObject hr=0x%08X" % (hr & 0xFFFFFFFF)
                return men
            men._coms.append(psf_extra.value)
            psf = psf_extra
        out = _view_menu(psf, hwnd)
        out._coms[0:0] = men._coms      # 把 desktop/psf 并进同一条释放链
        out._pidls[0:0] = men._pidls
        out._error = out._error or men._error
        return out
    except Exception as e:
        men._error = "建背景菜单失败: %s" % e
        return men


def _build(paths, hwnd=0):
    """建好菜单并填项，不弹窗。返回可解包成 (pcm, hmenu, n_items) 的对象。"""
    libs = _libs()
    if not libs:
        return _BuiltMenu(None, None, 0)
    # 第一项决定父容器：系统项走 CLSID 分支，文件走原分支，混选时丢掉另一类
    kept = [str(p) for p in (paths or [])
            if p and (os.path.exists(str(p)) or _is_virtual(str(p)))]
    if not kept:
        return _BuiltMenu(None, None, 0)
    if _is_virtual(kept[0]):
        return _build_virtual(kept[0], hwnd)
    paths = [os.path.abspath(p) for p in kept if not _is_virtual(p)]
    if not paths:
        return _BuiltMenu(None, None, 0)
    shell32 = libs["shell32"]
    user32 = libs["user32"]
    iid_sf = _guid("{000214e6-0000-0000-c000-000000000046}")
    iid_cm = _guid("{000214e4-0000-0000-c000-000000000046}")

    men = _BuiltMenu(None, None, 0)
    pidl_folder = ctypes.c_void_p()
    hr = shell32.SHParseDisplayName(ctypes.c_wchar_p(os.path.dirname(paths[0])),
                                    None, ctypes.byref(pidl_folder), 0,
                                    ctypes.byref(wintypes.ULONG()))
    if hr != 0 or not pidl_folder.value:
        return men
    men._pidls.append(pidl_folder.value)

    kids, psf = [], ctypes.c_void_p()
    for p in paths:
        full = ctypes.c_void_p()
        hr = shell32.SHParseDisplayName(ctypes.c_wchar_p(p), None,
                                        ctypes.byref(full), 0,
                                        ctypes.byref(wintypes.ULONG()))
        if hr != 0 or not full.value:
            continue
        men._pidls.append(full.value)
        ps = ctypes.c_void_p()
        ch = ctypes.c_void_p()
        if shell32.SHBindToParent(full, ctypes.byref(iid_sf),
                                  ctypes.byref(ps), ctypes.byref(ch)) != 0:
            continue
        if not psf.value:
            psf = ps
        kids.append(ch.value)
    if not kids or not psf.value:
        return men

    arr = (ctypes.c_void_p * len(kids))(*kids)
    dcm = DEFCONTEXTMENU()
    dcm.hwnd = hwnd or None
    dcm.pidlFolder = pidl_folder.value
    dcm.psf = psf.value
    dcm.cidl = len(kids)
    dcm.apidl = arr
    pcm = ctypes.c_void_p()
    hr = shell32.SHCreateDefaultContextMenu(ctypes.byref(dcm),
                                            ctypes.byref(iid_cm),
                                            ctypes.byref(pcm))
    if hr != 0 or not pcm.value:
        return men
    men.pcm = pcm

    hmenu = user32.CreatePopupMenu()
    if not hmenu:
        return men
    men.hmenu = hmenu
    # idCmdFirst 必须是 1，lpVerb 才能用 cmd - 1
    # 少了 CMF_CANRENAME 菜单里就没有「重命名」（实测 53 项而不是 54 项）
    _vfn(pcm, VT_QUERY_CONTEXT_MENU, ctypes.c_long, wintypes.HMENU,
         wintypes.UINT, wintypes.UINT, wintypes.UINT,
         wintypes.UINT)(pcm, hmenu, 0, 1, 0x7FFF,
                        CMF_EXPLORE | CMF_CANRENAME | CMF_EXTENDEDVERBS)
    try:
        men.n_items = int(user32.GetMenuItemCount(hmenu))
    except Exception:
        men.n_items = 0
    men.pcm2 = _qi(pcm, _guid("{000214f4-0000-0000-c000-000000000046}"))
    men.pcm3 = _qi(pcm, _guid("{bcfce0a0-ec17-11d0-8d10-00a0c90f2719}"))
    return men


# ---------------------------------------------------------------- 文本
def _walk(hmenu, depth=0, out=None):
    """递归收集 (depth, index, id, text)。"""
    libs = _libs()
    out = [] if out is None else out
    if not hmenu or not libs or depth > 4:
        return out
    user32 = libs["user32"]
    try:
        n = int(user32.GetMenuItemCount(hmenu))
    except Exception:
        return out
    buf = ctypes.create_unicode_buffer(512)
    for i in range(n):
        try:
            user32.GetMenuStringW(hmenu, i, buf, 512, MF_BYPOSITION)
            txt = buf.value.replace("&", "")
            mid = int(user32.GetMenuItemID(hmenu, i))
            sub = user32.GetSubMenu(hmenu, i)
        except Exception:
            continue
        out.append((depth, i, mid, txt))
        if sub:
            _walk(sub, depth + 1, out)
    return out


def _menu_texts(hmenu):
    """全部菜单项文本（含子菜单），给测试与调试用。"""
    return [t for _, _, _, t in _walk(hmenu) if t]


def _text_of(hmenu, cmd):
    """按命令 id 反查菜单文本；找不到退回到 WM_MENUSELECT 记录。"""
    for _d, _i, mid, txt in _walk(hmenu):
        if mid == cmd and txt:
            return txt
    sel = _CUR.get("sel_menu")
    if sel and _CUR.get("sel_id") == cmd - 1:
        for _d, _i, mid, txt in _walk(sel):
            if mid == cmd and txt:
                return txt
    return ""


def _is_rename_text(text):
    """选中项是不是「重命名」。

    shell 的 rename 动词只在 Explorer 文件夹视图里实现，裸 IContextMenu 宿主动用
    它必返回 E_NOTIMPL(0x80004001)（字符串动词 lpVerb=b"rename" 也一样）。
    所以 popup 见到它只回文本、不执行，真正的重命名交给 store.rename()。
    """
    return str(text or "").replace("&", "").strip().startswith("重命名")


def _invoke(pcm, hwnd, cmd):
    """执行菜单命令：cbSize=56、fMask=0、lpVerb=cmd-1。返回 hr。"""
    info = CMINVOKECOMMANDINFO()
    info.cbSize = ctypes.sizeof(CMINVOKECOMMANDINFO)      # 必须是 56
    info.fMask = 0
    info.hwnd = hwnd or None
    info.lpVerb = ctypes.c_void_p(max(0, int(cmd) - 1))
    info.nShow = 1
    try:
        return _vfn(pcm, VT_INVOKE_COMMAND, ctypes.c_long,
                    ctypes.POINTER(CMINVOKECOMMANDINFO))(pcm,
                                                         ctypes.byref(info))
    except Exception:
        return -1


# ---------------------------------------------------------------- 对外
class ShellMenu(object):
    """宿主窗口 + 原生菜单。用完 close()。"""

    def __init__(self, app=None):
        self.app = app
        self.last_error = ""
        self._host = 0

    def popup(self, paths, global_pos, hwnd=0):
        """阻塞弹菜单，返回被选中项文本；取消返回 None。

        hwnd 是「命令父窗口」：菜单项被选中后，属性对话框、确认框之类会挂它
        下面（传 Qt 主窗口即可，比挂在一个不可见宿主窗口上体验好）。
        菜单所有者（TrackPopupMenuEx 的 owner）永远是我们自建的不可见宿主窗口
        —— 换成调用方的 Qt 窗口会吞掉 WM_INITMENUPOPUP/MEASUREITEM/DRAWITEM，
        第三方扩展画不出来，菜单还会变得无法被 WM_CANCELMODE 打断。

        「重命名」是个例外：shell 自己的 rename 动词在裸 IContextMenu 宿主里必返回
        E_NOTIMPL(0x80004001)——它要 Explorer 的文件夹视图/OLE 站点，字符串动词
        lpVerb=b"rename" 也一样。所以这里只把文本交回上层，不调 _invoke，
        真正的重命名由上层走 store.rename()（IFileOperation::RenameItem）。
        """
        libs = _libs()
        if not libs:
            self.last_error = "非 Windows 环境"
            return None
        paths = [str(p) for p in (paths or [])
                 if p and (os.path.exists(str(p)) or _is_virtual(str(p)))]
        if not paths:
            return None
        x, y = _pos(global_pos)
        # 所有者与命令父窗口必须分开，见 docstring
        menu_owner = host_hwnd() or int(hwnd or 0)
        if not menu_owner:
            self.last_error = "宿主窗口创建失败"
            return None
        cmd_hwnd = int(hwnd or 0) or menu_owner
        ole32 = libs["ole32"]
        co = False
        try:
            ok, co, hr = _co_start(ole32)
            if not ok:
                self.last_error = "COM 初始化失败: hr=0x%08X" % hr
                return None
        except Exception as e:
            self.last_error = "COM 初始化失败: %s" % e
            return None
        men = None
        try:
            men = _build(paths, menu_owner)
            if not men.hmenu:
                self.last_error = "菜单创建失败"
                return None
            _CUR["pcm3"] = men.pcm3
            _CUR["pcm2"] = men.pcm2
            _CUR["sel_menu"] = 0
            _CUR["sel_id"] = 0
            cmd = int(libs["user32"].TrackPopupMenuEx(
                men.hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, menu_owner,
                None))
            if not cmd:
                return None
            text = _text_of(men.hmenu, cmd)
            if _is_rename_text(text):
                # 原生 rename 动词在裸宿主必 E_NOTIMPL，交给上层自己改
                return text
            hr = _invoke(men.pcm, cmd_hwnd, cmd)
            if hr != 0:
                self.last_error = "执行菜单命令失败: hr=0x%08X" % (hr & 0xFFFFFFFF)
            return text or None
        except Exception as e:
            self.last_error = "弹出菜单失败: %s" % e
            return None
        finally:
            _CUR["pcm3"] = None
            _CUR["pcm2"] = None
            if men is not None:
                men.release()
            if co:
                try:
                    ole32.CoUninitialize()
                except Exception:
                    pass

    def popup_background(self, folder=None, global_pos=(0, 0), hwnd=0):
        """弹「桌面背景」原生右键菜单，返回被选中项文本；取消返回 None。

        folder=None  -> 用桌面根命名空间（= 真桌面右键，新建落到用户桌面目录）
        folder=<路径> -> 用该文件夹的 IShellView 背景菜单（给测试用，写进该目录）
        hwnd 是命令父窗口（Qt 主窗），菜单 owner 仍是 host_hwnd()——与 popup()
        同一分离原则，理由见 popup 的 docstring。
        """
        libs = _libs()
        if not libs:
            self.last_error = "非 Windows 环境"
            return None
        if folder is not None and not os.path.isdir(str(folder)):
            self.last_error = "目录不存在: %s" % folder
            return None
        x, y = _pos(global_pos)
        menu_owner = host_hwnd() or int(hwnd or 0)
        if not menu_owner:
            self.last_error = "宿主窗口创建失败"
            return None
        cmd_hwnd = int(hwnd or 0) or menu_owner
        ole32 = libs["ole32"]
        co = False
        try:
            ok, co, hr = _co_start(ole32)
            if not ok:
                self.last_error = "COM 初始化失败: hr=0x%08X" % hr
                return None
        except Exception as e:
            self.last_error = "COM 初始化失败: %s" % e
            return None
        men = None
        old3, old2 = _CUR["pcm3"], _CUR["pcm2"]
        try:
            men = _build_background(folder, menu_owner)
            if not men.hmenu:
                self.last_error = men._error or "背景菜单创建失败"
                return None
            _CUR["pcm3"] = men.pcm3
            _CUR["pcm2"] = men.pcm2
            _CUR["sel_menu"] = 0
            _CUR["sel_id"] = 0
            cmd = int(libs["user32"].TrackPopupMenuEx(
                men.hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, menu_owner,
                None))
            if not cmd:
                return None
            text = _text_of(men.hmenu, cmd)
            hr = _invoke(men.pcm, cmd_hwnd, cmd)
            if hr != 0:
                self.last_error = "执行菜单命令失败: hr=0x%08X" % (hr & 0xFFFFFFFF)
            return text or None
        except Exception as e:
            self.last_error = "弹出背景菜单失败: %s" % e
            return None
        finally:
            _CUR["pcm3"] = old3
            _CUR["pcm2"] = old2
            if men is not None:
                men.release()
            if co:
                try:
                    ole32.CoUninitialize()
                except Exception:
                    pass

    def close(self):
        """销毁宿主窗口。"""
        if self._host and _LIBS:
            try:
                _LIBS["user32"].DestroyWindow(self._host)
            except Exception:
                pass
        self._host = 0
        if _HOST["hwnd"] and _LIBS:
            try:
                _LIBS["user32"].DestroyWindow(_HOST["hwnd"])
            except Exception:
                pass
        _HOST.update({"hwnd": 0, "proc": _HOST["proc"], "class": _HOST["class"]})


def _pos(global_pos):
    """(x, y) 元组或 QPoint 都接受。"""
    try:
        if hasattr(global_pos, "x") and hasattr(global_pos, "y"):
            return int(global_pos.x()), int(global_pos.y())
        x, y = global_pos
        return int(x), int(y)
    except Exception:
        return 0, 0


_MENU = {"inst": None}


def _inst():
    inst = _MENU["inst"]
    if inst is None:
        inst = ShellMenu()
        _MENU["inst"] = inst
    return inst


def popup(paths, global_pos, hwnd=0):
    """模块级便捷函数：内部复用单例 ShellMenu。"""
    return _inst().popup(paths, global_pos, hwnd)


def popup_background(folder=None, global_pos=(0, 0), hwnd=0):
    """弹「桌面背景」原生右键菜单，返回被选中项文本；取消返回 None。

    folder=None  -> 用桌面根命名空间（= 真桌面右键，新建落到用户桌面目录）
    folder=<路径> -> 用该文件夹的 IShellView 背景菜单（给测试用，写进该目录）
    hwnd 是命令父窗口（Qt 主窗），菜单 owner 仍是 host_hwnd()——与 popup() 同一分离原则。
    """
    return _inst().popup_background(folder, global_pos, hwnd)
