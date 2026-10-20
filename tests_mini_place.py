# -*- coding: utf-8 -*-
"""小窗口定位缺陷离屏自测：#4 新建即沉底摸不到 / #5 小窗口互相堆叠。

为何走子进程：QT_QPA_PLATFORM=offscreen 必须在 import PySide6 之前进环境。
    全部探针都在临时目录里跑，APPDATA / DESKBOX_* 一律指向临时文件，
    绝不碰用户真实的 APPDATA\\DeskBox，也绝不 show() 到屏幕上。

跑法：
    python -m unittest tests_mini_place -v
"""
import json
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
PROBE_FLAG = "--probe"
PROBE_PREFIX = "MINIPLACE_PROBE_JSON "


def _probe():
    import tempfile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    tmp = tempfile.mkdtemp(prefix="dsh_miniplace_")
    for key in ("UI", "MINIS", "GROUPS", "CATS", "HIDEICONS"):
        os.environ["DESKBOX_%s_JSON" % key] = os.path.join(tmp, key.lower() + ".json")
    os.environ["DESKBOX_ROOT"] = os.path.join(tmp, "root")
    os.makedirs(os.environ["DESKBOX_ROOT"], exist_ok=True)

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    import deskbox.ui_mock as U

    app = QApplication.instance() or QApplication([])
    U.NO_HIDE_PROMPT = True
    out = {"platform": app.platformName()}

    def geom(m):
        r = m.frameGeometry()
        return [r.x(), r.y(), r.width(), r.height()]

    def overlap_matrix(ms):
        """两两相交判定：True = 相交（有重叠）"""
        rows = []
        for i in range(len(ms)):
            row = []
            for j in range(len(ms)):
                if i == j:
                    row.append(None)
                    continue
                row.append(bool(ms[i].frameGeometry().intersects(ms[j].frameGeometry())))
            rows.append(row)
        return rows

    def has_bottom(m):
        return bool(m.windowFlags() & Qt.WindowStaysOnBottomHint)

    out["fatal"] = ""
    try:
        w = U.MainWindow()
        w.show()
        app.processEvents()

        # ------------------------------------------------ #4 新建即露头
        m1 = w._new_mini()
        app.processEvents()
        out["m1_flags_bottom_after_new"] = has_bottom(m1)
        out["m1_raised_after_new"] = bool(getattr(m1, "_raised", False))
        out["raise_ms"] = int(getattr(type(m1), "RAISE_MS", 0))
        out["m1_visible_after_new"] = bool(m1.isVisible())
        # 露头期真的 _keep_bottom 必须不碰 SetWindowPos：换掉模块里的 ctypes 记录调用
        import types as _t
        real_ctypes = U.ctypes
        setpos = []

        def fake_ctypes():
            return _t.SimpleNamespace(
                windll=_t.SimpleNamespace(user32=_t.SimpleNamespace(
                    SetWindowPos=lambda *a: setpos.append(a))))

        U.ctypes = fake_ctypes()
        m1._keep_bottom()
        out["setpos_while_raised"] = len(setpos)

        # 恢复函数可单调：直接调 _drop_back()
        m1._drop_back()
        app.processEvents()
        out["m1_flags_bottom_after_drop"] = has_bottom(m1)
        out["m1_raised_after_drop"] = bool(getattr(m1, "_raised", False))
        setpos2 = []
        U.ctypes = _t.SimpleNamespace(
            windll=_t.SimpleNamespace(user32=_t.SimpleNamespace(
                SetWindowPos=lambda *a: setpos2.append(a))))
        m1._keep_bottom()
        out["setpos_after_drop"] = len(setpos2)
        out["m1_visible_after_drop"] = bool(m1.isVisible())
        U.ctypes = real_ctypes

        # 定时器真实路径：再走一次 _raise_for_setup，qWait 等它自己恢复
        m1._raise_for_setup()
        app.processEvents()
        out["m1_raised_before_timer"] = bool(getattr(m1, "_raised", False))
        QTest.qWait(int(getattr(type(m1), "RAISE_MS", 0)) + 700)
        app.processEvents()
        out["m1_raised_after_timer"] = bool(getattr(m1, "_raised", False))
        out["m1_flags_bottom_after_timer"] = has_bottom(m1)

        # 已存在分支不受影响：再开一次同一个 wid，不能重新露头
        same = w._open_mini(m1.wid)
        app.processEvents()
        out["existing_same_object"] = same is m1
        out["existing_raised"] = bool(getattr(same, "_raised", False))
        out["existing_flags_bottom"] = has_bottom(same)

        # ------------------------------------------------ #5 落位避让
        m2 = w._new_mini()
        m3 = w._new_mini()
        app.processEvents()
        out["geom_after_new"] = [geom(m1), geom(m2), geom(m3)]
        out["overlap_after_new"] = overlap_matrix([m1, m2, m3])

        # 把二号搬到跟一号几乎完全重合的位置，再调避让
        m2.move(m1.pos() + QPoint(4, 4))
        app.processEvents()
        out["geom_m2_before_avoid"] = geom(m2)
        out["overlap_before_avoid"] = overlap_matrix([m1, m2, m3])
        m2._avoid_overlap()
        app.processEvents()
        out["geom_m2_after_avoid"] = geom(m2)
        out["overlap_after_avoid"] = overlap_matrix([m1, m2, m3])

        # 完全重合（0,0 偏移）也要推开
        m3.move(m1.pos())
        app.processEvents()
        out["overlap_m3_exact_before"] = overlap_matrix([m1, m2, m3])
        m3._avoid_overlap()
        app.processEvents()
        out["overlap_m3_exact_after"] = overlap_matrix([m1, m2, m3])

        # 屏幕可用区检查
        av = m2._screen_rect()
        out["avail"] = None if av is None else [av.x(), av.y(), av.width(), av.height()]
        inside = []
        for m in (m1, m2, m3):
            r = m.frameGeometry()
            inside.append(bool(av is not None and av.contains(r)))
        out["all_inside_screen"] = all(inside)
        out["inside_flags"] = inside

        # 关掉「自动粘连」时仍要避让（重叠是硬缺陷，不属于粘连）
        w._snap_on = False
        m3.move(m1.pos() + QPoint(3, 0))
        app.processEvents()
        m3._avoid_overlap()
        app.processEvents()
        out["overlap_snap_off"] = overlap_matrix([m1, m2, m3])
        w._snap_on = True

        # 相邻贴死不重叠的既有行为不能被破坏：两块并排贴着，避让不许把它们推开
        m1.resize(300, 240)
        m2.resize(300, 240)
        m1.move(100, 100)
        m2.move(m1.frameGeometry().right() + 1, 100)
        app.processEvents()
        before = [geom(m1), geom(m2)]
        m2._avoid_overlap()
        app.processEvents()
        out["touching_before"] = before
        out["touching_after"] = [geom(m1), geom(m2)]
        out["touching_kept"] = before == [geom(m1), geom(m2)]
    except Exception as exc:          # noqa: BLE001
        import traceback
        out["fatal"] = "%s: %s" % (type(exc).__name__, exc)
        out["traceback"] = traceback.format_exc()[-1500:]

    print(PROBE_PREFIX + json.dumps(out, ensure_ascii=False))
    os._exit(0)


