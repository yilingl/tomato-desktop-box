# -*- coding: utf-8 -*-
"""桌面实时监测：QFileSystemWatcher 做主（即时）+ 低频轮询兜底（防漏事件）。

只报「变了」，不判断文件类型，分类与显示交给 UI。
"""
import os

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

try:
    from . import store
except ImportError:
    import store

DEBOUNCE_MS = 400     # 抖动合并窗
POLL_MS = 3000        # 兜底轮询周期


def desktop_dirs():
    """当前要监测的目录：每次现取 store.DESK_DIRS，只留真实存在的。"""
    out = []
    try:
        cand = list(store.DESK_DIRS)
    except Exception:
        return out
    for d in cand:
        try:
            if d and os.path.isdir(d):
                out.append(d)
        except Exception:
            continue
    return out


class DesktopWatcher(QObject):
    """桌面目录变化播报器。changed 的参数是原因：\"fs\" / \"poll\"。"""

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_error = ""
        self._running = False
        self._base = frozenset()
        self._watch = QFileSystemWatcher(self)
        self._watch.directoryChanged.connect(self._on_fs)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._on_debounce)
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._on_poll)

    # ------------------------------------------------------------ 生命周期
    def start(self):
        """幂等：重复调用只补齐缺失的监测路径。"""
        if self._running:
            return
        self._running = True
        self._sync_paths()
        self._base = self.snapshot()
        self._poll.start()

    def stop(self):
        """幂等：停掉全部定时器并移除监测路径。"""
        if not self._running:
            return
        self._running = False
        self._debounce.stop()
        self._poll.stop()
        have = self._watch.directories()
        if have:
            try:
                self._watch.removePaths(have)
            except Exception as e:
                self.last_error = "移除监测路径失败: %s" % e

    def refresh(self):
        """重新对齐目录列表（目录被删/新建后调用），并刷新基线。"""
        self._sync_paths()
        self._base = self.snapshot()

    def is_running(self):
        return self._running

    # ------------------------------------------------------------ 路径维护
    def _reset(self):
        """换一个全新的 QFileSystemWatcher。

        目录被删掉后 Qt 的 removePath 会失败（返回 False 且路径仍留在列表里），
        旧的 native 句柄已经死了，只能整个换掉。
        """
        old = self._watch
        fresh = QFileSystemWatcher(self)
        fresh.directoryChanged.connect(self._on_fs)
        self._watch = fresh
        try:
            old.directoryChanged.disconnect(self._on_fs)
        except Exception:
            pass
        try:
            old.deleteLater()
        except Exception:
            pass

    def _sync_paths(self):
        want = desktop_dirs()
        try:
            have = self._watch.directories()
        except Exception:
            have = []
        stale = [d for d in have if d not in want]
        if stale:
            try:
                self._watch.removePaths(stale)
            except Exception as e:
                self.last_error = "移除监测路径失败: %s" % e
            left = [d for d in self._watch.directories() if d not in want]
            if left:
                self.last_error = "目录已消失，重建监测器: %s" % ", ".join(left)
                self._reset()
            have = self._watch.directories()
        missing = [d for d in want if d not in have]
        if not missing:
            return
        try:
            bad = self._watch.addPaths(missing)
        except Exception as e:
            self.last_error = "添加监测路径失败: %s" % e
            return
        if bad:
            self.last_error = "添加监测路径失败: %s" % ", ".join(bad)

    # ------------------------------------------------------------ 快照
    def snapshot(self):
        """当前 (目录, 名字) 集合，用于轮询比较。"""
        s = set()
        for d in desktop_dirs():
            try:
                names = os.listdir(d)
            except Exception:
                continue
            for n in names:
                s.add((d, n))
        return frozenset(s)

    # ------------------------------------------------------------ 事件
    def _on_fs(self, _path=""):
        if not self._running:
            return
        self._debounce.start()          # 重启即合并，一次连写只发一次

    def _on_debounce(self):
        if not self._running:
            return
        try:
            self._base = self.snapshot()
            self.changed.emit("fs")
        except Exception as e:
            self.last_error = "广播失败: %s" % e

    def _on_poll(self):
        if not self._running:
            return
        ok = True
        try:
            cur = self.snapshot()
        except Exception as e:
            self.last_error = "轮询失败: %s" % e
            ok = False
            cur = None
        if ok and cur != self._base:
            self._base = cur
            try:
                self.changed.emit("poll")
            except Exception as e:
                self.last_error = "广播失败: %s" % e
        # watcher 自愈：丢了目录就补回来，别只靠轮询
        try:
            if set(self._watch.directories()) != set(desktop_dirs()):
                self._sync_paths()
                self._base = self.snapshot()
        except Exception as e:
            self.last_error = "自愈失败: %s" % e
