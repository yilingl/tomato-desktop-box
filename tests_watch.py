# -*- coding: utf-8 -*-
"""watch.py 自测：全程临时目录，QCoreApplication（不要 QApplication）。"""
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, qInstallMessageHandler

import deskbox.store as store
import deskbox.watch as watch


def _quiet(_mode, _ctx, msg):
    """吞掉 Qt 那条「目录刚被删，native 通知句柄失效」的告警，它正是本测试的场景。"""
    if "FindNextChangeNotification" in msg:
        return
    print("[Qt] %s" % msg)


qInstallMessageHandler(_quiet)

_fail = []
_n = [0]
TMP = tempfile.mkdtemp(prefix="deskbox_watch_")
APP = QCoreApplication.instance() or QCoreApplication([])


def check(name, cond, detail=""):
    _n[0] += 1
    if cond:
        print("PASS - %s" % name)
    else:
        print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        _fail.append(name)


def pump(ms):
    """跑事件循环 ms 毫秒。"""
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


def touch(path, data=b"x"):
    with open(path, "wb") as f:
        f.write(data)
    return path


def new_dir(tag):
    d = os.path.join(TMP, tag)
    os.makedirs(d, exist_ok=True)
    return d


def use(*dirs):
    """把 store.DESK_DIRS 指向本用例的临时目录。"""
    store.DESK_DIRS = list(dirs)


class Rec(object):
    """收集 changed 信号。"""

    def __init__(self, watcher):
        self.hits = []
        watcher.changed.connect(self._on)

    def _on(self, why):
        self.hits.append(why)

    def clear(self):
        self.hits = []


# ---------------------------------------------------------------- 用例
def t_desktop_dirs():
    d1 = new_dir("dd1")
    d2 = new_dir("dd2")
    ghost = os.path.join(TMP, "dd_ghost_nope")
    use(d1, ghost, d2)
    got = watch.desktop_dirs()
    check("desktop_dirs 跟随 DESK_DIRS 并过滤不存在的",
          set(got) == {d1, d2}, got)
    check("desktop_dirs 结果顺序稳定", got == [d1, d2], got)

    use(d1)
    check("desktop_dirs 再次现取（不缓存）", watch.desktop_dirs() == [d1],
          watch.desktop_dirs())

    use()
    check("DESK_DIRS 为空时 desktop_dirs 返回空", watch.desktop_dirs() == [])


def t_start_stop():
    d = new_dir("ss")
    use(d)
    w = watch.DesktopWatcher()
    check("构造后未运行", w.is_running() is False)
    w.start()
    check("start 后 is_running 为 True", w.is_running() is True)
    w.start()
    w.start()
    check("重复 start 幂等且仍为 True", w.is_running() is True)
    check("start 后 watcher 已挂上目录",
          set(w._watch.directories()) == {d}, w._watch.directories())
    w.stop()
    check("stop 后 is_running 为 False", w.is_running() is False)
    w.stop()
    check("重复 stop 幂等", w.is_running() is False)
    check("stop 后已移除全部监测路径", w._watch.directories() == [],
          w._watch.directories())
    w.start()
    check("stop 后可再次 start", w.is_running() is True)
    w.stop()


def t_snapshot():
    d = new_dir("snap")
    use(d)
    w = watch.DesktopWatcher()
    s0 = w.snapshot()
    check("snapshot 返回 frozenset", isinstance(s0, frozenset), type(s0))
    check("空目录 snapshot 为空", s0 == frozenset(), s0)
    touch(os.path.join(d, "a.txt"))
    s1 = w.snapshot()
    check("snapshot 反映新增文件", ("a.txt") in [n for _p, n in s1], s1)
    touch(os.path.join(d, "b.txt"))
    s2 = w.snapshot()
    check("snapshot 内容随文件变化", s2 != s1 and len(s2) == len(s1) + 1,
          (len(s1), len(s2)))
    check("snapshot 元素是 (目录, 名字)",
          all(isinstance(p, str) and isinstance(n, str) for p, n in s2), s2)


def t_fs_event():
    d = new_dir("ev1")
    use(d)
    w = watch.DesktopWatcher()
    w.start()
    try:
        rec = Rec(w)
        touch(os.path.join(d, "one.txt"))
        pump(1000)
        check("写一个文件后 1 秒内收到 changed",
              len(rec.hits) >= 1, rec.hits)
        check("changed 原因是 fs", rec.hits and rec.hits[0] == "fs", rec.hits)
    finally:
        w.stop()


