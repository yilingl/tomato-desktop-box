# -*- coding: utf-8 -*-
"""deskbox/appicon.py — 程序图标（assets/tomato.ico）的唯一取用入口。

    path()  -> .ico 的绝对路径（由本文件 __file__ 往上推，不依赖 cwd）
    qicon() -> 缓存的 QIcon（从 .ico 载入，含 16/24/32/48/64/128/256 七档）

缓存按 (文件大小, mtime_ns) 记账：图标被换掉 / 被删掉后下次调用自动重载，
不需要谁记得手动清缓存（也正因如此，临时改名 .ico 会真的走降级画法）。

降级：.ico 缺失或损坏时，用 QPainter 现画一颗简化番茄（32 / 64 两档）返回 ——
番茄更大更圆、3 瓣叶、粗茎，32px 下也认得出。本模块绝不抛异常；唯一会返回空 QIcon
的情形是连 QPixmap 都建不出来（例如进程里根本没有 QGuiApplication），
那时调用方 setIcon 也照样不会炸。

本模块不 import 任何 win32 API，不读注册表，不弹窗口；
和其它用 QPixmap 的模块一样，画图前需要已经存在 QGuiApplication（测试里用离屏的）。
"""
import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPixmap, QRadialGradient)

ICON_NAME = "tomato.ico"
FALLBACK_SIZES = (32, 64)

_CACHE = ()                 # (key, QIcon)，key 见 _key()


def path():
    """assets/tomato.ico 的绝对路径。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, os.pardir, "assets", ICON_NAME))


def qicon():
    """缓存的 QIcon；文件缺失/损坏时返回现画的降级图标。本函数不抛异常。"""
    global _CACHE
    key = _key()
    if _CACHE and _CACHE[0] == key:
        return _CACHE[1]
    _CACHE = (key, _load())
    return _CACHE[1]


def _key():
    try:
        st = os.stat(path())
    except OSError:
        return None                      # 文件不在 -> 记账为「缺失态」
    return (st.st_size, st.st_mtime_ns)


def _load():
    try:
        ic = QIcon(path())
        if not ic.isNull():
            return ic
    except Exception:                       # noqa: BLE001 —— 坏文件/无 QGuiApplication 都退到画法
        pass
    return _fallback_icon()


def _fallback_icon():
    ic = QIcon()
    for s in FALLBACK_SIZES:
        try:
            ic.addPixmap(QPixmap.fromImage(_fallback_image(s)))
        except Exception:                   # noqa: BLE001
            pass
    return ic


def _fallback_image(size):
    """就地画一颗简化番茄（和 assets/make_icon.py 的 <=32px 画法同款）。"""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    try:
        s = float(size)
        pad = s * 0.04
        rad = s * 0.21
        g = QLinearGradient(0.0, pad, 0.0, s - pad)
        g.setColorAt(0.0, QColor("#39435a"))
        g.setColorAt(1.0, QColor("#191e2b"))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(pad, pad, s - 2 * pad, s - 2 * pad), rad, rad)

        r = s * 0.345
        tg = QRadialGradient(QPointF(s * 0.40, s * 0.50), r * 1.6)
        tg.setColorAt(0.0, QColor("#ff8a76"))
        tg.setColorAt(0.55, QColor("#ef4436"))
        tg.setColorAt(1.0, QColor("#c9241a"))
        p.setBrush(tg)
        p.drawEllipse(QPointF(s * 0.5, s * 0.615), r, r)

        lr = s * 0.28
        leaf = QPainterPath()
        for i in range(3):
            a = math.radians(-90.0 + i * 120.0)
            leaf.addEllipse(QPointF(s * 0.5 + lr * 0.55 * math.cos(a),
                                    s * 0.30 + lr * 0.55 * math.sin(a)),
                            lr * 0.46, lr * 0.30)
        lg = QRadialGradient(QPointF(s * 0.5, s * 0.30 - lr / 12.0), lr)
        lg.setColorAt(0.0, QColor("#5ecb4d"))
        lg.setColorAt(1.0, QColor("#2f7a26"))
        p.setBrush(lg)
        p.drawPath(leaf)

        sw = max(1.0, s * 0.13)
        p.setBrush(QColor("#3f7d2c"))
        p.drawRoundedRect(QRectF(s * 0.5 - sw / 2, s * 0.08, sw, s * 0.26), sw / 2, sw / 2)
    finally:
        p.end()
    return img
