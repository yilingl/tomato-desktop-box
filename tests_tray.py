# -*- coding: utf-8 -*-
"""tray.py 自测：全程离屏，绝不弹窗口、绝不在通知区里挂图标。

为什么走子进程：QT_QPA_PLATFORM=offscreen 必须在 import PySide6 之前设进环境。
    - 直接跑本文件 / -m unittest tests_tray 时本进程是干净的，子进程照样按规矩来；
    - 跑 unittest discover 时，同一个进程里 tests_store / tests_shellmenu 可能已经建过
      windows 平台的 QApplication（那时 isSystemTrayAvailable() 是 True），
      在父进程里实测就会真的往用户通知区挂一个图标 —— 所以断言一律在离屏子进程里做。

跑法：
    python tests_tray.py
    python -m unittest tests_tray -v
"""
import json
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

PROBE_FLAG = "--probe"
PROBE_PREFIX = "TRAY_PROBE_JSON "
LABELS = ["显示主面板", "显示全部小窗口", "设置", "退出"]


def _mkicon(sizes):
    from PySide6.QtGui import QIcon, QPixmap
    ic = QIcon()
    for s in sizes:
        ic.addPixmap(QPixmap(s, s))
    return ic


def _probe():
    """子进程入口：离屏实测 tray.py，结果打成一行 JSON 给父进程断言。"""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"   # 必须在 import PySide6 之前设
    sys.path.insert(0, REPO)
    import gc
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    import deskbox.tray as tray_mod

    out = {}
    app = QApplication.instance() or QApplication([])
    out["platform"] = app.platformName()
    out["const_labels"] = list(tray_mod.MENU_LABELS)
    out["const_tooltip"] = tray_mod.TOOLTIP
    out["qt_available"] = bool(QSystemTrayIcon.isSystemTrayAvailable())
    out["icon_sizes_in"] = sorted([s.width(), s.height()] for s in _mkicon((16, 32)).availableSizes())

    calls = {"toggle": 0, "show_panel": 0, "minis": 0, "settings": 0, "quit": 0}

    def cb(key):
        def f():
            calls[key] += 1
        return f

    def boom():
        raise RuntimeError("boom")

    out["construct_error"] = ""
    try:
        # 位置参数按文档签名顺序摆：toggle / show_panel / minis / settings / quit，
        # 谁把顺序改了这里立刻炸（这也是断言的一部分）
        t = tray_mod.Tray(None, _mkicon((16, 32)), cb("toggle"), cb("show_panel"),
                          cb("minis"), cb("settings"), cb("quit"))
    except Exception as exc:           # noqa: BLE001
        out["construct_error"] = "%s: %s" % (type(exc).__name__, exc)
        print(PROBE_PREFIX + json.dumps(out, ensure_ascii=False))
        return

    out["available"] = bool(t.available())
    out["tooltip"] = t.toolTip()
    out["menu_labels"] = [a.text() for a in t.menu.actions()]
    out["menu_is_ours"] = t.contextMenu() is t.menu
    gc.collect()                       # 菜单被 GC 掉的话文案就没了
    out["menu_labels_after_gc"] = [a.text() for a in t.menu.actions()]

    # 左键单击 -> 显示/隐藏主面板；其它 reason 不重复触发
    t.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    out["toggle_after_trigger"] = calls["toggle"]
    out["show_panel_after_trigger"] = calls["show_panel"]     # 左键不该碰「只显不隐」那条线
    for name in ("Context", "MiddleClick", "DoubleClick"):
        t.activated.emit(getattr(QSystemTrayIcon.ActivationReason, name))
    out["toggle_after_other_reasons"] = calls["toggle"]

    # 菜单四项各自接线（计数先清零，方便看「各点一次是不是各正好一次」）
    for k in calls:
        calls[k] = 0
    acts = t.menu.actions()
    acts[0].trigger()
    acts[1].trigger()
    acts[2].trigger()
    acts[3].trigger()
    out["after_menu"] = dict(calls)

    # 图标：可用尺寸 + set_icon 生效
    out["tray_icon_sizes"] = sorted([s.width(), s.height()] for s in t.icon().availableSizes())
    out["set_icon_error"] = ""
    try:
        t.set_icon(_mkicon((48,)))
    except Exception as exc:           # noqa: BLE001
        out["set_icon_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["icon_sizes_after_set"] = sorted([s.width(), s.height()] for s in t.icon().availableSizes())

    # 不可用环境下 show()/hide() 不炸，且不显形
    errs = []
    out["show_ret"] = None
    out["hide_ret"] = None
    try:
        out["show_ret"] = bool(t.show())
    except Exception as exc:           # noqa: BLE001
        errs.append("show %s: %s" % (type(exc).__name__, exc))
    out["raw_qt_visible_after_show"] = bool(t.isVisible())     # 底层原始值
    out["wrapper_visible_after_show"] = bool(t.is_visible())   # 本模块空转后的值
    try:
        out["hide_ret"] = bool(t.hide())
    except Exception as exc:           # noqa: BLE001
        errs.append("hide %s: %s" % (type(exc).__name__, exc))
    out["wrapper_visible_after_hide"] = bool(t.is_visible())
    out["call_errors"] = errs

    # 调用方回调炸了不许把托盘带走
    out["bad_cb_error"] = ""
    try:
        t2 = tray_mod.Tray(None, _mkicon((16, 32)), boom, boom, boom, boom, boom)
        t2.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        t2.menu.actions()[3].trigger()
    except Exception as exc:           # noqa: BLE001
        out["bad_cb_error"] = "%s: %s" % (type(exc).__name__, exc)

    print(PROBE_PREFIX + json.dumps(out, ensure_ascii=False))


def _run_probe():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-u", os.path.abspath(__file__), PROBE_FLAG],
                       cwd=REPO, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    line = ""
    for ln in (p.stdout or "").splitlines():
        if ln.startswith(PROBE_PREFIX):
            line = ln[len(PROBE_PREFIX):]
    return line, (p.stdout or ""), (p.stderr or ""), p.returncode


class TrayTest(unittest.TestCase):
    """全部断言都在离屏子进程的实测结果上做。"""

    data = None

    @classmethod
    def setUpClass(cls):
        line, stdout, stderr, rc = _run_probe()
        if not line:
            raise AssertionError("离屏子进程没回 JSON（rc=%s）\n--- stdout ---\n%s\n--- stderr ---\n%s"
                                 % (rc, stdout, stderr))
        cls.data = json.loads(line)
        print("离屏子进程实测: " + line)

    def check(self, name, cond, detail=""):
        if cond:
            print("PASS - %s" % name)
        else:
            print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        self.assertTrue(cond, "%s %s" % (name, detail))

    # ------------------------------------------------------------ 环境
    def test_01_env_is_offscreen_and_tray_unavailable(self):
        d = self.data
        self.check("子进程跑在 offscreen", d.get("platform") == "offscreen", d.get("platform"))
        self.check("离屏 isSystemTrayAvailable() 为 False", d.get("qt_available") is False,
                   d.get("qt_available"))

    # ------------------------------------------------------------ 构造
    def test_02_construct_does_not_raise(self):
        d = self.data
        self.check("不可用环境下构造不抛", d.get("construct_error") == "", d.get("construct_error"))
        self.check("内部标记不可用", d.get("available") is False, d.get("available"))

    # ------------------------------------------------------------ 菜单
    def test_03_menu_labels_and_order(self):
        d = self.data
        self.check("常量文案与顺序固定", d.get("const_labels") == LABELS, d.get("const_labels"))
        self.check("菜单文案顺序 == %s" % LABELS, d.get("menu_labels") == LABELS, d.get("menu_labels"))
        self.check("菜单对象被稳引用（GC 后仍在）",
                   d.get("menu_labels_after_gc") == LABELS, d.get("menu_labels_after_gc"))
        self.check("contextMenu 就是我们的菜单", d.get("menu_is_ours") is True, d.get("menu_is_ours"))
        self.check("tooltip == Tomato Desktop Box",
                   d.get("tooltip") == "Tomato Desktop Box" == d.get("const_tooltip"),
                   (d.get("tooltip"), d.get("const_tooltip")))

    def test_04_menu_items_wired_to_callbacks(self):
        """菜单四项各接各的回调：第 1 项是「只显不隐」的 on_show_panel，不是切换。"""
        d = self.data
        c = d.get("after_menu") or {}
        self.check("显示主面板 -> on_show_panel 恰好 1 次", c.get("show_panel") == 1, c)
        self.check("显示主面板 不碰 on_toggle_panel（只显不隐）", c.get("toggle") == 0, c)
        self.check("显示全部小窗口 -> on_show_all_minis", c.get("minis") == 1, c.get("minis"))
        self.check("设置 -> on_settings", c.get("settings") == 1, c.get("settings"))
        self.check("退出 -> on_quit", c.get("quit") == 1, c.get("quit"))

    # ------------------------------------------------------------ 左键
    def test_05_left_click_toggles_panel_once(self):
        d = self.data
        self.check("emit(Trigger) 后 on_toggle_panel 恰好 1 次",
                   d.get("toggle_after_trigger") == 1, d.get("toggle_after_trigger"))
        self.check("emit(Trigger) 不碰 on_show_panel",
                   d.get("show_panel_after_trigger") == 0, d.get("show_panel_after_trigger"))

    def test_06_other_reasons_do_not_retrigger(self):
        d = self.data
        self.check("Context/MiddleClick/DoubleClick 不再触发",
                   d.get("toggle_after_other_reasons") == 1, d.get("toggle_after_other_reasons"))

    # ------------------------------------------------------------ 图标
    def test_07_icon_sizes(self):
        d = self.data
        got = [tuple(s) for s in (d.get("tray_icon_sizes") or [])]
        self.check("传入图标的 availableSizes 含 16 和 32",
                   (16, 16) in got and (32, 32) in got, got)
        self.check("set_icon 不抛", d.get("set_icon_error") == "", d.get("set_icon_error"))
        self.check("set_icon 真换掉了图标",
                   [tuple(s) for s in (d.get("icon_sizes_after_set") or [])] == [(48, 48)],
                   d.get("icon_sizes_after_set"))

    # ------------------------------------------------------------ 空转
    def test_08_show_hide_safe_in_unavailable_env(self):
        d = self.data
        self.check("show()/hide() 一次异常都没有", d.get("call_errors") == [], d.get("call_errors"))
        self.check("show() 空转返回 False", d.get("show_ret") is False, d.get("show_ret"))
        self.check("hide() 空转返回 False", d.get("hide_ret") is False, d.get("hide_ret"))
        self.check("is_visible() show 后仍 False",
                   d.get("wrapper_visible_after_show") is False, d.get("wrapper_visible_after_show"))
        self.check("is_visible() hide 后仍 False",
                   d.get("wrapper_visible_after_hide") is False, d.get("wrapper_visible_after_hide"))

    def test_09_broken_callback_does_not_break_tray(self):
        d = self.data
        self.check("回调抛异常不影响托盘", d.get("bad_cb_error") == "", d.get("bad_cb_error"))


if __name__ == "__main__":
    if PROBE_FLAG in sys.argv:
        _probe()
    else:
        unittest.main(verbosity=2)
