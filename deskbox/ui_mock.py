# -*- coding: utf-8 -*-
"""Tomato Desktop Box — UI 层：自绘玻璃 + 虚拟分组桌面。

背景：自绘玻璃（glass.py），三档 —— 玻璃（默认，透模糊壁纸）/ 深色 / 浅色。
图标：Shell IShellItemImageFactory 取 256px 真图标（icons.py），不再糊。

重要：本程序不移动、不复制、不删除任何桌面文件。
    - 分组只写 deskbox/groupbook.py 的账本（%APPDATA%/DeskBox/groups.json）
    - 「一键导入桌面」= 给图标写分组记录，不是搬文件
    - 隐藏桌面图标 = 向 SHELLDLL_DefView 发 WM_COMMAND，也不动文件
    - 复制 / 剪切 / 属性 走系统原生右键菜单（shellmenu.py）
    - 重命名由 Tomato Desktop Box 自己做（store.rename），原生菜单里的「重命名」也接管到这里
    - 唯一会真删文件的是「删除到回收站」（用户明确保留）
    - 小窗口（MiniWindow）只往 minis.json 里登记路径，同样不动任何文件
"""
import ctypes
import importlib
import json
import os
import re
import sys

from PySide6.QtCore import (Qt, QRectF, QRect, QSize, QPointF, QTimer, QPoint,
                            QEvent, QMimeData, QUrl, Signal)
from PySide6.QtGui import (QColor, QPainter, QFont, QPen, QAction, QGuiApplication,
                           QFontMetrics, QDrag, QPainterPath)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                               QColorDialog, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QSlider, QStyledItemDelegate, QStyle, QSizeGrip,
                               QFrame, QAbstractItemView, QListView, QDialog,
                               QMenu, QGraphicsOpacityEffect, QApplication,
                               QCheckBox)



def _rel(name):
    """拿同级模块 (mod, err)。包内导入与脚本直跑都支持，缺失不抛。

    包内：from deskbox import ui_mock  -> __package__ = "deskbox"
    直跑：DeskBox.pyw 把 deskbox/ 塞进 sys.path 后 import ui_mock
    """
    if __package__:
        try:
            return importlib.import_module("." + name, __package__), None
        except Exception:              # noqa: BLE001
            pass
    try:
        return importlib.import_module(name), None
    except Exception as exc:           # noqa: BLE001 —— 缺文件 / 语法错都要能跑 UI
        return None, str(exc)


icons, _ICONS_ERR = _rel("icons")
glass, _GLASS_ERR = _rel("glass")
if icons is None or glass is None:
    raise RuntimeError("icons/glass 模块缺失：%s" % (_ICONS_ERR or _GLASS_ERR))

STORE, _STORE_ERR = _rel("store")      # 只用于扫描桌面 / 取图标分类 / 开文件 / 回收站
GB, _GB_ERR = _rel("groupbook")        # 虚拟分组账本（唯一的分组真相）
HD, _HD_ERR = _rel("hidedesk")         # 隐藏 / 显示桌面图标
SM, _SM_ERR = _rel("shellmenu")        # 系统原生右键菜单（含第三方扩展 verb）
WATCH, _WATCH_ERR = _rel("watch")      # 桌面目录实时监测（缺失就退化成手动 ⟳）
CATS, _CATS_ERR = _rel("cats")         # 分类注册表（新建 / 删除自定义分类）
SYS, _SYS_ERR = _rel("sysitems")       # 系统自带图标（此电脑 / 回收站）；缺失则整类不出现
MB, _MB_ERR = _rel("minibox")          # 小窗口清单（minis.json）；缺失则小窗口整块降级
AUTO, _AUTO_ERR = _rel("autostart")    # 开机自启动（HKCU 的 Run 键）
TRAY, _TRAY_ERR = _rel("tray")         # 系统托盘图标（通知区）
APPICON, _APPICON_ERR = _rel("appicon")   # 程序图标（窗口 / 托盘，自带降级画法）
TBFX, _TBFX_ERR = _rel("taskbarfx")    # 任务栏透明（纯 ctypes，缺失则整行降级）

ACCENT = QColor("#0A84FF")
FAMILY = "Microsoft YaHei UI"
RADIUS = 16
# 设置面板左侧文字 chip 的统一尺寸。宽度 = QFontMetrics(11px "Microsoft YaHei UI")
# .horizontalAdvance("小窗口自动粘连") = 77，再留 12px 内边距 = 89；高度沿用「背景」原来的 30。
# 五处（背景 / 开机自启动 / 最小化到托盘 / 点关闭 / 小窗口自动粘连）都引用这两个常量。
_CHIP_W = 89
_CHIP_H = 30
VERSION = "2.2.0"
# 截图 / 自动化时压制启动自检弹窗（--shot 会自动打开）
NO_HIDE_PROMPT = False


def _classify_fn():
    """store.classify 的现取引用：测试 monkeypatch 过也能生效。只读，不落盘。"""
    fn = getattr(STORE, "classify", None) if STORE is not None else None
    return fn if callable(fn) else None


def _auto_enabled():
    """开机自启动的**现读**状态（autostart.is_enabled 自己拿实际命令比注册表）。

    模块缺失 / 读注册表抛异常一律按「关」显示 —— 不拿本地标志冒充开关状态。
    """
    fn = getattr(AUTO, "is_enabled", None) if AUTO is not None else None
    if not callable(fn):
        return False
    try:
        return bool(fn())
    except Exception:                  # noqa: BLE001
        return False


def _tray_ok(tray):
    """托盘对象是否真的能用（对象在 + available() 为真）。异常一律当不可用。"""
    fn = getattr(tray, "available", None)
    if not callable(fn):
        return False
    try:
        return bool(fn())
    except Exception:                  # noqa: BLE001
        return False


def _release_tb():
    """程序退出（任何一条路径）时把任务栏还原成系统默认。

    任务栏外观是**系统级**状态：盒子走了却把它留在透明态，等于在用户桌面上留下
    一个没人负责的改动。所以正常退出一定还原；崩溃时这条走不到，由压缩包里的
    ResetTaskbar.exe 兜底。
    """
    if TBFX is None:
        return
    fn = getattr(TBFX, "release", None)
    if not callable(fn):
        return
    try:
        fn()
    except Exception:                  # noqa: BLE001 —— 退出路径上绝不能抛
        pass


def _unavail_note(row, why):
    """模块缺失时给该行摆一个写清原因的灰字：不摆一个看着能用的开关。"""
    na = QLabel("不可用")
    f = QFont(FAMILY)
    f.setPixelSize(11)
    na.setFont(f)
    na.setToolTip(why)
    _theme(na, lambda m: "color:%s;" % glass.inkcss(96, m))
    row.addWidget(na)
    return na



# 文字色跟随当前风格（深色档白 / 浅色档近黑），不要写成死常量
def _t1():
    return glass.ink(235)


def _t2():
    return glass.ink(152)


def _t3():
    return glass.ink(96)

# 分类配色：沿用视觉稿 CATS，缺的补 #8E8E93
CAT_COLORS = {"全部": "#8E8E93", "应用": "#0A84FF", "压缩包": "#FF9F0A",
              "文档": "#30D158", "图片": "#FF375F", "音视频": "#BF5AF2",
              "脚本": "#64D2FF", "文件夹": "#FFD60A", "未分类": "#8E8E93",
              "系统应用": "#5E5CE6"}
# 自定义分类没有预设色时，按名字哈希从这个 10 色板里取（同名永远同色）
CAT_PALETTE = ["#0A84FF", "#FF9F0A", "#30D158", "#FF375F", "#BF5AF2",
               "#64D2FF", "#FFD60A", "#5E5CE6", "#FF6482", "#32D74B"]
# 分类注册表不可用时的兜底分类（只影响侧栏显示，不做任何分类判断）
FALLBACK_GROUPS = [("应用", "apps"), ("压缩包", "archives"), ("文档", "docs"),
                   ("图片", "images"), ("音视频", "media"), ("脚本", "scripts"),
                   ("文件夹", "folders"), ("未分类", "_unsorted")]
UNSORTED = "未分类"
REMOVED = "已移除"                 # 面板里手动移除的条目：只记 ui.json 名单，文件原地不动
SYS_CAT_FALLBACK = "系统应用"


# ───────────────────────── 窗口状态持久化 ─────────────────────────
DEFAULT_STATE = {"x": 70, "y": 60, "w": 1120, "h": 680,
                 "density": 58, "pinned": False, "icon_idx": 1, "bg": "clear",
                 "solid": "#20222c", "snap": True, "tray": True, "close_tray": True,
                 "tb_mode": "off",   # 任务栏外观：blur 磨砂 / clear 透明 / off 关（默认关）
                 "removed": [], "gone": [],
                 "order": {},      # 界面顺序：scope -> [路径...]，纯显示用，不进账本
                 "hide_warned": False, "hide_quiet": False,
                 "maxed": False, "nx": 0, "ny": 0, "nw": 1120, "nh": 680}
# 背景风格（字段名沿用 bg，兼容旧 ui.json）：
#   clear = 玻璃（采样壁纸 + 模糊 + 半透明白纱，默认）；night = 黑玻璃
#           （同样采样壁纸，蒙一层深纱：壁纸只剩被压暗的色相）
#   frost = 深色（纯自绘，不采样壁纸）；light = 浅色（纯自绘）
#   solid = 纯色（用户自己挑底色，透明度直接换算成 alpha，最透时窗口只剩图标）
BG_MODES = tuple(glass.MODES)
# 旧版本的 "wall"（透壁纸）已下线，读到一律迁移成 "frost"
BG_LEGACY = {"wall": "frost"}

# ── 任务栏外观（taskbarfx）─────────────────────────────────────────
# 三档，顺序固定，与托盘子菜单、设置行共用这一份。taskbarfx 缺失时本模块仍要能跑：
# 下面这份是**本地兜底副本**，写得跟 taskbarfx.MODES 一样，不依赖它 import 成功。
TB_MODES = ("blur", "clear", "off")
TB_LABELS = ("磨砂", "透明", "关")
TB_DEFAULT = "off"                 # 默认档位：关（用户定的，不自作主张开）


def bg_mode():
    """当前风格名，直接取自 glass。"""
    return glass.style_name()


def set_bg_mode(m):
    """切换全局背景风格；非法值忽略。"""
    glass.set_style(str(m or ""))


def _theme(widget, builder):
    """登记主题函数：builder(mode) -> qss。切风格时能重套，不必重开窗口。

    存在控件自身属性上（不存全局列表），控件销毁后不会留下悬空引用。
    """
    widget._theme_builder = builder
    try:
        widget.setStyleSheet(builder(bg_mode()))
    except Exception:                  # noqa: BLE001 —— 主题失败不能拖崩界面
        pass


_RE_WHITE_RGBA = re.compile(r"^rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*(\d+)\s*\)$")
_WHITE_HEX = ("#fff", "#ffffff")


def _white_alpha(color):
    """视觉稿时代的白色淡彩 -> alpha 值；不是白色淡彩则返回 None。"""
    if not isinstance(color, str):
        return None
    s = color.strip().lower()
    if s in _WHITE_HEX:
        return 235
    m = _RE_WHITE_RGBA.match(s)
    if m:
        return int(m.group(1))
    return None


def _inkcss_of(color, mode=None):
    """把颜色换算成随风格反转的 CSS 字符串。

    白色淡彩（视觉稿遗留）按 alpha 走 glass.inkcss：深色档白、浅色档近黑；
    彩色（强调蓝、警示橙）与 QColor 原样保留。
    没给颜色（None）= 用当前风格的主文字色 —— 否则会退化成 Qt 默认黑字，
    纯色档选了深色底时黑底黑字，弹窗提示直接看不见。
    """
    if color is None:
        return glass.inkcss(232, mode)
    a = _white_alpha(color)
    if a is not None:
        return glass.inkcss(a, mode)
    if isinstance(color, QColor):
        return color.name(QColor.HexArgb) if color.alpha() < 255 else color.name()
    return str(color)


def _apply_theme_all():
    """按当前风格把所有登记过的控件样式重套一遍。"""
    m = bg_mode()
    for w in QApplication.allWidgets():
        b = getattr(w, "_theme_builder", None)
        if b is None:
            continue
        try:
            w.setStyleSheet(b(m))
        except Exception:              # noqa: BLE001
            pass


MIN_W, MIN_H = 760, 480
# 旧版竖长窗（644x862）—— 读到就迁移成新的长方形默认值
LEGACY_W, LEGACY_H = 644, 862


def _ui_json_path():
    """%APPDATA%/DeskBox/ui.json；DESKBOX_UI_JSON 可覆盖（自检用）。"""
    p = os.environ.get("DESKBOX_UI_JSON")
    if p:
        return p
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "DeskBox", "ui.json")


def _load_state():
    """读回窗口状态；读失败或字段越界一律退回 DEFAULT_STATE。"""
    st = dict(DEFAULT_STATE)
    st["order"] = dict(st.get("order") or {})    # 可变值必须另起一份，别写回 DEFAULT_STATE
    first = False
    try:
        with open(_ui_json_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:                  # noqa: BLE001 —— 缺失 / 坏 JSON / 权限
        first = True
        st["x"], st["y"] = _center_pos(st["w"], st["h"])
        return st
    if not isinstance(raw, dict):
        return st
    for k in ("x", "y", "w", "h", "density", "icon_idx"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            st[k] = int(v)
    if isinstance(raw.get("pinned"), bool):
        st["pinned"] = raw["pinned"]
    if isinstance(raw.get("hide_warned"), bool):
        st["hide_warned"] = raw["hide_warned"]
    elif isinstance(raw.get("hide_tip_shown"), bool):   # 旧字段
        st["hide_warned"] = raw["hide_tip_shown"]
    if isinstance(raw.get("hide_quiet"), bool):
        st["hide_quiet"] = raw["hide_quiet"]
    if not (MIN_W <= st["w"] <= 4000 and MIN_H <= st["h"] <= 4000):
        for k in ("x", "y", "w", "h"):
            st[k] = DEFAULT_STATE[k]
    elif (st["w"], st["h"]) == (LEGACY_W, LEGACY_H) or \
            (st["w"] < 820 and st["h"] > 800):
        # 旧默认竖窗迁移：只换尺寸，用户设过的置顶/透明度/图标档位都留着
        st["w"], st["h"] = DEFAULT_STATE["w"], DEFAULT_STATE["h"]
        st["x"], st["y"] = _center_pos(st["w"], st["h"])
    _bgv = raw.get("bg")
    if _bgv in BG_LEGACY:              # 旧版 "wall"（透壁纸）→ 风格1
        st["bg"] = BG_LEGACY[_bgv]
    elif _bgv in BG_MODES:
        st["bg"] = _bgv
    if not 0 <= st["density"] <= 100:
        st["density"] = DEFAULT_STATE["density"]
    if st["icon_idx"] not in (0, 1, 2):
        st["icon_idx"] = DEFAULT_STATE["icon_idx"]
    if isinstance(raw.get("snap"), bool):        # 小窗口自动粘连开关
        st["snap"] = raw["snap"]
    if isinstance(raw.get("tray"), bool):        # 最小化到托盘开关
        st["tray"] = raw["tray"]
    if isinstance(raw.get("close_tray"), bool):  # 点关闭：收进托盘 / 退出
        st["close_tray"] = raw["close_tray"]
    if raw.get("tb_mode") in TB_MODES:           # 任务栏外观：旧文件没有 / 坏值一律留 "off"
        st["tb_mode"] = raw["tb_mode"]
    _sv = raw.get("solid")
    if isinstance(_sv, str) and QColor(_sv).isValid():
        st["solid"] = _sv
    _rv = raw.get("removed")                     # 手动移除名单（归一后的路径）
    if isinstance(_rv, list):
        st["removed"] = [x for x in _rv if isinstance(x, str)]
    _gv = raw.get("gone")                        # 彻底移除名单：程序里彻底不露面
    if isinstance(_gv, list):
        st["gone"] = [x for x in _gv if isinstance(x, str)]
    _ov = raw.get("order")                       # 界面顺序：可选新字段，旧文件没有
    if isinstance(_ov, dict):                    # 坏值（非 dict / 非 list）一律忽略
        st["order"] = {str(k): [x for x in v if isinstance(x, str)]
                       for k, v in _ov.items() if isinstance(v, list)}
    # 最大化态：存 maxed + 还原用的普通几何 (nx, ny, nw, nh)
    for k in ("nx", "ny", "nw", "nh"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            st[k] = int(v)
    if isinstance(raw.get("maxed"), bool) and raw["maxed"] and \
            MIN_W <= st["nw"] <= 4000 and MIN_H <= st["nh"] <= 4000:
        st["maxed"] = True
    else:
        st["maxed"] = False
    try:                               # 跑到屏幕外就重新居中
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            av = scr.availableGeometry().adjusted(-200, -200, 200, 200)
            if not av.contains(QRect(st["x"], st["y"], st["w"], st["h"])):
                st["x"], st["y"] = _center_pos(st["w"], st["h"])
    except Exception:                  # noqa: BLE001
        pass
    if st["maxed"]:
        # 开机就是最大化时，普通态几何也要落在屏幕内，否则还原会跑到屏幕外
        try:
            scr = QGuiApplication.primaryScreen()
            if scr is not None:
                av = scr.availableGeometry()
                if not av.contains(QRect(st["nx"], st["ny"], st["nw"], st["nh"])):
                    st["maxed"] = False
        except Exception:              # noqa: BLE001
            st["maxed"] = False
    return st


def _center_pos(w, h):
    """左右居中、垂直略靠上（上留约 1/4 屏幕高）。"""
    try:
        av = QGuiApplication.primaryScreen().availableGeometry()
    except Exception:                  # noqa: BLE001
        return DEFAULT_STATE["x"], DEFAULT_STATE["y"]
    w = max(MIN_W, min(int(w), av.width() - 40))
    h = max(MIN_H, min(int(h), av.height() - 40))
    return av.x() + (av.width() - w) // 2, av.y() + max(40, (av.height() - h) // 4)


def _groups():
    """[(中文名, 目录名)]，来自 cats 注册表；不可用时用兜底表。"""
    if CATS is not None:
        try:
            g = list(CATS.groups())
            if g:
                return g
        except Exception:              # noqa: BLE001
            pass
    return FALLBACK_GROUPS


def _sys_cat():
    """「系统应用」的分类名，跟着 sysitems.SYS.CATEGORY 走；模块缺失时用兜底字面量。"""
    try:
        return SYS.CATEGORY if SYS is not None else SYS_CAT_FALLBACK
    except Exception:                  # noqa: BLE001
        return SYS_CAT_FALLBACK


def _nk(path):
    """路径归一，比较用（跟 groupbook.key_of 同一套规则）。"""
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except Exception:                  # noqa: BLE001
        return str(path)


def _is_virtual(path):
    """'::{CLSID}' 这类系统项：不是文件，不能改分类、不能送回收站、不能定位。"""
    p = str(path or "")
    if p.startswith("::"):             # 快速判据，不依赖模块是否已落地
        return True
    if SYS is not None:
        try:
            return bool(SYS.is_virtual(p))
        except Exception:              # noqa: BLE001
            return False
    return False


def _color_of(name):
    """预设色优先；自定义分类按名字哈希从固定色板取，保证同名同色。"""
    c = CAT_COLORS.get(name)
    if c:
        return c
    h = 0
    for ch in str(name or ""):
        h = (h * 131 + ord(ch)) & 0x7FFFFFFF
    return CAT_PALETTE[h % len(CAT_PALETTE)]


# ───────────────────────── 交通灯 ─────────────────────────
class TrafficLights(QWidget):
    """左下右上三个圆点。顺序按用户要求：最小化 -> 放大 -> 关闭。

    颜色跟着功能走（黄=最小化、绿=放大、红=关闭），不跟着位置走，
    这样红点永远是"关"，不会因为排位置而含义漂移。
    """
    ACTIONS = ("min", "max", "close")
    COLORS = [QColor("#FEBC2E"), QColor("#28C840"), QColor("#FF5F57")]
    GLYPH = ["−", "⤢", "×"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(62, 14)
        self._hover = -1
        self.setMouseTracking(True)

    def _pos(self, i):
        return QPointF(7 + i * 20, 7)

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mouseMoveEvent(self, e):
        h = -1
        for i in range(3):
            if (e.position() - self._pos(i)).manhattanLength() < 9:
                h = i
        if h != self._hover:
            self._hover = h
            self.update()

    def mousePressEvent(self, e):
        w = self.window()
        act = self.ACTIONS[self._hover] if 0 <= self._hover < 3 else ""
        if act == "min":
            # 主面板多了一条「最小化到托盘」：它接得下就收进托盘，接不下照旧缩任务栏
            hook = getattr(w, "_on_min_click", None)
            if callable(hook) and hook():
                return
            w.showMinimized()
        elif act == "max":
            w.showNormal() if w.isMaximized() else w.showMaximized()
        elif act == "close":
            w.close()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i, c in enumerate(self.COLORS):
            base = QColor(c)
            if self._hover != i:
                base.setAlpha(200)
            p.setPen(QPen(QColor(0, 0, 0, 45), 0.8))
            p.setBrush(base)
            p.drawEllipse(self._pos(i), 6.4, 6.4)
            if self._hover == i:
                p.setPen(QPen(QColor(0, 0, 0, 130), 1.3))
                f = QFont(FAMILY)
                f.setPixelSize(9)
                f.setBold(True)
                p.setFont(f)
                p.drawText(QRectF(self._pos(i).x() - 6.4, self._pos(i).y() - 6.4,
                                  12.8, 12.8), Qt.AlignCenter, self.GLYPH[i])


# ───────────────────────── 搜索框 ─────────────────────────
class SearchField(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setPlaceholderText("搜索图标")
        self.setFont(QFont(FAMILY, 9))
        _theme(self, self._qss)

    @staticmethod
    def _qss(m):
        return """
            QLineEdit{background:%s;border:1px solid %s;
                      border-radius:8px;padding-left:27px;padding-right:9px;color:%s;
                      selection-background-color:rgba(10,132,255,140);}
            QLineEdit:focus{background:%s;border:1px solid rgba(10,132,255,160);}
        """ % (glass.panelcss(17, m), glass.panelcss(26, m),
               glass.inkcss(235, m), glass.panelcss(24, m))

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(glass.ink(120), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(12, 15), 4.2, 4.2)
        p.drawLine(QPointF(15.3, 18.3), QPointF(18.2, 21.2))


# ───────────────────────── 分段控件 ─────────────────────────
class Segmented(QWidget):
    def __init__(self, labels, parent=None, seg_w=40):
        super().__init__(parent)
        self.labels = labels
        self.index = 0 if len(labels) == 2 else 1
        self.setFixedHeight(28)
        self.setFixedWidth(len(labels) * seg_w + 4)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._hover = -1
        self._cb = None

    def _rect(self, i):
        w = (self.width() - 4) / len(self.labels)
        return QRectF(2 + i * w, 2, w, self.height() - 4)

    def mouseMoveEvent(self, e):
        h = -1
        for i in range(len(self.labels)):
            if self._rect(i).contains(e.position()):
                h = i
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        for i in range(len(self.labels)):
            if self._rect(i).contains(e.position()):
                self.index = i
                self.update()
                if self._cb:
                    self._cb(i)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(glass.panel(15))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 7, 7)
        for i, lab in enumerate(self.labels):
            r = self._rect(i)
            if i == self.index:
                p.setBrush(glass.panel(56))
                p.setPen(QPen(glass.panel(20), 0.8))
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
            elif i == self._hover:
                p.setBrush(glass.panel(20))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
            p.setPen(_t1() if i == self.index else _t2())
            f = QFont(FAMILY)
            f.setPixelSize(11)
            f.setWeight(QFont.DemiBold if i == self.index else QFont.Normal)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, lab)


# ───────────────────────── 圆形小按钮 ─────────────────────────
class RoundButton(QPushButton):
    def __init__(self, glyph, tip="", parent=None):
        super().__init__(glyph, parent)
        self.setFixedSize(26, 26)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self._dim_eff = None
        self._dim = False
        self._font_px = 13                # ▤ 这类几何字符偏小，可单独放一号
        _theme(self, self._qss)

    def _qss(self, m):
        return """
            QPushButton{background:%s;border:0;border-radius:13px;
                        color:%s;font-size:%dpx;}
            QPushButton:hover{background:%s;color:%s;}
            QPushButton:pressed{background:%s;}
        """ % (glass.panelcss(17, m), glass.inkcss(120 if self._dim else 190, m),
               int(self._font_px),
               glass.panelcss(38, m), glass.inkcss(240, m), glass.panelcss(60, m))

    def set_font_px(self, px):
        self._font_px = int(px)
        _theme(self, self._qss)

    def set_dim(self, on):
        """on=True 时按钮变暗（用于「置顶」关闭态）。
        📌 是彩色 emoji，color 改不动它，所以另外叠一层透明度来压暗。"""
        self._dim = bool(on)
        _theme(self, self._qss)
        eff = self._dim_eff
        if eff is None:
            eff = QGraphicsOpacityEffect(self)
            self._dim_eff = eff
        eff.setOpacity(0.42 if on else 1.0)
        self.setGraphicsEffect(eff if on else None)
        if not on:
            self._dim_eff = None


# ───────────────────────── 侧栏 ─────────────────────────
class SideDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.drag_row = -1             # 拖拽经过的行，画高亮 + 虚框

    def sizeHint(self, o, i):
        return QSize(0, 32)

    def paint(self, p, opt, idx):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = opt.rect.adjusted(8, 2, -8, -2)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hov = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        drop = idx.row() == self.drag_row
        if sel or hov or drop:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(10, 132, 255, 96 if drop else (42 if sel else 20)))
            p.drawRoundedRect(r, 7, 7)
        if drop:
            pen = QPen(glass.ink(170), 1)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        color = QColor(idx.data(Qt.UserRole + 2))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(r.left() + 13, r.center().y() + 0.5), 3.4, 3.4)
        f = QFont(FAMILY)
        f.setPixelSize(12)
        if sel:
            f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(_t1() if sel else _t2())
        p.drawText(QRectF(r.left() + 24, r.top(), r.width() - 60, r.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, idx.data(Qt.DisplayRole))
        p.setPen(glass.ink(175) if sel else _t3())
        f.setWeight(QFont.Normal)
        f.setPixelSize(11)
        p.setFont(f)
        p.drawText(QRectF(r.right() - 34, r.top(), 28, r.height()),
                   Qt.AlignVCenter | Qt.AlignRight, str(idx.data(Qt.UserRole + 1)))
        p.restore()


# ───────────────────────── 网格 ─────────────────────────
ICON_TOP = 8            # 图标上留白
ICON_GAP = 6            # 图标与文字的间距
LINE_H = 15             # 文字行高
CELL_BOTTOM = 10
TEXT_PAD = 6            # 文字左右留白


def _cell_size(px):
    """网格单元尺寸：图标 + 两行文字，任何一档都不让文字压到下一行。"""
    return QSize(max(84, px + 36), ICON_TOP + px + ICON_GAP + LINE_H * 2 + CELL_BOTTOM)


def _wrap2(text, fm, width):
    """按宽度切成最多两行，第二行再省略。"""
    if fm.horizontalAdvance(text) <= width:
        return [text]
    lo, hi = 1, len(text)
    while lo < hi:                       # 二分找第一行能放下的最大字符数
        mid = (lo + hi + 1) // 2
        if fm.horizontalAdvance(text[:mid]) <= width:
            lo = mid
        else:
            hi = mid - 1
    return [text[:lo], fm.elidedText(text[lo:].strip(), Qt.ElideRight, width)]


class IconDelegate(QStyledItemDelegate):
    """自绘网格项：图标居中 + 最多两行名字 + 白块选中/hover。"""

    def __init__(self, parent=None, px=48):
        super().__init__(parent)
        self.px = int(px)

    def sizeHint(self, o, i):
        return _cell_size(self.px)

    def paint(self, p, opt, idx):
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        r = opt.rect
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hov = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        if sel or hov:
            p.setPen(Qt.NoPen)
            p.setBrush(glass.panel(38 if sel else 18))
            p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), 9, 9)

        # ---- 图标 ----
        px = self.px
        ix = r.left() + (r.width() - px) // 2
        iy = r.top() + ICON_TOP
        ico = idx.data(Qt.DecorationRole)
        if ico is not None:
            pm = ico.pixmap(QSize(px, px))
            if pm is not None and not pm.isNull():
                tgt = QRect(ix + (px - pm.width()) // 2, iy + (px - pm.height()) // 2,
                            pm.width(), pm.height())
                p.drawPixmap(tgt, pm)

        # ---- 文字：最多两行 ----
        text = idx.data(Qt.DisplayRole) or ""
        f = QFont(FAMILY)
        f.setPixelSize(11)
        p.setFont(f)
        fm = QFontMetrics(f)
        width = max(20, r.width() - TEXT_PAD * 2)
        lines = _wrap2(text, fm, width)
        ty = iy + px + ICON_GAP
        for n, ln in enumerate(lines[:2]):
            p.setPen(_t1() if (sel or n == 0) else _t2())
            box = QRectF(r.left() + TEXT_PAD, ty + n * LINE_H, width, LINE_H)
            p.drawText(box, Qt.AlignHCenter | Qt.AlignTop, ln)
        p.restore()


