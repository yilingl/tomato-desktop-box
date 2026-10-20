# -*- coding: utf-8 -*-
"""任务栏外观（Windows 10）—— 把任务栏做成磨砂 / 透明，或者关掉。

对外只有这一个模块：app 设置面板按 MODES 排三档，双击 ResetTaskbar.exe 走
reset_default()。**不 import PySide6**，纯 ctypes + 标准库，这样兜底程序能打包成
很小的 exe。

真实 Win32 调用全部收在 _WinBackend 里，一律通过 _backend() 取；单测把
taskbarfx._backend 换成一个返回假后端的函数即可，一个真实调用都不会发出去。

----------------------------------------------------------- 三个「为什么」
为什么不轮询：任务栏外观是「设一次就一直是这样」的状态 —— 实测（**没有 nudge**
的路径上）下发之后最大化、还原、切前台、静置 8 秒都不回退，只 set 一次等 3 秒
再截图也一模一样。没有任何需要盯着看的东西。任务栏在 explorer 进程里，每下一次
都是跨进程 SendMessage，几百毫秒一次纯属白烧 CPU；真要失效（explorer 重启），
用户重选一次即可，用不着常驻定时器。

为什么**不**抖 DWM：本机（Win10 21H2 19044.1288）实测过两个方向 —— 只调
SetWindowCompositionAttribute 就能立刻在屏幕上看见，不需要任何额外触发；反过来，
下发之后再补一记 WM_DWMCOMPOSITIONCHANGED(0x031E) 会让 DWM 重新计算任务栏外观、
把刚下发的 accent 整个丢掉，而那次 SetWindowCompositionAttribute 仍然返回 True，
纯属假成功。所以 set_accent() 里刻意「只 set、不抖」，也用不着 RedrawWindow /
SetWindowPos / InvalidateRect 之类别的补刀 —— 试都不用试。

为什么不注入：SetWindowCompositionAttribute 是 user32 导出的、跨进程直接生效的
接口，explorer 拿默认策略在自己的窗口上渲染，我们把同样的数据打到那个 hwnd 上
就行，不需要在我们的进程里跑 explorer 的代码。注入意味着 DLL + 卸载时机 +
explorer 崩溃风险，收益为零。

顺带一条安全闸：QT_QPA_PLATFORM == "offscreen" 时 apply / release / reset_default
一律直接返回 False，一个 Win32 都不碰 —— 这样任何离屏测试（例如在子进程里真的
构造 MainWindow、真的点设置开关）都绝不可能改到真机的任务栏。

还原为什么不需要备份：GetWindowCompositionAttribute 对任务栏返回 False
（last_error=0，语义是「从未被显式设置过」），读不到任何原始值；而系统默认就是
ACCENT_DISABLED(0)，下发 state=0 与「从未设置过」在渲染上等价 —— 实测还原后与
基线截图逐点比对 0/29440 个采样点有差异，所以备份那份读不到的东西没有意义。
"""
import ctypes
import os

# ---------------------------------------------------------------- 对外常量
MODES = ("blur", "clear", "off")            # 显示顺序：磨砂 / 透明 / 关
LABELS = {"blur": "磨砂", "clear": "透明", "off": "关"}

# 档位 → (AccentState, AccentFlags, GradientColor)；GradientColor 是 ABGR(0xAABBGGRR)。
# 三组数值都是真机比对截图定下来的，clear 的 alpha=0 才是壁纸全透，
# blur 的 0x66 是深色纱，off 就是系统的 ACCENT_DISABLED。
_POLICY = {
    "off":   (0, 0, 0x00000000),
    "blur":  (3, 2, 0x66000000),
    "clear": (2, 2, 0x00000000),
}

WCA_ACCENT_POLICY = 19

PEER_CLASS = "TTB_WorkerWindow"             # TranslucentTB 在跑就有这个窗口类
_PRIMARY_CLASS = "Shell_TrayWnd"
_SECONDARY_CLASS = "Shell_SecondaryTrayWnd"

PEER_HINT = "检测到 TranslucentTB 正在运行：两个程序会互相覆盖任务栏外观，只留一个就好。"


