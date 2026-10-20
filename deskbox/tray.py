# -*- coding: utf-8 -*-
"""deskbox/tray.py — 系统托盘（通知区）图标封装。

只干一件事：把 QSystemTrayIcon + 右键菜单包成一层薄壳，把用户动作转成回调。

用户已拍板的行为（不改）：
    - 左键单击托盘图标 = 显示 / 隐藏主面板  -> on_toggle_panel（切换）
    - 右键菜单四项，顺序固定：显示主面板 / 显示全部小窗口 / 设置 / 退出
      「显示主面板」是**只显不隐** -> on_show_panel（面板已经露着时点它也不会把它藏起来）
    - 退出 = 只触发 on_quit，真退不退（QApplication.quit）由调用方决定
    - 图标 QIcon 由调用方传入（本模块不生成图标、不读图标文件）

环境不可用时（离屏 QT_QPA_PLATFORM=offscreen / 无通知区的会话，
QSystemTrayIcon.isSystemTrayAvailable() 为 False）整块降级：构造不抛，
show() / hide() 空转，is_visible() 恒 False，set_icon() 只记不显。
风格对齐 ui_mock.py 里 minibox(MB) 缺失的处理。

本模块不移动、不复制、不删除任何桌面文件，也不读任何文件。
"""
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

TOOLTIP = "Tomato Desktop Box"
MENU_LABELS = ("显示主面板", "显示全部小窗口", "设置", "退出")


class Tray(QSystemTrayIcon):
    """托盘图标。parent = 主面板（QWidget 或 None），icon = 调用方给的 QIcon。"""

    def __init__(self, parent, icon, on_toggle_panel=None, on_show_panel=None,
                 on_show_all_minis=None, on_settings=None, on_quit=None):
        # 先立降级标志：万一底层对象没建成，后续一切调用都走空转分支，不炸。
        self._broken = True
        self.menu = None
        try:
            super().__init__(icon, parent)
        except Exception:              # noqa: BLE001 —— 底层造不出来就整块降级
            return
        try:
            self._toggle = on_toggle_panel
            self.setToolTip(TOOLTIP)
            self.menu = self._build_menu(on_show_panel, on_show_all_minis,
                                         on_settings, on_quit)
            self.setContextMenu(self.menu)   # self.menu 持引用，菜单不被 GC
            self.activated.connect(self._on_activated)
            self._broken = False
        except Exception:              # noqa: BLE001
            self.menu = None
            self._broken = True

    # ---------------------------------------------------------------- 生成
    def _build_menu(self, on_show_panel, on_show_all_minis, on_settings, on_quit):
        """四项，文案与顺序固定；QAction 由 menu 持有，menu 由 self.menu 持有。

        「显示主面板」接的是 on_show_panel（只显不隐）；
        左键单击的显示 / 隐藏切换是另一条线（self._toggle），别接混。
        """
        menu = QMenu(self.parent())
        fns = (on_show_panel, on_show_all_minis, on_settings, on_quit)
        for text, fn in zip(MENU_LABELS, fns):
            act = menu.addAction(text)
            if callable(fn):
                act.triggered.connect(lambda _checked=False, f=fn: self._fire(f))
        return menu

    # ---------------------------------------------------------------- 可用性
    def available(self):
        """有通知区且底层对象建起来了；不可用时对外方法全是空转。"""
        if self._broken:
            return False
        try:
            return bool(QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:              # noqa: BLE001
            return False

    # ---------------------------------------------------------------- 对外
    def show(self):
        if not self.available():
            return False
        try:
            super().show()
            return True
        except Exception:              # noqa: BLE001
            return False

    def hide(self):
        if not self.available():
            return False
        try:
            super().hide()
            return True
        except Exception:              # noqa: BLE001
            return False

    def is_visible(self):
        if not self.available():
            return False
        try:
            return bool(super().isVisible())
        except Exception:              # noqa: BLE001
            return False

    def set_icon(self, icon):
        """换图标：不可用时也不抛（照记，只是不会显出来）。"""
        try:
            super().setIcon(icon)
            return True
        except Exception:              # noqa: BLE001
            return False

    # ---------------------------------------------------------------- 内部
    def _fire(self, fn):
        """回调兜底：调用方给的回调炸了不许影响托盘。"""
        try:
            fn()
        except Exception:              # noqa: BLE001
            pass

    def _on_activated(self, reason):
        """只认左键单击（Trigger）：双击 / 中键 / 右键都不重复触发。"""
        if self._broken:
            return
        fn = getattr(self, "_toggle", None)
        if callable(fn) and reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._fire(fn)