# ───────────────────────── 拖拽容器 ─────────────────────────
DRAG_MIME = "application/x-deskbox-paths"


def _is_internal(md):
    """是否是 Tomato Desktop Box 自己发起的拖拽（网格项拖到网格/侧栏）。"""
    try:
        return md.hasFormat(DRAG_MIME)
    except Exception:                  # noqa: BLE001
        return False


def _paths_from_mime(md):
    """从拖拽数据里取路径：内部拖拽走自定义 MIME，外部走 urls。"""
    out = []
    try:
        if md.hasFormat(DRAG_MIME):
            raw = bytes(md.data(DRAG_MIME)).decode("utf-8", "ignore")
            out = [p for p in raw.splitlines() if p.strip()]
    except Exception:                  # noqa: BLE001
        out = []
    if not out:
        try:
            out = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        except Exception:              # noqa: BLE001
            out = []
    # 外部拖进来的 QUrl.toLocalFile() 给的是正斜杠形式，Shell 的
    # SHCreateItemFromParsingName 只认反斜杠（正斜杠 E_INVALIDARG），
    # 所以这里统一归一化：账本与后续取图都拿到反斜杠路径。
    return [os.path.normpath(p) for p in out if p]


# ───────────────────────── 界面顺序（拖拽换位）─────────────────────────
def _order_scope_main(cat):
    """主面板每个分类各有一份自己的顺序表。"""
    return "main:%s" % (cat or "全部")


def _order_scope_mini(wid):
    """小窗口各有一份自己的顺序表（wid 唯一）。"""
    return "mini:%s" % wid


def _apply_reorder(order, moved, target):
    """把 moved 挪到 target 上：单个就地对调，多个按原相对顺序整块插入。

    target 为 None（落在空白处）→ 整块移到末尾。
    只动列表次序，文件一个都不碰。
    """
    base = [p for p in (order or []) if p]
    if not base:
        return base
    keys = [_nk(p) for p in base]                # 比较一律走 _nk：账本里全是 normcase 形态
    mov = []
    for p in (moved or []):
        if p and _nk(p) in keys and _nk(p) not in mov:
            mov.append(_nk(p))
    if not mov:
        return base
    mov.sort(key=keys.index)          # 多选：按原来的相对顺序整块搬，别跟着选择次序乱
    tgt = _nk(target) if target else None
    if tgt in mov and len(mov) > 1:
        tgt = None                     # 多选里含落点：整块插到被拖块之外
    if len(mov) == 1 and tgt in keys:
        i, j = keys.index(mov[0]), keys.index(tgt)
        if i != j:
            base[i], base[j] = base[j], base[i]
        return base
    rest = [p for p, k in zip(base, keys) if k not in mov]
    rkeys = [k for k in keys if k not in mov]
    at = rkeys.index(tgt) if tgt in rkeys else len(rest)
    moved_paths = [base[keys.index(k)] for k in mov]   # 保持被拖块的原相对顺序
    return rest[:at] + moved_paths + rest[at:]


class SideList(QListWidget):
    """侧栏：接受把网格项拖到某个分类上（改分类），也接受从桌面拖入。"""

    dropped_paths = Signal(str, list, bool)
    hint_clicked = Signal()
    HINT = "+ 新建分类"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dropped = None            # (分类名, [path...], 是否内部) —— 由主窗消费
        self._row = -1
        self._hint_hot = False
        self.setToolTip("点这里新建分类；右键分类列表也行（含删除分类）")

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        hot = self._hint_rect().contains(e.position().toPoint())
        if hot != self._hint_hot:
            self._hint_hot = hot
            self.setCursor(Qt.PointingHandCursor if hot else Qt.ArrowCursor)
            self.viewport().update()

    def mousePressEvent(self, e):
        if (e.button() == Qt.LeftButton
                and self._hint_rect().contains(e.position().toPoint())):
            self.hint_clicked.emit()
            return
        super().mousePressEvent(e)

    # ---------- 分类列表下方的提示字 ----------
    def _hint_rect(self):
        """提示字画在最后一行分类下方；行数多到放不下就返回空矩形（不画）。"""
        vp = self.viewport().rect()
        n = self.count()
        top = vp.top() + 6
        if n > 0:
            r = self.visualItemRect(self.item(n - 1))
            if r.isValid():
                top = r.bottom() + 15
        if vp.bottom() - top < 26:
            return QRect()
        return QRect(vp.left() + 6, top + 2, vp.width() - 12, 22)

    def paintEvent(self, e):
        super().paintEvent(e)
        r = self._hint_rect()
        if r.isEmpty():
            return
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        hot = self._hint_hot
        # 画成一颗"幽灵按钮"：既有底色描边，又比分类行淡，不会被误认成分类
        p.setPen(QPen(glass.panel(46 if hot else 26), 1))
        p.setBrush(glass.panel(30 if hot else 14))
        p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        f = QFont(FAMILY)
        f.setPixelSize(10)
        p.setFont(f)
        p.setPen(glass.ink(236 if hot else 176))
        p.drawText(r, Qt.AlignHCenter | Qt.AlignVCenter, self.HINT)
        p.end()

    def _row_at(self, pos):
        it = self.itemAt(pos)
        if it is None:
            return -1, None
        return self.row(it), it.data(Qt.UserRole)

    def _hot(self, row):
        de = self.itemDelegate()
        if getattr(de, "drag_row", None) != row:
            de.drag_row = row
            self.viewport().update()

    def dragEnterEvent(self, e):
        if _paths_from_mime(e.mimeData()):
            e.acceptProposedAction()
            self._hot(self._row_at(e.position().toPoint())[0])
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if _paths_from_mime(e.mimeData()):
            e.acceptProposedAction()
            self._hot(self._row_at(e.position().toPoint())[0])
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._hot(-1)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        paths = _paths_from_mime(e.mimeData())
        row, cat = self._row_at(e.position().toPoint())
        self._hot(-1)
        if not paths:
            e.ignore()
            return
        e.acceptProposedAction()
        internal = _is_internal(e.mimeData())
        self.dropped = (cat or "全部", paths, internal)
        self.dropped_paths.emit(cat or "全部", paths, internal)


class DropGrid(QListWidget):
    """网格：接外部拖入，并给自己画一圈虚线边框做反馈。"""

    dropped_paths = Signal(list)
    reordered = Signal(list, object)     # 内部拖拽换位：(被拖路径, 落点路径或 None)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hot = False
        self.dropped = None

    def _set_hot(self, on):
        if self.hot != on:
            self.hot = on
            self.viewport().update()

    def dragEnterEvent(self, e):
        # 自己拖自己不是「拖入」：不亮虚线框，但要放行 —— 那是换位
        if _is_internal(e.mimeData()):
            if _paths_from_mime(e.mimeData()):
                e.acceptProposedAction()
            else:
                e.ignore()
            return
        if _paths_from_mime(e.mimeData()):
            self._set_hot(True)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if _is_internal(e.mimeData()):
            if _paths_from_mime(e.mimeData()):
                e.acceptProposedAction()
            else:
                e.ignore()
            return
        if _paths_from_mime(e.mimeData()):
            self._set_hot(True)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._set_hot(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._set_hot(False)
        if _is_internal(e.mimeData()):
            # 自己拖自己 = 调换位置，不是收纳动作（绝不转发给 ingest）。
            paths = _paths_from_mime(e.mimeData())
            it = self.itemAt(e.position().toPoint())
            target = it.data(Qt.UserRole) if it is not None else None
            e.setDropAction(Qt.MoveAction)
            e.accept()
            if paths:
                self.reordered.emit(paths, target)
            return
        paths = _paths_from_mime(e.mimeData())
        if not paths:
            e.ignore()
            return
        e.acceptProposedAction()
        self.dropped = paths
        self.dropped_paths.emit(paths)

    def startDrag(self, actions):
        """自建拖拽载荷：既给侧栏认（DRAG_MIME），也给资源管理器认（urls）。"""
        items = [i for i in self.selectedItems() if i.data(Qt.UserRole)]
        paths = [i.data(Qt.UserRole) for i in items]
        if not paths:
            return
        md = QMimeData()
        md.setData(DRAG_MIME, ("\n".join(paths)).encode("utf-8"))
        try:
            md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        except Exception:              # noqa: BLE001
            pass
        drag = QDrag(self)
        drag.setMimeData(md)
        ico = items[0].data(Qt.DecorationRole)
        if ico is not None:
            pm = ico.pixmap(QSize(48, 48))
            if pm is not None and not pm.isNull():
                drag.setPixmap(pm)
                drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        drag.exec(Qt.MoveAction)

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.hot:
            return
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(glass.ink(70), 1)
        pen.setStyle(Qt.DashLine)
        pen.setDashPattern([4, 6])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = self.viewport().rect().adjusted(3, 3, -4, -4)
        p.drawRoundedRect(QRectF(r), RADIUS - 2, RADIUS - 2)
        p.end()


# ───────────────────────── 条目模型 ─────────────────────────
# 这些后缀在界面上去掉，path 永远保留真名
HIDE_EXTS = (".lnk", ".url", ".exe", ".appref-ms")


def _disp_name(name, is_dir):
    """显示名：快捷方式/程序去掉后缀，文件夹原样。"""
    if is_dir:
        return name
    low = name.lower()
    for e in HIDE_EXTS:
        if low.endswith(e) and len(name) > len(e):
            return name[:-len(e)]
    return name


class Row(object):
    """UI 侧轻量条目，字段与 store.Item 对齐。"""
    __slots__ = ("path", "name", "disp", "group", "is_dir", "locked", "sys")

    def __init__(self, path, name, group, is_dir, locked=False, sys=False):
        self.path = path
        self.name = name
        self.is_dir = bool(is_dir)
        self.disp = _disp_name(name, self.is_dir)
        self.group = group
        self.locked = bool(locked)
        self.sys = bool(sys)

    def tip(self):
        """工具提示：显示名 + 真名（两行）。"""
        if self.disp == self.name:
            return self.disp
        return "\n".join([self.disp, self.name])


def _rows_from(items):
    out = []
    for it in items or []:
        try:
            out.append(Row(it.path, it.name, getattr(it, "group", ""),
                           getattr(it, "is_dir", False), getattr(it, "locked", False)))
        except Exception:              # noqa: BLE001 —— 契约外的坏条目直接跳过
            continue
    return out


# ───────────────────────── 玻璃对话框 ─────────────────────────
class GlassDialog(QDialog):
    """深色半透明圆角对话框，玻璃与主窗同源。"""

    def __init__(self, parent=None, title="", width=380, density=62):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(width)
        self._density = density
        self._drag = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 15, 18, 14)
        lay.setSpacing(9)

        self._title = QLabel(title)
        hf = QFont(FAMILY)
        hf.setPixelSize(13)
        hf.setWeight(QFont.DemiBold)
        self._title.setFont(hf)
        _theme(self._title, lambda m: "color:%s;" % glass.inkcss(232, m))
        lay.addWidget(self._title)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(6)
        lay.addLayout(self.body, 1)

        self.foot = QHBoxLayout()
        self.foot.setContentsMargins(0, 4, 0, 0)
        self.foot.setSpacing(8)
        self.foot.addStretch(1)
        lay.addLayout(self.foot)

    # ---- 玻璃底 ----
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        local = QRect(0, 0, self.width(), self.height())
        g = self.mapToGlobal(local.topLeft())
        glass.GLASS.paint(p, local,
                          QRect(g.x(), g.y(), self.width(), self.height()),
                          14, density=self._density, mode=bg_mode())

    # ---- 可拖动 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    # ---- 控件工厂 ----
    def add_line(self, text, color=None, px=12, bold=False):
        lb = QLabel(text)
        f = QFont(FAMILY)
        f.setPixelSize(px)
        if bold:
            f.setWeight(QFont.DemiBold)
        lb.setFont(f)
        _theme(lb, lambda m: "color:%s;" % _inkcss_of(color, m))
        lb.setWordWrap(True)
        self.body.addWidget(lb)
        return lb

    def add_box(self, top=11, bottom=11, left=14, right=14, spacing=6, bg_alpha=14):
        box = QWidget()
        box.setAttribute(Qt.WA_StyledBackground, True)
        _theme(box, lambda m: "background:%s;border-radius:9px;" % glass.panelcss(bg_alpha, m))
        v = QVBoxLayout(box)
        v.setContentsMargins(left, top, right, bottom)
        v.setSpacing(spacing)
        self.body.addWidget(box)
        return box, v

    def add_button(self, text, primary=False, slot=None):
        b = QPushButton(text)
        f = QFont(FAMILY)
        f.setPixelSize(11)
        if primary:
            f.setWeight(QFont.DemiBold)
        b.setFont(f)
        b.setFixedHeight(28)
        b.setCursor(Qt.PointingHandCursor)
        if primary:
            b.setStyleSheet("""
                QPushButton{background:#0A84FF;border:0;border-radius:7px;padding:0 15px;color:#fff;}
                QPushButton:hover{background:#3D9BFF;}
                QPushButton:pressed{background:#0873E0;}
            """)
        else:
            _theme(b, lambda m: """
                QPushButton{background:%s;border:0;border-radius:7px;
                            padding:0 13px;color:%s;font-size:11px;}
                QPushButton:hover{background:%s;}
            """ % (glass.panelcss(20, m), glass.inkcss(205, m), glass.panelcss(40, m)))
        if slot is not None:
            b.clicked.connect(slot)
        self.foot.addWidget(b)
        return b


class NameDialog(GlassDialog):
    """新建分类用的小输入框：名字非法时框内标红提示，不关窗。"""

    def __init__(self, parent=None, title="新建分类", hint="", ok_text="新建",
                 cancel_text="取消", text="", width=330, max_len=12):
        super().__init__(parent, title, width)
        self.value = ""
        self.max_len = int(max_len)
        if hint:
            self.add_line(hint, None, 11)
        self.edit = QLineEdit(text)
        self.edit.setMaxLength(self.max_len)
        ef = QFont(FAMILY)
        ef.setPixelSize(12)
        self.edit.setFont(ef)
        self.edit.setFixedHeight(30)
        self.edit.setPlaceholderText("最多 %d 个字" % self.max_len)
        self._qss()
        self.body.addWidget(self.edit)
        self.err = QLabel("")
        ef2 = QFont(FAMILY)
        ef2.setPixelSize(11)
        self.err.setFont(ef2)
        self.err.setWordWrap(True)
        self.err.setMinimumHeight(15)
        _theme(self.err, lambda m: "color:rgba(255,69,58,220);")
        self.err.hide()
        self.body.addWidget(self.err)
        self._ok_text = ok_text
        self.add_button(cancel_text, False, self.reject)
        self.add_button(ok_text, True, self._ok)
        self.edit.returnPressed.connect(self._ok)

    def _qss(self, bad=False):
        def build(m):
            border = "rgba(255,69,58,200)" if bad else glass.panelcss(60, m)
            return ("QLineEdit{background:%s;border:1px solid %s;border-radius:8px;"
                    "padding:0 10px;color:%s;selection-background-color:#0A84FF;}"
                    "QLineEdit:focus{border:1px solid %s;}"
                    % (glass.panelcss(22, m), border, glass.inkcss(232, m),
                       "#0A84FF" if not bad else "rgba(255,69,58,200)"))
        _theme(self.edit, build)

    def _set_err(self, msg):
        self.err.setText(msg or "")
        self.err.setVisible(bool(msg))
        self._qss(bool(msg))
        self.adjustSize()

    def _ok(self):
        """子类/主窗通过 validator 拦非法名字；这里只负责收值。"""
        txt = self.edit.text().strip()
        bad = self.validator(txt) if callable(getattr(self, "validator", None)) else None
        if bad:
            self._set_err(bad)
            return
        self.value = txt
        self.accept()



# ───────────────────────── 小窗口 ─────────────────────────
class MiniGrid(DropGrid):
    """小窗口网格。

    两处与主面板不同：
    1) 内部拖拽也要能接（主面板的网格会忽略自己发起的拖拽）；
    2) 拖出去只带 DRAG_MIME，**绝不 setUrls** —— 带了 urls 拖到资源管理器
       松手，Windows 会真的把桌面文件搬过去，那不是 Tomato Desktop Box 该做的事。
    """

    def dragEnterEvent(self, e):
        if _paths_from_mime(e.mimeData()):
            self._set_hot(True)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if _paths_from_mime(e.mimeData()):
            self._set_hot(True)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        self._set_hot(False)
        paths = _paths_from_mime(e.mimeData())
        if not paths:
            e.ignore()
            return
        if _is_internal(e.mimeData()):
            # 小窗口内部互相拖 = 换位；外部拖进来的文件仍然是「收进来」。
            it = self.itemAt(e.position().toPoint())
            target = it.data(Qt.UserRole) if it is not None else None
            e.setDropAction(Qt.MoveAction)
            e.accept()
            self.reordered.emit(paths, target)
            return
        e.acceptProposedAction()
        self.dropped = paths
        self.dropped_paths.emit(paths)

    def startDrag(self, actions):
        items = [i for i in self.selectedItems() if i.data(Qt.UserRole)]
        paths = [i.data(Qt.UserRole) for i in items]
        if not paths:
            return
        md = QMimeData()
        md.setData(DRAG_MIME, ("\n".join(paths)).encode("utf-8"))
        # 故意不调用 md.setUrls(...)：拖出去只是在小窗口之间挪，不是文件搬运
        drag = QDrag(self)
        drag.setMimeData(md)
        ico = items[0].data(Qt.DecorationRole)
        if ico is not None:
            pm = ico.pixmap(QSize(48, 48))
            if pm is not None and not pm.isNull():
                drag.setPixmap(pm)
                drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        drag.exec(Qt.CopyAction)


SNAP_PX = 12                   # 小窗口互相吸附的判定距离（px）

