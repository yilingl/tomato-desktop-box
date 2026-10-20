# -*- coding: utf-8 -*-
"""taskbarfx.py 自测：**一个真实 Win32 调用都不发**，全部走假后端。

假后端通过 setattr(taskbarfx, "_backend", ...) 顶上，_WinBackend 从来不构造，
所以这台机器上的真任务栏在这套测试里一个字节都不会被碰。

跑法对齐仓库其它测试：
    python tests_taskbarfx.py
    python -m unittest tests_taskbarfx
"""
import ast
import os
import tokenize
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deskbox.taskbarfx as fx

# taskbarfx / ResetTaskbar 允许 import 的顶层模块：标准库 + 本仓库自己
_ALLOWED = {"ctypes", "os", "sys", "deskbox"}


def _imports(path):
    """把文件里所有 import 语句的模块名摊平（不执行它，只做静态解析）。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.append(node.module)
    return out


class FakeBackend(object):
    """记账用的假后端：只记住收到了什么，一个 Win32 都不碰。"""

    def __init__(self, trays=(0x1014C,), peer=0, accept=True):
        self.trays_fixed = list(trays)
        self.find_class = peer          # 假装 FindWindowW 的返回值
        self.accept = accept            # set_accent 的返回值
        self.calls = []                 # [(hwnd, state, flags, color), ...]
        self.msgs = []                  # 任何「抖一下」的动作都记这里，正常必须为空
        self.raise_on_set = None        # 非 None 就抛出来，测异常吞掉

    def trays(self):
        return list(self.trays_fixed)

    def peer_running(self):
        return bool(self.find_class)    # 跟真后端同一套判断：FindWindowW 非 0 即在跑

    def _nudge(self, *a, **kw):
        """假装有谁想抖 DWM：真后端【不许】走到这条路上来。"""
        self.msgs.append(a)
        raise AssertionError("不许抖 DWM：%r" % (a,))

    SendMessageTimeoutW = _nudge         # 老写法的名字也一并堵死
    send_message_timeout = _nudge
    RedrawWindow = _nudge
    InvalidateRect = _nudge

    def set_accent(self, hwnd, state, flags, color):
        self.calls.append((hwnd, state, flags, color))
        if self.raise_on_set is not None:
            raise self.raise_on_set
        return self.accept


class TaskbarFxTest(unittest.TestCase):
    def setUp(self):
        self.n = 0
        # 离屏闸会挡住一切下发，所以功能用例先把它关掉；闸本身在 test_15 里单测
        self._qpa = os.environ.pop("QT_QPA_PLATFORM", None)
        self._keep = (fx._backend, fx._BACKEND, fx._BACKEND_TRIED,
                      fx._WinBackend, dict(fx._STATE))
        fx._STATE["mode"] = "off"
        fx._BACKEND = None
        fx._BACKEND_TRIED = True        # 别让真后端有机会被构造出来

    def tearDown(self):
        (fx._backend, fx._BACKEND, fx._BACKEND_TRIED,
         fx._WinBackend, state) = self._keep
        fx._STATE.update(state)
        if self._qpa is not None:
            os.environ["QT_QPA_PLATFORM"] = self._qpa

    # ------------------------------------------------------------ 工具
    def check(self, name, cond, detail=""):
        self.n += 1
        if cond:
            print("PASS - %s" % name)
        else:
            print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        self.assertTrue(cond, "%s %s" % (name, detail))

    def use(self, backend):
        """把假后端顶上，同时把模块内缓存也指过去。"""
        fx._backend = lambda: backend
        fx._BACKEND = backend
        fx._BACKEND_TRIED = True
        return backend

    # ------------------------------------------------------------ 常量
    def test_01_constants(self):
        self.check("MODES 顺序是 磨砂 / 透明 / 关",
                   tuple(fx.MODES) == ("blur", "clear", "off"), fx.MODES)
        self.check("LABELS 三个中文都齐",
                   fx.LABELS == {"blur": "磨砂", "clear": "透明", "off": "关"},
                   fx.LABELS)
        self.check("cbData 用的就是 sizeof(ACCENT_POLICY) = 16",
                   __import__("ctypes").sizeof(fx.ACCENT_POLICY) == 16,
                   __import__("ctypes").sizeof(fx.ACCENT_POLICY))
        self.check("WCA_ACCENT_POLICY = 19", fx.WCA_ACCENT_POLICY == 19,
                   fx.WCA_ACCENT_POLICY)
        gone = [n for n in ("WM_DWMCOMPOSITIONCHANGED", "SMTO_ABORTIFHUNG",
                            "DWM_TIMEOUT_MS") if hasattr(fx, n)]
        self.check("抖 DWM 用的常量已经从模块里删干净", gone == [], gone)

    def test_02_no_pyside(self):
        """兜底程序要能打包成很小的 exe，所以这模块不许把 Qt 拖进来。"""
        here = os.path.dirname(os.path.abspath(__file__))
        for rel in (os.path.join("deskbox", "taskbarfx.py"), "ResetTaskbar.py"):
            mods = _imports(os.path.join(here, rel))
            bad = [m for m in mods
                   if m.split(".")[0] in ("PySide6", "PyQt5", "PyQt6", "shiboken")]
            self.check("%s 的 import 里没有 Qt" % rel, bad == [], (rel, mods))
            self.check("%s 只 import 标准库" % rel,
                       all(m.split(".")[0] in _ALLOWED for m in mods),
                       (rel, [m for m in mods if m.split(".")[0] not in _ALLOWED]))

    # ------------------------------------------------------------ 不支持也不炸
    def test_03_unsupported_never_raises(self):
        def failing_backend():
            return None                  # 真后端构造失败时 _backend() 就返回 None

        fx._backend = failing_backend
        fx._BACKEND = None
        self.check("后端构造失败：is_supported() 返回 False 不抛",
                   fx.is_supported() is False, fx.is_supported())
        self.check("后端构造失败：apply() 返回 False 不抛",
                   fx.apply("clear") is False, None)
        self.check("后端构造失败：release() 返回 False 不抛",
                   fx.release() is False, None)
        self.check("后端构造失败：reset_default() 返回 False 不抛",
                   fx.reset_default() is False, None)
        self.check("后端构造失败：peer_running() 返回 False 不抛",
                   fx.peer_running() is False, None)
        self.check("后端构造失败：peer_hint() 给空串",
                   fx.peer_hint() == "", fx.peer_hint())
        self.check("后端构造失败：current() 仍然可用", fx.current() == "off",
                   fx.current())

    def test_04_real_backend_construction_swallowed(self):
        """_WinBackend 抛异常时 _backend() 必须吞掉，只降级成 None。"""
        class Boom(object):
            def __init__(self):
                raise OSError("假装没导出 SetWindowCompositionAttribute")

        keep_backend, keep_tried = fx._backend, fx._BACKEND_TRIED
        keep_cls = fx._WinBackend
        try:
            fx._WinBackend = Boom
            fx._BACKEND = None
            fx._BACKEND_TRIED = False
            fx._backend = fx.__dict__["_backend"]      # 拿回真函数本身
            got = fx._backend()
            self.check("构造抛异常时 _backend() 返回 None（不往外抛）", got is None, got)
        finally:
            fx._WinBackend = keep_cls
            fx._backend, fx._BACKEND_TRIED = keep_backend, keep_tried

    # ------------------------------------------------------------ 三档参数
    def test_05_apply_exact_policy(self):
        want = {"off": (0, 0, 0x00000000),
                "blur": (3, 2, 0x66000000),
                "clear": (2, 2, 0x00000000)}
        for mode in fx.MODES:
            b = self.use(FakeBackend())
            ok = fx.apply(mode)
            state, flags, color = want[mode]
            self.check("apply(%r) 返回 True" % mode, ok is True, ok)
            self.check("apply(%r) 只打主任务栏一个 hwnd" % mode,
                       [c[0] for c in b.calls] == [0x1014C], b.calls)
            self.check("apply(%r) 的 (state, flags, color) 完全对表" % mode,
                       [c[1:] for c in b.calls] == [(state, flags, color)],
                       (mode, [c[1:] for c in b.calls], (state, flags, color)))
            self.check("apply(%r) 后 current() 跟着变" % mode,
                       fx.current() == mode, fx.current())

    def test_06_apply_bogus(self):
        b = self.use(FakeBackend())
        fx.apply("clear")
        before = fx.current()
        b.calls[:] = []
        self.check("apply('bogus') 返回 False", fx.apply("bogus") is False, None)
        self.check("apply('bogus') 后端一次都没被调用", b.calls == [], b.calls)
        self.check("apply('bogus') 不改 current()", fx.current() == before,
                   (fx.current(), before))
        for bad in (None, "", "BLUR", "traslcent", 3):
            self.check("apply(%r) 也返回 False" % (bad,), fx.apply(bad) is False, None)
        self.check("一串非法档位之后后端还是没被调用", b.calls == [], b.calls)

    def test_07_failed_apply_keeps_current(self):
        b = self.use(FakeBackend(accept=False))
        fx.apply("blur")                # 这次后端说失败，所以 current() 留在 off
        self.check("前置：current() 是 off", fx.current() == "off", fx.current())
        b.calls[:] = []
        self.check("后端说失败：apply('clear') 返回 False", fx.apply("clear") is False, None)
        self.check("后端说失败：current() 保持 off 不变", fx.current() == "off",
                   fx.current())
        self.check("后端其实还是收到了 state=2（下发尝试确实发生了）",
                   [c[1:] for c in b.calls] == [(2, 2, 0x00000000)], b.calls)

    def test_08_no_tray_window(self):
        b = self.use(FakeBackend(trays=()))
        self.check("找不到任务栏：is_supported() False", fx.is_supported() is False, None)
        self.check("找不到任务栏：apply 返回 False", fx.apply("blur") is False, None)
        self.check("找不到任务栏：一次都没下发", b.calls == [], b.calls)
        self.check("找不到任务栏：current() 不变", fx.current() == "off", fx.current())

    def test_09_release_idempotent(self):
        b = self.use(FakeBackend())
        fx.apply("clear")
        self.check("release() 第一次 True", fx.release() is True, None)
        self.check("release() 第二次 True（幂等）", fx.release() is True, None)
        self.check("两次都真的下发了 state=0",
                   [c[1:] for c in b.calls] == [(2, 2, 0x00000000),
                                                (0, 0, 0x00000000),
                                                (0, 0, 0x00000000)], b.calls)
        self.check("release() 之后 current() 是 off", fx.current() == "off", fx.current())

    def test_10_reset_default_keeps_current(self):
        b = self.use(FakeBackend())
        fx.apply("blur")
        self.check("前置：current() 是 blur", fx.current() == "blur", fx.current())
        b.calls[:] = []
        self.check("reset_default() 返回 True", fx.reset_default() is True, None)
        self.check("reset_default() 下发 state=0 / flags=0 / color=0",
                   [c[1:] for c in b.calls] == [(0, 0, 0x00000000)], b.calls)
        self.check("reset_default() **不**改 current()（兜底程序不碰状态）",
                   fx.current() == "blur", fx.current())

    def test_11_peer_running(self):
        self.use(FakeBackend(peer=0))
        self.check("没有 TTB_WorkerWindow：peer_running() False",
                   fx.peer_running() is False, None)
        self.check("没有 TTB：peer_hint() 空串", fx.peer_hint() == "", fx.peer_hint())
        self.use(FakeBackend(peer=0x2A2A))
        self.check("有 TTB_WorkerWindow：peer_running() True",
                   fx.peer_running() is True, None)
        hint = fx.peer_hint()
        self.check("有 TTB：peer_hint() 给一句中文提示", bool(hint) and hint == fx.PEER_HINT, hint)
        self.check("提示里点名 TranslucentTB", "TranslucentTB" in hint, hint)

    def test_12_multi_monitor_all_windows(self):
        b = self.use(FakeBackend(trays=(0x1014C, 0x1014D)))
        self.check("两个任务栏窗口都算支持", fx.is_supported() is True, None)
        fx.apply("clear")
        self.check("多显示器：两个 hwnd 都下发了",
                   [c[0] for c in b.calls] == [0x1014C, 0x1014D], b.calls)

    def test_13_backend_exception_swallowed(self):
        b = self.use(FakeBackend())
        b.raise_on_set = RuntimeError("假装 Win32 抽风")
        self.check("set_accent 抛异常：apply 返回 False 不往外抛",
                   fx.apply("blur") is False, None)
        self.check("抛异常时 current() 不动", fx.current() == "off", fx.current())

    def test_14_trays_exception_swallowed(self):
        class Bad(FakeBackend):
            def trays(self):
                raise OSError("假装枚举失败")

        self.use(Bad())
        self.check("trays() 抛异常：is_supported() False", fx.is_supported() is False, None)
        self.check("trays() 抛异常：apply() False", fx.apply("clear") is False, None)


    # ------------------------------------------------------------ 不许抖 DWM
    def test_17_set_accent_is_one_call_only(self):
        """真后端 set_accent：只调一次底层 Set，参数就是那三个值，绝不抖 DWM。

        这里用 object.__new__ 造一个 _WinBackend —— 不跑 __init__，所以 WinDLL /
        GetProcAddress / 任务栏窗口一个都不碰，纯结构体算术。
        """
        import ctypes
        b = object.__new__(fx._WinBackend)
        got = []

        def fake_set(hwnd, ptr):
            pol = ptr._obj.pvData                 # 指向 ACCENT_POLICY 的裸指针
            acc = ctypes.cast(pol, ctypes.POINTER(fx.ACCENT_POLICY)).contents
            got.append({"hwnd": int(getattr(hwnd, "value", hwnd)),
                        "attrib": ptr._obj.Attrib,
                        "cb": ptr._obj.cbData,
                        "state": acc.AccentState, "flags": acc.AccentFlags,
                        "color": acc.GradientColor, "anim": acc.AnimationId})
            return 1

        b._set = fake_set

        class TrapU(object):
            """真后端 set_accent 不该再碰 user32 的任何东西（尤其 SendMessageTimeoutW）。"""
            def __getattr__(self, name):
                raise AssertionError("set_accent 之后又去碰 user32.%s 了" % name)

        b._u = TrapU()

        self.check("set_accent 返回 True", b.set_accent(0x1014C, 2, 2, 0x00000000) is True,
                   None)
        self.check("底层 Set 只被调用一次", len(got) == 1, got)
        self.check("hwnd 原样传下去（0x1014C）", got and got[0]["hwnd"] == 0x1014C, got)
        self.check("Attrib = WCA_ACCENT_POLICY(19)", got and got[0]["attrib"] == 19, got)
        self.check("cbData 正好 16", got and got[0]["cb"] == 16, got)
        self.check("(state, flags, color) 就是那三个值",
                   got and (got[0]["state"], got[0]["flags"], got[0]["color"])
                   == (2, 2, 0x00000000), got)
        self.check("AnimationId 留 0", got and got[0]["anim"] == 0, got)

        for mode, want in (("blur", (3, 2, 0x66000000)),
                           ("clear", (2, 2, 0x00000000)),
                           ("off", (0, 0, 0x00000000))):
            got[:] = []
            state, flags, color = want
            b.set_accent(0x1014C, state, flags, color)
            self.check("真后端 %r 也只调一次、值对表" % mode,
                       len(got) == 1 and (got[0]["state"], got[0]["flags"],
                                          got[0]["color"]) == want, got)

    def test_18_no_dwm_nudge_anywhere(self):
        """源码层面钉死：nudge 那套东西不许再回到这个模块里。"""
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "deskbox", "taskbarfx.py")
        # 只看「代码里的标识符」：docstring 与注释里点名骂它是允许的，代码里出现才是回归
        names = set()
        with open(path, encoding="utf-8") as fh:
            for tok in tokenize.generate_tokens(fh.readline):
                if tok.type == tokenize.NAME:
                    names.add(tok.string)
        for bad in ("SendMessageTimeoutW", "SendMessageW", "WM_DWMCOMPOSITIONCHANGED",
                    "SMTO_ABORTIFHUNG", "DWM_TIMEOUT_MS", "RedrawWindow",
                    "InvalidateRect"):
            self.check("taskbarfx.py 的代码里没有 %s" % bad, bad not in names, bad)

    def test_19_fake_backend_never_nudged(self):
        """跑完一轮三档，FakeBackend 的「抖一下」记账表必须始终为空。"""
        b = self.use(FakeBackend())
        for mode in fx.MODES:
            fx.apply(mode)
        fx.release()
        fx.reset_default()
        self.check("三档 + release + reset 之后一次都没抖 DWM",
                   b.msgs == [], b.msgs)
        self.check("下发次数 = 3 档 + release + reset（没有多余调用）",
                   len(b.calls) == 5, b.calls)

    def test_15_offscreen_gate(self):
        """离屏闸：QT_QPA_PLATFORM=offscreen 时 apply/release/reset_default 一律 False。"""
        b = self.use(FakeBackend())
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        try:
            self.check("离屏：apply('clear') 返回 False", fx.apply("clear") is False, None)
            self.check("离屏：release() 返回 False", fx.release() is False, None)
            self.check("离屏：reset_default() 返回 False", fx.reset_default() is False, None)
            self.check("离屏：fake backend 一次都没被调用", b.calls == [], b.calls)
            self.check("离屏：current() 没被写脏", fx.current() == "off", fx.current())
        finally:
            os.environ.pop("QT_QPA_PLATFORM", None)
        self.check("摘掉离屏变量之后 apply 能通",
                   fx.apply("blur") is True and fx.current() == "blur",
                   (fx.current(), b.calls))
        self.check("摘掉离屏之后后端确实收到了 blur 那组参数",
                   [c[1:] for c in b.calls] == [(3, 2, 0x66000000)], b.calls)

    def test_16_offscreen_gate_string_variants(self):
        """只有正好等于 offscreen 才拦；别把空串 / 别的值也当离屏。"""
        b = self.use(FakeBackend())
        for val in ("", "windows", "minimal"):
            os.environ["QT_QPA_PLATFORM"] = val
            try:
                self.check("QT_QPA_PLATFORM=%r 不算离屏，apply 照常下发" % val,
                           fx.apply("clear") is True, None)
            finally:
                os.environ.pop("QT_QPA_PLATFORM", None)
        self.check("三种非离屏取值都下发了", len(b.calls) == 3, b.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
