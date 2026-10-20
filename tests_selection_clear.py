# -*- coding: utf-8 -*-
"""#6 回归：点了图标之后那块选中底色，双击打开 / 点到别处之后必须退掉。

为什么要走子进程：QT_QPA_PLATFORM=offscreen 必须赶在 import PySide6 之前进环境，
    同进程里若已有 windows 平台的 QApplication，建 MainWindow 会真往屏幕上弹面板。

绝不启动任何真实文件：STORE.open_item 被换成记录型假对象，只数调用次数。
账本与状态全部落在临时目录（APPDATA / DESKBOX_* 全部重定向）。

跑法：
    python tests_selection_clear.py
    python -m unittest tests_selection_clear -v
"""
import json
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

PROBE_FLAG = "--probe"
PREFIX = "SEL_CLEAR_PROBE_JSON "


def _probe():
    """子进程入口：离屏实测选中态清除，结果打成一行 JSON。"""
    import tempfile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"      # 必须在 import PySide6 之前设
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    sys.path.insert(0, REPO)

    tmp = tempfile.mkdtemp(prefix="dsh_selclear_")
    for key in ("UI", "MINIS", "GROUPS", "CATS", "HIDEICONS"):
        os.environ["DESKBOX_%s_JSON" % key] = os.path.join(tmp, key.lower() + ".json")
    os.environ["DESKBOX_ROOT"] = os.path.join(tmp, "root")
    os.makedirs(os.environ["DESKBOX_ROOT"], exist_ok=True)
    os.environ["APPDATA"] = os.path.join(tmp, "appdata")
    os.environ["LOCALAPPDATA"] = os.path.join(tmp, "localappdata")
    for d in (os.environ["APPDATA"], os.environ["LOCALAPPDATA"]):
        os.makedirs(d, exist_ok=True)

    out = {}
    from PySide6.QtCore import QEvent, QRect, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import (QApplication, QStyle,
                                   QStyleOptionViewItem)

    import deskbox.ui_mock as U

    app = QApplication.instance() or QApplication([])
    U.NO_HIDE_PROMPT = True

    # 打开动作换成计数桩：绝不真的去启动用户桌面的文件
    opened = []
    real_open = getattr(U.STORE, "open_item", None) if U.STORE is not None else None
    if U.STORE is not None:
        U.STORE.open_item = lambda p, _a=opened: _a.append(p) or True

    out["construct_error"] = ""
    w = None
    m = None
    try:
        w = U.MainWindow()
        app.processEvents()
        m = w._new_mini()
        app.processEvents()
    except Exception as exc:          # noqa: BLE001
        out["construct_error"] = "%s: %s" % (type(exc).__name__, exc)
        if real_open is not None:
            U.STORE.open_item = real_open
        print(PREFIX + json.dumps(out, ensure_ascii=False))
        return

    # 小窗口新建出来是空的：先塞两条临时探针文件进去（全在临时目录，不碰真实桌面），
    # 网格里有了 item，双击 / 失活这两条路径才有东西可选中、可验证清除。
    out["seed_error"] = ""
    seeds = []
    if m is not None:
        try:
            for name in ("seed_note.txt", "seed_pack.zip"):
                p = os.path.join(tmp, name)
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write("seed")
                seeds.append(p)
            U.MB.add_paths(m.wid, seeds)
            m._reload()
            app.processEvents()
        except Exception as exc:      # noqa: BLE001
            out["seed_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["seed_paths"] = seeds
    out["seed_exists"] = [os.path.isfile(p) for p in seeds]

    out["platform"] = app.platformName()
    out["mini_created"] = m is not None
    out["main_items"] = w.grid.count()
    out["mini_items"] = m.grid.count() if m is not None else -1

    # ---------------------------------------------------------- 小窗口双击
    if m is not None and m.grid.count():
        m.grid.item(0).setSelected(True)
        out["mini_sel_before"] = len(m.grid.selectedItems())
        out["opened_before_mini"] = len(opened)
        m._on_double(m.grid.item(0))
        app.processEvents()
        out["mini_sel_after"] = len(m.grid.selectedItems())
        out["open_called_mini"] = len(opened) - out["opened_before_mini"]
    else:
        out["mini_sel_before"] = -1
        out["mini_sel_after"] = -1
        out["open_called_mini"] = 0

    # ---------------------------------------------------------- 主面板双击
    if w.grid.count():
        w.grid.item(0).setSelected(True)
        out["main_sel_before"] = len(w.grid.selectedItems())
        out["opened_before_main"] = len(opened)
        w._on_double(w.grid.item(0))
        app.processEvents()
        out["main_sel_after"] = len(w.grid.selectedItems())
        out["open_called_main"] = len(opened) - out["opened_before_main"]
    else:
        out["main_sel_before"] = -1
        out["main_sel_after"] = -1
        out["open_called_main"] = 0

    # ---------------------------------------------------------- 窗口失活
    # 离屏平台里 isActiveWindow() 恒为 True，真失活造不出来；
    # 这里把判定临时改成「失活」，直接走 changeEvent 的那条分支。
    if m is not None and m.grid.count():
        m.grid.item(0).setSelected(True)
        old = m.isActiveWindow
        m.isActiveWindow = lambda: False
        try:
            m.changeEvent(QEvent(QEvent.ActivationChange))
        finally:
            m.isActiveWindow = old
        app.processEvents()
        out["mini_sel_after_deactivate"] = len(m.grid.selectedItems())
        # 复原后正常激活路径不能崩
        m.changeEvent(QEvent(QEvent.ActivationChange))
        app.processEvents()
        out["mini_alive_after_reactivate"] = bool(m.isVisible() or True)
    else:
        out["mini_sel_after_deactivate"] = -1

    if w.grid.count():
        w.grid.item(0).setSelected(True)
        old = w.isActiveWindow
        w.isActiveWindow = lambda: False
        try:
            w.changeEvent(QEvent(QEvent.ActivationChange))
        finally:
            w.isActiveWindow = old
        app.processEvents()
        out["main_sel_after_deactivate"] = len(w.grid.selectedItems())
        w.changeEvent(QEvent(QEvent.ActivationChange))
        app.processEvents()
    else:
        out["main_sel_after_deactivate"] = -1

    # ---------------------------------------------------------- 委托的真实判定
    def opaque(state):
        """按给定 state 走一遍 delegate.paint，数不透明像素（选中会多画一块底色）。"""
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 96, 96)
        opt.state = state
        pm = QPixmap(96, 96)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        dele.paint(p, opt, w.grid.model().index(0, 0))
        p.end()
        img = pm.toImage()
        n = 0
        for y in range(0, 96, 2):
            for x in range(0, 96, 2):
                if img.pixelColor(x, y).alpha() > 0:
                    n += 1
        return n

    dele = U.IconDelegate(w.grid)
    out["opaque_plain"] = opaque(QStyle.StateFlag.State_Enabled)
    out["opaque_selected"] = opaque(QStyle.StateFlag.State_Enabled
                                    | QStyle.StateFlag.State_Selected)

    out["open_total"] = len(opened)
    out["probe_ok"] = True
    if real_open is not None:
        U.STORE.open_item = real_open
    try:
        if m is not None:
            m.hide()
        w.hide()
    except Exception:                 # noqa: BLE001
        pass
    print(PREFIX + json.dumps(out, ensure_ascii=False))


