# -*- coding: utf-8 -*-
"""#2 回归：手动拖动调换图标位置（小窗口 + 主面板，顺序要能持久化）。

为什么要走子进程：QT_QPA_PLATFORM=offscreen 必须赶在 import PySide6 之前进环境，
    否则建 MainWindow 会真的往用户屏幕上弹面板。断言一律在离屏子进程里做。

真相只有一个：只在临时目录造探针文件，账本与状态全部重定向到临时目录，
    绝不移动/复制/删除任何真实文件。

跑法：
    python tests_reorder.py
    python -m unittest tests_reorder -v
"""
import json
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

PROBE_FLAG = "--probe"
PREFIX = "REORDER_PROBE_JSON "


def _probe():
    """子进程入口：离屏实测「拖一下换位置」的完整链路，结果打成一行 JSON。"""
    import tempfile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"      # 必须在 import PySide6 之前设
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    sys.path.insert(0, REPO)

    tmp = tempfile.mkdtemp(prefix="dsh_reorder_")
    for key in ("UI", "MINIS", "GROUPS", "CATS", "HIDEICONS"):
        os.environ["DESKBOX_%s_JSON" % key] = os.path.join(tmp, key.lower() + ".json")
    os.environ["DESKBOX_ROOT"] = os.path.join(tmp, "root")
    os.makedirs(os.environ["DESKBOX_ROOT"], exist_ok=True)
    os.environ["APPDATA"] = os.path.join(tmp, "appdata")
    os.environ["LOCALAPPDATA"] = os.path.join(tmp, "localappdata")
    for d in (os.environ["APPDATA"], os.environ["LOCALAPPDATA"]):
        os.makedirs(d, exist_ok=True)

    out = {"tmp": tmp}
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QApplication

    import deskbox.ui_mock as U

    app = QApplication.instance() or QApplication([])
    U.NO_HIDE_PROMPT = True                # 压掉启动自检弹窗
    nk = os.path.normcase

    def _mk(name, text="x"):
        d = os.path.join(tmp, "probe")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def _mime(paths):
        md = QMimeData()
        md.setData(U.DRAG_MIME, ("\n".join(paths)).encode("utf-8"))
        return md

    def _internal_drop(grid, paths, target_item):
        """模拟网格内部拖拽：拖到 target_item 的矩形中心松手。

        drop 会触发 _reload() 把 item 全换掉，所以落点信息必须在投递前取完。
        """
        md = _mime(paths)
        rect = grid.visualItemRect(target_item)
        tgt_path = target_item.data(Qt.UserRole)
        pos = QPointF(rect.center())
        hit = grid.itemAt(rect.center())
        hit_path = hit.data(Qt.UserRole) if hit is not None else None
        info = {"rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                "hit_same": bool(hit_path is not None and tgt_path is not None
                                 and os.path.normcase(str(hit_path))
                                 == os.path.normcase(str(tgt_path))),
                "hit_path": hit_path, "target_path": tgt_path}
        ev = QDropEvent(pos, Qt.MoveAction, md, Qt.LeftButton, Qt.NoModifier)
        grid.dropEvent(ev)
        app.processEvents()
        return info

    def _external_drop(grid, paths):
        """外部拖入（资源管理器那条 QUrl 路线）。"""
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        pos = QPoint(10, 10)
        grid.dragEnterEvent(QDragEnterEvent(pos, Qt.CopyAction, md,
                                           Qt.LeftButton, Qt.NoModifier))
        grid.dropEvent(QDropEvent(pos, Qt.CopyAction, md,
                                  Qt.LeftButton, Qt.NoModifier))
        app.processEvents()

    def _grid_paths(grid):
        return [grid.item(i).data(Qt.UserRole) for i in range(grid.count())]

    def _only(paths, keep):
        return [p for p in paths if nk(p) in keep]

    out["construct_error"] = ""
    out["platform"] = app.platformName()
    try:
        w = U.MainWindow()
        w.resize(1000, 700)
        w.show()
        app.processEvents()
        m = w._new_mini()
        m.resize(420, 300)
        m.show()
        app.processEvents()
    except Exception as exc:          # noqa: BLE001
        out["construct_error"] = "%s: %s" % (type(exc).__name__, exc)
        print(PREFIX + json.dumps(out, ensure_ascii=False))
        return
    out["mini_created"] = m is not None
    if m is None:
        print(PREFIX + json.dumps(out, ensure_ascii=False))
        return

    # ---------------------------------------------------------- 小窗口
    p1, p2, p3 = _mk("aaa_first.txt"), _mk("bbb_mid.txt"), _mk("ccc_last.txt")
    U.MB.add_paths(m.wid, [p1, p2, p3])
    m._reload()
    app.processEvents()
    out["mini_before"] = _grid_paths(m.grid)
    out["mini_scope"] = U._order_scope_mini(m.wid)

    # 路径A：真丢一个 QDropEvent（第 1 个拖到第 3 个上）
    if m.grid.count() >= 3:
        out["mini_drop_ev"] = _internal_drop(m.grid, [p1], m.grid.item(2))
    else:
        out["mini_drop_ev"] = {"hit_same": False, "hit_path": None, "rect": None}
    out["mini_after_drop"] = _grid_paths(m.grid)

    # 路径B：直接调交换函数（第 3 个再拖回第 1 个 → 回到原序）
    m._on_reorder([p3], p1)
    app.processEvents()
    out["mini_after_manual"] = _grid_paths(m.grid)

    # 重新 _reload() / 缩小窗口，顺序不变
    m._on_reorder([p1], p3)
    app.processEvents()
    out["mini_after_drop2"] = _grid_paths(m.grid)
    m._reload()
    m.resize(260, 200)
    app.processEvents()
    m._reload()
    app.processEvents()
    out["mini_after_reload"] = _grid_paths(m.grid)

    # 重启：账本 + ui.json 里读回来
    try:
        with open(os.environ["DESKBOX_UI_JSON"], "r", encoding="utf-8") as fh:
            ui = json.load(fh)
    except Exception as exc:          # noqa: BLE001
        ui = {"_error": "%s: %s" % (type(exc).__name__, exc)}
    out["ui_order_mini"] = (ui.get("order") or {}).get(out["mini_scope"])
    try:
        w2 = U.MainWindow()
        w2.resize(1000, 700)
        m2 = w2._open_mini(m.wid)
        app.processEvents()
        out["mini_after_restart"] = _grid_paths(m2.grid) if m2 is not None else None
        out["restart_error"] = ""
    except Exception as exc:          # noqa: BLE001
        out["mini_after_restart"] = None
        out["restart_error"] = "%s: %s" % (type(exc).__name__, exc)

    def _reset(order):
        """把顺序表按给定次序钉死，让后面的断言不依赖前面的操作史。"""
        m._save_order(list(order))
        m._reload()
        app.processEvents()

    # 多选整块搬：把 [c,b] 整块插到 a 上 —— 相对顺序不许倒过来
    _reset([p1, p2, p3])
    m._on_reorder([p3, p2], p1)
    app.processEvents()
    out["mini_multi"] = _grid_paths(m.grid)
    _reset([p1, p2, p3])
    out["mini_multi_restore"] = _grid_paths(m.grid)

    # 拖到空白处 → 移到末尾
    m._on_reorder([p1], None)
    app.processEvents()
    out["mini_to_end"] = _grid_paths(m.grid)
    _reset([p1, p2, p3])
    out["mini_to_end_restore"] = _grid_paths(m.grid)

    # ---------------------------------------------------------- 坏 ui.json 容错
    bad = os.path.join(tmp, "bad_ui.json")
    with open(bad, "w", encoding="utf-8") as fh:
        json.dump({"order": "not-a-dict", "x": 70}, fh)
    old = os.environ["DESKBOX_UI_JSON"]
    os.environ["DESKBOX_UI_JSON"] = bad
    try:
        st = U._load_state()
        out["bad_state_order"] = st.get("order")
        out["bad_state_ok"] = isinstance(st.get("order"), dict)
    except Exception as exc:          # noqa: BLE001
        out["bad_state_order"] = "%s: %s" % (type(exc).__name__, exc)
        out["bad_state_ok"] = False
    with open(bad, "w", encoding="utf-8") as fh:
        json.dump({"order": {"main:全部": "nope", "mini:x": [1, 2, "ok.txt"]}}, fh)
    try:
        st2 = U._load_state()
        out["mixed_order"] = st2.get("order")
    except Exception as exc:          # noqa: BLE001
        out["mixed_order"] = "%s: %s" % (type(exc).__name__, exc)
    os.environ["DESKBOX_UI_JSON"] = old

    # ---------------------------------------------------------- 主面板
    q1, q2, q3 = _mk("aaa_m1.txt"), _mk("bbb_m2.txt"), _mk("ccc_m3.txt")
    w._ingest([q1, q2, q3], None)
    w._reload()
    w.side.setCurrentRow(0)               # 「全部」
    w._filter(0)
    app.processEvents()
    keep = {nk(q1), nk(q2), nk(q3)}
    out["main_scope"] = U._order_scope_main("全部")
    out["main_before"] = _only(_grid_paths(w.grid), keep)

    idx = {}
    for i in range(w.grid.count()):
        p = w.grid.item(i).data(Qt.UserRole)
        if p and nk(p) in keep:
            idx[nk(p)] = i
    out["main_index"] = idx
    if nk(q1) in idx and nk(q3) in idx:
        out["main_drop_ev"] = _internal_drop(w.grid, [q1], w.grid.item(idx[nk(q3)]))
    else:
        out["main_drop_ev"] = {"hit_same": False, "hit_path": None, "rect": None}
    out["main_after_drop"] = _only(_grid_paths(w.grid), keep)

    try:
        with open(os.environ["DESKBOX_UI_JSON"], "r", encoding="utf-8") as fh:
            ui2 = json.load(fh)
    except Exception as exc:          # noqa: BLE001
        ui2 = {"_error": "%s: %s" % (type(exc).__name__, exc)}
    tbl = (ui2.get("order") or {}).get("main:全部") or []
    out["ui_order_main"] = [p for p in tbl if nk(p) in keep]
    out["ui_order_main_head"] = out["ui_order_main"][:1]

    # 切分类再切回来，顺序不丢
    rows = [w.side.item(i).data(Qt.UserRole) for i in range(w.side.count())]
    out["side_names"] = rows
    if U.UNSORTED in rows:
        w.side.setCurrentRow(rows.index(U.UNSORTED))
        w._filter(rows.index(U.UNSORTED))
        app.processEvents()
        out["main_in_unsorted"] = _only(_grid_paths(w.grid), keep)
    w.side.setCurrentRow(0)
    w._filter(0)
    app.processEvents()
    out["main_after_switch"] = _only(_grid_paths(w.grid), keep)

    # 顺序表里没有的路径：排在后面，不能消失
    q4 = _mk("ddd_extra.txt")
    w._ingest([q4], None)
    w._reload()
    w.side.setCurrentRow(0)
    w._filter(0)
    app.processEvents()
    keep4 = keep | {nk(q4)}
    out["main_with_extra"] = _only(_grid_paths(w.grid), keep4)
    out["extra_present"] = nk(q4) in {nk(p) for p in _grid_paths(w.grid)}

    # ---------------------------------------------------------- 外部拖入 / 侧栏
    ext = _mk("incoming_ext.txt")
    before = m.grid.count()
    grids_before = _grid_paths(m.grid)
    _external_drop(m.grid, [ext])
    grids_after = _grid_paths(m.grid)
    m._reload()
    app.processEvents()
    out["ext_before"] = before
    out["ext_after_emit"] = len(grids_after)
    out["ext_in_mini"] = nk(ext) in {nk(p) for p in _grid_paths(m.grid)}
    out["ext_in_main"] = nk(ext) in {nk(r.path) for r in w.desktop_rows}
    try:
        book = U.GB.load() or {}
    except Exception:                 # noqa: BLE001
        book = {}
    out["ext_in_book"] = nk(ext) in book
    out["mini_count_after_ext"] = m.grid.count()
    out["ext_kept_prev"] = all(nk(p) in {nk(x) for x in _grid_paths(m.grid)}
                               for p in grids_before)

    # 拖到侧栏改分类仍然工作
    try:
        w._on_side_drop(U.UNSORTED, [q1], internal=True)
        book2 = U.GB.load() or {}
        out["side_group"] = (book2.get(nk(q1)) or {}).get("group")
    except Exception as exc:          # noqa: BLE001
        out["side_group"] = "%s: %s" % (type(exc).__name__, exc)
    out["side_error"] = ""

    out["probe_ok"] = True
    print(PREFIX + json.dumps(out, ensure_ascii=False))


class ReorderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ)
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                    "QT_QPA_PLATFORM": "offscreen",
                    "QT_QPA_FONTDIR": r"C:\Windows\Fonts"})
        env.pop("DESKBOX_UI_JSON", None)
        p = subprocess.run([sys.executable, os.path.abspath(__file__), PROBE_FLAG],
                           cwd=REPO, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        cls.raw = (p.stdout or "") + (p.stderr or "")
        cls.d = {}
        for line in (p.stdout or "").splitlines():
            if line.startswith(PREFIX):
                cls.d = json.loads(line[len(PREFIX):])
                break

    def test_00_probe_ran(self):
        self.assertEqual(self.d.get("construct_error"), "", self.d.get("construct_error"))
        self.assertTrue(self.d.get("probe_ok"), self.raw_tail())
        self.assertEqual(self.d.get("platform"), "offscreen")
        self.assertTrue(self.d.get("mini_created"))
        self.assertEqual(self.d.get("restart_error"), "")
        # 两条拖拽路径都真的命中了落点 item
        self.assertTrue(self.d.get("mini_drop_ev", {}).get("hit_same"),
                        self.d.get("mini_drop_ev"))
        self.assertTrue(self.d.get("main_drop_ev", {}).get("hit_same"),
                        self.d.get("main_drop_ev"))

    def raw_tail(self):
        return (getattr(self, "raw", "") or "")[-2000:]

    def test_01_mini_drop_swaps(self):
        b = [os.path.normcase(p) for p in self.d["mini_before"]]
        a = [os.path.normcase(p) for p in self.d["mini_after_drop"]]
        self.assertEqual(b, [os.path.normcase(p) for p in self.d["mini_before"]])
        self.assertEqual(len(a), 3, a)
        self.assertEqual([os.path.basename(p) for p in a],
                         ["ccc_last.txt", "bbb_mid.txt", "aaa_first.txt"])

    def test_02_mini_manual_api_swaps(self):
        a = [os.path.basename(p) for p in self.d["mini_after_manual"]]
        self.assertEqual(a, ["aaa_first.txt", "bbb_mid.txt", "ccc_last.txt"])
        b = [os.path.basename(p) for p in self.d["mini_after_drop2"]]
        self.assertEqual(b, ["ccc_last.txt", "bbb_mid.txt", "aaa_first.txt"])

    def test_03_mini_reload_and_resize_keep_order(self):
        a = [os.path.basename(p) for p in self.d["mini_after_reload"]]
        self.assertEqual(a, ["ccc_last.txt", "bbb_mid.txt", "aaa_first.txt"])
        self.assertEqual([os.path.basename(p) for p in self.d["ui_order_mini"]],
                         ["ccc_last.txt", "bbb_mid.txt", "aaa_first.txt"])

    def test_04_mini_persists_after_restart(self):
        self.assertIsNotNone(self.d.get("mini_after_restart"))
        a = [os.path.basename(p) for p in self.d["mini_after_restart"]]
        self.assertEqual(a, ["ccc_last.txt", "bbb_mid.txt", "aaa_first.txt"])

    def test_05_main_drop_swaps_and_saves(self):
        self.assertEqual([os.path.basename(p) for p in self.d["main_before"]],
                         ["aaa_m1.txt", "bbb_m2.txt", "ccc_m3.txt"])
        self.assertEqual([os.path.basename(p) for p in self.d["main_after_drop"]],
                         ["ccc_m3.txt", "bbb_m2.txt", "aaa_m1.txt"])
        self.assertEqual([os.path.basename(p) for p in self.d["ui_order_main"]],
                         ["ccc_m3.txt", "bbb_m2.txt", "aaa_m1.txt"])
        self.assertEqual(self.d.get("main_scope"), "main:全部")

    def test_06_category_switch_keeps_order(self):
        self.assertEqual([os.path.basename(p) for p in self.d["main_after_switch"]],
                         ["ccc_m3.txt", "bbb_m2.txt", "aaa_m1.txt"])

    def test_07_unlisted_path_goes_last_but_stays(self):
        self.assertTrue(self.d.get("extra_present"), self.d.get("main_with_extra"))
        self.assertEqual([os.path.basename(p) for p in self.d["main_with_extra"]],
                         ["ccc_m3.txt", "bbb_m2.txt", "aaa_m1.txt", "ddd_extra.txt"])

    def test_08_external_drop_still_ingests(self):
        self.assertEqual(self.d.get("mini_count_after_ext"), 4, self.d.get("ext_after_emit"))
        self.assertTrue(self.d.get("ext_in_mini"))
        self.assertTrue(self.d.get("ext_in_main"))
        self.assertTrue(self.d.get("ext_in_book"))
        self.assertTrue(self.d.get("ext_kept_prev"))      # 原有顺序一个没丢

    def test_09_multi_drag_keeps_relative_order(self):
        a = [os.path.basename(p) for p in self.d["mini_multi"]]
        self.assertEqual(a, ["bbb_mid.txt", "ccc_last.txt", "aaa_first.txt"])
        b = [os.path.basename(p) for p in self.d["mini_multi_restore"]]
        self.assertEqual(b, ["aaa_first.txt", "bbb_mid.txt", "ccc_last.txt"])

    def test_10_drop_on_blank_goes_to_end(self):
        a = [os.path.basename(p) for p in self.d["mini_to_end"]]
        self.assertEqual(a, ["bbb_mid.txt", "ccc_last.txt", "aaa_first.txt"])
        b = [os.path.basename(p) for p in self.d["mini_to_end_restore"]]
        self.assertEqual(b, ["aaa_first.txt", "bbb_mid.txt", "ccc_last.txt"])

    def test_11_bad_order_field_is_ignored(self):
        self.assertEqual(self.d.get("bad_state_order"), {}, self.d.get("bad_state_order"))
        self.assertTrue(self.d.get("bad_state_ok"))     # 坏值丢掉，字段本身还是 dict
        # 混了坏值的：坏的那条丢掉，好的留下
        self.assertEqual(self.d.get("mixed_order"), {"mini:x": ["ok.txt"]},
                         self.d.get("mixed_order"))

    def test_12_side_drop_still_regroups(self):
        self.assertEqual(self.d.get("side_group"), "未分类", self.d.get("side_group"))


if __name__ == "__main__":
    if PROBE_FLAG in sys.argv:
        _probe()
    else:
        unittest.main()
