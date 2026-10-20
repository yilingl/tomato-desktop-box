# -*- coding: utf-8 -*-
"""ui_mock.py 2.1.0 接线自测（现覆盖 2.2.0 任务栏透明行）：版本号 / 托盘开关 / 黄点 / 设置面板 / 托盘回调。

为什么走子进程：QT_QPA_PLATFORM=offscreen 必须在 import PySide6 之前设进环境。
    跑 unittest discover 时同进程里可能已经有 windows 平台的 QApplication，
    那时建 MainWindow 会真的往用户屏幕上弹面板 —— 所以断言一律在离屏子进程里做。

绝不碰真注册表：开机自启动那一段把 ui_mock.AUTO 换成记录型假对象，
    只断言调了 enable() / disable()，真值一个字节都不写。

跑法：
    python tests_ui_210.py
    python -m unittest tests_ui_210 -v
"""
import json
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

PROBE_FLAG = "--probe"
PROBE_PREFIX = "UI210_PROBE_JSON "


def _probe():
    """子进程入口：离屏实测 ui_mock 的托盘接线，结果打成一行 JSON 给父进程断言。"""
    import tempfile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"      # 必须在 import PySide6 之前设
    tmp = tempfile.mkdtemp(prefix="dsh_ui210_")
    for key in ("UI", "MINIS", "GROUPS", "CATS", "HIDEICONS"):
        os.environ["DESKBOX_%s_JSON" % key] = os.path.join(tmp, key.lower() + ".json")
    os.environ["DESKBOX_ROOT"] = os.path.join(tmp, "root")
    os.makedirs(os.environ["DESKBOX_ROOT"], exist_ok=True)
    sys.path.insert(0, REPO)

    from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QVBoxLayout,
                                   QWidget)
    import deskbox.ui_mock as U

    app = QApplication.instance() or QApplication([])
    U.NO_HIDE_PROMPT = True           # 压掉启动自检弹窗（--shot 那条现成开关）

    out = {}
    out["platform"] = app.platformName()
    out["version"] = U.VERSION
    out["default_tray"] = U.DEFAULT_STATE.get("tray")
    out["default_snap"] = U.DEFAULT_STATE.get("snap")
    out["tmp_ui_json"] = os.environ.get("DESKBOX_UI_JSON")

    # ---------------------------------------------------------- 构造主面板
    out["construct_error"] = ""
    w = None
    try:
        w = U.MainWindow()
        app.processEvents()
    except Exception as exc:          # noqa: BLE001
        out["construct_error"] = "%s: %s" % (type(exc).__name__, exc)
        print(PROBE_PREFIX + json.dumps(out, ensure_ascii=False))
        return

    # ---------------------------------------------------------- 状态：默认开
    out["tray_enabled_default"] = bool(w._tray_enabled())
    out["state_now_tray"] = w._state_now().get("tray")
    out["lights_actions"] = list(U.TrafficLights.ACTIONS)

    # 状态文件里 tray=False 时要现读跟随（不是拿默认值顶替）
    out["state_from_file_error"] = ""
    w2 = None
    try:
        with open(U._ui_json_path(), "w", encoding="utf-8") as fh:
            json.dump({"tray": False, "snap": True}, fh)
        w2 = U.MainWindow()
        app.processEvents()
        out["tray_enabled_from_file"] = bool(w2._tray_enabled())
        out["state_now_tray_from_file"] = w2._state_now().get("tray")
    except Exception as exc:          # noqa: BLE001
        out["state_from_file_error"] = "%s: %s" % (type(exc).__name__, exc)
    if w2 is not None:
        w2.hide()

    # ---------------------------------------------------------- 黄点
    class FakeTray(object):
        def __init__(self, avail=True):
            self._avail = avail
            self.shown = 0
            self.hidden = 0

        def available(self):
            return bool(self._avail)

        def show(self):
            self.shown += 1
            return True

        def hide(self):
            self.hidden += 1
            return True

    def click_min(win, tray, tray_on):
        """真的走黄点事件：TrafficLights.mousePressEvent 命中 index 0（min）。"""
        win._tray = tray
        win._tray_on = tray_on
        win.show()
        app.processEvents()
        counts = {"min": 0}
        win.showMinimized = lambda *a, **k: counts.__setitem__("min", counts["min"] + 1)
        win.lights._hover = 0          # TrafficLights.ACTIONS[0] == "min"
        win.lights.mousePressEvent(None)
        app.processEvents()
        return {"visible": bool(win.isVisible()), "minimized": counts["min"],
                "tray_hidden": (tray.hidden if tray is not None else None)}

    on_avail = FakeTray(True)
    out["click_on_avail"] = click_min(w, on_avail, True)
    unavail = FakeTray(False)
    out["click_on_unavail"] = click_min(w, unavail, True)
    out["click_off_avail"] = click_min(w, FakeTray(True), False)
    out["click_on_no_tray"] = click_min(w, None, True)

    # ---------------------------------------------------------- 假对话框（记录行）
    class FakeDialog(object):
        made = []

        def __init__(self, parent=None, title="", width=380, density=62):
            self.parent = parent
            self.title = title
            self.width = width
            self.foot = QHBoxLayout()
            self.lines = []
            self.buttons = []
            self.boxes = []
            self.execd = 0
            FakeDialog.made.append(self)

        def add_line(self, text, color=None, px=12, bold=False):
            self.lines.append(text)

        def add_box(self, top=11, bottom=11, left=14, right=14, spacing=6, bg_alpha=14):
            box = QWidget(self.parent)
            lay = QVBoxLayout(box)
            self.boxes.append((box, lay))
            return box, lay

        def add_button(self, text, primary=False, slot=None):
            self.buttons.append(text)

        def accept(self):
            pass

        def reject(self):
            pass

        def exec(self):
            self.execd += 1

    def rows_of(lay):
        """把 add_box 的竖排布局拆成 [(行标题, 该行的 Segmented 或 None)]。"""
        res = []
        for i in range(lay.count()):
            item = lay.itemAt(i)
            sub = item.layout()
            if sub is None:
                continue
            labels, segs = [], []
            for j in range(sub.count()):
                wd = sub.itemAt(j).widget()
                if wd is None:
                    continue
                if isinstance(wd, QLabel):
                    labels.append(wd.text())
                elif isinstance(wd, U.Segmented):
                    segs.append(wd)
            if labels:
                res.append((labels[0], segs[-1] if segs else None))
        return res

    # ---------------------------------------------------------- 假 AUTO（绝不碰真注册表）
    class FakeAuto(object):
        def __init__(self, state=False):
            self.state = bool(state)
            self.calls = []

        def is_enabled(self, value_name=None):
            self.calls.append("is_enabled")
            return self.state

        def enable(self, value_name=None):
            self.calls.append("enable")
            self.state = True
            return True

        def disable(self, value_name=None):
            self.calls.append("disable")
            self.state = False
            return False

    real_dialog = U.GlassDialog
    real_auto = U.AUTO
    U.GlassDialog = FakeDialog

    fake_auto = FakeAuto(False)        # 现读值先摆成「关」，看面板是不是照实显示
    U.AUTO = fake_auto
    flashes = []
    real_flash = w._flash
    w._flash = lambda text, ms=2600: flashes.append(text)

    out["settings_error"] = ""
    try:
        w._on_settings()
    except Exception as exc:          # noqa: BLE001
        out["settings_error"] = "%s: %s" % (type(exc).__name__, exc)

    dlg = FakeDialog.made[-1] if FakeDialog.made else None
    header_rows = rows_of(dlg.boxes[0][1]) if (dlg and dlg.boxes) else []
    out["rows"] = [r[0] for r in header_rows]
    rows = dict(header_rows)
    out["has_auto_row"] = "开机自启动" in rows
    out["has_tray_row"] = "最小化到托盘" in rows
    out["dialog_lines_head"] = list(dlg.lines)[:1] if dlg else []
    out["dialog_execd"] = dlg.execd if dlg else 0

    seg_auto = rows.get("开机自启动")
    seg_tray = rows.get("最小化到托盘")
    out["seg_auto_none"] = seg_auto is None
    out["seg_tray_none"] = seg_tray is None
    out["seg_auto_labels"] = list(seg_auto.labels) if seg_auto is not None else None
    out["seg_tray_labels"] = list(seg_tray.labels) if seg_tray is not None else None
    out["seg_auto_index_live_off"] = seg_auto.index if seg_auto is not None else None

    # ------------------------------------------------ 任务栏透明行（2.2.0 新增）
    out["default_tb_mode"] = U.DEFAULT_STATE.get("tb_mode")
    out["panel_tb_mode_initial"] = getattr(w, "_tb_mode", None)
    out["state_now_tb_mode"] = w._state_now().get("tb_mode")
    seg_tb = rows.get("任务栏透明")
    out["has_tb_row"] = "任务栏透明" in rows
    out["tb_row_is_last"] = bool(out["rows"]) and out["rows"][-1] == "任务栏透明"
    out["seg_tb_none"] = seg_tb is None
    out["seg_tb_labels"] = list(seg_tb.labels) if seg_tb is not None else None
    out["seg_tb_index_default"] = seg_tb.index if seg_tb is not None else None

    # 换成假 taskbarfx：绝不让设置面板真的去改系统任务栏
    class FakeTbFx(object):
        MODES = ("blur", "clear", "off")

        def __init__(self, ok=True):
            self.ok = ok
            self.calls = []
            self.cur = "off"

        def is_supported(self):
            return True

        def peer_running(self):
            return False

        def peer_hint(self):
            return "检测到 TranslucentTB，两个程序会互相覆盖"

        def current(self):
            return self.cur

        def apply(self, mode):
            self.calls.append(mode)
            if self.ok and mode in self.MODES:
                self.cur = mode
                return True
            return False

        def release(self):
            self.calls.append("release")
            self.cur = "off"
            return True

    if seg_tb is not None:
        real_tbfx = U.TBFX
        fake_tb = FakeTbFx(True)
        U.TBFX = fake_tb
        fake_tb.calls = []
        flashes[:] = []
        seg_tb._cb(0)                         # 点「磨砂」
        out["tb_calls_after_blur"] = list(fake_tb.calls)
        out["tb_mode_after_blur"] = getattr(w, "_tb_mode", None)
        out["tb_index_after_blur"] = seg_tb.index
        out["tb_state_now_after_blur"] = w._state_now().get("tb_mode")
        out["tb_flash_after_blur"] = list(flashes)   # 有 TTB 时该给一句提示

        fake_tb.calls = []
        flashes[:] = []
        seg_tb._cb(1)                         # 点「透明」
        out["tb_calls_after_clear"] = list(fake_tb.calls)
        out["tb_index_after_clear"] = seg_tb.index

        # 下发失败：档位与格子都必须拨回**实际**值，不许显示假的
        fake_tb.calls = []
        flashes[:] = []
        fake_tb.ok = False
        seg_tb._cb(2)                         # 想切「关」但后端说不行
        out["tb_calls_after_fail"] = list(fake_tb.calls)
        out["tb_mode_after_fail"] = getattr(w, "_tb_mode", None)
        out["tb_index_after_fail"] = seg_tb.index
        out["tb_flash_after_fail"] = list(flashes)

        # 启动对齐：即使档位是「关」也要下发一次（抹掉上次强杀的残留）
        fake_tb.ok = True
        fake_tb.calls = []
        w._tb_mode = "off"
        out["restore_ret"] = bool(w._restore_tb())
        out["restore_calls"] = list(fake_tb.calls)

        # 模块整个缺失：整行降级成灰字，不摆一个看着能用的开关
        U.TBFX = None
        w._on_settings()
        dlg_na = FakeDialog.made[-1] if FakeDialog.made else None
        rows_na = dict(rows_of(dlg_na.boxes[0][1])) if (dlg_na and dlg_na.boxes) else {}
        out["tb_row_na_present"] = "任务栏透明" in rows_na
        out["tb_row_na_no_seg"] = rows_na.get("任务栏透明") is None
        U.TBFX = real_tbfx

    # ---------------------------------------------------------- 自启动开关
    if seg_auto is not None:
        fake_auto.calls = []
        flashes[:] = []
        seg_auto._cb(0)                # 点「开」
        out["auto_calls_after_on"] = list(fake_auto.calls)
        out["auto_flash_after_on"] = list(flashes)
        out["auto_index_after_on"] = seg_auto.index
        out["auto_state_after_on"] = fake_auto.state

        fake_auto.calls = []
        flashes[:] = []
        seg_auto._cb(1)                # 点「关」
        out["auto_calls_after_off"] = list(fake_auto.calls)
        out["auto_flash_after_off"] = list(flashes)
        out["auto_index_after_off"] = seg_auto.index

        # 写入被拦（安全软件）：回显必须回到注册表实际值，不许假装成功
        fake_auto.calls = []

        def blocked_enable(*a, **k):
            fake_auto.calls.append("enable")
            return False               # 注册表其实没写进去（被安全软件拦住）
        fake_auto.enable = blocked_enable
        flashes[:] = []
        fake_auto.state = False
        seg_auto._cb(0)                # 想开，但 is_enabled() 再读还是 False
        out["auto_calls_after_fail"] = list(fake_auto.calls)
        out["auto_flash_after_fail"] = list(flashes)
        out["auto_index_after_fail"] = seg_auto.index

        # 抛异常（模块本不该抛）：不许炸，提示照给
        def boom(*a, **k):
            raise OSError("Access is denied")
        fake_auto.enable = boom
        fake_auto.calls = []
        flashes[:] = []
        out["auto_raise_error"] = ""
        try:
            seg_auto._cb(0)
        except Exception as exc:       # noqa: BLE001
            out["auto_raise_error"] = "%s: %s" % (type(exc).__name__, exc)
        out["auto_flash_after_raise"] = list(flashes)
        out["auto_index_after_raise"] = seg_auto.index

    # ---------------------------------------------------------- 托盘开关
    if seg_tray is not None:
        out["tray_on_before_click"] = bool(w._tray_enabled())
        seg_tray._cb(1)
        out["tray_on_after_off"] = bool(w._tray_enabled())
        out["state_now_tray_after_off"] = w._state_now().get("tray")
        seg_tray._cb(0)
        out["tray_on_after_on"] = bool(w._tray_enabled())

    w._flash = real_flash
    U.AUTO = real_auto

    # ---------------------------------------------------------- _setup_tray
    class FakeTrayMod(object):
        def __init__(self, avail=True):
            self.avail = avail
            self.ctor = {}
            outer = self

            class _Tray(object):
                # 末两个参数是 2.2.0 加的「任务栏透明」子菜单接线（真 tray.py 同样追加在
                # 末尾、都带默认值）；假对象必须跟着收，否则 MainWindow 构造就 TypeError。
                def __init__(self, parent, icon, on_toggle_panel=None, on_show_panel=None,
                             on_show_all_minis=None, on_settings=None, on_quit=None,
                             on_tb_mode=None, tb_mode=None):
                    outer.ctor = {"parent": parent, "icon": icon,
                                  "on_toggle_panel": on_toggle_panel,
                                  "on_show_panel": on_show_panel,
                                  "on_show_all_minis": on_show_all_minis,
                                  "on_settings": on_settings, "on_quit": on_quit,
                                  "on_tb_mode": on_tb_mode, "tb_mode": tb_mode}
                    self.shown = 0
                    self.hidden = 0
                    self.tb_mode = tb_mode

                def available(self):
                    return bool(outer.avail)

                def show(self):
                    self.shown += 1
                    return True

                def hide(self):
                    self.hidden += 1
                    return True

            self.Tray = _Tray

    real_tray_mod = U.TRAY
    fm = FakeTrayMod(True)
    U.TRAY = fm
    w._tray = None
    out["setup_error"] = ""
    tray = None
    try:
        tray = w._setup_tray()
    except Exception as exc:          # noqa: BLE001
        out["setup_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["setup_returned"] = tray is not None
    out["held_by_panel"] = bool(tray is not None and w._tray is tray)
    out["ctor_parent_is_panel"] = fm.ctor.get("parent") is w
    out["ctor_icon_present"] = fm.ctor.get("icon") is not None
    out["shown_after_setup"] = getattr(tray, "shown", None)
    cb = fm.ctor
    out["cb_identities"] = {
        "toggle": cb.get("on_toggle_panel") == w._tray_toggle,
        "show": cb.get("on_show_panel") == w._tray_show,
        "minis": cb.get("on_show_all_minis") == w._tray_show_minis,
        "settings": cb.get("on_settings") == w._on_settings,
        "quit": cb.get("on_quit") == w._tray_quit,
        "tb_mode": cb.get("on_tb_mode") == w._on_tb_from_tray,   # 2.2.0 新增
    }
    out["ctor_tb_mode"] = cb.get("tb_mode")
    out["panel_tb_mode"] = getattr(w, "_tb_mode", None)

    # 五个回调真的能跑一遍
    w.show()
    app.processEvents()
    out["cb_run_error"] = ""
    try:
        cb["on_toggle_panel"]()
        out["toggle_click_hides"] = not w.isVisible()
        cb["on_toggle_panel"]()
        out["toggle_click_shows"] = bool(w.isVisible())
        w.hide()
        cb["on_show_panel"]()
        out["show_click_shows"] = bool(w.isVisible())
        cb["on_show_all_minis"]()
        out["show_minis_minis"] = len(w._minis)
    except Exception as exc:          # noqa: BLE001
        out["cb_run_error"] = "%s: %s" % (type(exc).__name__, exc)

    out["settings_cb_error"] = ""
    made_before = len(FakeDialog.made)
    try:
        cb["on_settings"]()            # 假对话框，不弹窗
    except Exception as exc:          # noqa: BLE001
        out["settings_cb_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["settings_cb_built_dialog"] = len(FakeDialog.made) > made_before

    # 通知区不可用：安静返回 None、不 show，黄点必须退回 showMinimized
    w._tray = None
    fm2 = FakeTrayMod(False)
    U.TRAY = fm2
    out["unavail_setup_error"] = ""
    t2 = None
    try:
        t2 = w._setup_tray()
    except Exception as exc:          # noqa: BLE001
        out["unavail_setup_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["unavail_returns_none"] = t2 is None
    out["unavail_ref_kept"] = w._tray is not None
    out["unavail_not_shown"] = getattr(w._tray, "shown", None) == 0
    out["click_with_unavail_tray"] = click_min(w, w._tray, True)

    # tray 模块整个缺失
    U.TRAY = None
    w._tray = None
    out["missing_setup_error"] = ""
    missing_ret = None
    try:
        missing_ret = w._setup_tray()
    except Exception as exc:          # noqa: BLE001
        out["missing_setup_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["missing_returns_none"] = missing_ret is None
    out["missing_ref_none"] = w._tray is None
    out["click_with_missing_tray"] = click_min(w, None, True)

    # 退出：小窗口一起关 + QApplication.quit()
    fm3 = FakeTrayMod(True)
    U.TRAY = fm3
    t3 = w._setup_tray()
    w.show()
    app.processEvents()
    out["quit_prev_visible"] = bool(w.isVisible())
    out["quit_error"] = ""
    try:
        fm3.ctor["on_quit"]()
    except Exception as exc:          # noqa: BLE001
        out["quit_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["quit_panel_hidden"] = not w.isVisible()
    out["quit_tray_hidden"] = getattr(t3, "hidden", None)
    out["quit_saved_json_error"] = ""
    try:
        with open(U._ui_json_path(), encoding="utf-8") as fh:
            out["quit_saved_json_tray"] = json.load(fh).get("tray")
    except Exception as exc:          # noqa: BLE001
        out["quit_saved_json_error"] = "%s: %s" % (type(exc).__name__, exc)

    # ------------------------------------------- 小窗口任务栏样式（纯函数实测）
    # 只测算术：离屏下 winId() 是假句柄、GetWindowLongW 拿不到真值，
    # 「任务栏里到底有没有小窗口」这条只能真机确认（用户自测）。
    A, TOOL, LAYER, MBX = 0x00040000, 0x00000080, 0x00080000, 0x00020000
    ex_in = A | LAYER                       # 输入含 WS_EX_APPWINDOW
    ex_out = U._taskbar_ex(ex_in)
    out["tb_in"] = ex_in
    out["tb_out"] = ex_out
    out["tb_out_no_app"] = not (ex_out & A)
    out["tb_out_tool"] = bool(ex_out & TOOL)
    out["tb_out_layered"] = bool(ex_out & LAYER)
    out["tb_idem_out"] = U._taskbar_ex(ex_out)   # 幂等：再算一次必须一样
    ex_plain = U._taskbar_ex(LAYER)              # 输入本来就没有 APPWINDOW
    out["tb_plain_out"] = ex_plain
    out["tb_plain_clean"] = (not (ex_plain & A)) and bool(ex_plain & TOOL) \
        and bool(ex_plain & LAYER)
    ex_st, st_st = U._taskbar_ex(A | LAYER, A | MBX)
    out["tb_style_ex"] = ex_st
    out["tb_style_st"] = st_st
    out["tb_style_no_minbox"] = not (st_st & MBX)

    # ---------------------------------------------------------- 点关闭：收进托盘 / 退出
    out["default_close_tray"] = U.DEFAULT_STATE.get("close_tray")
    out["close_tray_default_read"] = bool(w._close_tray)
    out["state_now_close_tray"] = w._state_now().get("close_tray")

    def mini_open_flag(mid):
        try:
            return (U.MB.get(mid) or {}).get("open")
        except Exception:              # noqa: BLE001
            return None

    def ui_json_close_tray():
        try:
            with open(U._ui_json_path(), encoding="utf-8") as fh:
                return json.load(fh).get("close_tray")
        except Exception:              # noqa: BLE001
            return None

    out["close_mini_error"] = ""
    wid = None
    try:
        _d = U.MB.create(title="关闭测试")
        wid = (_d or {}).get("id")
        if wid:
            w._open_mini(wid)
    except Exception as exc:           # noqa: BLE001
        out["close_mini_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["close_mini_wid"] = wid
    out["close_mini_visible_before"] = bool(wid and w._minis[wid].isVisible())

    # ---- a) close_tray=True + 托盘可用：收进托盘，程序不退出
    fm4 = FakeTrayMod(True)
    U.TRAY = fm4
    w._tray = w._setup_tray()
    w._close_tray = True
    quit_calls = []
    real_maybe_quit = w._maybe_quit
    w._maybe_quit = lambda: quit_calls.append("maybe_quit")
    w.show()
    app.processEvents()
    out["close_a_prev_visible"] = bool(w.isVisible())
    out["close_a_tray_hidden_before"] = getattr(w._tray, "hidden", None)

    def minis_snapshot():
        """minis.json 的路径 / 全文 / mtime —— 收进托盘分支必须一字未改。"""
        try:
            pth = U.MB.json_path()
            with open(pth, encoding="utf-8") as fh:
                return [pth, fh.read(), os.path.getmtime(pth)]
        except Exception as exc:       # noqa: BLE001
            return [None, "%s: %s" % (type(exc).__name__, exc), 0.0]

    snap_before = minis_snapshot()
    out["close_a_minis_json_path"] = snap_before[0]
    out["close_a_error"] = ""
    ret_a = None
    try:
        ret_a = w.close()              # 真走 closeEvent
    except Exception as exc:           # noqa: BLE001
        out["close_a_error"] = "%s: %s" % (type(exc).__name__, exc)
    app.processEvents()
    out["close_a_return"] = ret_a
    out["close_a_visible"] = bool(w.isVisible())
    out["close_a_maybe_quit_calls"] = len(quit_calls)
    out["close_a_hidden_minis"] = list(w._hidden_minis)
    out["close_a_mini_visible"] = bool(wid and w._minis[wid].isVisible())
    out["close_a_mini_gone_flag"] = bool(wid and getattr(w._minis[wid], "_gone", False))
    out["close_a_mini_open_flag"] = mini_open_flag(wid)
    snap_after = minis_snapshot()
    out["close_a_minis_json_same"] = (snap_before[1] == snap_after[1])
    out["close_a_minis_json_mtime_same"] = (snap_before[2] == snap_after[2])
    out["close_a_minis_json_after_head"] = snap_after[1][:1]
    out["close_a_tray_hidden_after"] = getattr(w._tray, "hidden", None)
    out["close_a_app_alive"] = QApplication.instance() is app
    out["close_a_state_json"] = ui_json_close_tray()
    # 托盘左键能把它叫回来
    out["close_a_reopen_error"] = ""
    try:
        w._tray_toggle()
        app.processEvents()
    except Exception as exc:           # noqa: BLE001
        out["close_a_reopen_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["close_a_reopen_visible"] = bool(w.isVisible())

    # ---- b) close_tray=False：走原来的真退出路径（_maybe_quit 被排上）
    w._close_tray = False
    quit_calls[:] = []
    if wid:
        w._open_mini(wid)              # 小窗口重新露出来
    w.show()
    app.processEvents()
    out["close_b_prev_visible"] = bool(w.isVisible())
    out["close_b_error"] = ""
    ret_b = None
    try:
        ret_b = w.close()
    except Exception as exc:           # noqa: BLE001
        out["close_b_error"] = "%s: %s" % (type(exc).__name__, exc)
    app.processEvents()
    out["close_b_return"] = ret_b
    out["close_b_visible"] = bool(w.isVisible())
    out["close_b_maybe_quit_calls"] = len(quit_calls)
    out["close_b_hidden_minis"] = list(w._hidden_minis)
    out["close_b_mini_open_flag"] = mini_open_flag(wid)

    # ---- c) 开关关着 / 托盘不可用 / 模块缺失：一律回退真退出（绝不留孤儿进程）
    def try_tray_branch(close_flag, tray_obj, module):
        """把状态摆成指定样子，关一次，看是不是走了真退出分支。"""
        keep_tray, keep_mod = w._tray, U.TRAY
        w._close_tray = close_flag
        w._tray = tray_obj
        U.TRAY = module
        quit_calls[:] = []
        w.show()
        app.processEvents()
        res = {}
        try:
            w.close()
        except Exception as exc:       # noqa: BLE001
            res["error"] = "%s: %s" % (type(exc).__name__, exc)
        app.processEvents()
        res["visible"] = bool(w.isVisible())
        res["maybe_quit_calls"] = len(quit_calls)
        w._tray = keep_tray
        U.TRAY = keep_mod
        return res

    out["close_off_unavail"] = try_tray_branch(True, FakeTray(False), real_tray_mod)
    out["close_on_no_tray_obj"] = try_tray_branch(True, None, real_tray_mod)
    out["close_on_no_module"] = try_tray_branch(True, FakeTray(True), None)
    out["close_off_avail"] = try_tray_branch(False, FakeTray(True), real_tray_mod)
    w._maybe_quit = real_maybe_quit

    # ---- 设置面板里的「点关闭」行
    def close_row_of(module, tray_obj):
        """拿指定模块 / 托盘状态重建一次设置面板，取「点关闭」那一行。"""
        keep_mod, keep_tray = U.TRAY, w._tray
        U.TRAY = module
        w._tray = tray_obj
        n = len(FakeDialog.made)
        err = ""
        try:
            w._on_settings()
        except Exception as exc:       # noqa: BLE001
            err = "%s: %s" % (type(exc).__name__, exc)
        U.TRAY, w._tray = keep_mod, keep_tray
        if len(FakeDialog.made) <= n:
            return err, None, None
        dg = FakeDialog.made[-1]
        rows = dict(rows_of(dg.boxes[0][1]) if dg.boxes else [])
        return err, rows.get("点关闭"), list(rows)

    w._close_tray = True
    out["close_row_error"], seg_close, out["close_row_names"] = close_row_of(
        real_tray_mod, FakeTray(True))
    out["seg_close_labels"] = list(seg_close.labels) if seg_close is not None else None
    out["seg_close_index_on"] = seg_close.index if seg_close is not None else None
    if seg_close is not None:
        seg_close._cb(1)               # 点「退出」
        out["close_on_after_click"] = bool(w._close_tray_enabled())
        out["state_now_close_tray_after_off"] = w._state_now().get("close_tray")
        seg_close._cb(0)               # 点「收进托盘」
        out["close_on_after_click_back"] = bool(w._close_tray_enabled())
    w._close_tray = False
    _e2, seg_close2, _n2 = close_row_of(real_tray_mod, FakeTray(True))
    out["seg_close_index_off"] = seg_close2.index if seg_close2 is not None else None
    _e3, seg_close3, _n3 = close_row_of(None, None)   # tray.py 缺失
    out["close_row_missing_module_seg"] = seg_close3 is not None
    out["close_row_missing_module_error"] = _e3

    # ---- 状态往返：写盘再读回
    def roundtrip(flag):
        w._close_tray = flag
        w._save_state()
        wrote = ui_json_close_tray()
        fresh = U.MainWindow()
        app.processEvents()
        got = bool(fresh._close_tray_enabled())
        got_now = fresh._state_now().get("close_tray")
        fresh.hide()
        return wrote, got, got_now

    out["rt_true"] = list(roundtrip(True))
    out["rt_false"] = list(roundtrip(False))

    # ---------------------------------------------------------- 关机不该记成「已关闭」
    def minis_bytes():
        try:
            with open(U.MB.json_path(), "rb") as fh:
                return fh.read()
        except Exception as exc:       # noqa: BLE001
            return ("%s: %s" % (type(exc).__name__, exc)).encode("utf-8")

    def ledger_open(mid):
        try:
            return (U.MB.get(mid) or {}).get("open")
        except Exception:              # noqa: BLE001
            return None

    for _m in list(w._minis.values()):          # 先把前面的小窗口关干净
        if _m is not None:
            _m._user_closed = True
            try:
                _m.close()
            except Exception:      # noqa: BLE001
                pass
    w._minis.clear()
    w._hidden_minis = []

    # a) 模拟关机：不打标记直接 close()，账本必须一动不动
    out["mt_a_error"] = ""
    wid_a = None
    try:
        _da = U.MB.create(title="关机测试")
        wid_a = (_da or {}).get("id")
        if wid_a:
            w._open_mini(wid_a)
    except Exception as exc:           # noqa: BLE001
        out["mt_a_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["mt_a_wid"] = wid_a
    out["mt_a_created_open"] = ledger_open(wid_a)
    out["mt_a_visible_before"] = bool(wid_a and w._minis[wid_a].isVisible())
    bytes_before_a = minis_bytes()
    ma = w._minis.get(wid_a)
    if ma is not None:
        try:
            ma.close()                 # 关机 / 注销 / 托盘退出都是这条路
        except Exception as exc:       # noqa: BLE001
            out["mt_a_error"] = "%s: %s" % (type(exc).__name__, exc)
    app.processEvents()
    out["mt_a_open_after"] = ledger_open(wid_a)
    out["mt_a_bytes_same"] = (minis_bytes() == bytes_before_a)
    out["mt_a_still_in_open_ids"] = wid_a in list(U.MB.open_ids())
    w._minis.pop(wid_a, None)

    # b) 模拟用户亲手关：打标记再 close()，账本必须写 False
    out["mt_b_error"] = ""
    wid_b = None
    try:
        _db = U.MB.create(title="用户关闭测试")
        wid_b = (_db or {}).get("id")
        if wid_b:
            w._open_mini(wid_b)
    except Exception as exc:           # noqa: BLE001
        out["mt_b_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["mt_b_wid"] = wid_b
    out["mt_b_open_before"] = ledger_open(wid_b)
    mb = w._minis.get(wid_b)
    if mb is not None:
        mb._user_closed = True
        try:
            mb.close()
        except Exception as exc:       # noqa: BLE001
            out["mt_b_error"] = "%s: %s" % (type(exc).__name__, exc)
    app.processEvents()
    out["mt_b_open_after"] = ledger_open(wid_b)
    out["mt_b_not_in_open_ids"] = wid_b not in list(U.MB.open_ids())
    w._minis.pop(wid_b, None)

    # c) 回归：a) 里那个窗口在新一轮 _restore_minis() 里必须被开出来
    out["mt_c_error"] = ""
    try:
        w._restore_minis()
        app.processEvents()
    except Exception as exc:           # noqa: BLE001
        out["mt_c_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["mt_c_in_minis"] = wid_a in w._minis
    out["mt_c_visible"] = bool(wid_a in w._minis and w._minis[wid_a].isVisible())
    out["mt_c_b_not_restored"] = wid_b not in w._minis
    out["mt_c_open_ids_after"] = list(U.MB.open_ids())

    for _m in list(w._minis.values()):          # 收尾：别影响后面的用例
        if _m is not None:
            _m._user_closed = True
            try:
                _m.close()
            except Exception:      # noqa: BLE001
                pass
    w._minis.clear()
    w._hidden_minis = []

    U.TRAY = real_tray_mod
    U.GlassDialog = real_dialog
    print(PROBE_PREFIX + json.dumps(out, ensure_ascii=False))


def _run_probe():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-u", os.path.abspath(__file__), PROBE_FLAG],
                       cwd=REPO, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    line = ""
    for ln in (p.stdout or "").splitlines():
        if ln.startswith(PROBE_PREFIX):
            line = ln[len(PROBE_PREFIX):]
    return line, (p.stdout or ""), (p.stderr or ""), p.returncode


class Ui210Test(unittest.TestCase):
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

    # ------------------------------------------------------------ 环境 / 版本
    def test_01_offscreen_and_version(self):
        d = self.data
        self.check("子进程跑在 offscreen", d.get("platform") == "offscreen", d.get("platform"))
        self.check("VERSION == 2.2.0", d.get("version") == "2.2.0", d.get("version"))
        self.check("临时 ui.json 生效（不写用户真实状态）",
                   bool(d.get("tmp_ui_json")) and "dsh_ui210_" in str(d.get("tmp_ui_json")),
                   d.get("tmp_ui_json"))

    # ------------------------------------------------------------ 状态
    def test_02_default_state_has_tray_true(self):
        d = self.data
        self.check("主面板构造没炸", d.get("construct_error") == "", d.get("construct_error"))
        self.check('DEFAULT_STATE 含 "tray" 且默认 True',
                   d.get("default_tray") is True, d.get("default_tray"))
        self.check('DEFAULT_STATE 的 "snap" 没被动', d.get("default_snap") is True,
                   d.get("default_snap"))
        self.check("_tray_enabled() 默认 True", d.get("tray_enabled_default") is True,
                   d.get("tray_enabled_default"))
        self.check("_state_now() 里的 tray 跟开关一致",
                   d.get("state_now_tray") is True, d.get("state_now_tray"))
        self.check("TrafficLights.ACTIONS[0] 仍是 min",
                   (d.get("lights_actions") or [None])[0] == "min", d.get("lights_actions"))

    def test_03_tray_flag_follows_state_file(self):
        d = self.data
        self.check("读状态没炸", d.get("state_from_file_error") == "", d.get("state_from_file_error"))
        self.check("ui.json 里 tray=False -> _tray_enabled() 为 False",
                   d.get("tray_enabled_from_file") is False, d.get("tray_enabled_from_file"))
        self.check("ui.json 里 tray=False -> _state_now() 也是 False",
                   d.get("state_now_tray_from_file") is False, d.get("state_now_tray_from_file"))

    # ------------------------------------------------------------ 黄点
    def test_04_min_click_goes_to_tray_when_available(self):
        d = self.data
        c = d.get("click_on_avail") or {}
        self.check("开关开 + 托盘可用：面板收起来了", c.get("visible") is False, c)
        self.check("开关开 + 托盘可用：没调 showMinimized", c.get("minimized") == 0, c)

    def test_05_min_click_falls_back_to_showminimized(self):
        d = self.data
        u = d.get("click_on_unavail") or {}
        self.check("托盘抛不可用：面板仍然可见（绝不能藏起来）",
                   u.get("visible") is True, u)
        self.check("托盘不可用：照旧 showMinimized 恰好 1 次", u.get("minimized") == 1, u)
        self.check("托盘不可用：没调 hide()", u.get("tray_hidden") == 0, u)
        o = d.get("click_off_avail") or {}
        self.check("开关关（托盘可用）：面板仍可见", o.get("visible") is True, o)
        self.check("开关关：走 showMinimized", o.get("minimized") == 1, o)
        n = d.get("click_on_no_tray") or {}
        self.check("压根没有托盘对象：面板仍可见", n.get("visible") is True, n)
        self.check("压根没有托盘对象：走 showMinimized", n.get("minimized") == 1, n)

    # ------------------------------------------------------------ 设置面板
    def test_06_settings_has_two_new_rows(self):
        d = self.data
        self.check("设置面板构建没炸", d.get("settings_error") == "", d.get("settings_error"))
        self.check("确实有「开机自启动」行", d.get("has_auto_row") is True, d.get("rows"))
        self.check("确实有「最小化到托盘」行", d.get("has_tray_row") is True, d.get("rows"))
        self.check("两行排在「背景」之后",
                   (d.get("rows") or [])[:3] == ["背景", "开机自启动", "最小化到托盘"],
                   d.get("rows"))
        self.check("版本行还是第一行", (d.get("dialog_lines_head") or [""])[0] == "版本 2.2.0",
                   d.get("dialog_lines_head"))
        self.check("对话框 exec 了一次", d.get("dialog_execd") == 1, d.get("dialog_execd"))

    # ------------------------------------------------ 任务栏透明（2.2.0 新增）
    def test_06b_tb_default_is_off(self):
        d = self.data
        self.check('DEFAULT_STATE 含 "tb_mode" 且默认 "off"',
                   d.get("default_tb_mode") == "off", d.get("default_tb_mode"))
        self.check("面板初始化时 _tb_mode 也是 off",
                   d.get("panel_tb_mode_initial") == "off", d.get("panel_tb_mode_initial"))
        self.check("_state_now() 带着 tb_mode", d.get("state_now_tb_mode") == "off",
                   d.get("state_now_tb_mode"))

    def test_06c_tb_row_exists_and_is_last(self):
        d = self.data
        self.check("确实有「任务栏透明」行", d.get("has_tb_row") is True, d.get("rows"))
        self.check("它是最后一行的位置在「小窗口自动粘连」之后",
                   d.get("rows")[:5] == ["背景", "开机自启动", "最小化到托盘",
                                         "点关闭", "小窗口自动粘连"], d.get("rows"))
        self.check("三档文案 == ['磨砂','透明','关']",
                   d.get("seg_tb_labels") == ["磨砂", "透明", "关"], d.get("seg_tb_labels"))
        self.check("默认勾第 3 格「关」", d.get("seg_tb_index_default") == 2,
                   d.get("seg_tb_index_default"))

    def test_06d_tb_click_applies_and_syncs_state(self):
        d = self.data
        self.check("点「磨砂」把 blur 下发了一次",
                   d.get("tb_calls_after_blur") == ["blur"], d.get("tb_calls_after_blur"))
        self.check("点「磨砂」后 _tb_mode = blur", d.get("tb_mode_after_blur") == "blur",
                   d.get("tb_mode_after_blur"))
        self.check("点「磨砂」后格子停在索引 0", d.get("tb_index_after_blur") == 0,
                   d.get("tb_index_after_blur"))
        self.check("_state_now() 跟着变", d.get("tb_state_now_after_blur") == "blur",
                   d.get("tb_state_now_after_blur"))
        self.check("检测到 TTB 时给了一句提示",
                   any("TranslucentTB" in str(x) for x in (d.get("tb_flash_after_blur") or [])),
                   d.get("tb_flash_after_blur"))
        self.check("点「透明」下发 clear",
                   d.get("tb_calls_after_clear") == ["clear"], d.get("tb_calls_after_clear"))
        self.check("点「透明」后格子停在索引 1", d.get("tb_index_after_clear") == 1,
                   d.get("tb_index_after_clear"))

    def test_06e_tb_failure_rolls_back_display(self):
        d = self.data
        self.check("后端说失败时确实调了一次", d.get("tb_calls_after_fail") == ["off"],
                   d.get("tb_calls_after_fail"))
        self.check("没生效就不改 _tb_mode（仍是 clear）",
                   d.get("tb_mode_after_fail") == "clear", d.get("tb_mode_after_fail"))
        self.check("格子拨回实际档位（索引 1，不是用户点的 2）",
                   d.get("tb_index_after_fail") == 1, d.get("tb_index_after_fail"))
        self.check("没生效时给了一句提示", bool(d.get("tb_flash_after_fail")),
                   d.get("tb_flash_after_fail"))

    def test_06f_tb_restore_on_start_even_when_off(self):
        d = self.data
        self.check("_restore_tb() 返回 True", d.get("restore_ret") is True, d.get("restore_ret"))
        self.check("档位是「关」时也下发了一次 off（抹掉强杀残留）",
                   d.get("restore_calls") == ["off"], d.get("restore_calls"))

    def test_06g_tb_row_degrades_without_module(self):
        d = self.data
        self.check("taskbarfx 缺失时这一行还在", d.get("tb_row_na_present") is True,
                   d.get("tb_row_na_present"))
        self.check("taskbarfx 缺失时这一行没有 Segmented（置灰而不是假装能用）",
                   d.get("tb_row_na_no_seg") is True, d.get("tb_row_na_no_seg"))

    def test_07_row_controls_match_existing_style(self):
        d = self.data
        self.check("两行都建出了 Segmented", d.get("seg_auto_none") is False and
                   d.get("seg_tray_none") is False, (d.get("seg_auto_none"), d.get("seg_tray_none")))
        self.check("自启动行是两档 ['开','关']（与既有小窗口行同风格）",
                   d.get("seg_auto_labels") == ["开", "关"], d.get("seg_auto_labels"))
        self.check("托盘行是两档 ['开','关']", d.get("seg_tray_labels") == ["开", "关"],
                   d.get("seg_tray_labels"))
        self.check("自启动行初值现读（假 is_enabled()=False -> 显示「关」）",
                   d.get("seg_auto_index_live_off") == 1, d.get("seg_auto_index_live_off"))

    # ------------------------------------------------------------ 自启动开关
    def test_08_auto_toggle_calls_enable_disable(self):
        d = self.data
        self.check("点「开」调了 enable() 恰好 1 次",
                   (d.get("auto_calls_after_on") or []).count("enable") == 1,
                   d.get("auto_calls_after_on"))
        self.check("点「开」后提示「已开启」",
                   any("已开启" in t for t in (d.get("auto_flash_after_on") or [])),
                   d.get("auto_flash_after_on"))
        self.check("点「开」后控件回到「开」档", d.get("auto_index_after_on") == 0,
                   d.get("auto_index_after_on"))
        self.check("点「关」调了 disable() 恰好 1 次",
                   (d.get("auto_calls_after_off") or []).count("disable") == 1,
                   d.get("auto_calls_after_off"))
        self.check("点「关」后提示「已关闭」",
                   any("已关闭" in t for t in (d.get("auto_flash_after_off") or [])),
                   d.get("auto_flash_after_off"))
        self.check("点「关」后控件回到「关」档", d.get("auto_index_after_off") == 1,
                   d.get("auto_index_after_off"))

    def test_09_auto_toggle_flags_write_failure(self):
        d = self.data
        self.check("写完再读一次（enable 之后又读了 is_enabled）",
                   (d.get("auto_calls_after_fail") or [])[:2] == ["enable", "is_enabled"],
                   d.get("auto_calls_after_fail"))
        self.check("写失败提示里点明可能被安全软件拦住",
                   any("安全软件" in t for t in (d.get("auto_flash_after_fail") or [])),
                   d.get("auto_flash_after_fail"))
        self.check("写失败后控件回显注册表实际值（不许假装成功）",
                   d.get("auto_index_after_fail") == 1, d.get("auto_index_after_fail"))
        self.check("enable 抛异常也不炸界面", d.get("auto_raise_error") == "",
                   d.get("auto_raise_error"))
        self.check("抛异常时也给了失败提示",
                   any("写入失败" in t for t in (d.get("auto_flash_after_raise") or [])),
                   d.get("auto_flash_after_raise"))

    # ------------------------------------------------------------ 托盘开关
    def test_10_tray_toggle_writes_state(self):
        d = self.data
        self.check("托盘开关初值 True", d.get("tray_on_before_click") is True,
                   d.get("tray_on_before_click"))
        self.check("点「关」后开关为 False", d.get("tray_on_after_off") is False,
                   d.get("tray_on_after_off"))
        self.check("点「关」后 _state_now() 也是 False",
                   d.get("state_now_tray_after_off") is False, d.get("state_now_tray_after_off"))
        self.check("点回「开」后开关为 True", d.get("tray_on_after_on") is True,
                   d.get("tray_on_after_on"))

    # ------------------------------------------------------------ _setup_tray
    def test_11_setup_tray_wires_five_callbacks(self):
        d = self.data
        self.check("_setup_tray() 没抛", d.get("setup_error") == "", d.get("setup_error"))
        self.check("_setup_tray() 返回托盘对象", d.get("setup_returned") is True, d)
        self.check("托盘被主面板持引用（w._tray is 返回对象）",
                   d.get("held_by_panel") is True, d.get("held_by_panel"))
        self.check("托盘 parent 是主面板", d.get("ctor_parent_is_panel") is True, d)
        self.check("图标参数不是 None", d.get("ctor_icon_present") is True, d)
        self.check("可用时 show() 调了 1 次", d.get("shown_after_setup") == 1,
                   d.get("shown_after_setup"))
        ident = d.get("cb_identities") or {}
        self.check("on_toggle_panel -> MainWindow._tray_toggle", ident.get("toggle") is True, ident)
        self.check("on_tb_mode -> MainWindow._on_tb_from_tray（2.2.0 新增）",
                   ident.get("tb_mode") is True, ident)
        self.check("构造托盘时把当前档位传进去了",
                   d.get("ctor_tb_mode") == d.get("panel_tb_mode"), 
                   (d.get("ctor_tb_mode"), d.get("panel_tb_mode")))
        self.check("on_show_panel -> MainWindow._tray_show", ident.get("show") is True, ident)
        self.check("on_show_all_minis -> MainWindow._tray_show_minis", ident.get("minis") is True,
                   ident)
        self.check("on_settings -> MainWindow._on_settings", ident.get("settings") is True, ident)
        self.check("on_quit -> MainWindow._tray_quit", ident.get("quit") is True, ident)

    def test_12_callbacks_run(self):
        d = self.data
        self.check("五个回调跑一遍没抛", d.get("cb_run_error") == "", d.get("cb_run_error"))
        self.check("左键回调：露着 -> 收起", d.get("toggle_click_hides") is True,
                   d.get("toggle_click_hides"))
        self.check("左键回调：收着 -> 抬出来", d.get("toggle_click_shows") is True,
                   d.get("toggle_click_shows"))
        self.check("「显示主面板」只显不隐", d.get("show_click_shows") is True,
                   d.get("show_click_shows"))
        self.check("「显示全部小窗口」不抛", d.get("show_minis_minis") == 0,
                   d.get("show_minis_minis"))
        self.check("on_settings 回调能建出设置面板", d.get("settings_cb_built_dialog") is True, d)
        self.check("on_settings 回调没抛", d.get("settings_cb_error") == "",
                   d.get("settings_cb_error"))

    def test_13_unavailable_tray_degrades(self):
        d = self.data
        self.check("通知区不可用时 _setup_tray() 没抛", d.get("unavail_setup_error") == "",
                   d.get("unavail_setup_error"))
        self.check("通知区不可用时返回 None", d.get("unavail_returns_none") is True, d)
        self.check("引用仍然留着（对象没被 GC）", d.get("unavail_ref_kept") is True, d)
        self.check("通知区不可用时没有 show()", d.get("unavail_not_shown") is True, d)
        c = d.get("click_with_unavail_tray") or {}
        self.check("此时点黄点：面板仍可见 + 走 showMinimized",
                   c.get("visible") is True and c.get("minimized") == 1, c)

    def test_14_missing_tray_module_degrades(self):
        d = self.data
        self.check("tray.py 缺失时 _setup_tray() 没抛", d.get("missing_setup_error") == "",
                   d.get("missing_setup_error"))
        self.check("tray.py 缺失时返回 None", d.get("missing_returns_none") is True, d)
        self.check("tray.py 缺失时 _tray 保持 None", d.get("missing_ref_none") is True, d)
        c = d.get("click_with_missing_tray") or {}
        self.check("模块缺失时点黄点：面板仍可见 + 走 showMinimized",
                   c.get("visible") is True and c.get("minimized") == 1, c)

    def test_15_quit_really_quits(self):
        d = self.data
        self.check("退出前面板是露着的", d.get("quit_prev_visible") is True, d)
        self.check("退出回调没抛", d.get("quit_error") == "", d.get("quit_error"))
        self.check("退出后面板收起来了", d.get("quit_panel_hidden") is True, d)
        self.check("退出时托盘也收起来了", d.get("quit_tray_hidden") == 1, d.get("quit_tray_hidden"))
        self.check("退出时落了状态（临时 ui.json 里有 tray）",
                   d.get("quit_saved_json_tray") is True, d.get("quit_saved_json_tray"))


    # ------------------------------------------------------------ 点关闭
    def test_17_close_row_and_state_roundtrip(self):
        d = self.data
        self.check('DEFAULT_STATE 含 "close_tray" 且默认 True',
                   d.get("default_close_tray") is True, d.get("default_close_tray"))
        self.check("_close_tray_enabled() 默认 True",
                   d.get("close_tray_default_read") is True, d.get("close_tray_default_read"))
        self.check('_state_now() 里带着 "close_tray"',
                   d.get("state_now_close_tray") is True, d.get("state_now_close_tray"))
        names = d.get("close_row_names") or []
        self.check("设置面板有「点关闭」行，且紧跟在「最小化到托盘」下面",
                   names[:4] == ["背景", "开机自启动", "最小化到托盘", "点关闭"], names)
        self.check('「点关闭」两档是 [收进托盘, 退出]',
                   d.get("seg_close_labels") == ["收进托盘", "退出"], d.get("seg_close_labels"))
        self.check("默认选中「收进托盘」（index 0）",
                   d.get("seg_close_index_on") == 0, d.get("seg_close_index_on"))
        self.check("关掉之后 index 落到「退出」",
                   d.get("seg_close_index_off") == 1, d.get("seg_close_index_off"))
        self.check("点「退出」：开关变 False 且 _state_now() 跟着变",
                   d.get("close_on_after_click") is False
                   and d.get("state_now_close_tray_after_off") is False,
                   (d.get("close_on_after_click"), d.get("state_now_close_tray_after_off")))
        self.check("点回「收进托盘」：开关变回 True",
                   d.get("close_on_after_click_back") is True,
                   d.get("close_on_after_click_back"))
        self.check("tray.py 缺失时这一行没有 Segmented（置灰而不是假装能用）",
                   d.get("close_row_missing_module_seg") is False,
                   d.get("close_row_missing_module_seg"))
        self.check("tray.py 缺失时建面板没抛",
                   d.get("close_row_missing_module_error") == "",
                   d.get("close_row_missing_module_error"))
        self.check("往返 True：写盘的值 / 读回的开关 / _state_now() 三个都一致",
                   d.get("rt_true") == [True, True, True], d.get("rt_true"))
        self.check("往返 False：三个都一致",
                   d.get("rt_false") == [False, False, False], d.get("rt_false"))

    def test_18_close_goes_to_tray(self):
        d = self.data
        self.check("小窗口建好且露着（前置）",
                   d.get("close_mini_error") == "" and d.get("close_mini_visible_before") is True,
                   (d.get("close_mini_error"), d.get("close_mini_visible_before")))
        self.check("关之前面板是露着的", d.get("close_a_prev_visible") is True, d)
        self.check("closeEvent 没抛", d.get("close_a_error") == "", d.get("close_a_error"))
        self.check("close() 被 ignore（返回 False，窗口没真关掉）",
                   d.get("close_a_return") is False, d.get("close_a_return"))
        self.check("关之后面板收起来了（isVisible False）",
                   d.get("close_a_visible") is False, d.get("close_a_visible"))
        self.check("程序没退出：没有排 _maybe_quit",
                   d.get("close_a_maybe_quit_calls") == 0, d.get("close_a_maybe_quit_calls"))
        self.check("QApplication 还在", d.get("close_a_app_alive") is True, d)
        wid = d.get("close_mini_wid")
        self.check("小窗口一点没动：仍然 isVisible 为真、_gone 为 False",
                   d.get("close_a_mini_visible") is True
                   and d.get("close_a_mini_gone_flag") is False, d)
        self.check("_hidden_minis 是空的（没往里面塞小窗口）",
                   list(d.get("close_a_hidden_minis") or []) == [],
                   d.get("close_a_hidden_minis"))
        self.check("minis.json 一字未改：全文相同 + mtime 相同",
                   d.get("close_a_minis_json_same") is True
                   and d.get("close_a_minis_json_mtime_same") is True,
                   (d.get("close_a_minis_json_same"), d.get("close_a_minis_json_mtime_same"),
                    d.get("close_a_minis_json_path")))
        self.check("minis.json 里 open 仍然是 True",
                   d.get("close_a_mini_open_flag") is True, d.get("close_a_mini_open_flag"))
        self.check("托盘图标没被一起收掉（还在通知区）",
                   d.get("close_a_tray_hidden_after") == 0, d.get("close_a_tray_hidden_after"))
        self.check("状态落了盘（临时 ui.json 里 close_tray = True）",
                   d.get("close_a_state_json") is True, d.get("close_a_state_json"))
        self.check("托盘左键能把它叫回来",
                   d.get("close_a_reopen_error") == "" and d.get("close_a_reopen_visible") is True,
                   (d.get("close_a_reopen_error"), d.get("close_a_reopen_visible")))

    def test_19_close_falls_back_to_real_quit(self):
        d = self.data
        off = d.get("close_off_avail") or {}
        self.check("开关关着 + 托盘可用：走真退出（面板收、_maybe_quit 被排上）",
                   off.get("visible") is False and off.get("maybe_quit_calls") == 1, off)
        un = d.get("close_off_unavail") or {}
        self.check("托盘不可用（开关开着也一样）：回退真退出",
                   un.get("visible") is False and un.get("maybe_quit_calls") == 1, un)
        no = d.get("close_on_no_tray_obj") or {}
        self.check("压根没有托盘对象：回退真退出",
                   no.get("visible") is False and no.get("maybe_quit_calls") == 1, no)
        nomod = d.get("close_on_no_module") or {}
        self.check("tray.py 缺失：回退真退出",
                   nomod.get("visible") is False and nomod.get("maybe_quit_calls") == 1, nomod)
        b = d.get("close_b_prev_visible")
        self.check("close_tray=False 时面板本来是露着的", b is True, b)
        self.check("close_tray=False 时 closeEvent 没抛",
                   d.get("close_b_error") == "", d.get("close_b_error"))
        self.check("close_tray=False：真关掉了（close() 返回 True、面板不可见）",
                   d.get("close_b_return") is True and d.get("close_b_visible") is False, d)
        self.check("close_tray=False：和老路径一致，排上了 _maybe_quit",
                   d.get("close_b_maybe_quit_calls") == 1, d.get("close_b_maybe_quit_calls"))
        self.check("close_tray=False：小窗口仍然只 hide，open 标记没被写掉",
                   d.get("close_b_mini_open_flag") is True, d.get("close_b_mini_open_flag"))
        self.check("close_tray=False：小窗口照样进 _hidden_minis",
                   d.get("close_mini_wid") in list(d.get("close_b_hidden_minis") or []),
                   d.get("close_b_hidden_minis"))

    # ------------------------------------------------------------ 关机 / 退出不该改 open
    def test_20_shutdown_keeps_mini_open(self):
        d = self.data
        self.check("前置：探针没报错", d.get("mt_a_error") == "", d.get("mt_a_error"))
        self.check("前置：新建的小窗口账本里 open=True",
                   d.get("mt_a_created_open") is True, d.get("mt_a_created_open"))
        self.check("前置：小窗口真的露出来了",
                   d.get("mt_a_visible_before") is True, d.get("mt_a_visible_before"))
        self.check("模拟关机（不打标记直接 close）：账本 open 仍为 True",
                   d.get("mt_a_open_after") is True, d.get("mt_a_open_after"))
        self.check("minis.json 一个字节都没变",
                   d.get("mt_a_bytes_same") is True, d.get("mt_a_bytes_same"))
        self.check("它还在 open_ids() 里（下次开机照样开回来）",
                   d.get("mt_a_still_in_open_ids") is True, d.get("mt_a_still_in_open_ids"))

    def test_21_user_close_marks_closed(self):
        d = self.data
        self.check("前置：探针没报错", d.get("mt_b_error") == "", d.get("mt_b_error"))
        self.check("前置：新建的小窗口 open=True",
                   d.get("mt_b_open_before") is True, d.get("mt_b_open_before"))
        self.check("用户亲手关（_user_closed=True）：账本 open 变 False",
                   d.get("mt_b_open_after") is False, d.get("mt_b_open_after"))
        self.check("它从 open_ids() 里出去了",
                   d.get("mt_b_not_in_open_ids") is True, d.get("mt_b_not_in_open_ids"))

    def test_22_restore_reopens_shutdown_mini(self):
        d = self.data
        self.check("回归：_restore_minis() 没抛",
                   d.get("mt_c_error") == "", d.get("mt_c_error"))
        self.check("回归：关机前开着的小窗口被重新开出来（_minis 里有它）",
                   d.get("mt_c_in_minis") is True, d.get("mt_c_in_minis"))
        self.check("回归：而且真的 isVisible 为真",
                   d.get("mt_c_visible") is True, d.get("mt_c_visible"))
        self.check("回归：用户关掉的那个不会被开回来",
                   d.get("mt_c_b_not_restored") is True, d.get("mt_c_b_not_restored"))

    # ------------------------------------------------------------ 小窗口任务栏
    def test_16_mini_window_stays_off_taskbar(self):
        """纯函数 _taskbar_ex：小窗口样式必须落在「不上任务栏」那一侧。

        只判算术。任务栏里实不实见得真机看，离屏测不出来（winId 是假句柄）。
        """
        d = self.data
        ex_in = int(d.get("tb_in") or 0)
        ex_out = int(d.get("tb_out") or 0)
        self.check("输入样本确实带 WS_EX_APPWINDOW(0x40000)",
                   bool(ex_in & 0x00040000), hex(ex_in))
        self.check("算完不含 WS_EX_APPWINDOW(0x40000)",
                   d.get("tb_out_no_app") is True, hex(ex_out))
        self.check("算完含 WS_EX_TOOLWINDOW(0x80)",
                   d.get("tb_out_tool") is True, hex(ex_out))
        self.check("算完保留 WS_EX_LAYERED(0x80000)",
                   d.get("tb_out_layered") is True, hex(ex_out))
        self.check("幂等：拿算完的值再算一次结果相同",
                   int(d.get("tb_idem_out") or 0) == ex_out,
                   "%s -> %s" % (hex(ex_out), hex(int(d.get("tb_idem_out") or 0))))
        self.check("输入本来就没有 APPWINDOW 也照旧干净（换成 TOOLWINDOW + 保 LAYERED）",
                   d.get("tb_plain_clean") is True,
                   hex(int(d.get("tb_plain_out") or 0)))
        st_st = int(d.get("tb_style_st") or 0)
        self.check("带 style 时摘掉 WS_MINIMIZEBOX(0x20000)",
                   d.get("tb_style_no_minbox") is True, hex(st_st))
        self.check("带 style 时 ex 规则不变",
                   int(d.get("tb_style_ex") or 0) == ex_out,
                   hex(int(d.get("tb_style_ex") or 0)))


if __name__ == "__main__":
    if PROBE_FLAG in sys.argv:
        _probe()
    else:
        unittest.main(verbosity=2)
