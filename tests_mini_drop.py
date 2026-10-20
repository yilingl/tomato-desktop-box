# -*- coding: utf-8 -*-
"""#1 回归：从外部拖文件进小窗口 —— 图标要显示出来，并且要同步到主面板。

为什么要走子进程：QT_QPA_PLATFORM=offscreen 必须赶在 import PySide6 之前进环境，
    而 discover 跑起来时同进程可能已经有一个 windows 平台的 QApplication ——
    那时建 MainWindow 会真的往用户屏幕上弹面板。断言一律在离屏子进程里做。

真相只有一个：全程只读用户桌面上的真实文件（取图标用），绝不移动/复制/改名，
    账本与状态全部落在临时目录（APPDATA / DESKBOX_* 全部重定向）。

跑法：
    python tests_mini_drop.py
    python -m unittest tests_mini_drop -v
"""
import json
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

PROBE_FLAG = "--probe"
PREFIX = "MINI_DROP_PROBE_JSON "


def _probe():
    """子进程入口：离屏实测「拖进小窗口」的完整链路，结果打成一行 JSON。"""
    import tempfile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"      # 必须在 import PySide6 之前设
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    sys.path.insert(0, REPO)

    tmp = tempfile.mkdtemp(prefix="dsh_minidrop_")
    for key in ("UI", "MINIS", "GROUPS", "CATS", "HIDEICONS"):
        os.environ["DESKBOX_%s_JSON" % key] = os.path.join(tmp, key.lower() + ".json")
    os.environ["DESKBOX_ROOT"] = os.path.join(tmp, "root")
    os.makedirs(os.environ["DESKBOX_ROOT"], exist_ok=True)
    os.environ["APPDATA"] = os.path.join(tmp, "appdata")
    os.environ["LOCALAPPDATA"] = os.path.join(tmp, "localappdata")
    for d in (os.environ["APPDATA"], os.environ["LOCALAPPDATA"]):
        os.makedirs(d, exist_ok=True)

    out = {}
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QApplication

    import deskbox.ui_mock as U

    app = QApplication.instance() or QApplication([])
    U.NO_HIDE_PROMPT = True                # 压掉启动自检弹窗

    def _pick_desk_pair():
        """桌面上真实存在的 .txt / .zip 各一个（只读，不碰内容）。"""
        desk = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
        got = []
        if os.path.isdir(desk):
            names = sorted(os.listdir(desk))
            for ext in (".txt", ".zip"):
                for n in names:
                    p = os.path.join(desk, n)
                    if n.lower().endswith(ext) and os.path.isfile(p):
                        got.append(p)
                        break
        return got

    def _drop(widget, paths):
        """把一组路径当外部拖拽投进网格（QUrl 路线 = 资源管理器那条）。"""
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        pos = QPoint(10, 10)
        widget.dragEnterEvent(QDragEnterEvent(pos, Qt.CopyAction, md,
                                              Qt.LeftButton, Qt.NoModifier))
        widget.dropEvent(QDropEvent(pos, Qt.CopyAction, md,
                                    Qt.LeftButton, Qt.NoModifier))
        app.processEvents()

    def _icons(win):
        r = []
        for i in range(win.grid.count()):
            it = win.grid.item(i)
            ico = it.data(Qt.DecorationRole)
            r.append({
                "path": it.data(Qt.UserRole),
                "decoration_none": ico is None,
                "pixmap_null": (None if ico is None
                                else bool(ico.pixmap(48, 48).isNull())),
            })
        return r

    out["construct_error"] = ""
    out["platform"] = app.platformName()
    w = None
    m = None
    try:
        w = U.MainWindow()
        app.processEvents()
        m = w._new_mini()
        app.processEvents()
    except Exception as exc:          # noqa: BLE001
        out["construct_error"] = "%s: %s" % (type(exc).__name__, exc)
        print(PREFIX + json.dumps(out, ensure_ascii=False))
        return
    out["mini_created"] = m is not None
    if m is None:
        print(PREFIX + json.dumps(out, ensure_ascii=False))
        return

    # ---------------------------------------------------------- 桌面真实文件
    desk_pair = _pick_desk_pair()
    out["desk_pair"] = desk_pair
    out["desk_pair_exists"] = [os.path.isfile(p) for p in desk_pair]
    out["mime_forward_slash_needed"] = (
        "\\" not in QUrl.fromLocalFile(desk_pair[0]).toLocalFile() if desk_pair else None)

    _drop(m.grid, desk_pair)
    out["grid_count_desk"] = m.grid.count()
    out["icons_desk"] = _icons(m)
    out["paths_after_norm"] = [d["path"] for d in out["icons_desk"]]
    out["paths_all_backslash"] = all(
        ("/" not in (d["path"] or "")) for d in out["icons_desk"])

    # minis.json 里存的到底是什么形态
    out["minis_paths"] = []
    try:
        with open(os.environ["DESKBOX_MINIS_JSON"], "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        for win in doc.get("windows") or []:
            if win.get("id") == m.wid:
                out["minis_paths"] = list(win.get("paths") or [])
    except Exception as exc:          # noqa: BLE001
        out["minis_error"] = "%s: %s" % (type(exc).__name__, exc)

    # groups.json：拖进小窗口也必须登记进分组账本
    gp = os.environ["DESKBOX_GROUPS_JSON"]
    out["groups_exists"] = os.path.exists(gp)
    out["groups_keys"] = []
    try:
        with open(gp, "r", encoding="utf-8") as fh:
            out["groups_keys"] = sorted((json.load(fh) or {}).get("items") or {})
    except Exception as exc:          # noqa: BLE001
        out["groups_error"] = "%s: %s" % (type(exc).__name__, exc)

    norm = lambda p: os.path.normcase(os.path.abspath(p))          # noqa: E731
    want = {norm(p) for p in desk_pair}
    out["desktop_rows_hit"] = sorted(
        norm(r.path) for r in w.desktop_rows if norm(r.path) in want)
    out["desk_targets"] = sorted(want)

    # ---------------------------------------------------------- 桌面外的文件
    # 这一组才真正证明「同步到主面板」：它们不在桌面上，只能靠 groups.json 露面
    outside = os.path.join(tmp, "outside")
    os.makedirs(outside, exist_ok=True)
    ext_paths = []
    for name, body in (("dsh_probe_note.txt", "probe"), ("dsh_probe_pack.zip", "probe")):
        p = os.path.join(outside, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        ext_paths.append(p)
    _drop(m.grid, ext_paths)
    out["grid_count_total"] = m.grid.count()
    out["icons_total"] = _icons(m)
    want_ext = {norm(p) for p in ext_paths}
    out["desktop_rows_ext_hit"] = sorted(
        norm(r.path) for r in w.desktop_rows if norm(r.path) in want_ext)
    out["ext_targets"] = sorted(want_ext)
    out["groups_keys_after_ext"] = []
    try:
        with open(gp, "r", encoding="utf-8") as fh:
            out["groups_keys_after_ext"] = sorted((json.load(fh) or {}).get("items") or {})
    except Exception:                 # noqa: BLE001
        pass

    # ---------------------------------------------- 主面板 → 小窗口（内部 MIME 路线）
    # 主面板 startDrag 发出去的就是这个载荷：DRAG_MIME + setUrls。
    # 之前 MiniGrid 只看 MIME 就当换位，于是 on_drop_calls=0、minis.json 一条没进。
    board_paths = []
    for r in (w.desktop_rows or []):
        if os.path.isfile(r.path) and norm(r.path) not in {norm(p) for p in ext_paths}                 and norm(r.path) not in want:
            board_paths.append(r.path)
    board_paths = board_paths[:1]
    out["board_candidates"] = board_paths

    minis_before = []
    try:
        with open(os.environ["DESKBOX_MINIS_JSON"], "r", encoding="utf-8") as fh:
            for win in (json.load(fh) or {}).get("windows") or []:
                if win.get("id") == m.wid:
                    minis_before = list(win.get("paths") or [])
    except Exception as exc:          # noqa: BLE001
        out["minis_before_error"] = "%s: %s" % (type(exc).__name__, exc)

    on_drop, reordered = [], []
    m.grid.dropped_paths.connect(lambda ps: on_drop.append(list(ps)))
    m.grid.reordered.connect(lambda ps, t: reordered.append((list(ps), t)))
    if board_paths:
        md = QMimeData()
        md.setData(U.DRAG_MIME, ("\n".join(board_paths)).encode("utf-8"))
        try:
            md.setUrls([QUrl.fromLocalFile(p) for p in board_paths])
        except Exception:             # noqa: BLE001
            pass
        pos = QPoint(12, 12)
        m.grid.dragEnterEvent(QDragEnterEvent(pos, Qt.MoveAction, md,
                                             Qt.LeftButton, Qt.NoModifier))
        m.grid.dropEvent(QDropEvent(pos, Qt.MoveAction, md,
                                    Qt.LeftButton, Qt.NoModifier))
        app.processEvents()
        m._reload()
        app.processEvents()
    out["board_on_drop_calls"] = len(on_drop)
    out["board_reordered_calls"] = len(reordered)

    minis_after = []
    try:
        with open(os.environ["DESKBOX_MINIS_JSON"], "r", encoding="utf-8") as fh:
            for win in (json.load(fh) or {}).get("windows") or []:
                if win.get("id") == m.wid:
                    minis_after = list(win.get("paths") or [])
    except Exception as exc:          # noqa: BLE001
        out["minis_after_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["board_minis_before_n"] = len(minis_before)
    out["board_minis_after_n"] = len(minis_after)
    out["board_minis_added"] = sorted(
        {norm(p) for p in minis_after} - {norm(p) for p in minis_before})
    out["board_target"] = sorted({norm(p) for p in board_paths})
    out["board_in_grid"] = sorted(
        norm(it["path"]) for it in _icons(m) if norm(it["path"]) in {norm(p) for p in board_paths})
    out["groups_has_board"] = False
    try:
        with open(gp, "r", encoding="utf-8") as fh:
            keys2 = {os.path.normcase(k) for k in ((json.load(fh) or {}).get("items") or {})}
        out["groups_has_board"] = all(norm(p) in keys2 for p in board_paths)
    except Exception:                 # noqa: BLE001
        pass

    # ---------------------------------------------- 小窗口内部换位（顺序变、集合不变）
    own_before = [it["path"] for it in _icons(m)]
    seq_before = [norm(p) for p in own_before]
    swap_ok = False
    seq_after = list(seq_before)
    if len(own_before) >= 3:
        order = [p for p in own_before]
        tgt_item = m.grid.item(len(order) - 1)
        md2 = QMimeData()
        md2.setData(U.DRAG_MIME, order[0].encode("utf-8"))     # 只带自己的路径
        rect = m.grid.visualItemRect(tgt_item)
        pos2 = QPointF(rect.center())
        m.grid.dropEvent(QDropEvent(pos2, Qt.MoveAction, md2,
                                    Qt.LeftButton, Qt.NoModifier))
        app.processEvents()
        m._reload()
        app.processEvents()
        seq_after = [norm(it["path"]) for it in _icons(m)]
        swap_ok = True
    out["swap_attempted"] = swap_ok
    out["swap_seq_before"] = seq_before
    out["swap_seq_after"] = seq_after
    out["swap_set_unchanged"] = set(seq_before) == set(seq_after)
    out["swap_order_changed"] = seq_before != seq_after
    out["swap_count_same"] = len(seq_before) == len(seq_after)

    # ---------------------------------------------------------- 从账本恢复
    out["count_before_restore"] = m.grid.count()
    old_wid = m.wid
    try:
        w.hide()
        app.processEvents()
        w2 = U.MainWindow()
        app.processEvents()
        m2 = w2._minis.get(old_wid)
        out["restored_mini"] = m2 is not None
        out["icons_restored"] = _icons(m2) if m2 is not None else []
        w2.hide()
    except Exception as exc:          # noqa: BLE001
        out["restore_error"] = "%s: %s" % (type(exc).__name__, exc)
    try:
        w.hide()
    except Exception:                 # noqa: BLE001
        pass
    out["probe_ok"] = True
    print(PREFIX + json.dumps(out, ensure_ascii=False))


_RUN = {}


def _run_probe():
    """跑一次子进程，缓存结果（多个测试共用，省掉重复建面板）。"""
    if "d" in _RUN:
        return _RUN["d"]
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-u", os.path.abspath(__file__), PROBE_FLAG],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env,
                       cwd=REPO, timeout=600)
    data = None
    for line in (p.stdout or "").splitlines():
        if line.startswith(PREFIX):
            data = json.loads(line[len(PREFIX):])
    if data is None:
        raise AssertionError("离屏探针没吐出结果\nSTDOUT:\n%s\nSTDERR:\n%s"
                             % ((p.stdout or "")[-4000:], (p.stderr or "")[-4000:]))
    _RUN["d"] = data
    return data


@unittest.skipUnless(sys.platform.startswith("win"), "Shell 图标接口只在 Windows 上有")
class MiniDropTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = _run_probe()

    def test_00_probe_ran(self):
        self.assertEqual(self.d.get("construct_error"), "", self.d)
        self.assertTrue(self.d.get("probe_ok"), self.d)
        self.assertEqual(self.d.get("platform"), "offscreen", self.d.get("platform"))
        self.assertTrue(self.d.get("mini_created"))

    def test_01_mime_normalized_to_backslash(self):
        """外部拖拽的 QUrl 给正斜杠；归一化之后必须是反斜杠。"""
        self.assertTrue(self.d.get("mime_forward_slash_needed"),
                        "前置条件变了：QUrl.toLocalFile() 不再返回正斜杠，本用例的前提需复核")
        self.assertTrue(self.d.get("paths_all_backslash"), self.d.get("paths_after_norm"))
        for p, want in zip(self.d.get("paths_after_norm") or [], self.d.get("desk_pair") or []):
            self.assertEqual(os.path.normpath(p), os.path.normpath(want))

    def test_02_icons_render(self):
        """拖进去的两个文件必须真有图标（之前是空 QIcon → 网格里空白）。"""
        icons = self.d.get("icons_desk") or []
        self.assertEqual(self.d.get("grid_count_desk"), 2, icons)
        self.assertEqual(len(icons), 2)
        for it in icons:
            self.assertFalse(it["decoration_none"], it)
            self.assertIs(it["pixmap_null"], False, it)

    def test_03_groups_json_written(self):
        """拖进小窗口也要登记进分组账本，否则主面板看不见。"""
        self.assertTrue(self.d.get("groups_exists"), self.d)
        keys = {os.path.normcase(k) for k in (self.d.get("groups_keys") or [])}
        want = {os.path.normcase(os.path.abspath(p)) for p in (self.d.get("desk_pair") or [])}
        self.assertTrue(want, "桌面没找到 .txt/.zip，用例前提不成立")
        self.assertEqual(want - keys, set(), keys)

    def test_04_desktop_rows_contain_dropped(self):
        hit = set(self.d.get("desktop_rows_hit") or [])
        self.assertEqual(hit, set(self.d.get("desk_targets") or []))

    def test_05_outside_desktop_also_synced(self):
        """桌面外的文件：主面板只能靠 groups.json 看到它 —— 这才是同步的硬证据。"""
        want = set(self.d.get("ext_targets") or [])
        self.assertEqual(len(want), 2)
        self.assertEqual(set(self.d.get("desktop_rows_ext_hit") or []), want)
        self.assertEqual(self.d.get("grid_count_total"), 4)

    def test_06_minis_json_path_form(self):
        paths = self.d.get("minis_paths") or []
        self.assertEqual(len(paths), 2, paths)
        for p in paths:
            self.assertNotIn("/", p, p)          # 落库就是反斜杠形态
        for it in (self.d.get("icons_total") or []):
            self.assertIs(it["pixmap_null"], False, it)

    def test_08_board_drag_into_mini_is_ingested(self):
        """主面板 → 小窗口：带 DRAG_MIME 的载荷以前被误判成「小窗口内部换位」，
        结果 on_drop 一次没调、minis.json 一条没进。现在必须按数据分流。"""
        self.assertTrue(self.d.get("board_candidates"),
                        "桌面上没找到可用文件，用例前提不成立")
        self.assertEqual(self.d.get("board_reordered_calls"), 0,
                         self.d.get("board_reordered_calls"))     # 不许再走换位分支
        self.assertEqual(self.d.get("board_on_drop_calls"), 1,
                         self.d.get("board_on_drop_calls"))       # 必须走「收进来」
        self.assertEqual(self.d.get("board_minis_after_n"),
                         (self.d.get("board_minis_before_n") or 0) + 1,
                         self.d)
        self.assertEqual(set(self.d.get("board_minis_added") or []),
                         set(self.d.get("board_target") or []))
        self.assertEqual(set(self.d.get("board_in_grid") or []),
                         set(self.d.get("board_target") or []))
        self.assertTrue(self.d.get("groups_has_board"), self.d)   # 主面板也同步看得见

    def test_09_internal_swap_keeps_ledger_set(self):
        """小窗口内部互拖：顺序要变，但账本集合与条数一个都不许多。"""
        self.assertTrue(self.d.get("swap_attempted"), self.d)
        self.assertTrue(self.d.get("swap_order_changed"), self.d.get("swap_seq_after"))
        self.assertEqual(self.d.get("swap_seq_before"), self.d.get("swap_seq_before"))
        self.assertTrue(self.d.get("swap_set_unchanged"), self.d)
        self.assertTrue(self.d.get("swap_count_same"), self.d)

    def test_07_restore_from_ledger_keeps_icons(self):
        self.assertTrue(self.d.get("restored_mini"), self.d.get("restore_error"))
        icons = self.d.get("icons_restored") or []
        # 用「重建前那一刻的条数」比：grid_count_total 记在主面板那次拖入之前
        self.assertEqual(len(icons), self.d.get("count_before_restore"), icons)
        for it in icons:
            self.assertFalse(it["decoration_none"], it)
            self.assertIs(it["pixmap_null"], False, it)


if __name__ == "__main__":
    if PROBE_FLAG in sys.argv:
        _probe()
    else:
        unittest.main(verbosity=2)
