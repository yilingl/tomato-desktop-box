# -*- coding: utf-8 -*-
"""deskbox/tray.py — 系统托盘（通知区）图标封装。

只干一件事：把 QSystemTrayIcon + 右键菜单包成一层薄壳，把用户动作转成回调。

用户已拍板的行为（不改）：
    - 左键单击托盘图标 = 显示 / 隐藏主面板  -> on_toggle_panel（切换）
    - 右键菜单五项，顺序固定：
        1 显示主面板      只显不隐 -> on_show_panel（面板已经露着时点它也不会把它藏起来）
        2 显示全部小窗口  -> on_show_all_minis
        3 任务栏透明      子菜单，三档单选打勾（磨砂 / 透明 / 关）-> on_tb_mode(mode)
                          内存值 "blur" / "clear" / "off"，默认勾「关」
        4 设置            -> on_settings
        5 退出            -> on_quit（真退不退由调用方决定）
    - 图标 QIcon 由调用方传入（本模块不生成图标、不读图标文件）

环境不可用时（离屏 QT_QPA_PLATFORM=offscreen / 无通知区的会话，
QSystemTrayIcon.isSystemTrayAvailable() 为 False）整块降级：构造不抛，
show() / hide() 空转，is_visible() 恒 False，set_icon() 只记不显。
菜单对象仍然建得出来（离屏也能读文案与勾选），只是不往通知区挂。
风格对齐 ui_mock.py 里 minibox(MB) 缺失的处理。

「任务栏透明」只是把菜单项画出来、把用户点的档位回调出去；
真正去调 SetWindowCompositionAttribute 的活由 deskbox/taskbarfx.py 干，本模块不 import 它。

本模块不移动、不复制、不删除任何桌面文件，也不读任何文件。
"""
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

TOOLTIP = "Tomato Desktop Box"
MENU_LABELS = ("显示主面板", "显示全部小窗口", "任务栏透明", "设置", "退出")
TB_TITLE = "任务栏透明"
TB_MODES = ("blur", "clear", "off")
TB_LABELS = ("磨砂", "透明", "关")


def _norm_tb_mode(mode):
    """档位归一：只认 TB_MODES 里的三个字符串，其它（含 None）一律当 "off"。"""
    return mode if mode in TB_MODES else "off"


class Tray(QSystemTrayIcon):
    """托盘图标。parent = 主面板（QWidget 或 None），icon = 调用方给的 QIcon。"""

    def __init__(self, parent, icon, on_toggle_panel=None, on_show_panel=None,
                 on_show_all_minis=None, on_settings=None, on_quit=None,
                 on_tb_mode=None, tb_mode=None):
        # 先立降级标志：万一底层对象没建成，后续一切调用都走空转分支，不炸。
        self._broken = True
        self.menu = None
        self._tb_menu = None
        self._tb_action = None
        self._tb_group = None
        self._tb_actions = {}
        self._tb_mode = "off"
        try:
            super().__init__(icon, parent)
        except Exception:              # noqa: BLE001 —— 底层造不出来就整块降级
            return
        try:
            self._toggle = on_toggle_panel
            self._on_tb = on_tb_mode
            self._tb_mode = _norm_tb_mode(tb_mode)
            self.setToolTip(TOOLTIP)
            self.menu = self._build_menu(on_show_panel, on_show_all_minis,
                                         on_settings, on_quit)
            self.setContextMenu(self.menu)   # self.menu 持引用，菜单不被 GC
            self.activated.connect(self._on_activated)
            self._broken = False
        except Exception:              # noqa: BLE001
            self.menu = None
            self._tb_menu = None
            self._tb_action = None
            self._tb_actions = {}
            self._broken = True

    # ---------------------------------------------------------------- 生成
    def _build_menu(self, on_show_panel, on_show_all_minis, on_settings, on_quit):
        """五项，文案与顺序固定；QAction 由 menu 持有，menu 由 self.menu 持有。

        第 3 项「任务栏透明」是子菜单（_build_tb_menu 造），其余四项接各自的回调。
        「显示主面板」接的是 on_show_panel（只显不隐）；
        左键单击的显示 / 隐藏切换是另一条线（self._toggle），别接混。
        """
        menu = QMenu(self.parent())
        fns = {MENU_LABELS[0]: on_show_panel,
               MENU_LABELS[1]: on_show_all_minis,
               MENU_LABELS[3]: on_settings,
               MENU_LABELS[4]: on_quit}
        for text in MENU_LABELS:
            if text == TB_TITLE:
                self._tb_menu = self._build_tb_menu(menu)
                self._tb_action = menu.addMenu(self._tb_menu)
                continue
            act = menu.addAction(text)
            fn = fns.get(text)
            if callable(fn):
                act.triggered.connect(lambda _checked=False, f=fn: self._fire(f))
        return menu

    def _build_tb_menu(self, parent_menu):
        """三档互斥勾选：QActionGroup 保证同一时刻只有一项带勾。

        子菜单挂 parent_menu 做父对象、self._tb_menu 再持一份引用，GC 后文案仍在。
        """
        sub = QMenu(TB_TITLE, parent_menu)
        group = QActionGroup(sub)
        group.setExclusive(True)
        acts = {}
        for mode, label in zip(TB_MODES, TB_LABELS):
            act = sub.addAction(label)
            act.setCheckable(True)
            group.addAction(act)
            acts[mode] = act
            act.triggered.connect(lambda _checked=False, m=mode: self._on_tb_pick(m))
        self._tb_group = group
        self._tb_actions = acts
        self._apply_tb_mode(self._tb_mode)     # 建菜单时先把勾打在建菜单的档位上
        return sub

    def _apply_tb_mode(self, mode):
        """把勾落到底层 QAction 上；三档互斥，其余自动取消。"""
        for m, act in self._tb_actions.items():
            act.setChecked(m == mode)

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

    def set_tb_mode(self, mode):
        """外部（设置面板改了档）刷新勾选：非法值当「关」。

        注意判「不可用」只看菜单建没建出来，不看 isSystemTrayAvailable() ——
        离屏 / 没通知区时菜单照样存在，勾选也得跟得上，只是不挂到通知区。
        真降级（没菜单）时静默返回 False。
        """
        if self._broken or not self._tb_actions:
            return False
        try:
            mode = _norm_tb_mode(mode)
            self._apply_tb_mode(mode)
            self._tb_mode = mode
            return True
        except Exception:              # noqa: BLE001
            return False

    def set_tb_enabled(self, enabled, tip=""):
        """「任务栏透明」整项启用 / 禁用（将来 taskbarfx 缺失时用），可带一句 tooltip。"""
        act = self._tb_action
        if act is None:
            return False
        try:
            act.setEnabled(bool(enabled))
            act.setToolTip(tip or "")
            return True
        except Exception:              # noqa: BLE001
            return False

    # ---------------------------------------------------------------- 内部
    def _fire(self, fn, *args):
        """回调兜底：调用方给的回调炸了不许影响托盘。"""
        try:
            fn(*args)
        except Exception:              # noqa: BLE001
            pass

    def _on_tb_pick(self, mode):
        """用户点了某一档：记住档位再回调（互斥勾选由 QActionGroup 自己完成）。"""
        self._tb_mode = mode
        fn = getattr(self, "_on_tb", None)
        if callable(fn):
            self._fire(fn, mode)

    def _on_activated(self, reason):
        """只认左键单击（Trigger）：双击 / 中键 / 右键都不重复触发。"""
        if self._broken:
            return
        fn = getattr(self, "_toggle", None)
        if callable(fn) and reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._fire(fn)