# ── 小窗口的任务栏样式位（纯算术，离屏也能单测）─────────────────
GWL_EXSTYLE, GWL_STYLE = -20, -16
WS_EX_APPWINDOW = 0x00040000   # 进任务栏（+ Alt+Tab 条目）
WS_EX_TOOLWINDOW = 0x00000080  # 不进任务栏（也不进 Alt+Tab）
WS_EX_LAYERED = 0x00080000     # 透明玻璃靠它，必须留着
WS_MINIMIZEBOX = 0x00020000    # 任务栏按钮上那个最小化框


def _taskbar_ex(ex, style=None):
    """小窗口的窗口样式算术：稳稳地「不上任务栏」。

    去掉 WS_EX_APPWINDOW、加上 WS_EX_TOOLWINDOW，保留 WS_EX_LAYERED。
    给了 style 就顺手摘掉 WS_MINIMIZEBOX（不上任务栏，最小化框没意义）。
    纯函数：只算位、不碰 windll，窗口那边拿它算完再去调 SetWindowLongW。
    """
    ex = (int(ex) & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW | WS_EX_LAYERED
    if style is None:
        return ex
    return ex, int(style) & ~WS_MINIMIZEBOX


class MiniWindow(QWidget):
    """桌面上的一个小浮窗：只登记路径，不动任何文件。"""

    def __init__(self, main, wid):
        super().__init__(None)
        self.main = main
        self.wid = wid
        self._gone = False
        self._user_closed = False          # 只有用户亲手关掉才记 open=False
        self._drag = None
        self._rsz = None                   # 右下角拖尺寸时的锚点（尺寸 + 光标）
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._save_geom)
        self._loading = True

        d = self._data() or {}
        title = d.get("title") or "小窗口"
        self._pinned = bool(d.get("pinned", True))   # 固定位置：默认开
        self.setWindowTitle("Tomato Desktop Box · %s" % title)
        self._apply_flags()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(int(getattr(MB, "MIN_W", 200)),
                            int(getattr(MB, "MIN_H", 160)))
        self._build()
        self._sync_pin()
        self.resize(max(int(d.get("w") or 340), 200),
                    max(int(d.get("h") or 280), 160))
        self._reload()
        self.move(int(d.get("x") or 120), int(d.get("y") or 120))
        self._loading = False
        QTimer.singleShot(0, self._hide_from_taskbar)

    # ---------- 数据 ----------
    def _data(self):
        if MB is None:
            return None
        try:
            return MB.get(self.wid)
        except Exception:              # noqa: BLE001
            return None

    def _update(self, **kw):
        if MB is None:
            return
        try:
            MB.update(self.wid, **kw)
        except Exception:              # noqa: BLE001
            pass

    # ---------- 外观 ----------
    def _apply_flags(self):
        # 小窗口当桌面用：不置顶，而且永远压在最底层（点它也不会盖住别的程序）
        # 「刚新建先露头」那会儿用的是 _raise_for_setup / _drop_back，不走这里
        f = Qt.FramelessWindowHint | Qt.Window | Qt.WindowStaysOnBottomHint
        pos = self.pos()
        self.setWindowFlags(f)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if pos.x() or pos.y():
            self.move(pos)

    # ---------- 刚建出来时先露个头（免得一出生就摸不到） ----------
    RAISE_MS = 2500                # 露头时长：够看见、够上手拖，之后自己沉回去

    def _raise_for_setup(self):
        """新建的小窗口先浮到最上面，让人立刻看见、能拖。

        StaysOnBottomHint + _keep_bottom 会让新建的窗口直接沉到最底下、被别的
        窗口盖住。这里临时摘掉那个提示，并让 _keep_bottom 歇着；RAISE_MS 之后
        _drop_back() 再把桌面挂件的老样子装回去。状态只在内存里，不落盘。
        """
        self._raised = True
        pos = self.pos()
        was_loading = getattr(self, "_loading", False)
        self._loading = True           # 换个 flags 不算「用户挪过窗口」，别去写 minis.json
        try:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            if pos.x() or pos.y():
                self.move(pos)
            self.show()                # setWindowFlags 会把窗口藏起来，得再露一次
            self.raise_()
            self.activateWindow()
        except Exception:              # noqa: BLE001
            pass
        finally:
            self._loading = was_loading
        QTimer.singleShot(self.RAISE_MS, self._drop_back)

    def _drop_back(self):
        """恢复常态：回到 WindowStaysOnBottomHint，继续被压在最底层。

        单独可调（测试不必等定时器）。setWindowFlags 会把窗口藏起来，所以
        原来露着的话得补一次 show()；露头期间被用户关掉的就不复活它。
        """
        was_visible = self.isVisible()
        was_loading = getattr(self, "_loading", False)
        self._raised = False
        self._loading = True           # 同上：换 flags 不动账本
        try:
            self._apply_flags()
            if was_visible:
                self.show()
        except Exception:              # noqa: BLE001
            pass
        finally:
            self._loading = was_loading
        self._keep_bottom()

    def _sync_pin(self):
        btn = getattr(self, "btn_pin", None)
        if btn is not None:
            btn.set_dim(not self._pinned)
            btn.setText("🔒" if self._pinned else "🔓")
            btn.setToolTip("固定位置：开（顶栏拖不动）" if self._pinned
                           else "固定位置：关")

    def _hide_from_taskbar(self):
        """把小窗口从任务栏摘掉：去掉 WS_EX_APPWINDOW、加 WS_EX_TOOLWINDOW。

        无边框窗口本来会占一个任务栏按钮（顺带一个 Alt+Tab 条目），用户要求
        小窗口永远不占任务栏。收起小窗口走主面板 ▤ 或托盘「显示全部小窗口」，
        所以这里也不再补 WS_MINIMIZEBOX。
        算术在 _taskbar_ex 里，这里只负责调 windll；只在 MiniWindow 上调，
        主面板（MainWindow）样式保持不动。
        """
        try:
            hwnd = int(self.winId())
            u = ctypes.windll.user32
            ex, st = _taskbar_ex(u.GetWindowLongW(hwnd, GWL_EXSTYLE),
                                 u.GetWindowLongW(hwnd, GWL_STYLE))
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
            u.SetWindowLongW(hwnd, GWL_STYLE, st)
            # NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except Exception:              # noqa: BLE001 —— 拿不到 hwnd 也不影响使用
            pass

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        local = QRect(0, 0, self.width(), self.height())
        g = self.mapToGlobal(local.topLeft())
        gr = QRect(g.x(), g.y(), self.width(), self.height())
        rad = 0 if self.isMaximized() else RADIUS   # 与主面板 _toggle_max 行为对齐
        den = 58
        try:
            den = int(self.main._density)
        except Exception:              # noqa: BLE001
            pass
        glass.GLASS.paint(p, local, gr, rad, density=den, mode=bg_mode())

    # ---------- 骨架 ----------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(34)
        self.topbar = bar
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 10, 0)
        h.setSpacing(6)
        self.title = QLabel("")
        tf = QFont(FAMILY)
        tf.setPixelSize(12)
        tf.setWeight(QFont.DemiBold)
        self.title.setFont(tf)
        _theme(self.title, lambda m: "color:%s;" % glass.inkcss(215, m))
        self.title.setToolTip("双击改名")
        h.addWidget(self.title, 1)
        self.btn_pin = RoundButton("🔒", "固定位置")
        self.btn_pin.clicked.connect(self._on_pin)
        h.addWidget(self.btn_pin)
        self.btn_more = RoundButton("···", "更多")
        self.btn_more.clicked.connect(self._on_more_menu)
        h.addWidget(self.btn_more)
        root.addWidget(bar)

        px = 48
        try:
            px = int(self.main._icon_px)
        except Exception:              # noqa: BLE001
            pass
        self.grid = MiniGrid(self)
        gf = QFont(FAMILY)
        gf.setPixelSize(11)
        self.grid.setFont(gf)
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(2)
        self.grid.setFrameShape(QFrame.NoFrame)
        self.grid.setIconSize(QSize(px, px))
        self.grid.setGridSize(_cell_size(px))
        self._grid_dele = IconDelegate(self.grid, px)
        self.grid.setItemDelegate(self._grid_dele)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setDragEnabled(True)
        self.grid.setDragDropMode(QAbstractItemView.DragDrop)
        self.grid.setDefaultDropAction(Qt.CopyAction)
        self.grid.setAcceptDrops(True)
        self.grid.viewport().setAcceptDrops(True)
        self.grid.setDropIndicatorShown(False)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._on_ctx_menu)
        self.grid.itemDoubleClicked.connect(self._on_double)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid.dropped_paths.connect(self._on_drop)
        self.grid.reordered.connect(self._on_reorder)
        _theme(self.grid, lambda m: """
            QListWidget{background:transparent;border:0;outline:0;}
            QListWidget::item{border-radius:10px;}
            QScrollBar:vertical{background:transparent;width:9px;margin:2px 2px 2px 0;}
            QScrollBar::handle:vertical{background:%s;border-radius:4px;min-height:32px;}
            QScrollBar::handle:vertical:hover{background:%s;}
            QScrollBar::add-line,QScrollBar::sub-line{height:0;}
            QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}
        """ % (glass.panelcss(42, m), glass.panelcss(80, m)))
        root.addWidget(self.grid, 1)

        self.empty = QLabel("把图标拖进来，或者从主面板拖", self.grid.viewport())
        ef = QFont(FAMILY)
        ef.setPixelSize(12)
        self.empty.setFont(ef)
        _theme(self.empty, lambda m: "color:%s;background:transparent;" % glass.inkcss(90, m))
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.empty.hide()

        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(16, 16)
        self._grip.setCursor(Qt.SizeFDiagCursor)
        self._grip.installEventFilter(self)    # 拖尺寸自己算，见 _grip_event

        # 另外四条边（左 / 左下 / 下 / 右）：自己铺一层透明热区。
        # 右下沿用 QSizeGrip，剩下的靠这个循环补齐 —— 位置在 _place_edges 里算。
        self._rsz_edge = ""
        self._edges = {}
        for edge, cur in (("l", Qt.SizeHorCursor), ("bl", Qt.SizeBDiagCursor),
                          ("b", Qt.SizeVerCursor), ("r", Qt.SizeHorCursor)):
            hd = QWidget(self)
            hd.setCursor(cur)
            hd.installEventFilter(self)
            self._edges[edge] = hd

        # 顶栏（含标题）：右键 = 小窗口自己的菜单；拖动与双击改名走 eventFilter
        for w in (bar, self.title):
            w.setContextMenuPolicy(Qt.CustomContextMenu)
            w.customContextMenuRequested.connect(self._on_bar_menu)
            w.installEventFilter(self)

        # 网格视口：改尺寸时把空态文字重新铺满（新建那一刻 viewport 还是占位值）
        self.grid.viewport().installEventFilter(self)

    # ---------- 顶栏拖动 / 双击改名 ----------
    def eventFilter(self, obj, ev):
        """装到 topbar / self.title（拖动、双击改名）与 grid.viewport()（空态居中）。

        📌 和交通灯是 topbar 的子控件，它们的事件不会进父控件的 eventFilter，
        所以按它们不会误拖；childAt 那层只是再加一道保险。
        点标题文字也要能拖 —— QLabel 自己不吃事件，正好由这里的过滤器接管。
        """
        if obj not in (getattr(self, "topbar", None), getattr(self, "title", None)):
            if obj is getattr(self, "_grip", None) and self._grip_event(ev, "br"):
                return True                # 右下角拖动由自己接管（要按光标绝对位移算）
            for edge, hd in getattr(self, "_edges", {}).items():
                if obj is hd and self._grip_event(ev, edge):
                    return True            # 左 / 左下 / 下 / 右：同一套算法
            grid = getattr(self, "grid", None)
            if (grid is not None and obj is grid.viewport()
                    and ev.type() == QEvent.Resize):
                self._sync_empty_geo()
            return super().eventFilter(obj, ev)
        et = ev.type()
        if et == QEvent.ContextMenu:
            return super().eventFilter(obj, ev)      # 右键交给 CustomContextMenu
        pos = ev.position().toPoint() if hasattr(ev, "position") else None
        on_sub = False
        if pos is not None and hasattr(obj, "childAt"):
            child = obj.childAt(pos)
            # 标题本身是 QLabel，命中它不算"按在子控件上"
            if child is not None and child is not obj and not isinstance(child, QLabel):
                on_sub = True
        if et == QEvent.MouseButtonPress:
            if ev.button() == Qt.LeftButton and not on_sub:
                if self._pinned:           # 固定位置：顶栏拖不动，也别起拖拽
                    return True
                self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
        elif et == QEvent.MouseMove:
            if self._drag is not None and not on_sub and (ev.buttons() & Qt.LeftButton):
                self.move(self._snap_pos(ev.globalPosition().toPoint() - self._drag))
                self.update()
                return True
        elif et == QEvent.MouseButtonRelease:
            if self._drag is not None:
                self._drag = None
                self._avoid_overlap()      # 松手落位：压住邻居就推开
                return True
        elif et == QEvent.MouseButtonDblClick:
            if ev.button() == Qt.LeftButton and not on_sub:
                self._drag = None
                self._rename_window()
                return True
        return super().eventFilter(obj, ev)

    def _grip_event(self, ev, edge="br"):
        """右下角拖尺寸：自己接管，不交给 QSizeGrip。

        得按「按下那一刻的尺寸 + 光标绝对位移」算，才能在尺寸上做磁吸 ——
        要是让 QSizeGrip 内部算，中途被吸附改过尺寸它就乱了。返回 True 表示
        这个事件我处理了，不再往下发。
        """
        et = ev.type()
        if et == QEvent.MouseButtonPress:
            if ev.button() != Qt.LeftButton:
                return False
            g = self.frameGeometry()
            self._rsz = (QSize(self.width(), self.height()),
                         g.topLeft(), ev.globalPosition().toPoint())
            self._rsz_edge = edge
            return True
        if et != QEvent.MouseMove:
            if et == QEvent.MouseButtonRelease and self._rsz is not None:
                self._rsz = None
                self._rsz_edge = ""
                return True
            return False
        if self._rsz is None:
            return False
        if not (ev.buttons() & Qt.LeftButton):
            self._rsz = None
            return False
        size, org, p0 = self._rsz
        e = self._rsz_edge or "br"
        d = ev.globalPosition().toPoint() - p0
        if e == "br":
            self.resize(*self._snap_size(size.width() + d.x(),
                                         size.height() + d.y()))
            return True
        x, y = org.x(), org.y()
        right = x + size.width() - 1
        bottom = y + size.height() - 1
        if "r" in e:
            right = x + max(self.minimumWidth(), size.width() + d.x()) - 1
            right = self._snap_side("r", right, x)
        if "b" in e:
            bottom = y + max(self.minimumHeight(), size.height() + d.y()) - 1
            bottom = self._snap_side("b", bottom, y)
        if "l" in e:
            x = min(x + d.x(), right - self.minimumWidth() + 1)
            x = self._snap_side("l", x, right)
        self.setGeometry(x, y, right - x + 1, bottom - y + 1)
        return True

    def _snap_size(self, w, h):
        """拉到跟邻居一样长（或正好贴上）的时候顿一下，返回吸附后的尺寸。

        候选和 _snap_pos 是同一套，只不过这次动的是右边 / 下边：
        「贴上邻居的左边」「跟邻居右边齐平（也就是一样宽）」和纵向的同两条。
        差在 SNAP_PX 以内就直接吸到正正好，光标得再走超过 SNAP_PX 才拽得开 ——
        这一下就是那个「顿」。
        """
        if not getattr(self.main, "_snap_on", True):
            return (w, h)
        x = self.frameGeometry().x()
        y = self.frameGeometry().y()
        mw, mh = self.minimumWidth(), self.minimumHeight()
        bw = bh = None
        for m in self._neighbors():
            o = m.frameGeometry()
            for other in (o.left() - 1, o.right()):
                t = other - x + 1
                if (t >= mw and abs(t - w) <= SNAP_PX
                        and (bw is None or abs(t - w) < abs(bw - w))):
                    bw = t
            for other in (o.top() - 1, o.bottom()):
                t = other - y + 1
                if (t >= mh and abs(t - h) <= SNAP_PX
                        and (bh is None or abs(t - h) < abs(bh - h))):
                    bh = t
        av = self._screen_rect()
        if av is not None:                         # 屏幕右边 / 下边也吸
            t = av.right() - x + 1
            if (t >= mw and abs(t - w) <= SNAP_PX
                    and (bw is None or abs(t - w) < abs(bw - w))):
                bw = t
            t = av.bottom() - y + 1
            if (t >= mh and abs(t - h) <= SNAP_PX
                    and (bh is None or abs(t - h) < abs(bh - h))):
                bh = t
        return (bw or w, bh or h)

    def _snap_side(self, edge, val, span):
        """单边拖动时的磁吸：edge 取 "l"/"r"/"b"，val 是这条边的坐标，
        span 是钉死那条边的坐标。返回吸附后的边坐标，没吸住就原样返回。

        候选跟 _snap_size 是同一套：贴上邻居的对边、跟邻居同侧齐平、屏幕边。
        差在 SNAP_PX 以内就吸到正正好，光标得再走超过 SNAP_PX 才拽得开。
        """
        if not getattr(self.main, "_snap_on", True):
            return val
        horiz = edge in ("l", "r")
        lo = self.minimumWidth() if horiz else self.minimumHeight()
        best = None
        for m in self._neighbors():
            o = m.frameGeometry()
            if horiz:
                cands = ((o.left() - 1, o.right()) if edge == "r"
                         else (o.right() + 1, o.left()))
            else:
                cands = (o.top() - 1, o.bottom())
            for c in cands:
                if (abs(c - val) <= SNAP_PX
                        and (best is None or abs(c - val) < abs(best - val))):
                    best = c
        av = self._screen_rect()
        if av is not None:
            c = (av.right() if edge == "r" else
                 av.left() if edge == "l" else av.bottom())
            if (abs(c - val) <= SNAP_PX
                    and (best is None or abs(c - val) < abs(best - val))):
                best = c
        if best is None:
            return val
        if edge == "l":
            return best if span - best + 1 >= lo else val
        return best if best - span + 1 >= lo else val

    def _place_edges(self):
        """把四条边的热区铺到窗口边上；右下那 16x16 留给 QSizeGrip。"""
        try:
            w, h, g = self.width(), self.height(), 16
            box = {"l": QRect(0, g, 6, h - 2 * g),
                   "r": QRect(w - 6, g, 6, h - 2 * g),
                   "b": QRect(g, h - 6, w - 2 * g, 6),
                   "bl": QRect(0, h - g, g, g)}
            for edge, hd in getattr(self, "_edges", {}).items():
                r = box[edge]
                hd.setGeometry(r)
                hd.setVisible(r.width() > 0 and r.height() > 0)
        except Exception:              # noqa: BLE001
            pass

    def _sync_empty_geo(self):
        """空态文字铺满整个网格视口（居中靠 QLabel 的 AlignCenter）。"""
        try:
            self.empty.setGeometry(self.grid.viewport().rect())
        except Exception:              # noqa: BLE001
            pass

    def _keep_bottom(self):
        """把小窗口压回 Z 序最底层 —— 它是桌面挂件，不该盖住别的程序。

        Windows 点一下就会把窗口抬到最前，光靠 WindowStaysOnBottomHint 不够保险，
        所以每次显示 / 被激活都再 SetWindowPos 到底（HWND_BOTTOM=1；不挪位置、
        不改尺寸、不抢焦点）。
        """
        if getattr(self, "_raised", False):
            return                     # 新建后的露头期：别急着把它按回最底层
        try:
            ctypes.windll.user32.SetWindowPos(
                int(self.winId()), 1, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010)      # NOSIZE | NOMOVE | NOACTIVATE
        except Exception:              # noqa: BLE001
            pass

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                self._keep_bottom()        # 被点活了也立刻压回底层
            else:
                g = getattr(self, "grid", None)
                if g is not None:
                    g.clearSelection()     # 窗口失活：选中底色跟着退掉

    def showEvent(self, e):
        super().showEvent(e)
        # hwnd 只有真正建窗之后才有效，事件里再摘一次任务栏样式
        self._hide_from_taskbar()
        self._keep_bottom()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        try:
            self._grip.move(self.width() - 18, self.height() - 18)
            self._sync_empty_geo()
            self._place_edges()
        except Exception:              # noqa: BLE001
            pass
        self._follow(e.oldSize())      # 贴着的邻居跟着一起挪，保持贴死
        if not getattr(self, "_loading", True):
            self._queue_save()

    def moveEvent(self, e):
        super().moveEvent(e)
        if not getattr(self, "_loading", True):
            self._queue_save()

    # ---------- 吸附 ----------
    def _neighbors(self):
        """其它还显示着的小窗口（不含自己）。"""
        try:
            return [m for m in self.main._alive_minis() if m is not self]
        except Exception:              # noqa: BLE001
            return []

    def _screen_rect(self):
        """能吸的屏幕边：可用区域，避开任务栏。"""
        try:
            return self.screen().availableGeometry()
        except Exception:              # noqa: BLE001
            return None

    def _snap_pos(self, pos):
        """拖动落点先过一遍磁吸：四条边跟邻居 / 屏幕边贴齐，返回修正后的落点。

        每条边都试「贴着邻居的对边（并排）」与「跟邻居的同一侧对齐（齐平）」
        两种，取位移最小的那个；横竖各算一次，所以拐角也能一起吸上。
        屏幕只吸「同一侧对齐」那一种：左边贴屏幕左边、右边贴屏幕右边，上下同理。

        设置里关掉「小窗口自动粘连」就原样返回落点。
        """
        if not getattr(self.main, "_snap_on", True):
            return pos
        r = QRect(pos, self.size())
        bx = by = None
        for m in self._neighbors():
            o = m.frameGeometry()
            for mine, other in ((r.left(), o.right() + 1),      # 我贴在它右边
                                (r.right(), o.left() - 1),      # 我贴在它左边
                                (r.left(), o.left()),           # 左边齐平
                                (r.right(), o.right())):        # 右边齐平
                d = other - mine
                if abs(d) <= SNAP_PX and (bx is None or abs(d) < abs(bx)):
                    bx = d
            for mine, other in ((r.top(), o.bottom() + 1),      # 我贴在它下边
                                (r.bottom(), o.top() - 1),      # 我贴在它上边
                                (r.top(), o.top()),             # 上边齐平
                                (r.bottom(), o.bottom())):      # 下边齐平
                d = other - mine
                if abs(d) <= SNAP_PX and (by is None or abs(d) < abs(by)):
                    by = d
        av = self._screen_rect()
        if av is not None:                         # 屏幕四边也吸
            for mine, other in ((r.left(), av.left()), (r.right(), av.right())):
                d = other - mine
                if abs(d) <= SNAP_PX and (bx is None or abs(d) < abs(bx)):
                    bx = d
            for mine, other in ((r.top(), av.top()), (r.bottom(), av.bottom())):
                d = other - mine
                if abs(d) <= SNAP_PX and (by is None or abs(d) < abs(by)):
                    by = d
        return QPoint(pos.x() + (bx or 0), pos.y() + (by or 0))

    def _avoid_overlap(self):
        """把落位推到不压邻居为止：重叠是硬缺陷，跟「自动粘连」开关无关。

        每一轮：找出压着的邻居 → 沿**重叠较少的那条轴**推开（左右或上下两支里
        挑「推完还留在屏幕内、且跟所有邻居交叠面积最小」的那支）→ 再过一遍
        _snap_pos 磁吸贴死 → 夹回屏幕可用区。最多 8 轮，位置打转就收手。
        """
        av = self._screen_rect()
        if av is not None:                      # 起点先在屏幕内，收尾才不会把人拽到别人身上
            pos = self.pos()
            nx = min(max(pos.x(), av.left()), av.right() - self.width() + 1)
            ny = min(max(pos.y(), av.top()), av.bottom() - self.height() + 1)
            if (nx, ny) != (pos.x(), pos.y()):
                self.move(nx, ny)
        seen = set()
        for _ in range(8):
            r = self.frameGeometry()
            here = (r.x(), r.y())
            if here in seen:                    # 原地打转说明推不开，收手
                break
            seen.add(here)
            hit = None
            for m in self._neighbors():
                o = m.frameGeometry()
                if o.intersects(r):
                    hit = o
                    break
            if hit is None:
                break                           # 一个都不压了
            # 四个贴边位都试：重叠少的那条轴挑在前（并列时先挑它），
            # 再看哪个「推完跟所有邻居交叠面积最小」，面积打平就挑挪得近的。
            over_w = min(r.right(), hit.right()) - max(r.left(), hit.left()) + 1
            over_h = min(r.bottom(), hit.bottom()) - max(r.top(), hit.top()) + 1
            side_x = [QPoint(hit.right() + 1, r.y()),          # 贴它右边
                      QPoint(hit.left() - r.width(), r.y())]   # 贴它左边
            side_y = [QPoint(r.x(), hit.bottom() + 1),         # 贴它下边
                      QPoint(r.x(), hit.top() - r.height())]   # 贴它上边
            cands = (side_x + side_y) if over_w <= over_h else (side_y + side_x)
            best = None
            for c in cands:
                if av is not None:              # 先夹回屏幕可用区，再评估
                    c = QPoint(min(max(c.x(), av.left()), av.right() - r.width() + 1),
                               min(max(c.y(), av.top()), av.bottom() - r.height() + 1))
                rr = QRect(c.x(), c.y(), r.width(), r.height())
                area = 0
                for m in self._neighbors():
                    o = m.frameGeometry()
                    if rr.intersects(o):
                        ow = min(rr.right(), o.right()) - max(rr.left(), o.left()) + 1
                        oh = min(rr.bottom(), o.bottom()) - max(rr.top(), o.top()) + 1
                        area += max(ow, 0) * max(oh, 0)
                key = (area, abs(c.x() - r.x()) + abs(c.y() - r.y()))
                if best is None or key < best[0]:
                    best = (key, c)
            self.move(self._snap_pos(best[1]))
        av = self._screen_rect()
        if av is not None:                      # 收尾：不许留在屏幕外
            pos = self.pos()
            nx = min(max(pos.x(), av.left()), av.right() - self.width() + 1)
            ny = min(max(pos.y(), av.top()), av.bottom() - self.height() + 1)
            if (nx, ny) != (pos.x(), pos.y()):
                self.move(nx, ny)

    def _follow(self, old):
        """改尺寸时，贴着右边 / 下边（也就是被拉着走的那两条边）的邻居一起挪，
        两块永远贴死：不重叠，也不留缝。
        """
        if not (self.width() - old.width() or self.height() - old.height()):
            return
        if getattr(self, "_loading", True):
            return
        if not getattr(self.main, "_snap_on", True):   # 关掉粘连就不推邻居
            return
        dw = self.width() - old.width()
        dh = self.height() - old.height()
        r = self.frameGeometry()
        # 贴没贴死，要跟「改尺寸之前」那条边比：QSizeGrip 只动右边和下边，
        # 所以旧右边 = 新右边 - dw，旧下边 = 新下边 - dh。
        oright = r.right() - dw
        obot = r.bottom() - dh
        for m in self._neighbors():
            o = m.frameGeometry()
            nx, ny = o.left(), o.top()
            if (dw and 0 <= o.left() - oright <= 2
                    and o.top() < obot and o.bottom() > r.top()):
                nx += dw
            if (dh and 0 <= o.top() - obot <= 2
                    and o.left() < oright and o.right() > r.left()):
                ny += dh
            if (nx, ny) != (o.left(), o.top()):
                m.move(nx, ny)

    def _queue_save(self):
        try:
            self._save_timer.start()
        except Exception:              # noqa: BLE001
            pass

    def _save_geom(self):
        r = self.frameGeometry()
        self._update(x=r.x(), y=r.y(), w=self.width(), h=self.height())

    def closeEvent(self, e):
        """红点 / 用户关 = 收起来，不删数据。

        关机、注销、托盘「退出」也都会走到这里（Windows 一收工，Qt 就把所有窗口
        关一遍），所以**不能无条件落盘** open=False —— 那样下次开机账本里全是
        「已关闭」，小窗口一个都开不回来了。只认 `_user_closed` 这个标记。
        """
        self._save_geom()
        if self._user_closed:
            self._update(open=False)
        try:
            self.main._sync_mini_btn()
        except Exception:              # noqa: BLE001
            pass
        super().closeEvent(e)
        # 等这一轮事件走完再看「还有没有窗口」（此刻自己还算着可见）
        QTimer.singleShot(0, self.main._maybe_quit)

    # ---------- 网格 ----------
    # ---------- 界面顺序 ----------
    def _order_key(self):
        return _order_scope_mini(self.wid)

    def _ordered(self, paths):
        """账本顺序 + 用户的界面顺序：表里有的按表排，没有的按账本序跟在后头。"""
        raw = list(paths or [])
        norm = [os.path.normpath(p) for p in raw]
        table = (self.main._state.get("order") or {}).get(self._order_key())
        if not isinstance(table, list) or not table:
            return norm
        pos = {}
        for i, p in enumerate(table):
            pos.setdefault(_nk(p), i)
        head = sorted([p for p in norm if _nk(p) in pos], key=lambda p: pos[_nk(p)])
        tail = [p for p in norm if _nk(p) not in pos]
        return head + tail

    def _save_order(self, order):
        tbl = self.main._state.get("order")
        if not isinstance(tbl, dict):
            tbl = {}
        tbl[self._order_key()] = list(order or [])
        self.main._state["order"] = tbl
        self.main._save_state()

    def _on_reorder(self, paths, target):
        """小窗口内部拖拽换位：只改界面顺序，文件与账本分组都不动。"""
        d = self._data()
        cur = self._ordered(list((d or {}).get("paths") or []))
        new = _apply_reorder(cur, paths, target)
        if new == cur:
            return
        self._save_order(new)
        self._reload()

    def _reload(self):
        d = self._data()
        paths = self._ordered((d or {}).get("paths") or [])
        title = (d or {}).get("title") or "小窗口"
        fm = QFontMetrics(self.title.font())
        self.title.setText(fm.elidedText(title, Qt.ElideRight, max(60, self.width() - 110)))
        self.grid.setUpdatesEnabled(False)
        self.grid.clear()
        for p in paths:
            if not p:
                continue
            name = os.path.basename(p)
            is_dir = os.path.isdir(p)
            it = QListWidgetItem(self.main._icon_for(p), _disp_name(name, is_dir))
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.grid.addItem(it)
        self.grid.setUpdatesEnabled(True)
        self._sync_empty_geo()
        self.empty.setVisible(self.grid.count() == 0)
        # 新建那一刻 viewport 还是占位尺寸，等布局跑完再对齐一次
        QTimer.singleShot(0, self._sync_empty_geo)

    def _on_drop(self, paths):
        paths = list(paths or [])
        if MB is not None:
            try:
                added, skipped = MB.add_paths(self.wid, paths)
            except Exception:          # noqa: BLE001
                return
        # 拖进小窗口也得在主面板露头：登记进分组账本（GB），
        # 主面板的 _external_rows() 才看得到它们。文件本体照旧原地不动。
        try:
            self.main._ingest(paths, None)      # 返回 0 不算错，按下不表
        except Exception:          # noqa: BLE001
            pass
        self._reload()
        self.raise_()

    def _on_double(self, item):
        p = item.data(Qt.UserRole) if item is not None else None
        if not p or STORE is None:
            return
        try:
            STORE.open_item(p)
        except Exception:              # noqa: BLE001
            pass
        self.grid.clearSelection()     # 打开完就别留着那块选中底色

    # ---------- 菜单 ----------
    def _menu(self):
        return self.main._dark_menu()

    def _on_ctx_menu(self, pos):
        item = self.grid.itemAt(pos)
        gpos = self.grid.viewport().mapToGlobal(pos)
        if item is None:
            self.main._shell_bg_menu(gpos)
            return
        if not item.isSelected():
            self.grid.clearSelection()
            item.setSelected(True)
        paths = [i.data(Qt.UserRole) for i in self.grid.selectedItems()]
        paths = [p for p in paths if p]
        if not paths:
            return
        many = len(paths) > 1
        virtual = any(_is_virtual(p) for p in paths)
        menu = self._menu()
        a_open = QAction("打开", menu)
        a_open.setEnabled(not many)
        a_open.triggered.connect(lambda: self.main._open(paths[0]))
        menu.addAction(a_open)
        a_rev = QAction("在资源管理器中显示", menu)
        a_rev.setEnabled(not many and not virtual)
        if virtual:
            a_rev.setToolTip("系统图标没有对应的文件位置")
        a_rev.triggered.connect(lambda: self.main._reveal(paths[0]))
        menu.addAction(a_rev)
        can_ren = (not many and not virtual and STORE is not None
                   and callable(getattr(STORE, "rename", None)))
        a_ren = QAction("重命名" if not many else "重命名（只支持单个）", menu)
        a_ren.setEnabled(can_ren)
        if many:
            a_ren.setToolTip("一次只能重命名一个")
        elif virtual:
            a_ren.setToolTip("系统图标不能重命名")
        elif not can_ren:
            a_ren.setToolTip("数据层未就绪")
        a_ren.triggered.connect(lambda: self.main._rename(paths[0]))
        menu.addAction(a_ren)
        menu.addSeparator()
        a_del = QAction("删除到回收站" if not many
                        else "删除 %d 项到回收站" % len(paths), menu)
        a_del.setEnabled(not virtual)
        if virtual:
            a_del.setToolTip("系统图标不是文件，不能删除")
        a_del.triggered.connect(lambda: self.main._recycle(paths))
        menu.addAction(a_del)
        menu.addSeparator()
        a_more = QAction("更多操作 ▸", menu)
        a_more.setEnabled(bool(getattr(self.main, "_sm_ok", False)))
        a_more.triggered.connect(lambda: self.main._shell_menu(paths, gpos))
        if not getattr(self.main, "_sm_ok", False):
            a_more.setToolTip(getattr(self.main, "_sm_why", "") or "原生菜单不可用")
        menu.addAction(a_more)
        if not virtual:
            can_set = GB is not None
            sub = menu.addMenu("移到分类" if can_set else "移到分类（分组账本未就绪）")
            sub.setStyleSheet(menu.styleSheet())
            sub.setEnabled(not many and can_set)
            cur_g = ""
            try:
                cur_g = GB.get_group(paths[0]) or "" if GB is not None else ""
            except Exception:          # noqa: BLE001
                cur_g = ""
            for g in [n for n, _ in _groups()]:
                if g == cur_g or g == _sys_cat():
                    continue
                act = QAction(g, sub)
                act.triggered.connect(
                    lambda _=False, gg=g: self._move_category(paths[0], gg))
                sub.addAction(act)
        a_cp = QAction("复制路径" if not many else "复制 %d 个路径" % len(paths), menu)
        a_cp.triggered.connect(lambda: self.main._copy_paths(paths))
        menu.addAction(a_cp)
        menu.addSeparator()
        a_rm = QAction("从小窗口移除" if not many
                       else "从小窗口移除 %d 项" % len(paths), menu)
        a_rm.triggered.connect(lambda: self._remove_paths(paths))
        menu.addAction(a_rm)
        menu.addSeparator()
        self._px_submenu(menu)         # 图标上也能改大小，不用去顶栏
        if getattr(self.main, "_dbg_menupop", False):
            menu.popup(gpos)
            self.main._dbg_menu = menu
            return
        menu.exec(gpos)
        QTimer.singleShot(200, self._reload)

    def _move_category(self, path, group):
        self.main._set_group(path, group)
        self._reload()

    def _remove_paths(self, paths):
        if MB is None:
            return
        for p in paths:
            try:
                MB.remove_path(self.wid, p)
            except Exception:          # noqa: BLE001
                pass
        self._reload()

    def _on_more_menu(self):
        """顶栏 ··· ：关闭这个小窗口 / 删除这个小窗口。"""
        menu = self._menu()
        a_close = QAction("关闭这个小窗口", menu)

        def _do_close():
            self._user_closed = True       # 用户亲手的，记账本
            self.close()

        a_close.triggered.connect(_do_close)
        menu.addAction(a_close)
        menu.addSeparator()
        a_del = QAction("删除这个小窗口", menu)
        a_del.triggered.connect(self._delete_self)
        menu.addAction(a_del)
        gpos = self.btn_more.mapToGlobal(QPoint(0, self.btn_more.height() + 2))
        if getattr(self.main, "_dbg_menupop", False):
            menu.popup(gpos)               # 探针模式：不阻塞，见 main._dbg_menu
            self.main._dbg_menu = menu
            return
        menu.exec(gpos)

    def _on_bar_menu(self, pos):
        w = self.sender()
        gpos = (w.mapToGlobal(pos) if w is not None
                else self.mapToGlobal(pos))
        menu = self._menu()
        a_ren = QAction("重命名窗口", menu)
        a_ren.triggered.connect(self._rename_window)
        menu.addAction(a_ren)
        a_pin = QAction("解除固定位置" if self._pinned else "固定位置", menu)
        a_pin.triggered.connect(self._on_pin)
        menu.addAction(a_pin)
        self._px_submenu(menu)
        menu.addSeparator()
        a_main = QAction("打开主面板", menu)
        a_main.triggered.connect(self._show_main)
        menu.addAction(a_main)
        menu.addSeparator()
        a_del = QAction("删除这个小窗口", menu)
        a_del.triggered.connect(self._delete_self)
        menu.addAction(a_del)
        if getattr(self.main, "_dbg_menupop", False):
            menu.popup(gpos)
            self.main._dbg_menu = menu
            return
        menu.exec(gpos)

    def _rename_window(self):
        d = self._data() or {}
        cur = d.get("title") or "小窗口"
        dlg = NameDialog(self, "重命名窗口", "窗口标题最多 20 个字", "重命名", "取消",
                         cur, 340, max_len=20)

        def _dry(t):
            return None if (t or "").strip() else "名字不能为空"

        dlg.validator = _dry
        if not dlg.exec():
            return
        t = (dlg.value or "").strip()
        if not t:
            return
        self._update(title=t)
        self.setWindowTitle("Tomato Desktop Box · %s" % t)
        self._reload()

    def _on_pin(self):
        self._pinned = not self._pinned
        self._sync_pin()
        self._update(pinned=self._pinned)

    def _set_px(self, px):
        try:
            idx = [40, 48, 64].index(int(px))
        except Exception:              # noqa: BLE001
            return
        try:
            self.main.seg.index = idx
            self.main._on_size(idx)     # 跟随主面板档位，主面板也同步
        except Exception:              # noqa: BLE001
            pass
        self._apply_px(int(px))

    def _apply_px(self, px):
        self.grid.setIconSize(QSize(px, px))
        self.grid.setGridSize(_cell_size(px))
        try:
            self._grid_dele.px = px
        except Exception:              # noqa: BLE001
            pass
        self._reload()

    def _px_submenu(self, menu):
        """「图标大小」子菜单：顶栏右键、图标右键共用；跟主面板是同一个档位。"""
        sub = menu.addMenu("图标大小")
        sub.setStyleSheet(menu.styleSheet())
        cur_px = 48
        try:
            cur_px = int(self.main._icon_px)
        except Exception:              # noqa: BLE001
            pass
        for label, px in (("小", 40), ("中", 48), ("大", 64)):
            act = QAction(("✓ " if px == cur_px else "") + label, sub)
            act.triggered.connect(lambda _=False, p=px: self._set_px(p))
            sub.addAction(act)
        return sub

    def _show_main(self):
        try:
            self.main.show()
            self.main.raise_()
            self.main.activateWindow()
        except Exception:              # noqa: BLE001
            pass

    def _delete_self(self):
        if not self.main._confirm("删除小窗口",
                                  "删除这个小窗口？\n窗口里的图标记录会一起清掉，"
                                  "桌面文件不会被移动或删除。", "删除"):
            return
        if MB is not None:
            try:
                MB.remove(self.wid)
            except Exception:          # noqa: BLE001
                pass
        try:
            self.main._minis.pop(self.wid, None)
        except Exception:              # noqa: BLE001
            pass
        self._gone = True
        self._loading = True
        self.close()
        self.deleteLater()
        QTimer.singleShot(0, self.main._maybe_quit)


