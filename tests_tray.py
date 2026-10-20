# -*- coding: utf-8 -*-
"""tray.py 自测：全程离屏，绝不弹窗口、绝不在通知区里挂图标、绝不碰系统任务栏。

为什么走子进程：QT_QPA_PLATFORM=offscreen 必须在 import PySide6 之前设进环境。
    - 直接跑本文件 / -m unittest tests_tray 时本进程是干净的，子进程照样按规矩来；
    - 跑 unittest discover 时，同一个进程里 tests_store / tests_shellmenu 可能已经建过
      windows 平台的 QApplication（那时 isSystemTrayAvailable() 是 True），
      在父进程里实测就会真的往用户通知区挂一个图标 —— 所以断言一律在离屏子进程里做。

子进程里的隔离：DESKBOX_* 与 APPDATA / LOCALAPPDATA 全部指到一次性临时目录，
真实 %APPDATA%\\DeskBox\\ 一个字节都不碰（本模块其实不读盘，这里只做兜底）。

跑法：
    python tests_tray.py
    python -m unittest tests_tray -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

PROBE_FLAG = "--probe"
PROBE_PREFIX = "TRAY_PROBE_JSON "
LABELS = ["显示主面板", "显示全部小窗口", "任务栏透明", "设置", "退出"]
TB_LABELS = ["磨砂", "透明", "关"]
TB_MODES = ["blur", "clear", "off"]
ISOLATED_ENV = ("DESKBOX_UI_JSON", "DESKBOX_GROUPS_JSON", "DESKBOX_CATS_JSON",
                "DESKBOX_MINIS_JSON", "DESKBOX_ROOT", "DESKBOX_HIDEICONS_JSON",
                "APPDATA", "LOCALAPPDATA")


def _isolate_env(tmp):
    """把所有状态文件 / 漫游目录指到临时目录；离屏 + 中文字体目录一并带上。"""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
    for name in ISOLATED_ENV:
        os.environ[name] = tmp
    for name, fn in (("DESKBOX_UI_JSON", "ui.json"),
                     ("DESKBOX_GROUPS_JSON", "groups.json"),
                     ("DESKBOX_CATS_JSON", "cats.json"),
                     ("DESKBOX_MINIS_JSON", "minis.json"),
                     ("DESKBOX_HIDEICONS_JSON", "hideicons.json")):
        os.environ[name] = os.path.join(tmp, fn)


def _mkicon(sizes):
    from PySide6.QtGui import QIcon, QPixmap
    ic = QIcon()
    for s in sizes:
        ic.addPixmap(QPixmap(s, s))
    return ic


def _checked(menu):
    """子菜单三档的勾选状态，返回 [磨砂, 透明, 关]。"""
    return [a.isChecked() for a in menu.actions()]


def _probe():
    """子进程入口：离屏实测 tray.py，结果打成一行 JSON 给父进程断言。"""
    import shutil
    tmp = tempfile.mkdtemp(prefix="deskbox_tray_")
    _isolate_env(tmp)                  # 必须在 import PySide6 之前设
    sys.path.insert(0, REPO)
    import gc
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    import deskbox.tray as tray_mod

    out = {}
    app = QApplication.instance() or QApplication([])
    out["platform"] = app.platformName()
    out["fontdir"] = os.environ.get("QT_QPA_FONTDIR")
    out["const_labels"] = list(tray_mod.MENU_LABELS)
    out["const_tb_title"] = tray_mod.TB_TITLE
    out["const_tb_modes"] = list(tray_mod.TB_MODES)
    out["const_tb_labels"] = list(tray_mod.TB_LABELS)
    out["const_tooltip"] = tray_mod.TOOLTIP
    out["qt_available"] = bool(QSystemTrayIcon.isSystemTrayAvailable())
    out["icon_sizes_in"] = sorted([s.width(), s.height()] for s in _mkicon((16, 32)).availableSizes())
    # 本模块不许把 taskbarfx 拖进来（真改任务栏的活归它，测试也就不会碰到系统任务栏）
    out["taskbarfx_imported"] = "deskbox.taskbarfx" in sys.modules

    calls = {"toggle": 0, "show_panel": 0, "minis": 0, "settings": 0, "quit": 0}
    tb_calls = []
    tb_seen = []

    def cb(key):
        def f():
            calls[key] += 1
        return f

    def on_tb(mode):
        tb_calls.append(mode)

    def boom(*_a):
        raise RuntimeError("boom")

    out["construct_error"] = ""
    try:
        # 位置参数按文档签名顺序摆：toggle / show_panel / minis / settings / quit，
        # 谁把顺序改了这里立刻炸（这也是断言的一部分）；on_tb_mode 用关键字传
        t = tray_mod.Tray(None, _mkicon((16, 32)), cb("toggle"), cb("show_panel"),
                          cb("minis"), cb("settings"), cb("quit"),
                          on_tb_mode=on_tb)
    except Exception as exc:           # noqa: BLE001
        out["construct_error"] = "%s: %s" % (type(exc).__name__, exc)
        print(PROBE_PREFIX + json.dumps(out, ensure_ascii=False))
        shutil.rmtree(tmp, ignore_errors=True)
        return

    out["available"] = bool(t.available())
    out["tooltip"] = t.toolTip()
    acts = t.menu.actions()
    out["menu_labels"] = [a.text() for a in acts]
    out["menu_is_ours"] = t.contextMenu() is t.menu
    sub = acts[2].menu() if len(acts) > 2 else None
    out["has_submenu_at_2"] = sub is not None
    out["menu3_text"] = acts[2].text() if len(acts) > 2 else None
    out["submenu_title"] = sub.title() if sub is not None else None
    out["submenu_labels"] = [a.text() for a in sub.actions()] if sub is not None else None
    out["default_checked"] = _checked(sub) if sub is not None else None
    gc.collect()                       # 菜单被 GC 掉的话文案就没了
    out["menu_labels_after_gc"] = [a.text() for a in t.menu.actions()]
    sub2 = t.menu.actions()[2].menu()
    out["submenu_labels_after_gc"] = [a.text() for a in sub2.actions()] if sub2 is not None else None
    out["default_checked_after_gc"] = _checked(sub2) if sub2 is not None else None

    # 左键单击 -> 显示/隐藏主面板；其它 reason 不重复触发
    t.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    out["toggle_after_trigger"] = calls["toggle"]
    out["show_panel_after_trigger"] = calls["show_panel"]     # 左键不该碰「只显不隐」那条线
    for name in ("Context", "MiddleClick", "DoubleClick"):
        t.activated.emit(getattr(QSystemTrayIcon.ActivationReason, name))
    out["toggle_after_other_reasons"] = calls["toggle"]

    # 子菜单三档：依次点一遍，看回调收到的 mode 与勾选落点
    if sub is not None:
        for k in calls:                    # 先清零：只看「点这三档会不会碰那四条线」
            calls[k] = 0
        tb_checked = []
        for a in sub.actions():
            a.trigger()
            tb_checked.append(_checked(sub))
            tb_seen.append(dict(calls))        # 顺手记录：点档位不许碰那四个回调
        out["tb_calls"] = list(tb_calls)
        out["tb_checked_after_each"] = tb_checked
        out["calls_during_tb"] = tb_seen
        # 重复点已勾选的那一档：仍会回调同一 mode，勾选不变
        sub.actions()[2].trigger()
        out["tb_calls_after_repeat"] = list(tb_calls)
        out["tb_checked_after_repeat"] = _checked(sub)

        # set_tb_mode：外部改档 -> 勾选立刻跟上；非法值落回「关」
        out["set_tb_clear_ret"] = bool(t.set_tb_mode("clear"))
        out["checked_after_set_clear"] = _checked(sub)
        out["set_tb_blur_ret"] = bool(t.set_tb_mode("blur"))
        out["checked_after_set_blur"] = _checked(sub)
        out["set_tb_bogus_ret"] = bool(t.set_tb_mode("bogus"))
        out["checked_after_set_bogus"] = _checked(sub)
        out["set_tb_none_ret"] = bool(t.set_tb_mode(None))
        out["checked_after_set_none"] = _checked(sub)
        # set_tb_mode 只改勾选，不该再回调 on_tb_mode（那是用户点击才走的线）
        out["tb_calls_after_set"] = list(tb_calls)

        # 「任务栏透明」整项可禁用（将来 taskbarfx 缺失时用）
        out["set_tb_enabled_ret"] = bool(t.set_tb_enabled(False, "任务栏特效不可用"))
        out["tb_action_enabled"] = bool(acts[2].isEnabled())
        out["tb_action_tooltip"] = acts[2].toolTip()
        t.set_tb_enabled(True, "")
        out["tb_action_enabled_after"] = bool(acts[2].isEnabled())

    # 顶层四项各自接线（计数先清零，方便看「各点一次是不是各正好一次」）
    for k in calls:
        calls[k] = 0
    acts = t.menu.actions()
    for idx in (0, 1, 3, 4):           # 2 是子菜单，不 trigger
        acts[idx].trigger()
    out["after_menu"] = dict(calls)
    out["tb_calls_after_top4"] = list(tb_calls)

    # tb_mode 传入的三种情况：合法值 / 非法值 / 不传
    out["variant_errors"] = []
    try:
        v = tray_mod.Tray(None, _mkicon((16, 32)), tb_mode="clear")
        out["variant_clear"] = _checked(v.menu.actions()[2].menu())
        v2 = tray_mod.Tray(None, _mkicon((16, 32)), tb_mode="bogus")
        out["variant_bogus"] = _checked(v2.menu.actions()[2].menu())
        v3 = tray_mod.Tray(None, _mkicon((16, 32)))
        out["variant_none"] = _checked(v3.menu.actions()[2].menu())
        v4 = tray_mod.Tray(None, _mkicon((16, 32)), None, None, None, None, None,
                           None, "blur")
        out["variant_kwarg_only"] = _checked(v4.menu.actions()[2].menu())
    except Exception as exc:           # noqa: BLE001
        out["variant_errors"].append("%s: %s" % (type(exc).__name__, exc))

    # 真降级（菜单没建出来）时 set_tb_mode 静默返回 False，不炸
    out["broken_set_tb_ret"] = None
    out["broken_set_tb_error"] = ""
    try:
        v5 = tray_mod.Tray(None, _mkicon((16, 32)))
        v5._broken = True              # 白盒：模拟底层没建起来（与 __init__ 失败分支同形）
        v5._tb_actions = {}
        v5._tb_action = None
        out["broken_set_tb_ret"] = bool(v5.set_tb_mode("clear"))
        out["broken_set_tb_enabled_ret"] = bool(v5.set_tb_enabled(False))
    except Exception as exc:           # noqa: BLE001
        out["broken_set_tb_error"] = "%s: %s" % (type(exc).__name__, exc)

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

    # 调用方回调炸了不许把托盘带走（含 on_tb_mode 也炸）
    out["bad_cb_error"] = ""
    try:
        t2 = tray_mod.Tray(None, _mkicon((16, 32)), boom, boom, boom, boom, boom,
                           on_tb_mode=boom)
        t2.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        t2.menu.actions()[4].trigger()
        t2.menu.actions()[2].menu().actions()[0].trigger()
    except Exception as exc:           # noqa: BLE001
        out["bad_cb_error"] = "%s: %s" % (type(exc).__name__, exc)

    print(PROBE_PREFIX + json.dumps(out, ensure_ascii=False))
    shutil.rmtree(tmp, ignore_errors=True)


def _run_probe():
    env = dict(os.environ)
    tmp = tempfile.mkdtemp(prefix="deskbox_tray_env_")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for name in ISOLATED_ENV:
        env[name] = tmp
    for name, fn in (("DESKBOX_UI_JSON", "ui.json"),
                     ("DESKBOX_GROUPS_JSON", "groups.json"),
                     ("DESKBOX_CATS_JSON", "cats.json"),
                     ("DESKBOX_MINIS_JSON", "minis.json"),
                     ("DESKBOX_HIDEICONS_JSON", "hideicons.json")):
        env[name] = os.path.join(tmp, fn)
    p = subprocess.run([sys.executable, "-u", os.path.abspath(__file__), PROBE_FLAG],
                       cwd=REPO, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    line = ""
    for ln in (p.stdout or "").splitlines():
        if ln.startswith(PROBE_PREFIX):
            line = ln[len(PROBE_PREFIX):]
    try:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:                  # noqa: BLE001
        pass
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
        self.check("tray.py 没把 taskbarfx 拖进来（不碰系统任务栏）",
                   d.get("taskbarfx_imported") is False, d.get("taskbarfx_imported"))

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

    def test_04_tb_submenu_shape(self):
        d = self.data
        self.check("menu.actions()[2] 是子菜单", d.get("has_submenu_at_2") is True,
                   d.get("has_submenu_at_2"))
        self.check("子菜单项文案 == 任务栏透明", d.get("menu3_text") == "任务栏透明", d.get("menu3_text"))
        self.check("子菜单标题 == 任务栏透明", d.get("submenu_title") == d.get("const_tb_title") == "任务栏透明",
                   (d.get("submenu_title"), d.get("const_tb_title")))
        self.check("三档文案与顺序 == %s" % TB_LABELS, d.get("submenu_labels") == TB_LABELS,
                   d.get("submenu_labels"))
        self.check("三档内存值常量 == %s" % TB_MODES, d.get("const_tb_modes") == TB_MODES,
                   d.get("const_tb_modes"))
        self.check("三档标签常量 == %s" % TB_LABELS, d.get("const_tb_labels") == TB_LABELS,
                   d.get("const_tb_labels"))
        self.check("子菜单 GC 后文案仍在", d.get("submenu_labels_after_gc") == TB_LABELS,
                   d.get("submenu_labels_after_gc"))
        self.check("子菜单 GC 后勾选仍在", d.get("default_checked_after_gc") == [False, False, True],
                   d.get("default_checked_after_gc"))

    def test_05_tb_default_is_off(self):
        d = self.data
        self.check("默认勾「关」", d.get("default_checked") == [False, False, True],
                   d.get("default_checked"))

    def test_06_tb_three_modes_callback(self):
        d = self.data
        self.check("依次点三档 -> 回调收到 blur/clear/off 各一次",
                   d.get("tb_calls") == ["blur", "clear", "off"], d.get("tb_calls"))
        self.check("点「磨砂」后只有它带勾",
                   (d.get("tb_checked_after_each") or [None])[0] == [True, False, False],
                   d.get("tb_checked_after_each"))
        self.check("点「透明」后只有它带勾",
                   (d.get("tb_checked_after_each") or [None, None])[1] == [False, True, False],
                   d.get("tb_checked_after_each"))
        self.check("点「关」后只有它带勾",
                   (d.get("tb_checked_after_each") or [None, None, None])[2] == [False, False, True],
                   d.get("tb_checked_after_each"))
        self.check("重复点「关」再回调一次 off，勾选不变",
                   d.get("tb_calls_after_repeat") == ["blur", "clear", "off", "off"]
                   and d.get("tb_checked_after_repeat") == [False, False, True],
                   (d.get("tb_calls_after_repeat"), d.get("tb_checked_after_repeat")))

    def test_07_tb_items_do_not_touch_other_callbacks(self):
        d = self.data
        zero = {"toggle": 0, "show_panel": 0, "minis": 0, "settings": 0, "quit": 0}
        self.check("点三档期间四项回调计数全 0", d.get("calls_during_tb") == [zero, zero, zero],
                   d.get("calls_during_tb"))
        self.check("点顶层四项不会误触 on_tb_mode",
                   d.get("tb_calls_after_top4") == ["blur", "clear", "off", "off"],
                   d.get("tb_calls_after_top4"))

    def test_08_set_tb_mode(self):
        d = self.data
        self.check("set_tb_mode('clear') 返回 True 且勾到「透明」",
                   d.get("set_tb_clear_ret") is True
                   and d.get("checked_after_set_clear") == [False, True, False],
                   (d.get("set_tb_clear_ret"), d.get("checked_after_set_clear")))
        self.check("set_tb_mode('blur') 勾到「磨砂」",
                   d.get("set_tb_blur_ret") is True
                   and d.get("checked_after_set_blur") == [True, False, False],
                   (d.get("set_tb_blur_ret"), d.get("checked_after_set_blur")))
        self.check("set_tb_mode('bogus') 落回「关」",
                   d.get("set_tb_bogus_ret") is True
                   and d.get("checked_after_set_bogus") == [False, False, True],
                   (d.get("set_tb_bogus_ret"), d.get("checked_after_set_bogus")))
        self.check("set_tb_mode(None) 落回「关」",
                   d.get("set_tb_none_ret") is True
                   and d.get("checked_after_set_none") == [False, False, True],
                   (d.get("set_tb_none_ret"), d.get("checked_after_set_none")))
        self.check("set_tb_mode 只改勾选、不回调 on_tb_mode（那 4 次都是点击来的）",
                   d.get("tb_calls_after_set") == ["blur", "clear", "off", "off"],
                   d.get("tb_calls_after_set"))

    def test_09_tb_action_can_be_disabled(self):
        d = self.data
        self.check("set_tb_enabled(False) 生效", d.get("set_tb_enabled_ret") is True
                   and d.get("tb_action_enabled") is False, d.get("tb_action_enabled"))
        self.check("禁用时可带一句 tooltip", d.get("tb_action_tooltip") == "任务栏特效不可用",
                   d.get("tb_action_tooltip"))
        self.check("可以再启用回来", d.get("tb_action_enabled_after") is True,
                   d.get("tb_action_enabled_after"))

    def test_10_menu_items_wired_to_callbacks(self):
        """顶层四项各接各的回调：第 1 项是「只显不隐」的 on_show_panel，不是切换。"""
        d = self.data
        c = d.get("after_menu") or {}
        self.check("显示主面板 -> on_show_panel 恰好 1 次", c.get("show_panel") == 1, c)
        self.check("显示主面板 不碰 on_toggle_panel（只显不隐）", c.get("toggle") == 0, c)
        self.check("显示全部小窗口 -> on_show_all_minis", c.get("minis") == 1, c.get("minis"))
        self.check("设置 -> on_settings", c.get("settings") == 1, c.get("settings"))
        self.check("退出 -> on_quit", c.get("quit") == 1, c.get("quit"))

    # ------------------------------------------------------------ 传参
    def test_11_tb_mode_kwarg_variants(self):
        d = self.data
        self.check("构造不传 tb_mode 不抛", d.get("variant_errors") == [], d.get("variant_errors"))
        self.check("tb_mode='clear' -> 勾「透明」", d.get("variant_clear") == [False, True, False],
                   d.get("variant_clear"))
        self.check("tb_mode='bogus' -> 勾「关」", d.get("variant_bogus") == [False, False, True],
                   d.get("variant_bogus"))
        self.check("不传 tb_mode -> 勾「关」", d.get("variant_none") == [False, False, True],
                   d.get("variant_none"))
        self.check("全位置参数 + tb_mode 关键字 -> 勾「磨砂」",
                   d.get("variant_kwarg_only") == [True, False, False], d.get("variant_kwarg_only"))

    def test_12_broken_menu_set_tb_mode_silent(self):
        d = self.data
        self.check("真降级时 set_tb_mode 静默返回 False",
                   d.get("broken_set_tb_ret") is False, d.get("broken_set_tb_ret"))
        self.check("真降级时 set_tb_enabled 静默返回 False",
                   d.get("broken_set_tb_enabled_ret") is False, d.get("broken_set_tb_enabled_ret"))
        self.check("真降级调用不抛", d.get("broken_set_tb_error") == "",
                   d.get("broken_set_tb_error"))

    # ------------------------------------------------------------ 左键
    def test_13_left_click_toggles_panel_once(self):
        d = self.data
        self.check("emit(Trigger) 后 on_toggle_panel 恰好 1 次",
                   d.get("toggle_after_trigger") == 1, d.get("toggle_after_trigger"))
        self.check("emit(Trigger) 不碰 on_show_panel",
                   d.get("show_panel_after_trigger") == 0, d.get("show_panel_after_trigger"))

    def test_14_other_reasons_do_not_retrigger(self):
        d = self.data
        self.check("Context/MiddleClick/DoubleClick 不再触发",
                   d.get("toggle_after_other_reasons") == 1, d.get("toggle_after_other_reasons"))

    # ------------------------------------------------------------ 图标
    def test_15_icon_sizes(self):
        d = self.data
        got = [tuple(s) for s in (d.get("tray_icon_sizes") or [])]
        self.check("传入图标的 availableSizes 含 16 和 32",
                   (16, 16) in got and (32, 32) in got, got)
        self.check("set_icon 不抛", d.get("set_icon_error") == "", d.get("set_icon_error"))
        self.check("set_icon 真换掉了图标",
                   [tuple(s) for s in (d.get("icon_sizes_after_set") or [])] == [(48, 48)],
                   d.get("icon_sizes_after_set"))

    # ------------------------------------------------------------ 空转
    def test_16_show_hide_safe_in_unavailable_env(self):
        d = self.data
        self.check("show()/hide() 一次异常都没有", d.get("call_errors") == [], d.get("call_errors"))
        self.check("show() 空转返回 False", d.get("show_ret") is False, d.get("show_ret"))
        self.check("hide() 空转返回 False", d.get("hide_ret") is False, d.get("hide_ret"))
        self.check("is_visible() show 后仍 False",
                   d.get("wrapper_visible_after_show") is False, d.get("wrapper_visible_after_show"))
        self.check("is_visible() hide 后仍 False",
                   d.get("wrapper_visible_after_hide") is False, d.get("wrapper_visible_after_hide"))

    def test_17_broken_callback_does_not_break_tray(self):
        d = self.data
        self.check("回调抛异常不影响托盘（含 on_tb_mode）",
                   d.get("bad_cb_error") == "", d.get("bad_cb_error"))


if __name__ == "__main__":
    if PROBE_FLAG in sys.argv:
        _probe()
    else:
        unittest.main(verbosity=2)
