# -*- coding: utf-8 -*-
r"""assets/make_icon.py — 生成程序图标 assets/tomato.ico（候选 A「玻璃拟物」）。

设计稿基准 512x512，每个尺寸都按目标像素**矢量重绘**，
没有任何一步是把 512 位图缩下来（不出现 QPixmap.scaled / QImage.scaled）：

    底：圆角 112/512，线性渐变 #39435A -> #191E2B，内描边 3px rgba(255,255,255,38)
    番茄：径向渐变 中心(214,236) r=210：#FF8A76 -> #EF4436(0.45) -> #A8160C
          椭圆 中心(256,292) rx=132 ry=122
    高光：椭圆 中心(206,246) 44x30，白 alpha 92
    叶：6 瓣，中心(256,168) r=96，#5ECB4D -> #2F7A26
    茎：圆角矩形 (248,116) 16x48，#3F7D2C
    投影：椭圆 (256,402) 132x34，黑 alpha 70

    <=32px 改用简化画法（_draw_simple）：番茄占更多画面、去掉高光与投影、
    茎叶加粗、不画 3px 细边框 —— 保证 16px 下还看得出「一颗红番茄 + 一片绿叶」。

ICO 用标准库 struct 拼 PNG 帧（Vista+ 支持 PNG 帧），不引第三方依赖，不用 Pillow。

跑法：
    & "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe" -u assets/make_icon.py
"""
import math
import os
import struct

os.environ["QT_QPA_PLATFORM"] = "offscreen"       # 必须在 import PySide6 之前

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QRadialGradient)
from PySide6.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
ICO_PATH = os.path.join(HERE, "tomato.ico")

SIZES = (16, 24, 32, 48, 64, 128, 256)
MASTER = 512.0            # 设计稿基准边长
SIMPLE_MAX = 32           # <= 此值走简化画法
BG_TOP, BG_BOT = "#39435a", "#191e2b"


# ---------------------------------------------------------------- 画法

def _leaf(p, cx, cy, r, petals=6):
    """叶：petals 个椭圆花瓣绕 (cx,cy)，径向渐变 #5ecb4d -> #2f7a26。"""
    path = QPainterPath()
    for i in range(petals):
        a = math.radians(-90.0 + i * 360.0 / petals)
        path.addEllipse(QPointF(cx + r * 0.55 * math.cos(a), cy + r * 0.55 * math.sin(a)),
                        r * 0.46, r * 0.30)
    g = QRadialGradient(QPointF(cx, cy - r / 12.0), r)
    g.setColorAt(0.0, QColor("#5ecb4d"))
    g.setColorAt(1.0, QColor("#2f7a26"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawPath(path)


def _bg(p, s):
    """底板：圆角 + 竖向渐变 + 内描边。s = 目标边长（设备像素）。"""
    pad = s * (8.0 / MASTER)
    rad = s * (112.0 / MASTER)
    g = QLinearGradient(0.0, pad, 0.0, s - pad)
    g.setColorAt(0.0, QColor(BG_TOP))
    g.setColorAt(1.0, QColor(BG_BOT))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(pad, pad, s - 2 * pad, s - 2 * pad), rad, rad)
    # 内描边：设计稿 3px，按比例缩；再小也保底 1px，否则等于没画
    pen = QPen(QColor(255, 255, 255, 38))
    pen.setWidthF(max(1.0, s * (3.0 / MASTER)))
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    inset = pad + s * (1.5 / MASTER)
    r2 = rad * (110.0 / 112.0)
    p.drawRoundedRect(QRectF(inset, inset, s - 2 * inset, s - 2 * inset), r2, r2)
    p.setPen(Qt.NoPen)


def _draw_full(p, s):
    """48px 以上：按 512 设计稿等比铺到 s×s（矢量重绘，像素在目标尺寸一次成型）。"""
    p.save()
    p.scale(s / MASTER, s / MASTER)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 70))                      # 投影
    p.drawEllipse(QPointF(256, 402), 132, 34)
    tg = QRadialGradient(QPointF(214, 236), 210)         # 番茄
    tg.setColorAt(0.0, QColor("#ff8a76"))
    tg.setColorAt(0.45, QColor("#ef4436"))
    tg.setColorAt(1.0, QColor("#a8160c"))
    p.setBrush(tg)
    p.drawEllipse(QPointF(256, 292), 132, 122)
    p.setBrush(QColor(255, 255, 255, 92))                # 高光
    p.drawEllipse(QPointF(206, 246), 44, 30)
    _leaf(p, 256.0, 168.0, 96.0)                         # 叶
    p.setBrush(QColor("#3f7d2c"))                        # 茎
    p.drawRoundedRect(QRectF(248, 116, 16, 48), 8, 8)
    p.restore()


def _draw_simple(p, s):
    """<=32px：番茄更大更圆、无高光无投影、3 瓣叶 + 粗茎、不画细边框。"""
    pad = s * 0.04
    rad = s * 0.21
    g = QLinearGradient(0.0, pad, 0.0, s - pad)
    g.setColorAt(0.0, QColor(BG_TOP))
    g.setColorAt(1.0, QColor(BG_BOT))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(pad, pad, s - 2 * pad, s - 2 * pad), rad, rad)

    r = s * 0.345                                        # 番茄：半径占比比设计稿大
    tg = QRadialGradient(QPointF(s * 0.40, s * 0.50), r * 1.6)
    tg.setColorAt(0.0, QColor("#ff8a76"))
    tg.setColorAt(0.55, QColor("#ef4436"))
    tg.setColorAt(1.0, QColor("#c9241a"))
    p.setBrush(tg)
    p.drawEllipse(QPointF(s * 0.5, s * 0.615), r, r)

    _leaf(p, s * 0.5, s * 0.30, s * 0.28, petals=3)      # 叶：3 瓣，够粗才看得出

    sw = max(1.0, s * 0.13)                              # 茎：加粗
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#3f7d2c"))
    p.drawRoundedRect(QRectF(s * 0.5 - sw / 2, s * 0.08, sw, s * 0.26), sw / 2, sw / 2)


def draw_icon(size):
    """按目标像素画一颗番茄图标，返回带 alpha 的 QImage。"""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    try:
        s = float(size)
        if size <= SIMPLE_MAX:
            _draw_simple(p, s)
        else:
            _bg(p, s)
            _draw_full(p, s)
    finally:
        p.end()
    return img


def png_bytes(img):
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()
    return data


# ---------------------------------------------------------------- ICO 封装

def pack_ico(frames):
    """frames = [(size, png_bytes), ...] -> 完整 .ico 字节（PNG 帧，Vista+）。"""
    n = len(frames)
    head = struct.pack("<HHH", 0, 1, n)                  # reserved / type=icon / count
    off = 6 + 16 * n
    entries, blobs = b"", b""
    for size, data in frames:
        b = 0 if size >= 256 else size                   # 256 在目录里记 0
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), off)
        off += len(data)
        blobs += data
    return head + entries + blobs


def build(sizes=SIZES):
    return pack_ico([(s, png_bytes(draw_icon(s))) for s in sizes])


def main():
    if QApplication.instance() is None:
        QApplication([])
    data = build()
    with open(ICO_PATH, "wb") as f:
        f.write(data)
    print("wrote %s (%d bytes), sizes=%s" % (ICO_PATH, len(data), list(SIZES)), flush=True)


if __name__ == "__main__":
    main()
