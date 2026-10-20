# -*- coding: utf-8 -*-
"""Tomato Desktop Box 玻璃底：自绘磨砂玻璃（玻璃 / 黑玻璃两档采样壁纸，深色 / 浅色两档纯自绘）。

为什么不采样壁纸 / 不用系统亚克力：
    Win10 19044 上 ACCENT_ENABLE_ACRYLICBLURBEHIND 会把模糊层铺满整个窗口矩形、
    绕过 SetWindowRgn，实测 7 种 region/accent 顺序组合全部是方角；
    DwmSetWindowAttribute(38) 的 Mica 在此版本 API 级不存在。
    用户也明确要求"玻璃质感必须归自己画"，别让系统那层模糊糊住整个窗口矩形。
所以背景 100% 自绘：底色 + 厚度 + 柔光 + 反光 + 颗粒，圆角由 QPainterPath 裁剪，
精度完全归 Qt，跟壁纸没有一点关系。

四种风格：
    "clear"  玻璃  —— 采样壁纸 + 白纱，能看见桌面的亮玻璃（默认）
    "night"  黑玻璃 —— 采样壁纸 + 深纱，壁纸只剩一层压暗的色相，冷调白字
    "frost"  深色  —— 深石墨磨砂，冷调，白字
    "light"  浅色  —— 珍珠白磨砂，暖调，深字

"clear" 与 "night" 用真半透明：壁纸整屏模糊结果模块级缓存一次，paint 时按窗口在
屏幕上的位置裁一块贴上去，所以拖动窗口时背景钉在屏幕上、零重算；两档只差蒙层颜色
（白纱 / 深纱）。采样失败时 clear 退化成 frost、night 退化成纯色黑玻璃。
"""
import os
import random

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPixmap, QRadialGradient)

MODES = ("clear", "night", "frost", "light", "solid")

# 每档风格的底色、光效与"墨水"（文字 / 面板）颜色
STYLES = {
    "clear": {
        "top":   (255, 255, 255),
        "mid":   (250, 251, 253),
        "bot":   (238, 241, 246),
        "cool":  (170, 200, 255),
        "edge":  (255, 255, 255),
        "edge_a": 80,
        "shade": 44,
        "grain": (0, 0, 0),
        "grain_a": 9,
        "glow0": 34,          # 左上柔光（壁纸要透出来，光别太猛）
        "glow1": 12,
        "sheen": 20,          # 斜向反光
        "cool_a": 26,
        "ink":   (24, 27, 34),
        "panel": (0, 0, 0),
        "menu":  (247, 248, 251),
        "depth": 0.0,
        "border": (0, 0, 0),
    },
    "night": {
        "top":   (46, 46, 52),
        "mid":   (28, 28, 33),
        "bot":   (15, 15, 19),
        "cool":  (120, 145, 210),
        "edge":  (255, 255, 255),
        "edge_a": 38,
        "shade": 62,
        "grain": (255, 255, 255),
        "grain_a": 10,
        "glow0": 30,          # 左上柔光压得比 frost 更含蓄
        "glow1": 12,
        "sheen": 14,          # 斜向反光
        "cool_a": 34,
        "ink":   (255, 255, 255),
        "panel": (255, 255, 255),
        "menu":  (26, 26, 31),
        "depth": 0.18,
        "border": (255, 255, 255),
    },
    "frost": {
        "top":   (78, 84, 96),
        "mid":   (52, 57, 67),
        "bot":   (34, 38, 46),
        "cool":  (110, 165, 255),
        "edge":  (255, 255, 255),
        "edge_a": 46,
        "shade": 54,
        "grain": (255, 255, 255),
        "grain_a": 13,
        "ink":   (255, 255, 255),
        "panel": (255, 255, 255),
        "menu":  (34, 34, 40),
        "depth": -0.34,
        "border": (255, 255, 255),
    },
    "light": {
        "top":   (252, 253, 255),
        "mid":   (233, 236, 243),
        "bot":   (204, 209, 220),
        "cool":  (150, 180, 255),
        "edge":  (255, 255, 255),
        "edge_a": 170,
        "shade": 26,
        "grain": (0, 0, 0),
        "grain_a": 11,
        "ink":   (26, 29, 36),
        "panel": (0, 0, 0),
        "menu":  (247, 248, 251),
        "depth": -0.17,
        "border": (0, 0, 0),
    },
}

