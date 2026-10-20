# -*- coding: utf-8 -*-
r"""single.py 自测：单实例守卫。全程离屏，绝不弹窗口、绝不碰真实 %APPDATA%\DeskBox。

为什么全走子进程：QT_QPA_PLATFORM=offscreen 必须在 import PySide6 之前进环境，
而 discover 时同一进程里可能已经有别的测试建过 QApplication。子进程里环境干净，
而且「第二个实例」这条本来就必须用真进程才测得准。

三个探针：
    core    —— 管道名、acquire 两次、载荷内容、陈旧管道、子进程模拟「第二次双击」
    window  —— MainWindow._on_second_launch 的非退出分支（show + raise + 激活）
    quit    —— MainWindow._on_second_launch("--quit") 的退出收尾（单独一个进程，
               免得它把别的用例一起带走）

跑法：
    python tests_single.py
    python -m unittest tests_single -v
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
CORE_PREFIX = "SINGLE_CORE_JSON "
WIN_PREFIX = "SINGLE_WIN_JSON "
QUIT_PREFIX = "SINGLE_QUIT_JSON "
CHILD_PREFIX = "SINGLE_CHILD_JSON "

ISOLATED_ENV = ("DESKBOX_UI_JSON", "DESKBOX_GROUPS_JSON", "DESKBOX_CATS_JSON",
                "DESKBOX_MINIS_JSON", "DESKBOX_ROOT", "DESKBOX_HIDEICONS_JSON",
                "APPDATA", "LOCALAPPDATA")


def _isolate(tmp):
    """离屏 + 中文字体目录 + 所有状态文件指到一次性临时目录。"""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    for name in ISOLATED_ENV:
        os.environ[name] = tmp
    for key, fn in (("DESKBOX_UI_JSON", "ui.json"),
                    ("DESKBOX_GROUPS_JSON", "groups.json"),
                    ("DESKBOX_CATS_JSON", "cats.json"),
                    ("DESKBOX_MINIS_JSON", "minis.json"),
                    ("DESKBOX_HIDEICONS_JSON", "hideicons.json")):
        os.environ[key] = os.path.join(tmp, fn)
    os.environ["DESKBOX_ROOT"] = os.path.join(tmp, "root")
    for d in (tmp, os.environ["DESKBOX_ROOT"],
              os.path.join(tmp, "appdata"), os.path.join(tmp, "localappdata")):
        os.makedirs(d, exist_ok=True)
    os.environ["APPDATA"] = os.path.join(tmp, "appdata")
    os.environ["LOCALAPPDATA"] = os.path.join(tmp, "localappdata")
    return tmp


def _run(extra, timeout=120):
    """起一个子进程探针，抓回它打的那行 JSON。"""
    proc = subprocess.run([sys.executable, "-u", os.path.abspath(__file__)] + extra,
                          capture_output=True, text=True, timeout=timeout,
                          cwd=REPO,
                          env=dict(os.environ, PYTHONIOENCODING="utf-8",
                                   PYTHONUTF8="1"))
    prefix = {"core": CORE_PREFIX, "window": WIN_PREFIX, "quit": QUIT_PREFIX}[extra[1]]
    payload = ""
    for line in (proc.stdout or "").splitlines():
        if line.startswith(prefix):
            payload = line[len(prefix):]
    return proc, payload


# ─────────────────────────── 探针 core ───────────────────────────
def _probe_core():
    _isolate(tempfile.mkdtemp(prefix="dsh_single_core_"))
    out = {}
    import time

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalServer

    app = QCoreApplication.instance() or QCoreApplication([])
    import deskbox.single as S

    def pump(ms=2000, until=None):
        t0 = time.time()
        while True:
            app.processEvents()
            if until is not None and until():
                return True
            if (time.time() - t0) * 1000.0 > ms:
                return False
            time.sleep(0.01)

    # ── 1. 管道名 ──
    name = S._server_name()
    tail = name.rsplit("-", 1)[-1]
    out["name"] = name
    out["name_prefix_ok"] = name.startswith("DeskBox-tomato-desktop-box-")
    out["name_tail_ok"] = len(tail) == 8 and all(c in "0123456789abcdef" for c in tail)
    out["name_stable"] = S._server_name() == name          # 同一个用户，名字必须稳定
    out["name_is_module_constant"] = ("DeskBox-tomato-desktop-box" in name)

    # ── 2. 第一次 acquire + listen ──
    ok1, sock1 = S.acquire(["--first"])
    out["acq1_ok"] = bool(ok1)
    out["acq1_sock_none"] = sock1 is None
    got = []
    srv = S.listen(lambda t: got.append(t))
    out["listen_srv_not_none"] = srv is not None
    pump(300)

    # ── 3. 同进程第二次 acquire：必须是 False + 拿到 socket，且对方回调真的被调一次 ──
    ok2, sock2 = S.acquire(["--second", "带空格 的参"])
    out["acq2_ok"] = bool(ok2)
    out["acq2_sock_is_socket"] = sock2 is not None
    out["acq2_sock_type"] = type(sock2).__name__ if sock2 is not None else ""
    pump(3000, until=lambda: len(got) >= 1)
    out["msg_count"] = len(got)
    out["msg_raw"] = got[0] if got else ""
    lines = got[0].splitlines() if got else []
    out["msg_cwd"] = lines[0] if lines else ""
    out["msg_argv"] = lines[1:] if lines else []
    out["msg_cwd_ok"] = out["msg_cwd"] == os.getcwd()
    out["msg_argv_ok"] = out["msg_argv"] == ["--second", "带空格 的参"]
    out["pump_delivered"] = len(got) >= 1
    S.release(srv)
    pump(200)

    # ── 4. 陈旧管道 a：有人 listen 过、只 close() 不 removeServer ──
    stale = QLocalServer()
    out["stale_a_listen"] = bool(stale.listen(name))
    stale.close()                      # 故意不 removeServer：留个"残留名字"
    ok3, sock3 = S.acquire([])
    out["stale_a_acq_ok"] = bool(ok3)
    out["stale_a_sock_none"] = sock3 is None
    srv3 = S.listen(lambda t: None)
    out["stale_a_listen_again"] = srv3 is not None
    S.release(srv3)
    pump(150)

    # ── 5. 陈旧管道 b：另一个进程 listen 着，被直接杀掉（真·强杀残留）──
    holder = os.path.join(os.environ["TEMP"], "single_holder_%d.py" % os.getpid())
    with open(holder, "w", encoding="utf-8") as fh:
        fh.write(
            "import sys, time\n"
            "from PySide6.QtCore import QCoreApplication\n"
            "from PySide6.QtNetwork import QLocalServer\n"
            "app = QCoreApplication([])\n"
            "import deskbox.single as S\n"
            "s = QLocalServer()\n"
            "print('READY' if s.listen(S._server_name()) else 'FAIL', flush=True)\n"
            "time.sleep(30)\n")
    env = dict(os.environ, PYTHONPATH=REPO, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    hp = subprocess.Popen([sys.executable, "-u", holder], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, cwd=REPO, env=env)
    ready = (hp.stdout.readline() or "").strip()
    out["stale_b_holder_ready"] = ready == "READY"
    hp.kill()                          # 强杀：管道由系统回收，名字可能还留着
    try:
        hp.wait(timeout=10)
    except Exception:                  # noqa: BLE001
        pass
    try:
        os.remove(holder)
    except Exception:                  # noqa: BLE001
        pass
    pump(300)
    ok4, sock4 = S.acquire([])
    out["stale_b_acq_ok"] = bool(ok4)
    out["stale_b_sock_none"] = sock4 is None
    got4 = []
    srv4 = S.listen(lambda t: got4.append(t))
    out["stale_b_listen_ok"] = srv4 is not None

    # ── 6. 子进程模拟「第二次双击」──
    runner = os.path.join(os.environ["TEMP"], "single_child_%d.py" % os.getpid())
    with open(runner, "w", encoding="utf-8") as fh:
        fh.write(
            "import json, os, sys\n"
            "sys.path.insert(0, %r)\n"
            "from PySide6.QtCore import QCoreApplication\n"
            "app = QCoreApplication.instance() or QCoreApplication([])\n"
            "import deskbox.single as S\n"
            "try:\n"
            "    ok, sock = S.acquire(['--from-child', 'x'])\n"
            "    print(%r + json.dumps({'ok': bool(ok), 'sock': sock is not None,\n"
            "                           'name': S._server_name(), 'cwd': os.getcwd()},\n"
            "                          ensure_ascii=False), flush=True)\n"
            "except Exception as exc:\n"
            "    print(%r + json.dumps({'error': '%%s: %%s' %% (type(exc).__name__, exc)}),\n"
            "          flush=True)\n"
            "    raise\n"
            % (REPO, CHILD_PREFIX, CHILD_PREFIX))
    child_cwd = tempfile.mkdtemp(prefix="dsh_single_child_")
    cp = subprocess.Popen([sys.executable, "-u", runner], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, cwd=child_cwd, env=env)
    child_line = ""
    t0 = time.time()
    while time.time() - t0 < 30:
        app.processEvents()
        if got4:
            break
        if cp.poll() is not None and not cp.stdout.readable():
            break
        time.sleep(0.01)
    pump(3000, until=lambda: bool(got4))
    try:
        child_line = cp.stdout.readline().strip()
    except Exception:                  # noqa: BLE001
        child_line = ""
    try:
        rc = cp.wait(timeout=20)
    except Exception:                  # noqa: BLE001
        cp.kill()
        rc = -1
    child_err = cp.stderr.read() if cp.stderr is not None else ""
    out["child_rc"] = rc
    out["child_stderr"] = (child_err or "")[-400:]
    out["child_line"] = child_line
    try:
        out["child_json"] = json.loads(child_line[len(CHILD_PREFIX):]) \
            if child_line.startswith(CHILD_PREFIX) else {}
    except Exception:                  # noqa: BLE001
        out["child_json"] = {}
    out["child_acquire_ok"] = out["child_json"].get("ok")
    out["child_has_sock"] = out["child_json"].get("sock")
    out["child_cwd_ok"] = out["child_json"].get("cwd") == child_cwd
    out["child_name_matches"] = out["child_json"].get("name") == name
    out["parent_msg_after_child"] = list(got4)
    clines = got4[0].splitlines() if got4 else []
    out["parent_child_cwd"] = clines[0] if clines else ""
    out["parent_child_argv"] = clines[1:] if clines else []
    out["parent_child_cwd_ok"] = out["parent_child_cwd"] == child_cwd
    out["parent_child_argv_ok"] = out["parent_child_argv"] == ["--from-child", "x"]
    try:
        os.remove(runner)
    except Exception:                  # noqa: BLE001
        pass
    S.release(srv4)

    # ── 7. release 之后名字应该又能抢 ──
    ok5, sock5 = S.acquire([])
    out["after_release_acq_ok"] = bool(ok5)
    S.release()

    # ── 8. _rel 缺失降级：single 模块拿不到时返回 (None, err)，不许抛 ──
    import deskbox.ui_mock as U
    saved = sys.modules.pop("deskbox.single", None)
    sys.modules["deskbox.single"] = None      # 让 import_module 直接失败
    try:
        mod, err = U._rel("single")
        out["rel_missing_mod_none"] = mod is None
        out["rel_missing_err"] = str(err)[:120]
        out["rel_missing_err_nonempty"] = bool(err)
    except Exception as exc:                   # noqa: BLE001
        out["rel_missing_mod_none"] = False
        out["rel_missing_err"] = "RAISED %s: %s" % (type(exc).__name__, exc)
        out["rel_missing_err_nonempty"] = False
    finally:
        if saved is not None:
            sys.modules["deskbox.single"] = saved
    out["ui_single_is_module"] = U.SINGLE is not None
    out["has_release_single"] = callable(getattr(U, "_release_single", None))

    print(CORE_PREFIX + json.dumps(out, ensure_ascii=False))


# ─────────────────────────── 探针 window ───────────────────────────
def _probe_window():
    _isolate(tempfile.mkdtemp(prefix="dsh_single_win_"))
    out = {}
    from PySide6.QtWidgets import QApplication

    import deskbox.ui_mock as U

    app = QApplication.instance() or QApplication([])
    U.NO_HIDE_PROMPT = True
    if U.STORE is not None:
        U.STORE.open_item = lambda p, _a=None: True

    out["construct_error"] = ""
    w = None
    try:
        w = U.MainWindow()
        app.processEvents()
    except Exception as exc:               # noqa: BLE001
        out["construct_error"] = "%s: %s" % (type(exc).__name__, exc)
        print(WIN_PREFIX + json.dumps(out, ensure_ascii=False))
        return

    calls = []
    w.raise_ = lambda: calls.append("raise")
    w.activateWindow = lambda: calls.append("activate")

    # ── A. 面板正常可见时收到消息：不该抛，raise/activate 都调到 ──
    w.show()
    app.processEvents()
    out["shown_before"] = bool(w.isVisible())
    calls.clear()
    err = ""
    try:
        w._on_second_launch("C:\\some\\where\r\n--hello\r\n")
    except Exception as exc:               # noqa: BLE001
        err = "%s: %s" % (type(exc).__name__, exc)
    app.processEvents()
    out["visible_case_no_raise"] = (err == "")
    out["visible_case_error"] = err
    out["visible_case_still_visible"] = bool(w.isVisible())
    out["visible_case_calls"] = list(calls)

    # ── B. 被收进托盘（hide 过）后收到消息：必须 show() 回来，并且不抛 ──
    w.hide()
    app.processEvents()
    out["hidden_before"] = (not w.isVisible())
    calls.clear()
    err2 = ""
    try:
        w._on_second_launch("C:\\x\n")     # 只有 cwd，没有参数 = 纯叫醒
    except Exception as exc:               # noqa: BLE001
        err2 = "%s: %s" % (type(exc).__name__, exc)
    app.processEvents()
    out["hidden_case_no_raise"] = (err2 == "")
    out["hidden_case_error"] = err2
    out["hidden_case_visible_after"] = bool(w.isVisible())
    out["hidden_case_calls"] = list(calls)
    out["hidden_case_saved"] = os.path.isfile(os.environ["DESKBOX_UI_JSON"])

    # ── C. 空载荷 / 垃圾载荷：纯叫醒，不抛 ──
    calls.clear()
    errs = []
    for junk in ("", "\n", "\r\n", "cwd\n\n\n", "\x00\x01乱码"):
        try:
            w._on_second_launch(junk)
        except Exception as exc:           # noqa: BLE001
            errs.append("%r -> %s: %s" % (junk, type(exc).__name__, exc))
    out["junk_errors"] = errs
    out["junk_no_raise"] = (errs == [])

    # ── D. 参数里有 --quit 之外的东西时不该退出：窗口还在 ──
    calls.clear()
    w.show()
    app.processEvents()
    try:
        w._on_second_launch("cwd\n--foo\n--bar baz\n")
        out["args_ignored_ok"] = bool(w.isVisible())
    except Exception as exc:               # noqa: BLE001
        out["args_ignored_ok"] = False
        out["args_ignored_err"] = "%s: %s" % (type(exc).__name__, exc)
    out["final_visible"] = bool(w.isVisible())
    print(WIN_PREFIX + json.dumps(out, ensure_ascii=False))


# ─────────────────────────── 探针 quit ───────────────────────────
def _probe_quit():
    _isolate(tempfile.mkdtemp(prefix="dsh_single_quit_"))
    out = {}
    from PySide6.QtWidgets import QApplication

    import deskbox.ui_mock as U

    app = QApplication.instance() or QApplication([])
    U.NO_HIDE_PROMPT = True
    if U.STORE is not None:
        U.STORE.open_item = lambda p, _a=None: True

    w = U.MainWindow()
    app.processEvents()
    w.show()
    app.processEvents()
    out["visible_before"] = bool(w.isVisible())

    # 第一段：把 _tray_quit 换成本地桩，只验证「--quit 被路由过去、且没抛」。
    quit_calls = []
    real_tray_quit = w._tray_quit
    w._tray_quit = lambda: quit_calls.append("tray_quit")
    err = ""
    try:
        w._on_second_launch("C:\\cwd\n--quit\n")
    except Exception as exc:               # noqa: BLE001
        err = "%s: %s" % (type(exc).__name__, exc)
    out["quit_no_raise"] = (err == "")
    out["quit_error"] = err
    out["quit_routed"] = quit_calls == ["tray_quit"]
    out["quit_calls"] = list(quit_calls)

    # 第二段：把真的 _tray_quit 放回去，只把 app.quit 换成计数桩
    # （不换的话事件循环一退，探针后面什么都打不出来）。
    w._tray_quit = real_tray_quit
    w.show()
    app.processEvents()
    app_quit_calls = []
    try:
        app.quit = lambda: app_quit_calls.append(1)
    except Exception:                      # noqa: BLE001 —— 万一 Qt 不让改属性
        app_quit_calls.append(-1)
    try:
        w._on_second_launch("cwd\n--quit\n")
    except Exception as exc:               # noqa: BLE001
        out["real_quit_error"] = "%s: %s" % (type(exc).__name__, exc)
    app.processEvents()
    out["real_quit_patched"] = app_quit_calls != [-1]
    out["real_quit_ran"] = (len(app_quit_calls) == 1 and app_quit_calls[0] == 1) \
        or app_quit_calls == [-1]
    out["quit_all_calls"] = list(quit_calls)
    out["panel_hidden_after"] = (not w.isVisible())
    out["state_saved"] = os.path.isfile(os.environ["DESKBOX_UI_JSON"])
    print(QUIT_PREFIX + json.dumps(out, ensure_ascii=False))


# ─────────────────────────── 单元测试 ───────────────────────────
class SingleGuard(unittest.TestCase):
    """core 探针：管道名、acquire 语义、载荷、陈旧管道、子进程「第二次双击」。"""

    @classmethod
    def setUpClass(cls):
        cls.proc, cls.payload = _run([PROBE_FLAG, "core"])
        cls.d = json.loads(cls.payload) if cls.payload else {}

    def setUp(self):
        self.assertTrue(self.payload, "core 探针没打出 JSON：%s" % (self.proc.stderr or "")[-600:])

    def test_01_server_name_shape(self):
        self.assertTrue(self.d["name_prefix_ok"], self.d.get("name"))
        self.assertTrue(self.d["name_tail_ok"], self.d.get("name"))
        self.assertTrue(self.d["name_stable"])

    def test_02_first_acquire_wins(self):
        self.assertTrue(self.d["acq1_ok"])
        self.assertTrue(self.d["acq1_sock_none"])
        self.assertTrue(self.d["listen_srv_not_none"])

    def test_03_second_acquire_returns_false_with_socket(self):
        self.assertFalse(self.d["acq2_ok"])
        self.assertTrue(self.d["acq2_sock_is_socket"])
        self.assertEqual(self.d["acq2_sock_type"], "QLocalSocket")

    def test_04_message_delivered_exactly_once(self):
        self.assertTrue(self.d["pump_delivered"])
        self.assertEqual(self.d["msg_count"], 1)

    def test_05_payload_cwd_and_argv(self):
        self.assertTrue(self.d["msg_cwd_ok"], self.d.get("msg_cwd"))
        self.assertTrue(self.d["msg_argv_ok"], self.d.get("msg_argv"))

    def test_06_stale_pipe_after_close_still_acquirable(self):
        self.assertTrue(self.d["stale_a_listen"])
        self.assertTrue(self.d["stale_a_acq_ok"])
        self.assertTrue(self.d["stale_a_sock_none"])
        self.assertTrue(self.d["stale_a_listen_again"])

    def test_07_stale_pipe_after_kill_still_acquirable(self):
        self.assertTrue(self.d["stale_b_holder_ready"])
        self.assertTrue(self.d["stale_b_acq_ok"])
        self.assertTrue(self.d["stale_b_sock_none"])
        self.assertTrue(self.d["stale_b_listen_ok"])

    def test_08_child_process_second_launch(self):
        self.assertEqual(self.d["child_rc"], 0, self.d.get("child_stderr"))
        self.assertFalse(self.d["child_acquire_ok"], self.d.get("child_json"))
        self.assertTrue(self.d["child_has_sock"])
        self.assertTrue(self.d["child_cwd_ok"], self.d.get("child_json"))
        self.assertTrue(self.d["child_name_matches"])

    def test_09_parent_notified_by_child(self):
        self.assertEqual(len(self.d["parent_msg_after_child"]), 1)
        self.assertTrue(self.d["parent_child_cwd_ok"], self.d.get("parent_child_cwd"))
        self.assertTrue(self.d["parent_child_argv_ok"], self.d.get("parent_child_argv"))

    def test_10_release_frees_name(self):
        self.assertTrue(self.d["after_release_acq_ok"])

    def test_11_rel_degrades_when_single_missing(self):
        self.assertTrue(self.d["rel_missing_mod_none"], self.d.get("rel_missing_err"))
        self.assertTrue(self.d["rel_missing_err_nonempty"])
        self.assertTrue(self.d["ui_single_is_module"])
        self.assertTrue(self.d["has_release_single"])


class SecondLaunchBringsToFront(unittest.TestCase):
    """window 探针：非退出分支必须把面板叫到前面，且任何载荷都不抛。"""

    @classmethod
    def setUpClass(cls):
        cls.proc, cls.payload = _run([PROBE_FLAG, "window"])
        cls.d = json.loads(cls.payload) if cls.payload else {}

    def setUp(self):
        self.assertTrue(self.payload, "window 探针没打出 JSON：%s" % (self.proc.stderr or "")[-600:])
        self.assertEqual(self.d.get("construct_error"), "", "MainWindow 构造失败")

    def test_01_visible_case_raises_and_activates(self):
        self.assertTrue(self.d["shown_before"])
        self.assertTrue(self.d["visible_case_no_raise"], self.d.get("visible_case_error"))
        self.assertTrue(self.d["visible_case_still_visible"])
        self.assertEqual(self.d["visible_case_calls"], ["raise", "activate"])

    def test_02_hidden_case_shows_again(self):
        self.assertTrue(self.d["hidden_before"])
        self.assertTrue(self.d["hidden_case_no_raise"], self.d.get("hidden_case_error"))
        self.assertTrue(self.d["hidden_case_visible_after"])
        self.assertIn("raise", self.d["hidden_case_calls"])
        self.assertIn("activate", self.d["hidden_case_calls"])

    def test_03_junk_payload_never_raises(self):
        self.assertTrue(self.d["junk_no_raise"], self.d.get("junk_errors"))

    def test_04_other_args_do_not_quit(self):
        self.assertTrue(self.d["args_ignored_ok"], self.d.get("args_ignored_err"))
        self.assertTrue(self.d["final_visible"])


class SecondLaunchQuit(unittest.TestCase):
    """quit 探针：--quit 必须走托盘退出那套收尾。"""

    @classmethod
    def setUpClass(cls):
        cls.proc, cls.payload = _run([PROBE_FLAG, "quit"])
        cls.d = json.loads(cls.payload) if cls.payload else {}

    def setUp(self):
        self.assertTrue(self.payload, "quit 探针没打出 JSON：%s" % (self.proc.stderr or "")[-600:])

    def test_01_quit_routed_to_tray_quit(self):
        self.assertTrue(self.d["visible_before"])
        self.assertTrue(self.d["quit_no_raise"], self.d.get("quit_error"))
        self.assertTrue(self.d["quit_routed"], self.d.get("quit_calls"))

    def test_02_real_tray_quit_runs_teardown(self):
        self.assertTrue(self.d["real_quit_ran"], self.d.get("quit_all_calls"))
        self.assertTrue(self.d["panel_hidden_after"])
        self.assertTrue(self.d["state_saved"])


def main():
    if PROBE_FLAG in sys.argv:
        which = sys.argv[sys.argv.index(PROBE_FLAG) + 1]
        {"core": _probe_core, "window": _probe_window, "quit": _probe_quit}[which]()
        return 0
    unittest.main(verbosity=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