# ---------------------------------------------------------------- 结构体
class ACCENT_POLICY(ctypes.Structure):
    """16 字节；cbData 必须正好等于 sizeof(这个)，否则 SetWindowCompositionAttribute 不认。"""
    _fields_ = [("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int)]


class WCA_DATA(ctypes.Structure):
    _fields_ = [("Attrib", ctypes.c_int),
                ("pvData", ctypes.c_void_p),
                ("cbData", ctypes.c_size_t)]


# ---------------------------------------------------------------- 默认后端
class _WinBackend:
    """所有真实 Win32 调用都在这里；构造失败就是「不支持」，不影响 import。

    SetWindowCompositionAttribute 在本机没有 *DpiAware 变体，直接
    ctypes.windll.user32.SetWindowCompositionAttribute 取不到（AttributeError），
    所以必须走 kernel32!GetProcAddress 拿地址，再用 WINFUNCTYPE 包一层。
    """

    def __init__(self):
        if os.name != "nt":
            raise OSError("只有 Windows 才有任务栏外观")
        self._u = ctypes.WinDLL("user32", use_last_error=True)
        self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._wire()
        self._set = self._resolve(b"SetWindowCompositionAttribute",
                                  ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                                     ctypes.POINTER(WCA_DATA)))
        if not self._set:
            raise OSError("user32 没有导出 SetWindowCompositionAttribute")

    # ---------- 绑定 ----------
    def _wire(self):
        u = self._u
        u.FindWindowW.restype = ctypes.c_void_p
        u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        u.FindWindowExW.restype = ctypes.c_void_p
        u.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_wchar_p, ctypes.c_wchar_p]
        self._k.GetProcAddress.restype = ctypes.c_void_p
        self._k.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

    def _resolve(self, name, proto):
        addr = self._k.GetProcAddress(self._u._handle, name)
        return proto(addr) if addr else None

    # ---------- 查窗口 ----------
    def trays(self):
        """任务栏全部实例：主屏那一个 + 多显示器的 Shell_SecondaryTrayWnd。"""
        out = []
        h = self._u.FindWindowW(_PRIMARY_CLASS, None)
        if h:
            out.append(int(h))
        after = None
        while True:                     # FindWindowExW 一类的实例得一个个往后数
            h = self._u.FindWindowExW(None, after, _SECONDARY_CLASS, None)
            if not h:
                break
            h = int(h)
            if h not in out:
                out.append(h)
            after = ctypes.c_void_p(h)
        return out

    def peer_running(self):
        return bool(self._u.FindWindowW(PEER_CLASS, None))

    # ---------- 下发 ----------
    def set_accent(self, hwnd, state, flags, color):
        """下发一组 accent 参数。**只发这一个调用，后面什么都不做。**

        实测（Win10 21H2 19044.1288，Shell_TrayWnd 0x1014C）：纯 set 立刻就能在
        屏幕上看见，不需要任何额外触发；反过来，再补一记 WM_DWMCOMPOSITIONCHANGED
        会让 DWM 重新算任务栏外观、把刚下发的 accent 丢掉 —— 返回值还是 True，
        纯属假成功。所以这里刻意「只 set、不抖」。
        """
        pol = ACCENT_POLICY(int(state), int(flags), int(color), 0)
        data = WCA_DATA(WCA_ACCENT_POLICY,
                        ctypes.cast(ctypes.byref(pol), ctypes.c_void_p),
                        ctypes.sizeof(pol))
        return bool(self._set(ctypes.c_void_p(int(hwnd)), ctypes.byref(data)))


# ---------------------------------------------------------------- 后端取值
_BACKEND = None
_BACKEND_TRIED = False
_STATE = {"mode": "off"}            # 本模块上一次**成功**下发的档位


def _blocked():
    """离屏（单测 / 自检）下一律不下发 —— 测试绝不许真的改到系统任务栏。

    ui_mock 的设置行与托盘菜单都会调 apply()，而 tests_ui_210 / tests_tray 会在
    离屏子进程里真的建 MainWindow、真的点开关。有这道闸，那些测试再真也碰不到
    真任务栏，只会拿到一个 False。
    """
    return os.environ.get("QT_QPA_PLATFORM") == "offscreen"


def _backend():
    """当前后端；第一次真要用的时候才构造，构造失败就永久降级成 None。"""
    global _BACKEND, _BACKEND_TRIED
    if _BACKEND is not None or _BACKEND_TRIED:
        return _BACKEND
    _BACKEND_TRIED = True
    try:
        _BACKEND = _WinBackend()
    except Exception:               # noqa: BLE001 —— 没导出 / 非 Windows 都算不支持
        _BACKEND = None
    return _BACKEND


# ---------------------------------------------------------------- 对外 API
def is_supported():
    """这台机器能不能改任务栏外观。任何异常都算 False，绝不抛。"""
    try:
        b = _backend()
        return b is not None and bool(b.trays())
    except Exception:               # noqa: BLE001
        return False


def peer_running():
    """TranslucentTB 是不是在跑（有这个窗口类就说明在跑）。绝不抛。"""
    try:
        b = _backend()
        return bool(b is not None and b.peer_running())
    except Exception:               # noqa: BLE001
        return False


def peer_hint():
    """检测到 TTB 时给用户看的一句中文，否则空串。绝不替用户去杀进程。"""
    return PEER_HINT if peer_running() else ""


def current():
    """本模块上一次**成功**下发的档位；一次都没成功过就是 "off"。"""
    return _STATE["mode"]


def _send(state, flags, color, track):
    """把一组参数打到所有任务栏窗口；track 非 None 时成功才记账。

    多显示器上只要有一个窗口接受就算成功（其余记不上也只是那一个屏不变），
    全都不接受才返回 False。
    """
    try:
        b = _backend()
        if b is None:
            return False
        hwnds = list(b.trays() or [])
        if not hwnds:
            return False
        ok = False
        for h in hwnds:
            try:
                if b.set_accent(h, state, flags, color):
                    ok = True
            except Exception:       # noqa: BLE001 —— 单个窗口失败不影响别的
                continue
        if ok and track is not None:
            _STATE["mode"] = track
        return ok
    except Exception:               # noqa: BLE001 —— 界面拿到的是 False，不是异常
        return False


def apply(mode):
    """切到某一档：下发（只 set，不抖 DWM），成功才更新 current()。非法档位直接 False。"""
    if _blocked():                 # 离屏：一个 Win32 都不碰
        return False
    pol = _POLICY.get(mode)
    if pol is None:                 # 先验档位：认不出来就一个 Win32 都不碰
        return False
    state, flags, color = pol
    return _send(state, flags, color, mode)


def release():
    """等价 apply("off")，退出前调；幂等，每次都真的下发 state=0。"""
    if _blocked():                 # 离屏：一个 Win32 都不碰
        return False
    state, flags, color = _POLICY["off"]
    return _send(state, flags, color, "off")


def reset_default():
    """无状态还原成系统默认（兜底程序专用）：下发 state=0，**不**碰 current()。"""
    if _blocked():                 # 离屏：一个 Win32 都不碰
        return False
    state, flags, color = _POLICY["off"]
    return _send(state, flags, color, None)