_STYLE = "clear"


def set_style(name):
    """设置当前风格；非法值忽略。"""
    global _STYLE
    if name in MODES:
        _STYLE = name


def style_name():
    return _STYLE


# ── 纯色档（"solid"）：底色由用户挑，透明度直接换算成 alpha ──
_SOLID = {"rgb": (32, 34, 40), "st": None}


def solid_color():
    """纯色档当前底色（QColor）。"""
    r, g, b = _SOLID["rgb"]
    return QColor(r, g, b)


def set_solid(c):
    """设置纯色档底色；文字 / 面板配色按亮度自动切白字或黑字。"""
    try:
        col = QColor(c)
    except Exception:              # noqa: BLE001
        return
    if not col.isValid():
        return
    rgb = (col.red(), col.green(), col.blue())
    if rgb != _SOLID["rgb"]:
        _SOLID["rgb"] = rgb
        _SOLID["st"] = None


def solid_alpha(density=58):
    """纯色底的 alpha：浓度 0（透明度拉满）→ 0，窗口真透光；100 → 实心。"""
    d = max(0.0, min(1.0, float(density) / 100.0))
    return int(round(255.0 * d))


def _solid_style():
    """纯色档借深色 / 浅色档的墨水配色，底色换成用户挑的那个色。"""
    st = _SOLID.get("st")
    if st is None:
        r, g, b = _SOLID["rgb"]
        dark = (0.299 * r + 0.587 * g + 0.114 * b) < 138
        st = dict(STYLES["frost"] if dark else STYLES["light"])
        st["border"] = (255, 255, 255) if dark else (0, 0, 0)
        st["menu"] = (34, 34, 40) if dark else (247, 248, 251)
        st["_dark"] = dark
        _SOLID["st"] = st
    return st


def _st(mode=None):
    m = mode or _STYLE
    if m == "solid":
        return _solid_style()
    return STYLES.get(m, STYLES["frost"])


# ─────────────── UI 配色（随风格走：深色档白字，浅色档黑字） ───────────────
def ink(alpha=235, mode=None):
    """文字色。风格 1 是白色，风格 2 是近黑，alpha 沿用调用方原来的值。"""
    r, g, b = _st(mode)["ink"]
    return QColor(r, g, b, max(0, min(255, int(alpha))))


def inkcss(alpha=235, mode=None):
    r, g, b = _st(mode)["ink"]
    return "rgba(%d,%d,%d,%d)" % (r, g, b, max(0, min(255, int(alpha))))


def panel(alpha=14, mode=None):
    """面板/按钮/输入框底色。深色档是白色叠一层，浅色档是黑色叠一层。"""
    r, g, b = _st(mode)["panel"]
    return QColor(r, g, b, max(0, min(255, int(alpha))))


def panelcss(alpha=14, mode=None):
    r, g, b = _st(mode)["panel"]
    return "rgba(%d,%d,%d,%d)" % (r, g, b, max(0, min(255, int(alpha))))


def menu_bg(mode=None):
    r, g, b = _st(mode)["menu"]
    return "rgba(%d,%d,%d,242)" % (r, g, b)


# ───────────────────────────── 基础绘制 ─────────────────────────────
def rounded_path(w, h, radius, inset=0.0):
    path = QPainterPath()
    r = QRectF(inset, inset, w - inset * 2, h - inset * 2)
    path.addRoundedRect(r, radius, radius)
    return path


def _scaled(c, k):
    return (max(0, min(255, int(c[0] * k))),
            max(0, min(255, int(c[1] * k))),
            max(0, min(255, int(c[2] * k))))


_TILES = {}