# ───────────────────────── 主窗 ─────────────────────────
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._state = _load_state()
        try:
            glass.set_solid(self._state.get("solid") or DEFAULT_STATE["solid"])
        except Exception:                  # noqa: BLE001
            pass
        set_bg_mode(self._state["bg"])  # 先定风格，_build 里的控件按当前风格取色
        self._pinned = bool(self._state["pinned"])
        self.setWindowTitle("Tomato Desktop Box")
        self._apply_flags()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(MIN_W, MIN_H)
        self._drag = None
        self._maxed = bool(self._state.get("maxed"))
        self._normal_geom = QRect(int(self._state.get("nx", self._state["x"])),
                                  int(self._state.get("ny", self._state["y"])),
                                  int(self._state.get("nw", self._state["w"])),
                                  int(self._state.get("nh", self._state["h"])))
        self._density = int(self._state["density"])
        self._snap_on = bool(self._state.get("snap", True))   # 小窗口自动粘连
        self._tray_on = bool(self._state.get("tray", True))   # 最小化到托盘
        self._close_tray = bool(self._state.get("close_tray", True))  # 点关闭：收进托盘
        # 任务栏外观的当前档位。只记**用户想要的**档位；真正下发到系统是 _apply_tb 的事，
        # 这里不碰任何 Win32 —— 构造期间改系统外观会让人在启动瞬间看见任务栏闪一下。
        _tbv = self._state.get("tb_mode")
        self._tb_mode = _tbv if _tbv in TB_MODES else TB_DEFAULT
        self._removed = {_nk(p) for p in (self._state.get("removed") or [])}
        self._gone = {_nk(p) for p in (self._state.get("gone") or [])}
        self.removed_rows = []             # 「已移除」档里的条目（从常规视图摘出去的那些）
        self.all_rows = []                 # 桌面扫到的全部条目（含被移除 / 彻底移除的）
        self._prov = None
        self._icon_px = [40, 48, 64][int(self._state["icon_idx"])]
        self._icons = icons.IconCache()
        self._shadow = None
        self.desktop_rows = []
        self.sys_rows = []                 # 虚拟系统项（此电脑 / 回收站），永不进 desktop_rows
        self._minis = {}                   # wid -> MiniWindow，别让它们被 GC 掉
        self._mini_tip = ""                # 小窗口工具提示缓存
        self._hidden_minis = []            # 随主面板一起收起来的小窗口 wid
        self._tray = None                  # 系统托盘（main() 里 _setup_tray() 建，必须留引用）
        self._shown_once = False           # showEvent 第一次不算「主面板重新打开」
        self._ready = False
        self._hide_warned = bool(self._state.get("hide_warned"))
        self._hide_quiet = bool(self._state.get("hide_quiet"))   # 启动自检「不再提示」
        self.desktop_hidden = False        # 由 _sync_hide_btn() 刷新，永远以 HD 为准
        self._dbg = None
        self._sm = None                    # shellmenu.ShellMenu 单例，懒建
        self._sm_ok, self._sm_why = (False, "shellmenu 模块缺失")
        if SM is not None:
            try:
                self._sm_ok, self._sm_why = SM.available()
            except Exception as exc:       # noqa: BLE001
                self._sm_ok, self._sm_why = False, str(exc)
        self._watch = None                 # 桌面目录监测（模块缺失则退化成手动 ⟳）
        self._watch_timer = QTimer(self)
        self._watch_timer.setSingleShot(True)
        self._watch_timer.setInterval(120)
        self._watch_timer.timeout.connect(self._flush_watch)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._save_state)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._unflash)
        self.resize(int(self._state["w"]), int(self._state["h"]))
        self._build()
        self._sync_pin()
        self._reload()
        self._sync_hide_btn()          # 按真实隐藏状态定按钮文字
        self._start_watch()            # 桌面目录实时监测（模块缺失就只靠 ⟳）
        QTimer.singleShot(180, self._check_hidden_on_start)   # 崩过没恢复只询问
        x, y = int(self._state["x"]), int(self._state["y"])
        if not os.path.isfile(_ui_json_path()):
            x, y = _center_pos(self.width(), self.height())   # 首次启动居中偏上
        self.move(x, y)
        if self._maxed:                    # 上次关窗时是最大化，原样恢复
            self._maxed = False
            self._toggle_max()
        self._restore_minis()              # 上次开着的小窗口原样开回来

    # ---------- 置顶 / 状态持久化 ----------
    def _apply_flags(self):
        """FramelessWindowHint 必须保留；置顶只加 WindowStaysOnTopHint。"""
        f = Qt.FramelessWindowHint | Qt.Window
        if self._pinned:
            f |= Qt.WindowStaysOnTopHint
        pos = self.pos()
        self.setWindowFlags(f)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if pos.x() or pos.y():
            self.move(pos)             # 切 flags 后位置不跳

    def _sync_pin(self):
        btn = getattr(self, "btn_pin", None)
        if btn is not None:
            btn.set_dim(not self._pinned)
            btn.setToolTip("置顶：开" if self._pinned else "置顶：关")

    def _on_pin(self):
        self._pinned = not self._pinned
        self._apply_flags()
        self.show()
        self._sync_pin()
        self._save_state()

    # ---------- 小窗口（MiniWindow / minis.json）----------
    def _alive_minis(self):
        """还真的显示在屏幕上的小窗口（点了红点收起来的不算）。"""
        out = []
        for m in list(self._minis.values()):
            if m is None or getattr(m, "_gone", False):
                continue
            try:
                if m.isVisible():
                    out.append(m)
            except Exception:          # noqa: BLE001
                pass
        return out

    def _maybe_quit(self):
        """主面板收起来了 + 一个小窗口都不剩 → 程序才真的退出。

        main() 里 setQuitOnLastWindowClosed(False) 是为了「关主面板不退出」，
        代价是必须有人显式判断「已经一个窗口都没有了」。
        """
        try:
            if self.isVisible() or self._alive_minis():
                return
            inst = QApplication.instance()
            if inst is not None:
                inst.quit()
        except Exception:              # noqa: BLE001
            pass

    # ---------- 托盘 / 最小化到托盘 ----------
    def _tray_enabled(self):
        """「最小化到托盘」开关的现读值（设置面板与黄点共用这一处）。"""
        return bool(getattr(self, "_tray_on", True))

    def _close_tray_enabled(self):
        """「点关闭」是否收进托盘（默认 True）。设置面板与 closeEvent 共用这一处。"""
        return bool(getattr(self, "_close_tray", True))

    # ---------- 任务栏外观 ----------
    def _tb_supported(self):
        """这一行到底能不能用：taskbarfx 在 + 本机支持（真 Windows + 找得到任务栏）。"""
        if TBFX is None:
            return False
        fn = getattr(TBFX, "is_supported", None)
        if not callable(fn):
            return False
        try:
            return bool(fn())
        except Exception:              # noqa: BLE001
            return False

    def _tb_peer_hint(self):
        """检测到 TranslucentTB 时的一句提示；没有就空串。"""
        if TBFX is None:
            return ""
        fn = getattr(TBFX, "peer_hint", None)
        if not callable(fn):
            return ""
        try:
            return str(fn() or "")
        except Exception:              # noqa: BLE001
            return ""

    def _on_tb_from_tray(self, mode):
        """托盘子菜单点了某一档：下发；失败就把勾选拨回**实际生效**的那一档。

        托盘自己已经按用户点的把勾挪过去了（QActionGroup 排他），所以这里没生效时
        必须显式 set_tb_mode 拨回来，否则菜单会显示一个假的当前档位。
        """
        if self._apply_tb(mode):
            return
        t = getattr(self, "_tray", None)
        setter = getattr(t, "set_tb_mode", None)
        if callable(setter):
            try:
                setter(getattr(self, "_tb_mode", TB_DEFAULT))
            except Exception:          # noqa: BLE001
                pass

    def _restore_tb(self):
        """启动时把任务栏对齐到 ui.json 里记的档位。

        即使档位是「关」也要下发一次 state=0：上次万一被强杀，系统可能还留在
        透明态，这一次写正好把残留抹掉 —— 崩溃自愈靠的就是这一条（再加上
        ResetTaskbar.exe 给「盒子根本没起来」的情况兜底）。
        """
        if not self._tb_supported():
            return False
        return self._apply_tb(getattr(self, "_tb_mode", TB_DEFAULT), quiet=True)

    def _sync_tray_tb(self):
        """把当前档位同步到托盘子菜单的勾选（设置面板改档后调；没托盘就跳过）。"""
        t = getattr(self, "_tray", None)
        setter = getattr(t, "set_tb_mode", None)
        if not callable(setter):
            return
        try:
            setter(getattr(self, "_tb_mode", TB_DEFAULT))
        except Exception:              # noqa: BLE001
            pass

    def _apply_tb(self, mode=None, quiet=False):
        """把任务栏切到 mode（默认当前档位）；成功才落进 self._tb_mode 并排队存盘。

        设置行、托盘子菜单、启动恢复三条线都走这里 —— 只有这一处会去碰系统。
        taskbarfx 缺失 / 下发失败时**不改** self._tb_mode：界面上的档位与实际
        必须一致，不能出现「显示磨砂、其实没生效」。
        """
        want = mode if mode in TB_MODES else getattr(self, "_tb_mode", TB_DEFAULT)
        if TBFX is None:
            if not quiet:
                self._flash("任务栏外观模块不可用：%s" % (_TBFX_ERR or "导入失败"))
            return False
        try:
            ok = bool(TBFX.apply(want))
        except Exception as exc:       # noqa: BLE001
            if not quiet:
                self._flash("任务栏切换失败：%s" % exc)
            return False
        if ok:
            self._tb_mode = want
            self._queue_save()
            hint = self._tb_peer_hint()
            if hint and not quiet:
                self._flash(hint)
        elif not quiet:
            self._flash("任务栏切换没生效（可能被另一个程序占用）")
        return ok

    def _on_min_click(self):
        """黄点被点：收进托盘返回 True，没接手返回 False（调用方照旧 showMinimized）。

        无边框面板上不了任务栏，showMinimized() 之后就点不回来了（见
        _hide_from_taskbar 的注释），所以只有「开关开着 + 托盘真的可用」才敢
        hide()；托盘不可用时**绝不能**把面板藏起来 —— 那样用户找不回窗口。
        """
        if not self._tray_enabled():
            return False
        t = getattr(self, "_tray", None)
        if t is None or not _tray_ok(t):
            return False
        try:
            self.hide()
        except Exception:              # noqa: BLE001
            return False
        return True

    def _setup_tray(self):
        """建系统托盘并挂到 self._tray（不留引用会被 GC，图标就没了）。

        tray 模块缺失 / 建不出来 / 没有通知区一律安静返回 None，主面板照常能用。
        """
        if TRAY is None:
            self._tray = None
            return None
        try:
            t = TRAY.Tray(
                self, (APPICON.qicon() if APPICON is not None else self.windowIcon()),
                on_toggle_panel=self._tray_toggle,
                on_show_panel=self._tray_show,
                on_show_all_minis=self._tray_show_minis,
                on_settings=self._on_settings,
                on_quit=self._tray_quit,
                on_tb_mode=self._on_tb_from_tray,   # 托盘里点了某一档
                tb_mode=getattr(self, "_tb_mode", TB_DEFAULT))
        except Exception:              # noqa: BLE001 —— 模块在但起不来
            self._tray = None
            return None
        self._tray = t                 # 无论显不显，引用先留住
        # 任务栏外观不支持时把子菜单整项禁掉（菜单还是留着，灰着比消失好懂）
        if not self._tb_supported():
            setter = getattr(t, "set_tb_enabled", None)
            if callable(setter):
                try:
                    setter(False, "当前系统不支持改任务栏外观")
                except Exception:      # noqa: BLE001
                    pass
        try:
            if not _tray_ok(t):
                return None            # 没通知区：不 show，也不假装有托盘
            t.show()
        except Exception:              # noqa: BLE001
            return None
        return t

    def _tray_toggle(self):
        """托盘左键：面板露着且没缩着就收起来，否则抬出来。"""
        try:
            if self.isVisible() and not self.isMinimized():
                self.hide()
                return
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:              # noqa: BLE001
            pass

    def _tray_show(self):
        """托盘菜单「显示主面板」：只显不隐（面板已经露着也不把它藏起来）。"""
        try:
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:              # noqa: BLE001
            pass

    def _tray_show_minis(self):
        """托盘菜单「显示全部小窗口」：登记过的小窗口全露出来。"""
        self._resume_minis()           # 跟着主面板一起收起来的先开回来
        ids = []
        try:
            ids = list(MB.open_ids()) if MB is not None else []
        except Exception:              # noqa: BLE001
            ids = []
        if not ids:
            ids = list(getattr(self, "_minis", {}) or {})
        for wid in ids[:12]:           # 别一口气开二十个（沿用 _restore_minis 的上限）
            try:
                self._open_mini(wid)   # 已经开着就只抬起来
            except Exception:          # noqa: BLE001
                pass
        self._sync_mini_btn()

    def _tray_quit(self):
        """托盘菜单「退出」：真的退出程序。

        setQuitOnLastWindowClosed(False) 之后没人喊退出，得自己来。
        小窗口只 hide() 不 close()：close() 会往 minis.json 写 open=False，
        下次开程序它们就再也回不来了（跟主面板 closeEvent 同一条规矩）。
        """
        for m in (getattr(self, "_minis", {}) or {}).values():
            if m is None or getattr(m, "_gone", False):
                continue
            try:
                m.hide()
            except Exception:          # noqa: BLE001
                pass
        t = getattr(self, "_tray", None)
        if t is not None:
            try:
                t.hide()
            except Exception:          # noqa: BLE001
                pass
        try:
            self.hide()                # 面板自己也要收掉，别留个光秃秃的窗口在屏幕上
        except Exception:              # noqa: BLE001
            pass
        self._save_state()
        try:
            inst = QApplication.instance()
            if inst is not None:
                inst.quit()
        except Exception:              # noqa: BLE001
            pass

    def _sync_mini_btn(self):
        btn = getattr(self, "btn_mini", None)
        if btn is None:
            return
        ok = MB is not None
        btn.setEnabled(True)           # 一直可点，点不动就弹一句
        if not ok:
            self._mini_tip = "数据层未就绪"
        else:
            n = 0
            try:
                n = len(MB.open_ids())
            except Exception:          # noqa: BLE001
                n = len(self._alive_minis())
            self._mini_tip = "小窗口" + ("（开着 %d 个）" % n if n else "")
        btn.setToolTip(self._mini_tip)
        btn.set_dim(not ok)            # minibox 缺失时置灰

    def _open_mini(self, wid):
        """把某个窗口数据变成 MiniWindow（已开则只抬起来）。"""
        m = self._minis.get(wid)
        if m is not None and not getattr(m, "_gone", False):
            try:
                m.show()               # 不 raise：小窗口只待在桌面那一层
            except Exception:          # noqa: BLE001
                pass
            return m
        try:
            m = MiniWindow(self, wid)
        except Exception as exc:       # noqa: BLE001
            self._info("小窗口", ["没能打开小窗口", str(exc)])
            return None
        self._minis[wid] = m
        m.show()                       # 不 raise：小窗口只待在桌面那一层
        # 这里是「真新建」分支（已存在的在上面就 return 了）：先别压在别人身上，
        # 再露个头，RAISE_MS 之后 _drop_back() 自己沉回桌面层
        m._avoid_overlap()
        try:
            m._save_timer.stop()       # 避让的落位当场记账，别留延迟写挂在后面
            m._save_geom()
        except Exception:              # noqa: BLE001
            pass
        m._raise_for_setup()
        self._sync_mini_btn()
        return m

    def _new_mini(self):
        if MB is None:
            self._info("小窗口", ["数据层未就绪", "deskbox/minibox.py 尚未就位，等它落地后再试。"])
            return None
        try:
            d = MB.create()
            wid = (d or {}).get("id")
        except Exception as exc:       # noqa: BLE001
            self._info("小窗口", ["没能新建小窗口", str(exc)])
            return None
        if not wid:
            return None
        self._sync_mini_btn()
        return self._open_mini(wid)

    def _on_mini_click(self):
        """左键 = 直接弹「小窗口」菜单（开着的打勾，收起来的也能点开）。"""
        if MB is None:
            self._info("小窗口", ["数据层未就绪", "deskbox/minibox.py 尚未就位，等它落地后再试。"])
            return
        self._on_mini_menu(None)

    def _on_mini_menu(self, pos=None):
        """▤ 右键 = 管理菜单：列全部窗口，open 的打勾，点一下切换。"""
        if MB is None:
            self._info("小窗口", ["数据层未就绪", "deskbox/minibox.py 尚未就位。"])
            return
        try:
            wins = list(MB.windows())
        except Exception as exc:       # noqa: BLE001
            self._info("小窗口", ["读不到小窗口清单", str(exc)])
            return
        menu = self._dark_menu()
        if not wins:
            a_none = QAction("还没有小窗口", menu)
            a_none.setEnabled(False)
            menu.addAction(a_none)
        for d in wins:
            wid = d.get("id")
            if not wid:
                continue
            title = (d.get("title") or "小窗口").strip()
            opened = bool(d.get("open"))
            # 开着的打 ✓，收起来的用一个淡色前缀标出名字；两种都必须可点
            label = ("✓ " if opened else "· ") + title
            act = QAction(label, menu)
            act.setToolTip("点一下收起来" if opened else "点一下打开")
            act.triggered.connect(lambda _=False, w=wid: self._toggle_mini(w))
            menu.addAction(act)
        menu.addSeparator()
        a_new = QAction("新建小窗口…", menu)
        a_new.triggered.connect(self._new_mini)
        menu.addAction(a_new)
        if pos is None:
            gpos = self.btn_mini.mapToGlobal(QPoint(0, self.btn_mini.height() + 4))
        else:
            gpos = self.btn_mini.mapToGlobal(pos)
        menu.exec(gpos)

    def _toggle_mini(self, wid):
        d = None
        try:
            d = MB.get(wid) if MB is not None else None
        except Exception:              # noqa: BLE001
            d = None
        opened = bool((d or {}).get("open"))
        m = self._minis.get(wid)
        if opened:
            if m is not None and not getattr(m, "_gone", False):
                m._user_closed = True  # 用户亲手的，closeEvent 才写 open=False
                m.close()
                try:
                    MB.update(wid, open=False)   # 兜底：closeEvent 没落盘也记上
                except Exception:      # noqa: BLE001
                    pass
            else:
                try:
                    MB.update(wid, open=False)
                except Exception:      # noqa: BLE001
                    pass
            self._sync_mini_btn()
            return
        self._open_mini(wid)
        try:
            MB.update(wid, open=True)
        except Exception:              # noqa: BLE001
            pass
        self._sync_mini_btn()

    def _resume_minis(self):
        """主面板重新出现：把上次跟着它一起收起来的小窗口再开回来。"""
        ids = list(self._hidden_minis)
        if not ids:
            return
        self._hidden_minis = []
        for wid in ids[:12]:           # 别一口气开二十个
            self._open_mini(wid)
        self._sync_mini_btn()

    def _restore_minis(self):
        """启动时把上次开着的小窗口原样开回来，顺手清掉失效条目。"""
        self._sync_mini_btn()
        if MB is None:
            return
        try:
            MB.prune()                 # minis.json 里已经不存在的路径清掉
        except Exception:              # noqa: BLE001
            pass
        try:
            ids = list(MB.open_ids())
        except Exception:              # noqa: BLE001
            ids = []
        for wid in ids[:12]:           # 别一口气开二十个
            self._open_mini(wid)
        self._sync_mini_btn()

    def _repaint_all(self):
        """背景模式变了：主窗与所有还开着的玻璃对话框一起重画。"""
        self.update()
        for w in QApplication.allWidgets():
            if isinstance(w, (MainWindow, GlassDialog, MiniWindow)) and w.isVisible():
                w.update()

    def _state_now(self):
        r = self.frameGeometry()
        # 最大化时不把全屏尺寸存成 "w"/"h"，否则下次还原就没尺寸可回了
        if self._maxed and not self._normal_geom.isNull():
            x, y = self._normal_geom.x(), self._normal_geom.y()
            w, h = self._normal_geom.width(), self._normal_geom.height()
        else:
            x, y, w, h = r.x(), r.y(), self.width(), self.height()
        return {"x": x, "y": y, "w": w, "h": h,
                "density": int(self._density), "pinned": bool(self._pinned),
                "icon_idx": self.seg.index, "bg": bg_mode(),
                "solid": glass.solid_color().name(),
                "snap": bool(self._snap_on),
                "tray": bool(self._tray_on),
                "close_tray": bool(self._close_tray),
                "tb_mode": str(getattr(self, "_tb_mode", TB_DEFAULT)),
                "removed": sorted(self._removed),
                "gone": sorted(self._gone),
                "order": {str(k): list(v) for k, v in
                          (self._state.get("order") or {}).items()
                          if isinstance(v, list)},
                "hide_warned": bool(self._hide_warned),
                "hide_quiet": bool(self._hide_quiet),
                "maxed": bool(self._maxed),
                "nx": x, "ny": y, "nw": w, "nh": h}

    def _save_state(self):
        try:
            p = _ui_json_path()
            d = os.path.dirname(p)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state_now(), f, ensure_ascii=False)
            os.replace(tmp, p)         # 原子替换，半个 JSON 也不会留下
        except Exception:              # noqa: BLE001 —— 写失败不影响使用
            pass

    def _queue_save(self):
        try:
            self._save_timer.start()
        except Exception:              # noqa: BLE001
            pass

    def moveEvent(self, e):
        super().moveEvent(e)
        self._queue_save()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow():
            try:
                self.grid.clearSelection()      # 窗口失活：选中底色跟着退掉
            except Exception:      # noqa: BLE001
                pass

    def showEvent(self, e):
        super().showEvent(e)
        # 主面板又露头了：上次跟着它一起收起来的小窗口原样开回来。
        # 第一次 show 不算（__init__ 末尾的 _restore_minis 已经处理过一次）。
        if self._shown_once:
            self._resume_minis()
        else:
            self._shown_once = True

    def _close_to_tray(self, e):
        """点关闭 = 收进托盘。收成了返回 True（closeEvent 直接收工），否则 False 走真退出。

        硬要求：开关关着、托盘模块缺失、没有通知区 —— 一律回退成真退出。
        **绝不允许**留一个点不回来的程序，所以这里只要有一点不确定就返回 False。
        """
        if not self._close_tray_enabled():
            return False
        if TRAY is None:
            return False               # 托盘模块都没有，谈不上收进托盘
        t = getattr(self, "_tray", None)
        if t is None or not _tray_ok(t):
            return False               # 托盘不可用：让 closeEvent 照旧真退出
        try:
            # 只管主面板：小窗口一个都不许动（用户要求），所以不碰 _hidden_minis、
            # 不 hide 小窗口、不写任何 open 标记 —— 托盘把面板叫回来时
            # _resume_minis() 自然是空操作，不会多此一举去开 / 收小窗口。
            self.hide()
        except Exception:              # noqa: BLE001
            return False               # 没收拾干净就别装成收进托盘了
        e.ignore()                     # 不许 Qt 真关；窗口已经自己 hide 了
        self._save_state()
        return True

    def closeEvent(self, e):
        if self._close_to_tray(e):
            return
        self._save_state()
        # 关主面板 = 整个程序收工：小窗口跟着一起收起来，但**不能 close()** ——
        # MiniWindow.closeEvent 会往 minis.json 写 open=False，那样下次开主面板
        # 就再也看不见它们了。用户要的是「主面板关、小窗口跟着关；主面板开、
        # 小窗口跟着开」，所以只 hide，把 open 标记原样留在账本里。
        self._hidden_minis = [m.wid for m in self._alive_minis()]
        for m in list(self._minis.values()):
            if m is None or getattr(m, "_gone", False):
                continue
            try:
                m.hide()
            except Exception:          # noqa: BLE001
                pass
        self._stop_watch()             # 别把轮询线程带走
        try:
            if self._sm is not None:
                self._sm.close()
                self._sm = None
        except Exception:              # noqa: BLE001
            pass
        super().closeEvent(e)
        # setQuitOnLastWindowClosed(False) 之后，窗口全关也得有人把程序收掉
        QTimer.singleShot(0, self._maybe_quit)

    # ---------- 玻璃底 ----------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        local = QRect(0, 0, self.width(), self.height())
        g = self.mapToGlobal(local.topLeft())
        global_rect = QRect(g.x(), g.y(), self.width(), self.height())
        rad = 0 if self._maxed else RADIUS   # 贴边时留圆角会露出后面的桌面
        glass.GLASS.paint(p, local, global_rect, rad,
                          density=self._density, mode=bg_mode())
        # 四角之外保持透明 —— WA_TranslucentBackground + 上面的圆角裁剪已实现

    # ---------- 骨架 ----------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(44)
        self.bar_widget = bar
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 14, 0)
        h.setSpacing(8)
        self.btn_refresh = RoundButton("⟳", "刷新")
        self.btn_refresh.clicked.connect(self._on_refresh)
        h.addWidget(self.btn_refresh)
        self.btn_hide = RoundButton("👁", "隐藏桌面图标")
        self.btn_hide.clicked.connect(self._on_hide_toggle)
        h.addWidget(self.btn_hide)
        self.btn_set = RoundButton("⚙", "设置")
        self.btn_set.clicked.connect(self._on_settings)
        h.addWidget(self.btn_set)
        self.btn_pin = RoundButton("📌", "置顶")
        self.btn_pin.clicked.connect(self._on_pin)
        h.addWidget(self.btn_pin)
        self.btn_mini = RoundButton("▤", "小窗口")
        self.btn_mini.set_font_px(16)      # ▤ 字形本身很小，放大一号才看得清
        self.btn_mini.clicked.connect(self._on_mini_click)
        self.btn_mini.setContextMenuPolicy(Qt.CustomContextMenu)
        self.btn_mini.customContextMenuRequested.connect(self._on_mini_menu)
        h.addWidget(self.btn_mini)
        self.lb_mini_hint = QLabel("点击添加小窗口")
        _hf = QFont(FAMILY)
        _hf.setPixelSize(11)
        self.lb_mini_hint.setFont(_hf)
        _theme(self.lb_mini_hint, lambda m: "color:%s;" % glass.inkcss(150, m))
        h.addWidget(self.lb_mini_hint)
        h.addStretch(1)
        title = QLabel("Tomato Desktop Box")
        tf = QFont(FAMILY)
        tf.setPixelSize(13)
        tf.setWeight(QFont.DemiBold)
        title.setFont(tf)
        _theme(title, lambda m: "color:%s;" % glass.inkcss(215, m))
        h.addWidget(title)
        h.addStretch(1)
        self.lights = TrafficLights()
        h.addWidget(self.lights)
        root.addWidget(bar)

        tool = QWidget()
        tool.setFixedHeight(42)
        self.tool_widget = tool
        th = QHBoxLayout(tool)
        th.setContentsMargins(16, 0, 16, 8)
        th.setSpacing(10)
        self.search = SearchField()
        self.search.textChanged.connect(lambda _: self._filter(self.side.currentRow()))
        th.addWidget(self.search, 1)
        self.seg = Segmented(["小", "中", "大"])
        self.seg.index = int(self._state["icon_idx"])
        self.seg._cb = self._on_size
        th.addWidget(self.seg)
        root.addWidget(tool)

        body = QWidget()
        bh = QHBoxLayout(body)
        bh.setContentsMargins(8, 0, 8, 0)
        bh.setSpacing(0)

        self.side = SideList(self)
        self.side.setFixedWidth(146)
        self.side.setItemDelegate(SideDelegate(self.side))
        self.side.setFrameShape(QFrame.NoFrame)
        self.side.setMouseTracking(True)
        self.side.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.side.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.side.setStyleSheet("QListWidget{background:transparent;border:0;outline:0;}")
        self.side.setSelectionMode(QAbstractItemView.SingleSelection)
        self.side.currentRowChanged.connect(self._filter)
        self.side.setAcceptDrops(True)          # 从网格拖过来改分类
        self.side.setDragDropMode(QAbstractItemView.DropOnly)
        self.side.viewport().setAcceptDrops(True)
        self.side.dropped_paths.connect(self._on_side_drop)
        self.side.setContextMenuPolicy(Qt.CustomContextMenu)
        self.side.customContextMenuRequested.connect(self._on_side_menu)
        self.side.hint_clicked.connect(self._on_side_hint)
        bh.addWidget(self.side)

        self.grid = DropGrid(self)
        self.grid.dropped_paths.connect(self._on_grid_drop)
        self.grid.reordered.connect(self._on_grid_reorder)
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(2)
        self.grid.setFrameShape(QFrame.NoFrame)
        self.grid.setIconSize(QSize(self._icon_px, self._icon_px))
        self.grid.setGridSize(_cell_size(self._icon_px))
        self._grid_dele = IconDelegate(self.grid, self._icon_px)
        self.grid.setItemDelegate(self._grid_dele)
        self.grid.setAcceptDrops(True)          # 从桌面直接拖进来
        self.grid.setDropIndicatorShown(False)  # 自己画虚线框
        self.grid.viewport().setAcceptDrops(True)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setDragEnabled(True)          # 拖到侧栏改分类
        self.grid.setDragDropMode(QAbstractItemView.DragDrop)
        self.grid.setDefaultDropAction(Qt.MoveAction)
        self._drop_hot = False
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._on_ctx_menu)
        self.grid.itemDoubleClicked.connect(self._on_double)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        _theme(self.grid, lambda m: """
            QListWidget{background:transparent;border:0;outline:0;}
            QListWidget::item{border-radius:10px;}
            QScrollBar:vertical{background:transparent;width:9px;margin:2px 2px 2px 0;}
            QScrollBar::handle:vertical{background:%s;border-radius:4px;min-height:32px;}
            QScrollBar::handle:vertical:hover{background:%s;}
            QScrollBar::add-line,QScrollBar::sub-line{height:0;}
            QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}
        """ % (glass.panelcss(42, m), glass.panelcss(80, m)))
        gf = QFont(FAMILY)
        gf.setPixelSize(11)
        self.grid.setFont(gf)
        bh.addWidget(self.grid, 1)
        root.addWidget(body, 1)

        foot = QWidget()
        foot.setFixedHeight(46)
        self.foot_widget = foot
        fh = QHBoxLayout(foot)
        fh.setContentsMargins(16, 0, 12, 10)
        fh.setSpacing(10)
        self.stat = QLabel("")
        sf = QFont(FAMILY)
        sf.setPixelSize(11)
        self.stat.setFont(sf)
        _theme(self.stat, lambda m: "color:%s;" % glass.inkcss(105, m))
        fh.addWidget(self.stat)
        hf2 = QFont(FAMILY)
        hf2.setPixelSize(11)
        fh.addStretch(1)
        lab = QLabel("透明度")
        lab.setFont(sf)
        _theme(lab, lambda m: "color:%s;" % glass.inkcss(120, m))
        fh.addWidget(lab)
        self.sld = QSlider(Qt.Horizontal)
        self.sld.setFixedWidth(96)
        self.sld.setRange(0, 100)
        # 滑块显示的是「透明度」：0 = 最实（density 100），100 = 最透（density 0）。
        # ui.json 里 density 的语义完全不变，换算只活在这两个方向相反的边界上。
        self.sld.setValue(100 - self._density)
        self.sld.setCursor(Qt.PointingHandCursor)
        self.sld.valueChanged.connect(self._on_opacity)
        _theme(self.sld, lambda m: """
            QSlider::groove:horizontal{height:4px;background:%s;border-radius:2px;}
            QSlider::sub-page:horizontal{background:%s;border-radius:2px;}
            QSlider::handle:horizontal{width:14px;height:14px;margin:-5px 0;border-radius:7px;
                background:%s;border:1px solid %s;}
            QSlider::handle:horizontal:hover{background:%s;}
        """ % (glass.panelcss(34, m), glass.panelcss(90, m), glass.panelcss(210, m),
               glass.panelcss(70, m), glass.panelcss(245, m)))
        fh.addWidget(self.sld)
        fh.addSpacing(6)
        ghost = QPushButton("重置分组")
        ghost.setFont(sf)
        ghost.setFixedHeight(28)
        ghost.setCursor(Qt.PointingHandCursor)
        _theme(ghost, lambda m: """
            QPushButton{background:%s;border:0;border-radius:7px;
                        padding:0 13px;color:%s;font-size:11px;}
            QPushButton:hover{background:%s;}
        """ % (glass.panelcss(20, m), glass.inkcss(205, m), glass.panelcss(40, m)))
        ghost.clicked.connect(self._on_reset)
        fh.addWidget(ghost)
        prim = QPushButton("一键导入桌面")
        pf = QFont(FAMILY)
        pf.setPixelSize(11)
        pf.setWeight(QFont.DemiBold)
        prim.setFont(pf)
        prim.setFixedHeight(28)
        prim.setCursor(Qt.PointingHandCursor)
        prim.setToolTip("把桌面上的图标都收进来：缺哪个就列出哪个，确认后导入\n"
                        "（只写记录，文件原地不动）")
        prim.setStyleSheet("""
            QPushButton{background:#0A84FF;border:0;border-radius:7px;padding:0 15px;color:#fff;}
            QPushButton:hover{background:#3D9BFF;}
            QPushButton:pressed{background:#0873E0;}
        """)
        prim.clicked.connect(self._on_tidy)
        fh.addWidget(prim)
        root.addWidget(foot)

        self.empty = QLabel("这里还没有内容", self.grid.viewport())
        ef = QFont(FAMILY)
        ef.setPixelSize(12)
        self.empty.setFont(ef)
        _theme(self.empty, lambda m: "color:%s;background:transparent;" % glass.inkcss(90, m))
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.empty.hide()

        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(16, 16)

        # 顶栏 / 工具行 / 底栏的空白处双击 = 最大化/还原（子按钮自己收不到就冒泡上来）
        for w in (bar, tool, foot):
            w.installEventFilter(self)
            w.setAttribute(Qt.WA_StyledBackground, False)

    # ---------- 最大化 / 还原 ----------
    def eventFilter(self, obj, ev):
        """只吃容器空白处的双击与右键。

        按钮/交通灯/搜索框/滑块自己接收鼠标事件，不会冒泡到这里；
        能冒泡上来的只有空位与被忽略的 QLabel（标题文字），都是想要的区域。
        """
        if ev.type() == QEvent.MouseButtonDblClick and ev.button() == Qt.LeftButton:
            hit = obj.childAt(ev.position().toPoint()) if hasattr(ev, "position") else None
            if hit is None or hit is obj or isinstance(hit, QLabel):
                self._toggle_max()
                return True
        elif ev.type() == QEvent.ContextMenu:
            # 顶栏 / 工具栏 / 底栏的空白处：跟右键桌面一样（可新建）。
            # 侧栏与网格不装这个过滤器，它们有各自的右键菜单。
            hit = obj.childAt(ev.pos()) if hasattr(ev, "pos") else None
            if hit is None or hit is obj or isinstance(hit, QLabel):
                gpos = ev.globalPos() if hasattr(ev, "globalPos") else None
                if gpos is not None:
                    self._shell_bg_menu(gpos)
                    return True
        return super().eventFilter(obj, ev)

    def _toggle_max(self):
        """FramelessWindowHint 下不能用 showMaximized（会盖住任务栏），
        只把几何设成屏幕工作区。"""
        if self._maxed:
            g = self._normal_geom
            self._maxed = False
            if g is not None and not g.isNull():
                self.setGeometry(g)
        else:
            self._normal_geom = QRect(self.pos(), self.size())
            self._maxed = True
            try:
                av = self.screen().availableGeometry()
            except Exception:              # noqa: BLE001
                av = QApplication.primaryScreen().availableGeometry()
            self.setGeometry(av)
        self._sync_grip()
        self.update()
        self._queue_save()

    def _sync_grip(self):
        """最大化时角落拉手没有意义，藏起来。"""
        try:
            self._grip.setVisible(not self._maxed)
        except Exception:                  # noqa: BLE001
            pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._maxed:
            self._grip.move(self.width() - 18, self.height() - 18)
        if getattr(self, "empty", None) is not None:
            self.empty.setGeometry(self.grid.viewport().rect())
        self._queue_save()

    # ---------- 数据 ----------
    def _desk_dirs(self):
        if STORE is not None:
            try:
                d = list(getattr(STORE, "DESK_DIRS"))
                if d:
                    return d
            except Exception:          # noqa: BLE001
                pass
        return [os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
                r"C:\Users\Public\Desktop"]

    def _scan_dirs(self):
        """store 不可用时的兜底扫描：只 listdir，不碰文件。"""
        rows = []
        for d in self._desk_dirs():
            if not os.path.isdir(d):
                continue
            try:
                names = sorted(os.listdir(d), key=lambda s: s.casefold())
            except OSError:
                continue
            for n in names:
                if n.lower() == "desktop.ini" or n.startswith("."):
                    continue
                p = os.path.join(d, n)
                rows.append(Row(p, n, "", os.path.isdir(p)))
        return rows

    def _scan_desktop(self):
        """返回 (rows, ready)。ready=False 表示 store 未就绪，rows 是无分类的兜底列表。"""
        ready = False
        rows = []
        if STORE is not None:
            try:
                rows = _rows_from(STORE.scan())
                ready = True
            except Exception:          # noqa: BLE001
                ready = False
        if not ready:
            rows = self._scan_dirs()
        self._assign_groups(rows)
        self.all_rows = list(rows)               # 只装桌面实扫到的：一键导入只认桌面
        rows.extend(self._external_rows(rows))   # 桌面外拖进来的也得露面
        if self._gone:                           # 彻底移除的：程序里彻底不露面
            rows = [r for r in rows if _nk(r.path) not in self._gone]
        if self._removed:                        # 手动移除的：从常规视图里摘出去
            self.removed_rows = [r for r in rows if _nk(r.path) in self._removed]
            rows = [r for r in rows if _nk(r.path) not in self._removed]
        else:
            self.removed_rows = []
        return rows, ready

    def _external_rows(self, rows):
        """账本里登记过、却不在桌面上的条目 —— 从资源管理器别处拖进收纳盒的东西。

        收纳盒只管「谁跟谁一组」，不挑来源：桌面外的程序 / 快捷方式拖进来也该
        在网格里露头（文件本体仍然原地不动，只是登记 + 显示）。
        名字只能取文件名：账本键是 normcase 后的路径，原始大小写已经丢了。
        """
        if GB is None:
            return []
        try:
            book = GB.load() or {}
            have = {GB.key_of(r.path) for r in rows}
            desks = {os.path.normcase(os.path.abspath(dd))
                     for dd in (getattr(STORE, "DESK_DIRS", None) or [])}
        except Exception:              # noqa: BLE001
            return []
        out = []
        for key in list(book):
            if (not isinstance(key, str) or key in have
                    or os.path.dirname(key) in desks
                    or _is_virtual(key) or not os.path.exists(key)):
                continue            # 桌面的上面已经列过，虚拟项和已删除的不算
            try:
                nm = os.path.basename(key.rstrip("\\/")) or key
                is_dir = os.path.isdir(key)
            except Exception:          # noqa: BLE001
                continue
            out.append(Row(key, nm, (book.get(key) or {}).get("group") or UNSORTED,
                           is_dir, False))
        out.sort(key=lambda r: r.name.casefold())
        return out

    def _scan_sys(self):
        """系统自带图标（此电脑 / 回收站）。模块未落地就返回空列表，分类整个不出现。"""
        if SYS is None:
            return []
        try:
            items = SYS.visible_items()
        except Exception:              # noqa: BLE001 —— 注册表 / COM 失败都只是少一个分类
            return []
        cat = _sys_cat()
        out = []
        for it in items or []:
            try:
                p = it["path"] if isinstance(it, dict) else it.path
                nm = it["name"] if isinstance(it, dict) else it.name
            except Exception:          # noqa: BLE001 —— 契约外的坏条目直接跳过
                continue
            if not p:
                continue
            out.append(Row(p, nm or p, cat, False, True, sys=True))
        return out

    def _assign_groups(self, rows):
        """分组真相只在账本里；账本没有的条目用 store.classify 现算一次（只读）。"""
        if not rows:
            return
        assign = None
        if GB is not None:
            try:
                assign = GB.assign_of(rows) or {}
            except Exception:          # noqa: BLE001
                assign = {}
        cl = _classify_fn()
        for r in rows:
            g = assign.get(r.path) if assign else None
            if not g and cl is not None:
                try:
                    g = cl(r.path)
                except Exception:      # noqa: BLE001
                    g = None
            r.group = g or UNSORTED

    def _counts(self):
        """侧栏计数：全部按桌面条目算，没有任何「已收纳」维度了。"""
        cnt = {n: 0 for n, _ in _groups()}
        cnt["全部"] = len(self.desktop_rows)          # 虚拟系统项不进「全部」
        for r in self.desktop_rows:
            if r.group in cnt:
                cnt[r.group] += 1
        if SYS is not None:                          # 模块在就出这条键（哪怕暂时 0 项）
            cnt[_sys_cat()] = len(self.sys_rows)
        if self.removed_rows:                        # 手动移除那一档：有货才出现
            cnt[REMOVED] = len(self.removed_rows)
        return cnt

    def _ledger_prune(self):
        """把账本里文件已经不存在的条目清掉，免得计数虚高。

        已知集合 = 桌面条目 ∪ 账本里文件仍在的那些路径。
        之所以把后者也算进来：用户可以从资源管理器把任意文件拖进网格
        （只登记分组），它不是桌面项，但文件还在，不该被当成垃圾清掉。
        """
        if GB is None:
            return
        try:
            known = {r.path for r in self.desktop_rows}
            for k in list(GB.load() or {}):
                if k not in known and os.path.exists(k):
                    known.add(k)
            GB.prune(known)
        except Exception:              # noqa: BLE001
            pass

    def _load_items(self):
        self.desktop_rows, self._ready = self._scan_desktop()
        self.sys_rows = self._scan_sys()
        self._ledger_prune()               # 只清桌面 / 账本项，虚拟项不参与
        self._build_side()

    def _build_side(self):
        prev = self.side.currentRow()
        cnt = self._counts()
        names = ["全部"] + [n for n, _ in _groups()]
        sc = _sys_cat()
        if SYS is not None and sc not in names:
            # 插在「未分类」之前；未分类必须仍是最后一个
            if UNSORTED in names:
                names.insert(names.index(UNSORTED), sc)
            else:
                names.append(sc)
        if cnt.get(REMOVED) and REMOVED not in names:
            if UNSORTED in names:
                names.insert(names.index(UNSORTED), REMOVED)
            else:
                names.append(REMOVED)
        self.side.blockSignals(True)
        self.side.clear()
        for name in names:
            it = QListWidgetItem(name)
            it.setData(Qt.UserRole, name)
            it.setData(Qt.UserRole + 1, cnt.get(name, 0))
            it.setData(Qt.UserRole + 2, _color_of(name))
            self.side.addItem(it)
        self.side.blockSignals(False)
        if prev < 0:
            prev = 0
        self.side.setCurrentRow(max(0, min(prev, self.side.count() - 1)))

    # ---------- 分类增删 ----------
    def _on_side_menu(self, pos, fallback="全部"):
        """侧栏右键：新建分类 / 删除分类。用自绘深色菜单，不走系统原生。"""
        it = self.side.itemAt(pos)
        cat = it.data(Qt.UserRole) if it is not None else fallback
        m = self._dark_menu()
        a_new = QAction("新建分类…", m)
        a_new.setEnabled(CATS is not None)
        a_new.triggered.connect(self._on_new_category)
        m.addAction(a_new)
        a_del = QAction("删除分类…", m)
        can_del = (CATS is not None
                   and cat not in (None, "", "全部", UNSORTED, _sys_cat(), REMOVED))
        a_del.setEnabled(can_del)
        if CATS is None:
            a_del.setToolTip("分类注册表未就绪")
        elif cat in (None, "", "全部", UNSORTED, _sys_cat(), REMOVED):
            a_del.setToolTip("「%s」不能删除" % cat)
        a_del.triggered.connect(lambda _=False, c=cat: self._on_del_category(c))
        m.addAction(a_del)
        m.exec(self.side.viewport().mapToGlobal(pos))

    def _on_side_hint(self):
        """点侧栏下方那颗「+ 新建分类」= 直接开新建分类对话框。"""
        self._on_new_category()

    def _on_new_category(self):
        if CATS is None:
            self._info("新建分类", ["分类注册表未就绪", "deskbox/cats.py 尚未就位。"])
            return
        d = NameDialog(self, "新建分类", "名字最多 12 个字，可用中文、字母、数字。",
                       text="")

        def _dry(t):
            """只校验不落盘：直接借 cats.check 的同一套规则（空/超长/非法字符/重名）。"""
            if str(t or "").strip() in (_sys_cat(), REMOVED, "全部"):
                return "「%s」是内置名字，换个名字" % str(t or "").strip()
            try:
                return CATS.check(t)
            except Exception:          # noqa: BLE001
                return None

        d.validator = _dry
        if not d.exec():
            return
        name = d.value
        try:
            ok, err = CATS.add(name)
        except Exception as exc:       # noqa: BLE001
            ok, err = False, str(exc)
        if not ok:
            self._info("新建分类", ["没能新建", err or "未知原因"])
            return
        self._build_side()
        self._filter(self.side.currentRow())
        self._flash("已新建分类 %s" % name)

    def _on_del_category(self, cat):
        if CATS is None or cat in (None, "", "全部", UNSORTED, _sys_cat(), REMOVED):
            return
        n = sum(1 for r in self.desktop_rows if r.group == cat)
        if not self._confirm("删除分类",
                             "删除分类「%s」？\n该分类下的 %d 项将移到「%s」，"
                             "文件不会被移动或删除。" % (cat, n, UNSORTED),
                             "删除", cancel_text="取消"):
            return
        try:
            ok, err = CATS.remove(cat)
        except Exception as exc:       # noqa: BLE001
            ok, err = False, str(exc)
        if not ok:
            self._info("删除分类", ["没能删除", err or "未知原因"])
            return
        moved = 0
        mv = getattr(GB, "move_category", None) if GB is not None else None
        if callable(mv):
            try:
                moved = int(mv(cat, UNSORTED) or 0)
            except Exception:          # noqa: BLE001
                moved = 0
        cur = self.side.currentItem()
        was_cur = cur is not None and cur.data(Qt.UserRole) == cat
        self._build_side()
        if was_cur:                    # 当前选中的分类被删了，退回「全部」
            self.side.setCurrentRow(0)
        self._reload()
        self._flash("已删除分类 %s，%d 项已移到%s" % (cat, moved, UNSORTED))

    def _reload(self):
        self._load_items()
        self._filter(self.side.currentRow())

    # ---------- 交互 ----------
    def _cat_order(self, name):
        """侧栏里的分类顺序索引：「全部」= 0，其余按 _groups()，系统应用插在未分类前。"""
        names = ["全部"] + [n for n, _d in _groups()]
        sc = _sys_cat()
        if SYS is not None and sc not in names:
            if UNSORTED in names:
                names.insert(names.index(UNSORTED), sc)
            else:
                names.append(sc)
        try:
            return names.index(name)
        except ValueError:
            return len(names)

    def _filter(self, row):
        cur = self.side.currentRow() if row is None else row
        if cur < 0:
            return
        it = self.side.item(cur)
        cat = it.data(Qt.UserRole) if it is not None else "全部"

        if cat == "全部":
            # 虚拟系统项不混进来；按左侧分类顺序排，同分类内按显示名
            rows = sorted(self.desktop_rows,
                          key=lambda r: (self._cat_order(r.group),
                                         r.disp.casefold()))
        elif cat == _sys_cat():
            rows = list(self.sys_rows)
        elif cat == REMOVED:
            rows = list(getattr(self, "removed_rows", []))
        else:
            rows = [r for r in self.desktop_rows if r.group == cat]

        # 用户拖过顺序的，按顺序表排；表里没有的排后面，仍保持上面算好的相对次序
        _ord = (self._state.get("order") or {}).get(_order_scope_main(cat))
        if isinstance(_ord, list) and _ord:
            _pos = {}
            for _i, _p in enumerate(_ord):
                _pos.setdefault(_nk(_p), _i)
            _hit = [r for r in rows if _nk(r.path) in _pos]
            _rest = [r for r in rows if _nk(r.path) not in _pos]
            _hit.sort(key=lambda r: _pos[_nk(r.path)])
            rows = _hit + _rest

        kw = self.search.text().strip().lower()
        self.grid.setUpdatesEnabled(False)
        self.grid.clear()
        for r in rows:
            if kw and kw not in r.disp.lower() and kw not in r.name.lower():
                continue
            item = QListWidgetItem(self._icon_for(r.path), r.disp)
            item.setData(Qt.UserRole, r.path)      # 真名 + 全路径永远在 UserRole
            item.setData(Qt.UserRole + 3, r.name)
            item.setData(Qt.UserRole + 4, r.group)
            item.setData(Qt.UserRole + 5, bool(r.sys))
            item.setToolTip(r.tip())
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.grid.addItem(item)
        self.grid.setUpdatesEnabled(True)
        self.empty.setGeometry(self.grid.viewport().rect())
        self.empty.setVisible(self.grid.count() == 0)
        self._update_stat(self.grid.count(), cat)

    def _icon_for(self, path):
        """取图标：图片走缩略图，其余走 Shell；任何失败都退回空图标，绝不让网格崩。"""
        try:
            path = os.path.normpath(str(path))   # 历史正斜杠路径也得能取到图
            if not _is_virtual(path) and icons.is_image(path):
                th = self._icons.thumb(path, self._icon_px * 2)   # 高分屏多给一倍
                if th is not None:
                    return th
            return self._icons.icon(path, self._icon_px)
        except Exception:              # noqa: BLE001
            from PySide6.QtGui import QIcon as _QI
            return _QI()

    def _update_stat(self, shown, cat):
        if cat == "全部":
            apps = sum(1 for r in self.desktop_rows if r.group == "应用")
            txt = "%d 个图标 · 应用 %d" % (shown, apps)
        else:
            txt = "%d 个图标 · %s" % (shown, cat)
        if self.desktop_hidden:
            txt += " · 桌面图标已隐藏"
        if GB is None:
            txt = "分组账本未就绪 · " + txt
        elif not self._ready:
            txt = "扫描降级 · " + txt
        self.stat.setText(txt)

    def _on_size(self, i):
        px = [40, 48, 64][i]
        if px == self._icon_px:
            return
        self._icon_px = px
        self.grid.setIconSize(QSize(px, px))
        self.grid.setGridSize(_cell_size(px))
        self._grid_dele.px = px
        for w in list(getattr(self, "_minis", {}).values()):
            try:
                w._apply_px(px)            # 小窗口跟着主面板换档，不必各自去点
            except Exception:              # noqa: BLE001
                pass
        self._filter(self.side.currentRow())        # 重新按新尺寸向 Shell 取图
        self._queue_save()

    def _on_opacity(self, v):
        """v 是滑块上的「透明度」（0 最实 / 100 最透），存盘仍用 density。"""
        self._density = 100 - int(v)
        self.update()
        for w in list(getattr(self, "_minis", {}).values()):
            w.update()                     # 小窗口的浓淡是画的时候读主面板的，得叫它重画
        self._queue_save()

    def _on_refresh(self):
        self._reload()
        self._sync_hide_btn()          # 用户可能在别处改过隐藏状态
        if self._watch is not None:
            try:
                self._watch.refresh()  # 顺手清一次监测缓存，免得旧快照立刻触发一次重扫
            except Exception:          # noqa: BLE001
                pass

    # ---------- 桌面目录实时监测 ----------
    def _start_watch(self):
        """建 DesktopWatcher 并接信号；模块缺失 / 构造失败都只是退化成手动刷新。"""
        if WATCH is None:
            self._watch = None
            return
        try:
            self._watch = WATCH.DesktopWatcher(self)
        except Exception:              # noqa: BLE001 —— 模块在但起不来
            self._watch = None
            return
        try:
            self._watch.changed.connect(self._on_desktop_changed)
            self._watch.start()
        except Exception:              # noqa: BLE001
            self._watch = None

    def _stop_watch(self):
        w = getattr(self, "_watch", None)
        if w is None:
            return
        try:
            w.stop()
        except Exception:              # noqa: BLE001
            pass
        self._watch = None

    def _on_desktop_changed(self, reason=""):
        """桌面目录变了：合并成一次重扫（监测自己也会抖动合并，这里再兜一层）。"""
        self._watch_timer.start()

    def _flush_watch(self):
        before = {r.path for r in self.desktop_rows}
        keep = [self._path_of(it) for it in self.grid.selectedItems()
                if self._path_of(it)]
        self._reload()
        self._restore_selection(keep)
        fresh = [r for r in self.desktop_rows if r.path not in before]
        if fresh:
            self._flash("新增 %d 项" % len(fresh), 2000)

    def _restore_selection(self, paths):
        """重扫后尽量把原来的选中项选回来（按全路径匹配）。"""
        want = {os.path.normcase(p) for p in paths if p}
        if not want:
            return
        first = None
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            p = self._path_of(it)
            if p and os.path.normcase(p) in want:
                it.setSelected(True)
                if first is None:
                    first = it
        if first is not None:
            self.grid.scrollToItem(first)

    # ---------- 隐藏 / 显示桌面图标 ----------
    def _hidden_now(self):
        """真实隐藏状态；模块缺失时当作「未隐藏」。"""
        if HD is None:
            return False
        try:
            return bool(HD.is_hidden())
        except Exception:              # noqa: BLE001
            return False

    def _sync_hide_btn(self):
        """按真实状态刷新 👁 按钮：隐藏时变暗 + 加斜线，文字不变。"""
        btn = getattr(self, "btn_hide", None)
        if btn is None:
            return
        self.desktop_hidden = self._hidden_now()
        btn.setText("👁\u0338" if self.desktop_hidden else "👁")
        btn.set_dim(bool(self.desktop_hidden))
        tip = ""
        if HD is not None:
            try:
                tip = HD.describe()
            except Exception:          # noqa: BLE001
                tip = ""
        btn.setToolTip(tip or ("桌面图标已隐藏，点一下显示" if self.desktop_hidden
                               else "隐藏桌面图标"))
        self._update_stat(self.grid.count(), self._cur_cat())

    def _hide_confirm_text(self):
        return ("隐藏后桌面上看不到任何图标，但文件一个都不会动 —— "
                "不移动、不复制、不删除、不改属性。\n\n"
                "恢复方式一：在桌面空白处右键 → 查看 → 显示桌面图标。\n"
                "恢复方式二：在本程序顶栏再点一次 👁。\n\n"
                "注意：如果 Tomato Desktop Box 被强制关闭（任务管理器结束进程 / 崩溃），"
                "桌面会保持隐藏状态，请用方式一恢复。")

    def _on_hide_toggle(self):
        """点击切换。首次隐藏前于本机弹一次说明，之后直接切。"""
        if HD is None:
            self._info("桌面图标", ["隐藏模块未就绪",
                                   "deskbox/hidedesk.py 尚未就位。"])
            return
        going_hide = not self._hidden_now()
        if going_hide and not self._hide_warned:
            if not self._confirm("隐藏桌面图标", self._hide_confirm_text(),
                                 "隐藏", cancel_text="取消"):
                return
            self._hide_warned = True
            self._save_state()
        try:
            ok, err, hidden = HD.toggle()
        except Exception as exc:       # noqa: BLE001
            self._info("桌面图标", ["切换失败", str(exc)])
            return
        if not ok:
            self._info("桌面图标", ["切换失败", err or "未知原因"])
            self._sync_hide_btn()      # 按钮态跟真实状态回滚
            return
        self._sync_hide_btn()
        if self._hidden_now():
            self._flash("桌面图标已隐藏")
        else:
            self._flash("桌面图标已显示")

    def _build_hide_start_dialog(self):
        """构造启动自检框（不 exec，截图自检也用它）。"""
        d = GlassDialog(self, "桌面图标处于隐藏状态", 370)
        d.add_line("检测到桌面图标处于隐藏状态，要显示回来吗？", None, 12)
        d.add_line("隐藏只影响桌面显示，文件一个都不会动。", None, 11)
        chk = QCheckBox("以后不再提示")
        cf = QFont(FAMILY)
        cf.setPixelSize(11)
        chk.setFont(cf)
        chk.setChecked(False)
        _theme(chk, lambda m: """
            QCheckBox{color:%s;spacing:7px;background:transparent;}
            QCheckBox::indicator{width:14px;height:14px;border-radius:4px;
                                 border:1px solid %s;background:%s;}
            QCheckBox::indicator:checked{background:%s;border:1px solid %s;}
        """ % (glass.inkcss(205, m), glass.inkcss(120, m), glass.panelcss(16, m),
               "#0A84FF", "#0A84FF"))
        d.body.addWidget(chk)
        d.add_button("保持隐藏", False, d.reject)
        d.add_button("显示", True, d.accept)
        return d, chk

    def _hide_start_dialog(self):
        """启动自检框：带「以后不再提示」勾选框。返回 (显示回来, 不再提示)。"""
        d, chk = self._build_hide_start_dialog()
        ok = bool(d.exec())
        return ok, bool(chk.isChecked())

    def _check_hidden_on_start(self):
        """上次可能是崩溃退出的：检测到隐藏态只询问，绝不自动改。"""
        if NO_HIDE_PROMPT or HD is None:
            return
        if not self._hidden_now():
            return
        if getattr(self, "_hide_quiet", False):
            return                     # 用户勾过「以后不再提示」
        show_it, dont_ask = self._hide_start_dialog()
        if dont_ask:
            self._hide_quiet = True
            self._queue_save()
        if show_it:
            try:
                ok, err = HD.show()
            except Exception as exc:   # noqa: BLE001
                ok, err = False, str(exc)
            if not ok:
                self._info("桌面图标", ["显示失败", err or "未知原因"])
        self._sync_hide_btn()

    # ---------- 网格：双击 / 右键 ----------
    def _on_double(self, item):
        self._open(self._path_of(item))
        self.grid.clearSelection()     # 打开完就别留着那块选中底色

    @staticmethod
    def _path_of(item):
        return item.data(Qt.UserRole) if item is not None else None

    def _open(self, path):
        if not path or STORE is None:
            return
        try:
            STORE.open_item(path)
        except Exception:              # noqa: BLE001
            pass

    def _dark_menu(self):
        m = QMenu(self)
        m.setWindowFlags(m.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        m.setAttribute(Qt.WA_TranslucentBackground, True)
        _theme(m, lambda md: """
            QMenu{background:%s;border:1px solid %s;
                  border-radius:9px;padding:5px;color:%s;
                  font-family:"Microsoft YaHei UI";font-size:11px;}
            QMenu::item{padding:6px 24px 6px 14px;border-radius:6px;}
            QMenu::item:selected{background:rgba(10,132,255,160);}
            QMenu::item:disabled{color:%s;}
            QMenu::separator{height:1px;background:%s;margin:4px 8px;}
        """ % (glass.menu_bg(md), glass.inkcss(70, md), glass.inkcss(232, md),
               glass.inkcss(80, md), glass.inkcss(26, md)))
        return m

    def _on_ctx_menu(self, pos):
        item = self.grid.itemAt(pos)
        gpos = self.grid.viewport().mapToGlobal(pos)
        if item is None:
            # 空白处右键：走桌面背景命名空间的原生菜单（右键桌面本体，新建落到用户桌面）
            self._shell_bg_menu(gpos)
            return
        if not item.isSelected():
            self.grid.clearSelection()
            item.setSelected(True)
        paths = [self._path_of(i) for i in self.grid.selectedItems()]
        paths = [p for p in paths if p]
        if not paths:
            return
        many = len(paths) > 1
        # 选中项里有系统虚拟项（::CLSID）时，文件类操作一律不可用
        virtual = bool(item.data(Qt.UserRole + 5)) or any(_is_virtual(p) for p in paths)
        menu = self._dark_menu()
        a_open = QAction("打开", menu)
        a_open.setEnabled(not many)
        a_open.triggered.connect(lambda: self._open(paths[0]))
        menu.addAction(a_open)
        a_rev = QAction("在资源管理器中显示", menu)
        a_rev.setEnabled(not many and not virtual)
        if virtual:
            a_rev.setToolTip("系统图标没有对应的文件位置")
        a_rev.triggered.connect(lambda: self._reveal(paths[0]))
        menu.addAction(a_rev)
        # ---- 重命名（Tomato Desktop Box 自己做；原生 shell 的 rename 动词在裸宿主里是 E_NOTIMPL）----
        can_ren = (not many and not virtual and STORE is not None
                   and callable(getattr(STORE, "rename", None)))
        a_ren = QAction("重命名" if not many else "重命名（只支持单个）", menu)
        a_ren.setEnabled(can_ren)
        if many:
            a_ren.setToolTip("一次只能重命名一个")
        elif virtual:
            a_ren.setToolTip("系统图标不能重命名")
        elif STORE is None or not callable(getattr(STORE, "rename", None)):
            a_ren.setToolTip("数据层未就绪")
        a_ren.triggered.connect(lambda: self._rename(paths[0]))
        menu.addAction(a_ren)
        menu.addSeparator()
        a_del = QAction("删除到回收站" if not many
                        else "删除 %d 项到回收站" % len(paths), menu)
        a_del.setEnabled(not virtual)
        if virtual:
            a_del.setToolTip("系统图标不是文件，不能删除")
        a_del.triggered.connect(lambda: self._recycle(paths))
        menu.addAction(a_del)
        menu.addSeparator()
        # ---- 更多操作 ▸ 原生右键菜单（复制 / 剪切 / 重命名 / 属性…） ----
        a_more = QAction("更多操作 ▸", menu)
        a_more.setEnabled(bool(self._sm_ok))
        a_more.triggered.connect(lambda: self._shell_menu(paths, gpos))
        menu.addAction(a_more)
        if not self._sm_ok:
            a_more.setToolTip(self._sm_why or "原生菜单不可用")
        # ---- 移到分类 ▸ 8 个分组（排除当前项所在的那个）；系统项不显示 ----
        groups = [n for n, _ in _groups()]
        cur_g = item.data(Qt.UserRole + 4) or ""
        can_set = GB is not None and not virtual
        sub = None
        if not virtual:
            mv_title = "移到分类" if can_set else "移到分类（分组账本未就绪）"
            sub = menu.addMenu(mv_title)
            sub.setStyleSheet(menu.styleSheet())
            sub.setEnabled(not many and can_set)
            for g in groups:
                if g == cur_g or g == _sys_cat():
                    continue
                act = QAction(g, sub)
                act.triggered.connect(
                    lambda _=False, gg=g: self._set_group(paths[0], gg))
                sub.addAction(act)

        a_cp = QAction("复制路径" if not many else "复制 %d 个路径" % len(paths), menu)
        a_cp.triggered.connect(lambda: self._copy_paths(paths))
        menu.addAction(a_cp)
        menu.addSeparator()
        # ---- 移除这个图标 / 放回面板：只动 ui.json 里的名单，文件一个都不碰 ----
        in_rm = bool(paths) and all(_nk(p) in self._removed for p in paths)
        a_rm = QAction("放回面板" if in_rm
                       else ("移除这个图标" if not many
                             else "移除 %d 个图标" % len(paths)), menu)
        a_rm.setEnabled(not virtual)
        if virtual:
            a_rm.setToolTip("系统图标不能移除")
        elif in_rm:
            a_rm.setToolTip("重新排回「%s」里" % UNSORTED)
        a_rm.triggered.connect(lambda: self._remove_icons(paths, back=in_rm))
        menu.addAction(a_rm)
        if in_rm:                        # 只在「已移除」档里给：彻底不显示（文件照旧）
            a_gone = QAction("彻底移除图标" if not many
                             else "彻底移除 %d 个图标" % len(paths), menu)
            a_gone.setToolTip("程序里不再显示它，文件还在原处")
            a_gone.triggered.connect(lambda: self._forget_icons(paths))
            menu.addAction(a_gone)
        if sub is not None and getattr(self, "_dbg_sub", False):
            # 仅截图自检：把「移到分类」子菜单展开
            QTimer.singleShot(260, lambda: sub.popup(
                gpos + QPoint(menu.width() - 6, 96)))
        menu.exec(gpos)

    # ---------- 改分类（只写账本，绝不搬文件） ----------
    def _set_group(self, path, group, quiet=False):
        """把一个条目的分组写进账本。不移动、不复制、不删除任何文件。"""
        if _is_virtual(path):
            if not quiet:
                self._flash("系统项不能改分类")
            return False
        if GB is None:
            if not quiet:
                self._info("移到分类", ["分组账本未就绪",
                                        "deskbox/groupbook.py 尚未就位。"])
            return False
        try:
            ok = bool(GB.set_group(path, group))
        except Exception as exc:       # noqa: BLE001
            if not quiet:
                self._info("移到分类", ["失败", str(exc)])
            return False
        if not ok:
            if not quiet:
                self._info("移到分类", ["没能记到「%s」" % group, "账本写入失败。"])
            return False
        if quiet:
            return True
        self._reload()
        self._flash("已移到「%s」" % group)
        return True

    def _forget_icons(self, paths):
        """从「已移除」里彻底拿掉：程序里不再显示，文件一个都不碰。"""
        paths = [p for p in paths if not _is_virtual(p)]
        if not paths:
            return
        if not self._confirm("彻底移除图标",
                             "这 %d 个图标以后不会再出现在面板里（文件还在原处）。\n"
                             "想找回来，把它从桌面再拖进来一次就行。\n确认彻底移除？"
                             % len(paths),
                             "彻底移除", cancel_text="取消"):
            return
        n = 0
        for p in paths:
            k = _nk(p)
            self._removed.discard(k)
            if k not in self._gone:
                self._gone.add(k)
                n += 1
        if not n:
            return
        self._queue_save()
        self._reload()
        self._flash("已彻底移除 %d 个图标（文件还在原处）" % n)

    def _remove_icons(self, paths, back=False):
        """把条目从面板里拿掉 / 放回去。只改 ui.json 的名单，文件原地不动。"""
        n = 0
        for p in paths:
            if _is_virtual(p):
                continue
            k = _nk(p)
            if back:
                if k in self._removed:
                    self._removed.discard(k)
                    n += 1
            elif k not in self._removed:
                self._removed.add(k)
                n += 1
        if not n:
            return
        self._queue_save()
        self._reload()
        self._flash("已放回 %d 个图标" % n if back
                    else "已移除 %d 个图标（文件还在原处）" % n)

    def _on_side_drop(self, cat, paths, internal=False):
        """侧栏落点。两种来源都只改账本分组，文件原地不动。

        internal=True ：网格项拖到侧栏 —— 改分类；拖到「全部」不行（要具体分类）。
        internal=False：从桌面/资源管理器拖进来 —— 收进视图，同样只登记。
        """
        target = None if cat in (None, "", "全部") else cat
        if internal and target is None:
            self._flash("拖到具体分类上才能改分类")
            return
        real = [p for p in (paths or []) if p and not _is_virtual(p)]
        if len(real) != len([p for p in (paths or []) if p]):
            self._flash("系统项不能改分类")     # 混选时只拦虚拟项，真实项照常
        if not real:
            return
        if target == _sys_cat():
            self._flash("系统应用分类不是收纳目标")
            return
        self._ingest(real, target)

    def _on_grid_reorder(self, paths, target):
        """主面板内部拖拽换位：写进 ui.json 的顺序表，账本一个字都不改。"""
        cat = self._cur_cat()
        key = _order_scope_main(cat)
        cur = [os.path.normpath(it.data(Qt.UserRole))
               for it in (self.grid.item(i) for i in range(self.grid.count()))
               if it is not None and it.data(Qt.UserRole)]
        new = _apply_reorder(cur, paths, target)
        if new == cur:
            return
        tbl = self._state.get("order")
        if not isinstance(tbl, dict):
            tbl = {}
        tbl[key] = new
        self._state["order"] = tbl
        self._save_state()
        self._filter(self.side.currentRow())

    def _on_grid_drop(self, paths):
        """从桌面/资源管理器拖进网格：停在具体分类就记该分类，否则按文件名自动归类。"""
        if not paths:
            return
        cat = self._cur_cat()
        if cat == _sys_cat():              # 「系统应用」不是收纳目标，按自动分类登记
            cat = None
        self._ingest(paths, None if cat in (None, "", "全部") else cat)

    def _cur_cat(self):
        it = self.side.item(self.side.currentRow())
        return it.data(Qt.UserRole) if it is not None else "全部"

    def _ingest(self, paths, group):
        """登记到账本。group=None 时按扩展名现算分类。

        这里以前调用数据层的真实搬移接口，新流程下已彻底切断：
        全程只有 groupbook 的 JSON 读写，被拖进来的文件留在原处。
        """
        if GB is None:
            self._info("拖入", ["分组账本未就绪",
                                "deskbox/groupbook.py 尚未就位。"])
            return 0
        plist = [p for p in paths if p and not _is_virtual(p)]   # 系统项不写账本
        if not plist:
            return 0
        n = 0
        try:
            if group:
                n = GB.set_groups({p: group for p in plist})
            else:
                n = GB.auto_group(plist, only_unset=False, classify=_classify_fn())
        except Exception as exc:       # noqa: BLE001
            self._info("拖入", ["登记失败", str(exc)])
            return 0
        back = 0                      # 重新拖进来 = 明确要它回来：把移除名单撤掉
        for p in plist:
            k = _nk(p)
            if k in self._gone or k in self._removed:
                self._removed.discard(k)
                self._gone.discard(k)
                back += 1
        if back:
            self._queue_save()
        self._reload()
        self._flash("已登记 %d 项（文件原地未动）" % n if n else "没有登记任何项")
        return n

    def _flash(self, text, ms=2600):
        """状态栏临时提示，到点自动恢复成真实状态。"""
        self.stat.setText(text)
        self._flash_timer.start(ms)

    def _unflash(self):
        self._update_stat(self.grid.count(), self._cur_cat())

    def _copy_paths(self, paths):
        """完整路径进剪贴板，多项换行分隔。"""
        try:
            QGuiApplication.clipboard().setText("\n".join(paths))
        except Exception:              # noqa: BLE001
            pass

    # ---------- 重命名（Tomato Desktop Box 自己做；原生 shell 的 rename verb 在裸宿主里执行不了） ----------
    def _rename(self, path):
        """就地改名。只改名字，不移动、不复制、不删除。

        界面隐藏的后缀（.lnk/.url/.exe/.appref-ms）不显示给用户，
        用户没自己敲扩展名时自动接回去 —— 改名不该把快捷方式弄废。
        """
        if not path or STORE is None:
            return
        if _is_virtual(path):
            self._flash("系统图标不能重命名")
            return
        fn = getattr(STORE, "rename", None)
        if not callable(fn):
            self._info("重命名", ["数据层未就绪", "store.rename 尚未提供。"])
            return
        name = os.path.basename(path)
        is_dir = os.path.isdir(path)
        stem, suffix = name, ""
        if not is_dir:
            low = name.lower()
            for e in HIDE_EXTS:
                if low.endswith(e) and len(name) > len(e):
                    stem, suffix = name[:-len(e)], name[-len(e):]
                    break
        d = NameDialog(self, "重命名", "文件名最多 120 个字", "重命名", "取消",
                       stem, 360, max_len=120)
        bad_chars = '\\/:*?"<>|'          # 空格是合法文件名字符，不在里面

        def _dry(t):
            t = (t or "").strip()
            if not t:
                return "名字不能为空"
            if set(t) & set(bad_chars):
                return "名字里不能出现这些字符：" + " ".join(bad_chars)
            return None

        d.validator = _dry
        if not d.exec():
            return
        txt = (d.value or "").strip()
        if not txt:
            return
        full = txt if ("." in txt or not suffix) else txt + suffix
        try:
            ok, err = fn(path, full)
        except Exception as exc:       # noqa: BLE001 —— 数据层异常一律转提示
            ok, err = False, str(exc)
        if not ok:
            self._info("重命名", ["没能改名", err or "未知原因"])
            return
        # 账本按路径归一化作 key，路径变了键就变了 —— 跟着搬过去
        if GB is not None:
            try:
                g = GB.get_group(path)
                if g:
                    GB.clear_group(path)
                    GB.set_group(os.path.join(os.path.dirname(path), full), g)
            except Exception:          # noqa: BLE001 —— 账本搬不动也不该拦住改名
                pass
        self._reload()
        self._flash("已重命名为 %s" % full)

    # ---------- 原生右键菜单（系统自己的 verb） ----------
    def _blank_paths(self):
        """网格空白处右键：把桌面目录本身交给原生菜单（新建 / 粘贴 / 查看…）。"""
        ds = [d for d in self._desk_dirs() if os.path.isdir(d)]
        return ds[:1]

    def _shell_menu(self, paths, gpos):
        """弹原生 Shell 菜单。阻塞调用，返回被选中的菜单文本（取消为 None）。

        这里不做任何文件操作 —— 真正的复制 / 剪切 / 重命名 / 删除 / 属性
        全部由系统 verb 完成。返回后 300ms 重扫一次，因为第三方 verb
        可能改了名字或删掉了文件。
        """
        if SM is None:
            self._info("原生菜单", ["原生菜单模块未就绪",
                                   "deskbox/shellmenu.py 尚未就位。"])
            return None
        plist = [p for p in (paths or []) if p]
        if not plist:
            return None
        if not self._sm_ok:
            self._info("原生菜单", ["原生菜单不可用", self._sm_why or "未知原因"])
            return None
        try:
            if self._sm is None:
                self._sm = SM.ShellMenu()
            text = self._sm.popup(plist, gpos, int(self.winId()))
        except Exception as exc:       # noqa: BLE001 —— COM 失败 / 模块内部异常
            self._info("原生菜单", ["弹出失败", str(exc)])
            return None
        # 原生菜单里的「重命名(M)」在裸 IContextMenu 宿主里只把文本返回，不执行任何
        # shell 命令 —— 这里接管，走我们自己的改名实现。
        if text and text.replace("&", "").startswith("重命名"):
            self._rename(plist[0])
            return text
        if text:
            self._flash("已执行：%s" % text, 3000)
            QTimer.singleShot(300, self._reload)   # verb 可能改了文件名或删了项
        return text

    def _shell_bg_menu(self, gpos, folder=None):
        """桌面背景命名空间的原生菜单（等同右键桌面空白处：查看 / 排序 / 新建 / 粘贴 / 显示设置）。

        folder=None 交给 shellmenu.popup_background，由它落到桌面根命名空间，
        「新建」出来的文件直接出现在用户桌面。模块还是旧版（没有这个 API）时
        自动回退成原来的「把桌面目录本身交给菜单」，功能少一点但不会炸。
        """
        fn = getattr(SM, "popup_background", None) if SM is not None else None
        if not callable(fn):
            return self._shell_menu(self._blank_paths(), gpos)
        try:
            text = fn(folder=folder, global_pos=gpos, hwnd=int(self.winId()))
        except Exception as exc:       # noqa: BLE001 —— COM 失败 / 模块内部异常
            self._info("桌面菜单", ["弹出失败", str(exc)])
            return None
        if text:
            self._flash("已执行：%s" % text, 3000)
            QTimer.singleShot(300, self._reload)   # 新建 / 粘贴会多出文件，立刻重扫
        return text

    def _reveal(self, path):
        if _is_virtual(path):              # ::CLSID 没有文件位置
            return
        if STORE is None:
            return
        try:
            STORE.reveal(path)
        except Exception:              # noqa: BLE001
            pass

    def _recycle(self, paths):
        paths = [p for p in (paths or []) if p and not _is_virtual(p)]
        if not paths:                      # 只选了系统项：不是文件，直接不动
            self._flash("系统项不能删除")
            return
        if STORE is None:
            self._info("送回收站", ["数据层未就绪", "deskbox/store.py 尚未就位。"])
            return
        if len(paths) == 1:
            ask = "将「%s」送入回收站？\n可在回收站里还原。" % os.path.basename(paths[0])
        else:
            ask = "将选中的 %d 项送入回收站？\n可在回收站里还原。" % len(paths)
        if not self._confirm("送回收站", ask, "送回收站"):
            return
        try:
            ok, failed = STORE.delete_to_recycle(paths)
        except Exception as exc:       # noqa: BLE001
            self._info("送回收站", ["操作失败", str(exc)])
            return
        lines = ["成功 %d 项" % int(ok or 0)]
        if failed:
            lines.append("失败 %d 项" % len(failed))
            for f in list(failed)[:4]:
                lines.append("· %s" % f)
        self._info("送回收站", lines)
        self._reload()

    # ---------- 一键导入桌面（纯虚拟分组，不动任何文件） ----------
    def _on_tidy(self):
        """一键导入桌面 = 把桌面上的图标都收进面板，顺带给没分组的归类。

        先拿「桌面实扫全量」跟「面板现在显示的」比对：
          · 没有缺项 -> 提示「桌面文件已经全部在了」
          · 有缺项（被移除 / 彻底移除的）-> 列出是哪些，确认后才导进来
        全程只写分组记录和移除名单，不移动、不复制、不删除，桌面图标一个不动。
        """
        if GB is None:
            self._info("一键导入桌面", ["分组模块未就绪",
                                   "deskbox/groupbook.py 尚未就位，等它落地后再试。"])
            return
        rows = list(self.desktop_rows or [])
        allr = list(getattr(self, "all_rows", None) or rows)
        if not allr:
            self._info("一键导入桌面", ["桌面上没有图标", "先刷新一下看看。"])
            return
        have = {_nk(r.path) for r in rows}
        miss = [r for r in allr if _nk(r.path) not in have]
        if miss:
            names = [r.disp or r.name or os.path.basename(r.path) for r in miss]
            body = "\n".join("· " + x for x in names[:8])
            if len(names) > 8:
                body += "\n· …还有 %d 个" % (len(names) - 8)
            if not self._confirm("一键导入桌面",
                                 "桌面有 %d 个文件不在收纳盒里：\n%s\n\n"
                                 "导入后它们会重新显示（文件原地不动）。"
                                 % (len(miss), body),
                                 "导入", cancel_text="取消"):
                return
            for r in miss:                 # 明确要它回来：移除 / 彻底移除名单一并撤掉
                k = _nk(r.path)
                self._removed.discard(k)
                self._gone.discard(k)
            self._queue_save()
            self._reload()
            rows = list(self.desktop_rows or [])
            self._info("一键导入桌面",
                       ["导入成功：%d 个文件已经回到面板上了" % len(miss),
                        "它们重新显示出来了；文件原地没动过。"])
        else:
            self._info("一键导入桌面", ["桌面文件已经全部在了",
                                   "收纳盒里没有缺项。"])
        try:
            n = GB.auto_group([r.path for r in rows], only_unset=True,
                              classify=_classify_fn())
        except Exception as exc:       # noqa: BLE001
            self._info("一键导入桌面", ["整理失败", str(exc)])
            return
        self._reload()
        if n:
            self._flash("顺手归好了 %d 项（文件原地未动）" % n)

    # ---------- 重置分组（只清账本，不动文件） ----------
    def _on_reset(self):
        if GB is None:
            self._info("重置分组", ["分组账本未就绪",
                                    "deskbox/groupbook.py 尚未就位。"])
            return
        total = 0
        try:
            total = int(GB.stats().get("total") or 0)
        except Exception:              # noqa: BLE001
            total = 0
        if total == 0:
            self._info("重置分组", ["还没有分组记录", "先点「一键导入桌面」或手动拖一下。"])
            return
        if not self._confirm("重置分组",
                             "将清空全部 %d 条分组记录。\n只清分组，不动文件 —— "
                             "桌面图标原地不动。" % total,
                             "重置", cancel_text="取消"):
            return
        try:
            GB.save({})
        except Exception as exc:       # noqa: BLE001
            self._info("重置分组", ["清空失败", str(exc)])
            return
        self._reload()
        self._flash("分组已重置，桌面图标未受任何影响")

    # ---------- 设置 / 关于 ----------
    def _on_settings(self):
        d = GlassDialog(self, "Tomato Desktop Box 设置", 400)
        d.add_line("版本 %s" % VERSION, None, 11)

        # 背景：玻璃 / 黑玻璃（这两档都采样壁纸，只差白纱与深纱）/ 深色 / 浅色。
        bgbox, bgh = d.add_box(23, 23, 18, 18, 6, bg_alpha=32)
        lbb = QLabel("背景")
        fb = QFont(FAMILY)
        fb.setPixelSize(11)
        lbb.setFont(fb)
        # 这两个字后面那块底色以前是白捡的：label 自己没有任何选择器的 QSS，
        # 直接继承了 box 的 background，尺寸刚好卡着两个字，用户连着两次说太小。
        # 现在五个 chip 统一用模块级 _CHIP_W / _CHIP_H，自己明确画底色，行高不变。
        lbb.setFixedSize(_CHIP_W, _CHIP_H)
        lbb.setAlignment(Qt.AlignCenter)
        _theme(lbb, lambda m: "color:%s;background:%s;border-radius:9px;"
               % (glass.inkcss(150, m), glass.panelcss(32, m)))
        # 五段的段宽必须 >= 最长标签「黑玻璃」的 33px + 8px 内边距 = 41，
        # 否则「黑玻璃」贴边；同时 5*seg_w+4 得让整行留在内容区里：
        # 89 + 8 + 30 + 8 + (5*41+4=209) = 344 <= 364，右端 380 < 内容右边界 382。
        # 41 是同时满足这两条的最小值（seg_w=40 时每段 40 会挤字，44 时右端 395 越界 13px）。
        seg_bg = Segmented(["玻璃", "黑玻璃", "深色", "浅色", "纯色"], seg_w=41)
        _modes = list(BG_MODES)
        try:
            seg_bg.index = _modes.index(bg_mode())
        except ValueError:
            seg_bg.index = 0

        # 纯色档的色块：只有纯色档能点，点开是系统取色器
        chip = QPushButton()
        chip.setFixedSize(30, 30)
        chip.setCursor(Qt.PointingHandCursor)
        chip.setToolTip("纯色档的底色")
        chip.setEnabled(bg_mode() == "solid")
        _theme(chip, lambda m: "QPushButton{background:%s;border:1px solid %s;"
               "border-radius:9px;}" % (glass.solid_color().name(),
                                        glass.panelcss(70, m)))

        def _pick_solid():
            try:
                c = QColorDialog.getColor(glass.solid_color(), d, "选择纯色底色")
            except Exception:              # noqa: BLE001
                return
            if not c.isValid():
                return
            glass.set_solid(c)
            _apply_theme_all()             # 色块自己跟着变色
            self._repaint_all()
            self._queue_save()

        chip.clicked.connect(_pick_solid)

        def _on_bg(i):
            if 0 <= i < len(_modes):
                set_bg_mode(_modes[i])
            chip.setEnabled(bg_mode() == "solid")
            _apply_theme_all()         # 已建好的控件立刻换肤
            self._repaint_all()        # 自绘玻璃重画
            self._queue_save()

        seg_bg._cb = _on_bg
        brow = QHBoxLayout()
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(8)
        brow.addWidget(lbb)
        brow.addStretch(1)
        brow.addWidget(chip)
        brow.addWidget(seg_bg)
        bgh.addLayout(brow)

        # 开机自启动：开关状态**现读** HKCU 的 Run 键（AUTO.is_enabled 自己拿实际
        # 命令比），本地不留标志位；切换后**再读一次**，以注册表现状回显，
        # 写失败绝不假装成功。
        lba = QLabel("开机自启动")
        lba.setFont(fb)
        lba.setFixedSize(_CHIP_W, _CHIP_H)
        lba.setAlignment(Qt.AlignCenter)
        _theme(lba, lambda m: "color:%s;background:%s;border-radius:9px;"
               % (glass.inkcss(150, m), glass.panelcss(32, m)))
        arow = QHBoxLayout()
        arow.setContentsMargins(0, 0, 0, 0)
        arow.setSpacing(8)
        arow.addWidget(lba)
        arow.addStretch(1)
        if AUTO is None:
            why = "autostart 模块不可用：%s" % (_AUTO_ERR or "导入失败")
            lba.setToolTip(why)
            _unavail_note(arow, why)
        else:
            seg_auto = Segmented(["开", "关"], seg_w=44)
            seg_auto.index = 0 if _auto_enabled() else 1

            def _on_auto(i):
                want = (i == 0)
                try:
                    AUTO.enable() if want else AUTO.disable()
                except Exception as exc:   # noqa: BLE001 —— 模块本不该抛，双保险
                    self._flash("写入失败：%s" % exc)
                now = _auto_enabled()      # 写完再读：以注册表实际值为准
                if now == want:
                    self._flash("开机自启动已%s" % ("开启" if now else "关闭"))
                else:
                    self._flash("写入失败，可能被安全软件拦住，现在是「%s」"
                                % ("开" if now else "关"))
                seg_auto.index = 0 if now else 1
                seg_auto.update()

            seg_auto._cb = _on_auto
            arow.addWidget(seg_auto)
        bgh.addLayout(arow)

        # 最小化到托盘：开着时点黄点收进托盘、不占任务栏（默认开）
        lbt = QLabel("最小化到托盘")
        lbt.setFont(fb)
        lbt.setFixedSize(_CHIP_W, _CHIP_H)
        lbt.setAlignment(Qt.AlignCenter)
        _theme(lbt, lambda m: "color:%s;background:%s;border-radius:9px;"
               % (glass.inkcss(150, m), glass.panelcss(32, m)))
        trow = QHBoxLayout()
        trow.setContentsMargins(0, 0, 0, 0)
        trow.setSpacing(8)
        trow.addWidget(lbt)
        trow.addStretch(1)
        if TRAY is None:
            why = "tray 模块不可用：%s" % (_TRAY_ERR or "导入失败")
            lbt.setToolTip(why)
            _unavail_note(trow, why)
        else:
            seg_tray = Segmented(["开", "关"], seg_w=44)
            seg_tray.index = 0 if self._tray_enabled() else 1
            _live = getattr(self, "_tray", None)
            if _live is not None and not _tray_ok(_live):
                seg_tray.setToolTip("当前通知区不可用，开着也不会收进托盘")
            else:
                seg_tray.setToolTip("开着时点黄点收进托盘，不占任务栏")

            def _on_tray(i):
                self._tray_on = (i == 0)
                self._queue_save()

            seg_tray._cb = _on_tray
            trow.addWidget(seg_tray)
        bgh.addLayout(trow)

        # 点关闭：收进托盘（默认）/ 退出。托盘不可用时禁用这一行 —— 那种情况下
        # 点关闭一律真退出，不能让用户以为已经收进托盘了。
        lbc = QLabel("点关闭")
        lbc.setFont(fb)
        lbc.setFixedSize(_CHIP_W, _CHIP_H)
        lbc.setAlignment(Qt.AlignCenter)
        _theme(lbc, lambda m: "color:%s;background:%s;border-radius:9px;"
               % (glass.inkcss(150, m), glass.panelcss(32, m)))
        crow = QHBoxLayout()
        crow.setContentsMargins(0, 0, 0, 0)
        crow.setSpacing(8)
        crow.addWidget(lbc)
        crow.addStretch(1)
        _clive = getattr(self, "_tray", None)
        if TRAY is None or (_clive is not None and not _tray_ok(_clive)):
            why = ("tray 模块不可用：%s" % (_TRAY_ERR or "导入失败")
                   if TRAY is None else "当前通知区不可用，点关闭一律真退出")
            lbc.setToolTip(why)
            _unavail_note(crow, why)
        else:
            seg_close = Segmented(["收进托盘", "退出"], seg_w=58)
            seg_close.index = 0 if self._close_tray_enabled() else 1
            seg_close.setToolTip("收进托盘：关闭后程序还在通知区，点一下就能回来；"
                                 "通知区不可用时会直接退出")

            def _on_close(i):
                self._close_tray = (i == 0)
                self._queue_save()

            seg_close._cb = _on_close
            crow.addWidget(seg_close)
        bgh.addLayout(crow)

        # 小窗口自动粘连：关掉之后拖动 / 拉尺寸都不再吸附（默认开）
        lbo = QLabel("小窗口自动粘连")
        lbo.setFont(fb)
        lbo.setFixedSize(_CHIP_W, _CHIP_H)
        lbo.setAlignment(Qt.AlignCenter)
        _theme(lbo, lambda m: "color:%s;background:%s;border-radius:9px;"
               % (glass.inkcss(150, m), glass.panelcss(32, m)))
        seg_sn = Segmented(["开", "关"], seg_w=44)
        seg_sn.index = 0 if bool(getattr(self, "_snap_on", True)) else 1

        def _on_snap(i):
            self._snap_on = (i == 0)
            self._queue_save()

        seg_sn._cb = _on_snap
        srow = QHBoxLayout()
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(8)
        srow.addWidget(lbo)
        srow.addStretch(1)
        srow.addWidget(seg_sn)
        bgh.addLayout(srow)

        # 任务栏透明：磨砂 / 透明 / 关（默认关，用户自己开）。
        # 这一行是**唯一**会改系统任务栏外观的入口之一（另一个是托盘子菜单），
        # 两处都走 self._apply_tb，所以档位真相只有 self._tb_mode 一份。
        lbtb = QLabel("任务栏透明")
        lbtb.setFont(fb)
        lbtb.setFixedSize(_CHIP_W, _CHIP_H)
        lbtb.setAlignment(Qt.AlignCenter)
        _theme(lbtb, lambda m: "color:%s;background:%s;border-radius:9px;"
               % (glass.inkcss(150, m), glass.panelcss(32, m)))
        tbrow = QHBoxLayout()
        tbrow.setContentsMargins(0, 0, 0, 0)
        tbrow.setSpacing(8)
        tbrow.addWidget(lbtb)
        tbrow.addStretch(1)
        if not self._tb_supported():
            why = ("taskbarfx 模块不可用：%s" % (_TBFX_ERR or "导入失败")
                   if TBFX is None else "当前系统不支持改任务栏外观（需要 Windows 10 经典任务栏）")
            lbtb.setToolTip(why)
            _unavail_note(tbrow, why)
        else:
            seg_tb = Segmented(list(TB_LABELS), seg_w=44)   # 磨砂 / 透明 / 关
            _cur = getattr(self, "_tb_mode", TB_DEFAULT)
            seg_tb.index = TB_MODES.index(_cur) if _cur in TB_MODES else len(TB_MODES) - 1
            seg_tb.setToolTip("磨砂：模糊壁纸 + 深色纱；透明：壁纸直接透出；"
                              "关：恢复系统默认。只改外观，不动任何文件")

            def _on_tb(i):
                want = TB_MODES[i] if 0 <= i < len(TB_MODES) else TB_DEFAULT
                if self._apply_tb(want):
                    self._sync_tray_tb()          # 托盘子菜单的勾跟着走
                    seg_tb.index = TB_MODES.index(
                        getattr(self, "_tb_mode", TB_DEFAULT))
                else:
                    # 没生效：把格子拨回**实际**档位，别让界面说谎
                    seg_tb.index = TB_MODES.index(
                        getattr(self, "_tb_mode", TB_DEFAULT))
                seg_tb.update()

            seg_tb._cb = _on_tb
            tbrow.addWidget(seg_tb)
        bgh.addLayout(tbrow)

        box, vb = d.add_box(13, 13, 18, 18, 6)
        lb = QLabel("分组存放位置")
        f = QFont(FAMILY)
        f.setPixelSize(11)
        lb.setFont(f)
        lb.setMinimumHeight(22)
        _theme(lb, lambda m: "color:%s;" % glass.inkcss(140, m))
        vb.addWidget(lb)
        gp = None
        if GB is not None:
            try:
                gp = GB.groups_path()
            except Exception:          # noqa: BLE001
                gp = None
        pv = QLabel(gp or "（分组账本未就绪）")
        f2 = QFont(FAMILY)
        f2.setPixelSize(11)
        pv.setFont(f2)
        pv.setWordWrap(True)
        _theme(pv, lambda m: "color:%s;" % glass.inkcss(225, m))
        pv.setTextInteractionFlags(Qt.TextSelectableByMouse)
        vb.addWidget(pv)

        open_btn = QPushButton("打开目录")
        of = QFont(FAMILY)
        of.setPixelSize(11)
        open_btn.setFont(of)
        open_btn.setFixedHeight(26)
        open_btn.setCursor(Qt.PointingHandCursor)
        _theme(open_btn, lambda m: """
            QPushButton{background:%s;border:0;border-radius:7px;
                        padding:0 12px;color:%s;font-size:11px;}
            QPushButton:hover{background:%s;}
        """ % (glass.panelcss(20, m), glass.inkcss(205, m), glass.panelcss(40, m)))
        open_btn.setEnabled(bool(gp and os.path.isfile(gp)))
        open_btn.clicked.connect(lambda: self._open_dir(os.path.dirname(gp or "")))
        d.foot.addWidget(open_btn)
        d.add_button("好", True, d.accept)
        d.exec()

    def _open_dir(self, path):
        if not path:
            return
        try:
            os.startfile(path)
        except Exception:              # noqa: BLE001
            pass

    # ---------- 通用小对话框 ----------
    def _confirm(self, title, text, ok_text, cancel_text="取消"):
        d = GlassDialog(self, title, 350)
        d.add_line(text, None, 12)
        d.add_button(cancel_text, False, d.reject)
        d.add_button(ok_text, True, d.accept)
        return bool(d.exec())

    def _info(self, title, lines):
        d = GlassDialog(self, title, 350)
        for i, ln in enumerate(lines):
            d.add_line(ln, "rgba(255,255,255,232)" if i == 0 else "rgba(255,255,255,150)",
                       12 if i == 0 else 11)  # 交给 _inkcss_of 按当前风格转换
        d.add_button("好", True, d.accept)
        return d.exec()

    # ---------- 截图自检钩子（仅供 --shot --dialog 使用） ----------
    def _debug_dialog(self, name):
        if name == "confirm":
            d = GlassDialog(self, "重置分组", 350)
            d.add_line("将清空全部 73 条分组记录。\n只清分组，不动文件 —— "
                       "桌面图标原地不动。", "rgba(255,255,255,205)", 12)
            d.add_button("取消", False, d.reject)
            d.add_button("重置", True, d.accept)
        elif name in ("hidewarn", "hidetip"):
            d = GlassDialog(self, "隐藏桌面图标", 400)
            d.add_line(self._hide_confirm_text(), None, 12)
            d.add_button("取消", False, d.reject)
            d.add_button("隐藏", True, d.accept)
        elif name in ("hidestart", "hideprempt"):
            d, _chk = self._build_hide_start_dialog()
            d.setModal(False)
            d.show()
            self._dbg = d
            return
        elif name in ("rename", "renamebad"):
            # 自检：重命名对话框（初值去掉隐藏后缀，max_len=120）
            d = NameDialog(self, "重命名", "文件名最多 120 个字", "重命名", "取消",
                           "Steam 快捷方式", 360, max_len=120)
            if name == "renamebad":
                d.edit.setText("a/b")
                d._set_err("名字里不能出现这些字符：\\ / : * ? " + chr(34) + " < > |")
            d.setModal(False)
            d.show()
            self._dbg = d
            return
        elif name in ("newcat", "newcatbad"):
            d = NameDialog(self, "新建分类",
                           "名字最多 12 个字，可用中文、字母、数字。", text="截图")
            if name == "newcatbad":
                d.edit.setText("a/b")
                d._set_err("分类名不能包含 \\ / : * ? \" < > |")
            d.setModal(False)
            d.show()
            self._dbg = d
            return
        elif name == "delcat":
            d = GlassDialog(self, "删除分类", 350)
            d.add_line("删除分类「图片」？\n该分类下的 3 项将移到「未分类」，"
                       "文件不会被移动或删除。", "rgba(255,255,255,205)", 12)
            d.add_button("取消", False, d.reject)
            d.add_button("删除", True, d.accept)
        elif name == "sidemenu":
            m = self._dark_menu()
            a1 = QAction("新建分类…", m)
            a2 = QAction("删除分类…", m)
            m.addAction(a1)
            m.addAction(a2)
            m.popup(self.side.mapToGlobal(self.side.rect().center()))
            self._dbg_menu = m
            return
        elif name in ("sysview", "sysmenu"):
            # 自检：选中「系统应用」分类；sysmenu 再对第一个虚拟项弹右键菜单
            sc = _sys_cat()
            for i in range(self.side.count()):
                if self.side.item(i).data(Qt.UserRole) == sc:
                    self.side.setCurrentRow(i)
                    break
            if name == "sysmenu":
                if self.grid.count():
                    it = self.grid.item(0)
                    self.grid.clearSelection()
                    it.setSelected(True)
                    self._on_ctx_menu(self.grid.visualItemRect(it).center())
            return
        elif name == "mini":
            # 自检：开一个小窗口（minibox 缺失时走提示分支不崩）
            self._new_mini()
            return
        elif name == "maxed":
            self._toggle_max()
            return
        elif name == "settings":
            self._on_settings()
            return
        elif name == "submenu":
            self._dbg_sub = True
            name = "menu"
            self._debug_dialog(name)
            return
        elif name == "menu":
            it = self.grid.itemAt(QPoint(40, 40))
            if it is None and self.grid.count():
                it = self.grid.item(0)
            if it is not None:
                self.grid.clearSelection()
                it.setSelected(True)
                self._on_ctx_menu(self.grid.visualItemRect(it).center())
            return
        elif name == "drop":
            self.grid.hot = True
            self.grid.viewport().update()
            self.side.itemDelegate().drag_row = 3
            self.side.viewport().update()
            return
        else:
            return
        if d is None:
            return
        d.setModal(False)
        d.show()
        self._dbg = d

    # ---------- 窗口拖动 ----------
    def mousePressEvent(self, e):
        if e.position().y() < 44:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None


def _force_hidden_state(w):
    """自检用：把界面切到「已隐藏」外观，不碰 hidedesk 的真实实现。"""
    w.desktop_hidden = True
    w.btn_hide.setText("👁\u0338")
    w.btn_hide.set_dim(True)
    w.btn_hide.setToolTip("桌面图标已隐藏，点一下显示")
    w._update_stat(w.grid.count(), w._cur_cat())


def main():
    global NO_HIDE_PROMPT
    from PySide6.QtWidgets import QApplication
    if "--shot" in sys.argv or "--hidestate" in sys.argv:
        NO_HIDE_PROMPT = True          # 自动化出图不弹启动自检
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # 关主面板不等于退出程序（还有小窗口）
    # 退出时把任务栏还原成系统默认。挂在 aboutToQuit 上而不是 closeEvent：
    # 托盘退出 / 关主面板 / app.quit() 三条路都会走到这里，只要漏一处就会
    # 在用户桌面上留一个没人负责的透明任务栏。
    app.aboutToQuit.connect(_release_tb)
    app.setFont(QFont(FAMILY, 9))
    if APPICON is not None:
        try:
            app.setWindowIcon(APPICON.qicon())   # 缺失 / 损坏时 appicon 自带降级画法
        except Exception:              # noqa: BLE001
            pass
    w = MainWindow()
    if "--bg" in sys.argv:                 # 自检：强制风格（frost|light）
        set_bg_mode(sys.argv[sys.argv.index("--bg") + 1])
        _apply_theme_all()
        w.update()
    if "--hidestate" in sys.argv:          # 自检：强制「已隐藏」外观
        _force_hidden_state(w)
    if "--size" in sys.argv:               # 自检：指定窗口尺寸出图
        i = sys.argv.index("--size")
        w.resize(int(sys.argv[i + 1]), int(sys.argv[i + 2]))
    if "--density" in sys.argv:
        w._density = int(sys.argv[sys.argv.index("--density") + 1])
        w.sld.blockSignals(True)
        w.sld.setValue(100 - w._density)     # 滑块是透明度，反向取值
        w.sld.blockSignals(False)
    w._setup_tray()                        # 系统托盘；模块缺失 / 无通知区安静跳过
    w._restore_tb()                        # 把任务栏对齐到上次的档位（顺带抹掉强杀残留）
    w.show()
    if "--shot" in sys.argv:
        out = sys.argv[sys.argv.index("--shot") + 1]
        if "--dialog" in sys.argv:
            name = sys.argv[sys.argv.index("--dialog") + 1]
            QTimer.singleShot(900, lambda: w._debug_dialog(name))

        def grab():
            geo = w.frameGeometry()
            d = getattr(w, "_dbg", None)
            if d is not None and d.isVisible():
                geo = geo.united(d.frameGeometry())
            geo = geo.adjusted(-10, -10, 10, 10)
            img = app.primaryScreen().grabWindow(
                0, geo.x(), geo.y(), geo.width(), geo.height()).toImage()
            img.save(out)
            app.quit()

        QTimer.singleShot(2200, grab)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
