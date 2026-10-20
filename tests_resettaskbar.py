# -*- coding: utf-8 -*-
"""ResetTaskbar.py 自测：全程 mock，**绝不真弹框、绝不真改任务栏**。

两个 red line：
  1. deskbox.taskbarfx.reset_default 一律 patch 掉，一个真实 Win32 调用都不发；
  2. MessageBoxW 不去碰 —— 直接 patch 本模块暴露的 _alert，所以哪怕这台机器没有
     windll 也不会在属性访问上炸，更不会真弹出个窗口把跑测试的人挡住。

跑法对齐仓库其它测试：
    python tests_resettaskbar.py
    python -m unittest tests_resettaskbar
"""
import ast
import contextlib
import io
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ResetTaskbar as rt

_HERE = os.path.dirname(os.path.abspath(__file__))
_SELF = os.path.join(_HERE, "ResetTaskbar.py")


def _imports(path):
    """把文件里所有 import 语句的模块名摊平（静态解析，不执行它）。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.append(node.module)
    return out


class ResetTaskbarTest(unittest.TestCase):
    def setUp(self):
        self.n = 0

    # ------------------------------------------------------------ 工具
    def check(self, name, cond, detail=""):
        self.n += 1
        if cond:
            print("PASS - %s" % name)
        else:
            print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        self.assertTrue(cond, "%s %s" % (name, detail))

    def run_main(self, argv, ret):
        """把 reset_default 换成返回 ret（或抛出它），_alert 换成记录器，再跑 main。

        返回 (退出码, alert 记账表, stdout 内容)。
        """
        calls = []

        def fake_alert(text, warning=False):
            calls.append((text, warning))
            return True

        with mock.patch.object(rt.taskbarfx, "reset_default",
                               side_effect=(ret if isinstance(ret, BaseException)
                                            else None),
                               return_value=(None if isinstance(ret, BaseException)
                                             else ret)), \
             mock.patch.object(rt, "_alert", side_effect=fake_alert):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = rt.main(list(argv))
        return code, calls, buf.getvalue()

    # ------------------------------------------------------------ 文案
    def test_01_strings(self):
        self.check("标题就是 Tomato Desktop Box",
                   rt.TITLE == "Tomato Desktop Box", rt.TITLE)
        self.check("成功文案一字不差",
                   rt.OK_TEXT == "任务栏外观已经还原成系统默认了。", rt.OK_TEXT)
        self.check("失败文案一字不差",
                   rt.FAIL_TEXT == "没能还原任务栏外观。可能不在 Windows 10 上，或者任务栏窗口没找到。",
                   rt.FAIL_TEXT)
        self.check("MB_ICONINFORMATION = 0x40",
                   rt.MB_ICONINFORMATION == 0x00000040, hex(rt.MB_ICONINFORMATION))
        self.check("MB_ICONWARNING = 0x30",
                   rt.MB_ICONWARNING == 0x00000030, hex(rt.MB_ICONWARNING))

    def test_02_no_gui_imports(self):
        """兜底程序的全部价值就是依赖最少：只许 ctypes / os / sys / 本仓库。"""
        mods = _imports(_SELF)
        bad = [m for m in mods
               if m.split(".")[0] in ("PySide6", "PyQt5", "PyQt6", "shiboken",
                                      "tkinter", "PySimpleGUI", "wx")]
        self.check("ResetTaskbar.py 的 import 里没有 GUI 库", bad == [], (bad, mods))
        self.check("ResetTaskbar.py 只 import 标准库 + 本仓库",
                   all(m.split(".")[0] in ("ctypes", "os", "sys", "deskbox")
                       for m in mods), mods)

    # ------------------------------------------------------------ 默认：弹框
    def test_03_success_alerts_once(self):
        code, calls, out = self.run_main([], True)
        self.check("成功：退出码 0", code == 0, code)
        self.check("成功：_alert 正好调一次", len(calls) == 1, calls)
        self.check("成功：文案是成功那句", calls and calls[0][0] == rt.OK_TEXT, calls)
        self.check("成功：warning 不是真值", calls and not calls[0][1], calls)
        self.check("成功：不靠 print 给反馈（stdout 空）", out == "", out)

    def test_04_failure_alerts_warning(self):
        code, calls, out = self.run_main([], False)
        self.check("失败：退出码 1", code == 1, code)
        self.check("失败：_alert 正好调一次", len(calls) == 1, calls)
        self.check("失败：文案是失败那句", calls and calls[0][0] == rt.FAIL_TEXT, calls)
        self.check("失败：warning=True（走 MB_ICONWARNING）",
                   calls and calls[0][1] is True, calls)
        self.check("失败：stdout 空", out == "", out)

    def test_05_reset_default_raises(self):
        code, calls, out = self.run_main([], RuntimeError("假装 taskbarfx 炸了"))
        self.check("reset_default 抛异常：main 不向上抛，退出码 1", code == 1, code)
        self.check("reset_default 抛异常：照样调了 _alert", len(calls) == 1, calls)
        self.check("reset_default 抛异常：弹的是失败文案",
                   calls and calls[0][0] == rt.FAIL_TEXT and calls[0][1] is True, calls)
        self.check("reset_default 抛异常：stdout 空", out == "", out)

    # ------------------------------------------------------------ --quiet：全静音
    def test_06_quiet_success(self):
        code, calls, out = self.run_main(["--quiet"], True)
        self.check("--quiet 成功：退出码仍然是 0", code == 0, code)
        self.check("--quiet 成功：_alert 一次都没调", calls == [], calls)
        self.check("--quiet 成功：stdout 一个字符都没有", out == "", repr(out))

    def test_07_quiet_failure(self):
        code, calls, out = self.run_main(["--quiet"], False)
        self.check("--quiet 失败：退出码仍然是 1", code == 1, code)
        self.check("--quiet 失败：_alert 一次都没调", calls == [], calls)
        self.check("--quiet 失败：stdout 一个字符都没有", out == "", repr(out))

    def test_08_quiet_with_raises(self):
        code, calls, out = self.run_main(["--quiet"], OSError("假装炸"))
        self.check("--quiet + 抛异常：退出码 1", code == 1, code)
        self.check("--quiet + 抛异常：还是不弹框", calls == [], calls)
        self.check("--quiet + 抛异常：stdout 空", out == "", repr(out))

    def test_09_extra_args_tolerated(self):
        """别的参数不该把它弄崩，--quiet 出现在哪一位都算。"""
        code, calls, _out = self.run_main(["--whatever", "--quiet"], True)
        self.check("乱参数 + --quiet：仍然静音且退出码 0", code == 0 and calls == [],
                   (code, calls))
        code, calls, _out = self.run_main(["--whatever"], True)
        self.check("只有一个乱参数：不静音，照常弹框", code == 0 and len(calls) == 1,
                   (code, calls))

    # ------------------------------------------------------------ _alert 自己
    def test_10_alert_swallows_messagebox_error(self):
        """MessageBoxW 自己抛异常也要吞掉 —— 兜底程序不许崩。"""
        fake = types.SimpleNamespace(
            name="nt",
            path=os.path,
        )

        def boom(*a, **kw):
            raise OSError("假装 MessageBoxW 抽风")

        fake_user32 = types.SimpleNamespace(MessageBoxW=boom)
        fake_ctypes = types.SimpleNamespace(
            windll=types.SimpleNamespace(user32=fake_user32))
        with mock.patch.object(rt, "os", fake), mock.patch.object(rt, "ctypes", fake_ctypes):
            got = rt._alert(rt.OK_TEXT)
        self.check("MessageBoxW 抛异常：_alert 返回 False 且不往外抛", got is False, got)

    def test_11_alert_calls_messagebox_with_right_args(self):
        seen = []
        fake_user32 = types.SimpleNamespace(
            MessageBoxW=lambda *a: seen.append(a) or 1)
        fake_ctypes = types.SimpleNamespace(
            windll=types.SimpleNamespace(user32=fake_user32))
        fake_os = types.SimpleNamespace(name="nt", path=os.path)
        with mock.patch.object(rt, "os", fake_os), mock.patch.object(rt, "ctypes", fake_ctypes):
            self.check("成功框：_alert 返回 True", rt._alert(rt.OK_TEXT) is True, None)
            self.check("失败框：_alert 返回 True",
                       rt._alert(rt.FAIL_TEXT, warning=True) is True, None)
        self.check("成功框参数 = (0, OK_TEXT, TITLE, MB_ICONINFORMATION)",
                   seen and seen[0] == (0, rt.OK_TEXT, rt.TITLE, rt.MB_ICONINFORMATION),
                   seen)
        self.check("失败框参数 = (0, FAIL_TEXT, TITLE, MB_ICONWARNING)",
                   len(seen) == 2 and seen[1] == (0, rt.FAIL_TEXT, rt.TITLE,
                                                  rt.MB_ICONWARNING), seen)

    def test_12_alert_non_windows_prints(self):
        """非 Windows 不弹框，只 print —— 原来的行为保留。"""
        fake_os = types.SimpleNamespace(name="posix", path=os.path)
        with mock.patch.object(rt, "os", fake_os):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                got = rt._alert(rt.OK_TEXT)
        self.check("非 Windows：_alert 返回 True", got is True, got)
        self.check("非 Windows：print 出那句同样的文案", buf.getvalue().strip() == rt.OK_TEXT,
                   repr(buf.getvalue()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