def _run_probe():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
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


class MiniPlaceTest(unittest.TestCase):
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
        print(("PASS - " if cond else "FAIL - ") + name + ("" if cond else "  << %s" % (detail,)))
        self.assertTrue(cond, "%s %s" % (name, detail))

    def test_01_offscreen(self):
        self.check("子进程跑在 offscreen", self.data.get("platform") == "offscreen",
                   self.data.get("platform"))

    # ---------------------------------------------------------------- #4
    def test_02_new_mini_is_raised_not_bottom(self):
        d = self.data
        self.check("构造过程没抛", d.get("fatal") == "", d.get("traceback") or d.get("fatal"))
        self.check("新建后 flags **不含** WindowStaysOnBottomHint",
                   d.get("m1_flags_bottom_after_new") is False, d.get("m1_flags_bottom_after_new"))
        self.check("新建后 _raised 为 True", d.get("m1_raised_after_new") is True,
                   d.get("m1_raised_after_new"))
        self.check("露头期 _keep_bottom 真的没碰 SetWindowPos（0 次）",
                   d.get("setpos_while_raised") == 0, d.get("setpos_while_raised"))
        self.check("新建后窗口是可见的（setWindowFlags 后补了 show）",
                   d.get("m1_visible_after_new") is True, d.get("m1_visible_after_new"))
        self.check("露头时长 2500ms 且不超过 4000ms",
                   0 < int(d.get("raise_ms") or 0) <= 4000, d.get("raise_ms"))

    def test_03_drop_back_restores_bottom(self):
        d = self.data
        self.check("_drop_back() 后 flags 含 WindowStaysOnBottomHint",
                   d.get("m1_flags_bottom_after_drop") is True, d.get("m1_flags_bottom_after_drop"))
        self.check("_drop_back() 后 _raised 为 False",
                   d.get("m1_raised_after_drop") is False, d.get("m1_raised_after_drop"))
        self.check("_drop_back() 后 _keep_bottom 恢复动作（SetWindowPos 1 次）",
                   d.get("setpos_after_drop") == 1, d.get("setpos_after_drop"))
        self.check("_drop_back() 后窗口仍然可见（setWindowFlags 藏起来后补了 show）",
                   d.get("m1_visible_after_drop") is True, d.get("m1_visible_after_drop"))

    def test_04_timer_path_really_restores(self):
        d = self.data
        self.check("定时器触发前 _raised 为 True", d.get("m1_raised_before_timer") is True,
                   d.get("m1_raised_before_timer"))
        self.check("QTest.qWait 过 RAISE_MS 后 _raised 自动变 False",
                   d.get("m1_raised_after_timer") is False, d.get("m1_raised_after_timer"))
        self.check("定时器恢复后 flags 含 WindowStaysOnBottomHint",
                   d.get("m1_flags_bottom_after_timer") is True,
                   d.get("m1_flags_bottom_after_timer"))

    def test_05_existing_window_branch_untouched(self):
        d = self.data
        self.check("再开同一个 wid 返回同一个对象", d.get("existing_same_object") is True, d)
        self.check("已存在的窗口不会被重新露头（_raised False）",
                   d.get("existing_raised") is False, d.get("existing_raised"))
        self.check("已存在的窗口仍在桌面层（flags 含 StaysOnBottom）",
                   d.get("existing_flags_bottom") is True, d.get("existing_flags_bottom"))

    # ---------------------------------------------------------------- #5
    def test_06_new_minis_do_not_overlap(self):
        d = self.data
        self.check("三个新建 mini 的 frameGeometry 互不相交",
                   not any(any(x for x in row if x) for row in d.get("overlap_after_new") or []),
                   d.get("overlap_after_new"))
        self.check("都在屏幕可用区内", d.get("all_inside_screen") is True, d.get("inside_flags"))

    def test_07_avoid_overlap_pushes_apart(self):
        d = self.data
        before = d.get("overlap_before_avoid") or []
        after = d.get("overlap_after_avoid") or []
        self.check("构造出的是真重叠（前置条件成立）",
                   any(any(x for x in row if x) for row in before), before)
        self.check("_avoid_overlap() 后两两不再相交",
                   not any(any(x for x in row if x) for row in after), after)
        self.check("位置确实动了",
                   d.get("geom_m2_before_avoid") != d.get("geom_m2_after_avoid"), d)
        self.check("完全重合也能推开",
                   not any(any(x for x in row if x)
                           for row in d.get("overlap_m3_exact_after") or []),
                   d.get("overlap_m3_exact_after"))

    def test_08_avoid_overlap_respects_snap_off_and_touching(self):
        d = self.data
        self.check("关掉「小窗口自动粘连」仍然避让重叠",
                   not any(any(x for x in row if x)
                           for row in d.get("overlap_snap_off") or []),
                   d.get("overlap_snap_off"))
        self.check("并排贴死不重叠的两块，避让不把它们推开（磁吸观感保住）",
                   d.get("touching_kept") is True,
                   [d.get("touching_before"), d.get("touching_after")])


if __name__ == "__main__":
    if PROBE_FLAG in sys.argv:
        _probe()
    else:
        unittest.main(verbosity=2)