def t_debounce():
    d = new_dir("ev2")
    use(d)
    w = watch.DesktopWatcher()
    w.start()
    try:
        rec = Rec(w)
        for i in range(5):
            touch(os.path.join(d, "burst%d.txt" % i))
        pump(1200)
        check("连写 5 个文件只收到一次 changed", len(rec.hits) == 1, rec.hits)
    finally:
        w.stop()


def t_delete_event():
    d = new_dir("ev3")
    p = touch(os.path.join(d, "gone.txt"))
    use(d)
    w = watch.DesktopWatcher()
    w.start()
    try:
        pump(200)
        rec = Rec(w)
        os.remove(p)
        pump(1000)
        check("删除文件也触发 changed", len(rec.hits) >= 1, rec.hits)
    finally:
        w.stop()


def t_refresh_after_dir_gone():
    d = new_dir("ev4")
    use(d)
    w = watch.DesktopWatcher()
    w.start()
    try:
        pump(200)
        shutil.rmtree(d, ignore_errors=True)
        ok = True
        try:
            w.refresh()
        except Exception as e:
            ok = False
            check("被监测目录删除后 refresh 不抛异常", False, e)
        if ok:
            check("被监测目录删除后 refresh 不抛异常", True)
        check("目录没了 is_running 仍为 True", w.is_running() is True)
        check("refresh 后不再挂已消失的目录", w._watch.directories() == [],
              w._watch.directories())
        d2 = new_dir("ev4")
        use(d2)
        w.refresh()
        check("目录重建后 refresh 重新挂上",
              set(w._watch.directories()) == {d2}, w._watch.directories())
        os.makedirs(d, exist_ok=True)
    finally:
        w.stop()


def t_poll_fallback():
    """轮询兜底：watcher 被清空后仍能靠轮询发现变化。"""
    d = new_dir("ev5")
    use(d)
    w = watch.DesktopWatcher()
    w.start()
    try:
        old = watch.POLL_MS
        w._poll.setInterval(80)
        w._watch.removePaths(w._watch.directories())
        pump(200)
        rec = Rec(w)
        touch(os.path.join(d, "poll.txt"))
        pump(700)
        check("watcher 掉线后轮询仍报 changed", len(rec.hits) >= 1, rec.hits)
        check("轮询原因标记为 poll", "poll" in rec.hits, rec.hits)
        check("轮询自愈后目录被重新挂上",
              set(w._watch.directories()) == {d}, w._watch.directories())
        w._poll.setInterval(old)
    finally:
        w.stop()


def t_poll_no_dup():
    """轮询：无变化时绝不 emit。"""
    d = new_dir("ev6")
    use(d)
    w = watch.DesktopWatcher()
    w.start()
    try:
        w._poll.setInterval(80)
        pump(200)
        rec = Rec(w)
        pump(600)
        check("无变化时轮询不 emit", rec.hits == [], rec.hits)
    finally:
        w.stop()


def t_bad_dir():
    """不存在的目录不让 start 炸。"""
    ghost = os.path.join(TMP, "no_such_dir_9931")
    use(ghost)
    w = watch.DesktopWatcher()
    try:
        w.start()
        check("目录不存在 start 不抛异常", True)
    except Exception as e:
        check("目录不存在 start 不抛异常", False, e)
    check("目录不存在时 is_running 仍为 True", w.is_running() is True)
    check("目录不存在时未挂任何路径", w._watch.directories() == [],
          w._watch.directories())
    w.stop()


def main():
    print("临时工作区:", TMP)
    print("-" * 60)
    t_desktop_dirs()
    t_start_stop()
    t_snapshot()
    t_fs_event()
    t_debounce()
    t_delete_event()
    t_refresh_after_dir_gone()
    t_poll_fallback()
    t_poll_no_dup()
    t_bad_dir()
    print("-" * 60)
    if _fail:
        print("FAIL 汇总: %d/%d 项失败 -> %s" % (len(_fail), _n[0], _fail))
        return 1
    print("全部通过 %d/%d" % (_n[0], _n[0]))
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