_RUN = {}


def _run_probe():
    if "d" in _RUN:
        return _RUN["d"]
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-u", os.path.abspath(__file__), PROBE_FLAG],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=REPO, timeout=600)
    data = None
    for line in (p.stdout or "").splitlines():
        if line.startswith(PREFIX):
            data = json.loads(line[len(PREFIX):])
    if data is None:
        raise AssertionError("离屏探针没吐出结果\nSTDOUT:\n%s\nSTDERR:\n%s"
                             % ((p.stdout or "")[-4000:], (p.stderr or "")[-4000:]))
    _RUN["d"] = data
    return data


@unittest.skipUnless(sys.platform.startswith("win"), "这条链只在 Windows 上有意义")
class SelectionClearTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = _run_probe()

    def test_00_probe_ran(self):
        self.assertEqual(self.d.get("construct_error"), "", self.d)
        self.assertTrue(self.d.get("probe_ok"), self.d)
        self.assertEqual(self.d.get("platform"), "offscreen")
        self.assertTrue(self.d.get("mini_created"))
        # 前置条件：两条临时探针文件已放进小窗口网格（否则下面两条用例无从验证）
        self.assertEqual(self.d.get("seed_error"), "", self.d.get("seed_error"))
        self.assertEqual(len(self.d.get("seed_paths") or []), 2, self.d)
        self.assertEqual(self.d.get("seed_exists"), [True, True], self.d)
        self.assertEqual(self.d.get("mini_items"), 2, self.d)

    def test_01_mini_double_click_clears(self):
        self.assertEqual(self.d.get("mini_items"), 2, self.d)
        self.assertEqual(self.d.get("mini_sel_before"), 1)
        self.assertEqual(self.d.get("open_called_mini"), 1)   # 打开动作确实跑了（桩）
        self.assertEqual(self.d.get("mini_sel_after"), 0)

    def test_02_main_double_click_clears(self):
        self.assertEqual(self.d.get("main_sel_before"), 1)
        self.assertEqual(self.d.get("open_called_main"), 1)
        self.assertEqual(self.d.get("main_sel_after"), 0)

    def test_03_mini_deactivate_clears(self):
        self.assertEqual(self.d.get("mini_items"), 2, self.d)
        self.assertEqual(self.d.get("mini_sel_after_deactivate"), 0)
        self.assertTrue(self.d.get("mini_alive_after_reactivate"))

    def test_04_main_deactivate_clears(self):
        self.assertEqual(self.d.get("main_sel_after_deactivate"), 0)

    def test_05_delegate_bottom_band_comes_from_selection(self):
        """委托那块底色确实由 selected 状态决定：选中态多画出一大片不透明像素。"""
        plain = self.d.get("opaque_plain")
        sel = self.d.get("opaque_selected")
        self.assertGreater(plain, 0)
        self.assertGreater(sel, plain, (plain, sel))


if __name__ == "__main__":
    if PROBE_FLAG in sys.argv:
        _probe()
    else:
        unittest.main(verbosity=2)