def grain_tile(mode=None):
    """磨砂颗粒。每档风格只生成一次，振幅很低：远看是质感，近看才见颗粒。"""
    st = _st(mode)
    key = tuple(st["grain"])
    if key in _TILES:
        return _TILES[key]
    size = 128
    rnd = random.Random(20241019)          # 固定种子，每次启动纹理一致
    r, g, b = st["grain"]
    amp = st["grain_a"]
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.transparent)
    for y in range(size):
        for x in range(size):
            v = rnd.randint(0, 255)
            if v < 168:                    # 七成像素干净，避免整片发灰
                continue
            a = (v - 168) * amp // 87
            img.setPixelColor(x, y, QColor(r, g, b, a))
    pm = QPixmap.fromImage(img)
    _TILES[key] = pm
    return pm


# ──────────────── "clear" 档：壁纸采样 + 整屏模糊缓存 ────────────────
_WALL = {"origin": None, "img": None, "tried": False}


def _reg_value(key, default=None):
    """读 HKCU\\Control Panel\\Desktop 的字符串值；失败返回 default。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Control Panel\Desktop") as k:
            return winreg.QueryValueEx(k, key)[0]
    except Exception:
        return default


def _wallpaper_path():
    """SPI_GETDESKWALLPAPER 拿当前壁纸文件路径；没有则 None。"""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(4096)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x0073, 4096, buf, 0)
        p = (buf.value or "").strip()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    return None


def _desktop_color():
    v = _reg_value("Background", "")
    parts = [x.strip() for x in str(v).split()] if v else []
    if len(parts) == 3:
        try:
            return QColor(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            pass
    return QColor(28, 30, 36)


def _screen_geometry():
    try:
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            g = scr.virtualGeometry()
            if g.isValid() and g.width() > 0 and g.height() > 0:
                return g
    except Exception:
        pass
    return QRect(0, 0, 1920, 1080)


def _blur_down_up(img, rounds=3):
    """连续降采样再升采样，近似高斯模糊。绝不用 QGraphicsBlurEffect
    （PySide6 下会 RuntimeError: libshiboken: Internal C++ object already deleted）。"""
    out = img
    w, h = out.width(), out.height()
    if w < 4 or h < 4:
        return out
    fmt = QImage.Format.Format_ARGB32_Premultiplied
    for _ in range(max(1, rounds)):
        small = out.scaled(max(1, w // 12), max(1, h // 12),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        out = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return out.convertToFormat(fmt)


def _paint_wallpaper(canvas, src):
    """按注册表 WallpaperStyle 把壁纸铺到 canvas 上。"""
    style = str(_reg_value("WallpaperStyle", "10") or "10").strip()
    p = QPainter(canvas)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    cw, ch = canvas.width(), canvas.height()
    iw, ih = src.width(), src.height()
    try:
        if style == "2":                                   # 拉伸
            p.drawImage(QRect(0, 0, cw, ch), src)
        elif style == "10":                                # 填充
            s = src.scaled(cw, ch, Qt.KeepAspectRatioByExpanding,
                           Qt.SmoothTransformation)
            p.drawImage(QPoint((cw - s.width()) // 2, (ch - s.height()) // 2), s)
        elif style == "6":                                 # 适应
            s = src.scaled(cw, ch, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawImage(QPoint((cw - s.width()) // 2, (ch - s.height()) // 2), s)
        elif style == "22":                                # 平铺
            for y in range(0, ch, max(1, ih)):
                for x in range(0, cw, max(1, iw)):
                    p.drawImage(QPoint(x, y), src)
        else:                                              # 居中
            p.drawImage(QPoint((cw - iw) // 2, (ch - ih) // 2), src)
    finally:
        p.end()


def blurred_wallpaper():
    """整屏模糊壁纸（模块级单例，只算一次）。返回 (QImage, 左上角 QPoint) 或 None。"""
    if _WALL["tried"]:
        return (_WALL["img"], _WALL["origin"]) if _WALL["img"] is not None else None
    _WALL["tried"] = True
    try:
        geo = _screen_geometry()
        canvas = QImage(max(1, geo.width()), max(1, geo.height()),
                        QImage.Format.Format_ARGB32_Premultiplied)
        path = _wallpaper_path()
        src = QImage(path) if path else QImage()
        if src.isNull():
            canvas.fill(_desktop_color())
        else:
            canvas.fill(_desktop_color())
            _paint_wallpaper(canvas, src)
        _WALL["img"] = _blur_down_up(canvas, 3)
        _WALL["origin"] = QPoint(geo.x(), geo.y())
        return (_WALL["img"], _WALL["origin"])
    except Exception:
        _WALL["img"] = None
        return None


def paint_clear(painter, r, global_rect, mode="clear", density=58):
    """第 1 层换成「模糊壁纸 + 蒙层」，第 2-7 层照旧。

    clear 蒙白纱（越浓越白，壁纸始终透得出来），night 蒙深纱（越浓越暗，
    壁纸被压成一层色相）—— 这就是黑玻璃与深色档的区别：深色档根本不采样壁纸。
    """
    got = blurred_wallpaper()
    if got is None:                                        # 采样失败：退成纯色玻璃
        paint_glass(painter, r, "frost" if mode == "clear" else mode, density)
        return
    img, origin = got
    d = max(0.0, min(1.0, float(density) / 100.0))
    gx = int(global_rect.x() - origin.x())
    gy = int(global_rect.y() - origin.y())
    painter.save()
    src = QRect(max(0, gx), max(0, gy), max(1, r.width()), max(1, r.height()))
    src = src.intersected(QRect(0, 0, img.width(), img.height()))
    if not src.isEmpty():
        painter.drawImage(r, img, src)
    else:
        painter.fillRect(r, QColor(28, 30, 36))
    painter.restore()

    # 蒙层：浓度滑块只调纱的厚薄。白纱 6 → 168（越浓越白），
    # 深纱 88 → 218（越浓越暗，壁纸只留下色相，对齐参考图里约 0.56 的通透比）。
    if mode == "night":
        alpha = int(88 + (d ** 1.8) * 130)
        painter.fillRect(r, QColor(10, 11, 15, max(0, min(255, alpha))))
    else:
        alpha = int(6 + (d ** 2.2) * 162)
        painter.fillRect(r, QColor(255, 255, 255, max(0, min(255, alpha))))

    _overlays(painter, r, mode)


def paint_glass(painter, r, mode=None, density=58):
    """画一块不透壁纸的磨砂玻璃。r 为局部矩形（QRect 或 QRectF）。"""
    st = _st(mode)
    d = max(0.0, min(1.0, float(density) / 100.0))
    k = 1.0 + st["depth"] * (d - 0.5)      # 浓度越高底越沉，越低越透气

    # 1) 底色：不透明的上亮下暗，保证一丝壁纸都透不进来
    gr = QLinearGradient(0, r.top(), 0, r.bottom())
    for pos, col in ((0.0, st["top"]), (0.55, st["mid"]), (1.0, st["bot"])):
        c = _scaled(col, k)
        gr.setColorAt(pos, QColor(c[0], c[1], c[2]))
    painter.fillRect(r, gr)

    _overlays(painter, r, mode)


def paint_solid(painter, r, density=58):
    """纯色底：就一层平涂，没有颗粒 / 反光 / 压暗，alpha 全交给透明度。

    拉满透明度时 alpha 到 0，窗口只剩图标浮在桌面上 —— 这是用户点名要的效果。
    """
    c = solid_color()
    painter.fillRect(r, QColor(c.red(), c.green(), c.blue(),
                               solid_alpha(density)))


def _overlays(painter, r, mode=None):
    """paint_glass 的第 2-7 层：柔光 / 冷反射 / 反光 / 亮线 / 压暗 / 颗粒。"""
    st = _st(mode)
    w, h = r.width(), r.height()

    # 2) 主光源：左上角一大团柔光，撑出玻璃的"面"
    rad = QRadialGradient(QPointF(r.left() + w * 0.18, r.top() + h * 0.02),
                          max(w, h) * 0.85)
    rad.setColorAt(0.0, QColor(255, 255, 255, st.get("glow0", 62 if _dark(mode) else 96)))
    rad.setColorAt(0.42, QColor(255, 255, 255, st.get("glow1", 20 if _dark(mode) else 34)))
    rad.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(r, rad)

    # 3) 冷色反射：右下一点蓝，让玻璃"有环境"
    cr, cg, cb = st["cool"]
    rad2 = QRadialGradient(QPointF(r.left() + w * 1.02, r.bottom() + h * 0.16),
                           max(w, h) * 0.82)
    rad2.setColorAt(0.0, QColor(cr, cg, cb, st.get("cool_a", 42 if _dark(mode) else 30)))
    rad2.setColorAt(1.0, QColor(cr, cg, cb, 0))
    painter.fillRect(r, rad2)

    # 4) 斜向一线反光：玻璃表面倾斜的那道亮痕
    sx, sy = r.left() - w * 0.1, r.top() + h * 0.30
    sheen = QLinearGradient(sx, sy, sx + w * 0.72, sy + h * 0.44)
    a_mid = st.get("sheen", 17 if _dark(mode) else 90)
    sheen.setColorAt(0.0, QColor(255, 255, 255, 0))
    sheen.setColorAt(0.46, QColor(255, 255, 255, a_mid))
    sheen.setColorAt(0.58, QColor(255, 255, 255, 5))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(r, sheen)

    # 5) 顶部边缘一道亮线：玻璃切口的反光
    eh = min(56, int(h * 0.2))
    er, eg, eb = st["edge"]
    edge = QLinearGradient(0, r.top(), 0, r.top() + eh)
    edge.setColorAt(0.0, QColor(er, eg, eb, st["edge_a"]))
    edge.setColorAt(1.0, QColor(er, eg, eb, 0))
    painter.fillRect(QRect(r.left(), r.top(), w, eh), edge)

    # 6) 底部压暗，把面板"坐"下去
    bh = min(120, int(h * 0.35))
    bot = QLinearGradient(0, r.bottom() - bh, 0, r.bottom())
    bot.setColorAt(0.0, QColor(0, 0, 0, 0))
    bot.setColorAt(1.0, QColor(0, 0, 0, st["shade"]))
    painter.fillRect(QRect(r.left(), r.bottom() - bh, w, bh), bot)

    # 7) 颗粒压在最上面
    tile = grain_tile(mode)
    if tile is not None:
        painter.drawTiledPixmap(r, tile)


def _dark(mode=None):
    """底色暗、需要白字的档位：黑玻璃与深色档；玻璃档与浅色档是亮底深字。"""
    m = mode or _STYLE
    if m == "solid":
        return bool(_solid_style().get("_dark", True))
    return m in ("night", "frost")


class GlassPainter:
    """在窗口的局部坐标里画一块圆角玻璃；全局坐标只用于兼容旧调用。"""

    def paint(self, painter, local_rect, global_rect, radius, density=58,
              tint=None, border=True, strength=None, mode="frost"):
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        path = rounded_path(local_rect.width(), local_rect.height(), radius)
        path.translate(local_rect.x(), local_rect.y())

        painter.save()
        painter.setClipPath(path, Qt.IntersectClip)
        if (mode or _STYLE) == "solid":
            paint_solid(painter, local_rect, density)
        elif (mode or _STYLE) in ("clear", "night"):
            paint_clear(painter, local_rect, global_rect, mode, density)
        else:
            paint_glass(painter, local_rect, mode, density)
        painter.restore()

        if border:
            st = _st(mode)
            d = max(0.0, min(1.0, float(density) / 100.0))
            br, bg, bb = st["border"]
            painter.save()
            painter.setBrush(Qt.NoBrush)
            if (mode or _STYLE) == "solid":
                # 纯色档边框跟底色一起淡：浓度 0 时整窗一干二净
                ba = int((72 if _dark(mode) else 40) * d)
            else:
                ba = int((78 if _dark(mode) else 34) * (1.0 - d * 0.45))
            painter.setPen(QColor(br, bg, bb, ba))
            inner = QRectF(local_rect.adjusted(0.5, 0.5, -0.5, -0.5))
            p2 = QPainterPath()
            p2.addRoundedRect(inner, radius - 0.5, radius - 0.5)
            painter.drawPath(p2)
            painter.restore()


GLASS = GlassPainter()
