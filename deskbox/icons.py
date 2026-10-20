# -*- coding: utf-8 -*-
"""Tomato Desktop Box 高清图标：IShellItemImageFactory -> 256px 真图标"""
import ctypes
from ctypes import wintypes, POINTER, byref, c_void_p, c_int, c_long, Structure
from PySide6 import QtGui
from PySide6.QtCore import Qt, QRect, QRectF, QSize, QPointF

_ole32 = ctypes.WinDLL("ole32")
_shell32 = ctypes.WinDLL("shell32")
_gdi32 = ctypes.WinDLL("gdi32")
_user32 = ctypes.WinDLL("user32")


class _GUID(Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


_IID_ISHELLITEMIMAGEFACTORY = _GUID(
    0xbcc18b79, 0xba16, 0x442f,
    (ctypes.c_ubyte * 8)(0x80, 0xc4, 0x8a, 0x59, 0xc3, 0x0c, 0x46, 0x3b))


class _SIZE(Structure):
    _fields_ = [("cx", c_long), ("cy", c_long)]


class _BITMAP(Structure):
    _fields_ = [("bmType", c_long), ("bmWidth", c_long), ("bmHeight", c_long),
                ("bmWidthBytes", c_long), ("bmPlanes", wintypes.WORD),
                ("bmBitsPixel", wintypes.WORD), ("bmBits", c_void_p)]


class _BMIH(Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", c_long), ("biHeight", c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", c_long), ("biYPelsPerMeter", c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class _BMI(Structure):
    _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wintypes.DWORD * 3)]


_SIIGBF_ICONONLY = 0x04
_initialized = False


def _ensure_com():
    """初始化 COM 并在成功时记标志，故意不与 CoUninitialize 配对。

    hr==S_OK(0)：这次由我们初始化，按契约本应配对 CoUninitialize；但本模块是
    进程级常驻的图标加载器，取图调用散落在各处，没有单一时机能成对释放，
    所以故意不配对——泄漏一个 STA 引用计数，进程退出由系统回收。
    hr==S_FALSE(1)：Qt 主线程已是 STA，本次没加计数，也不需要任何配对。
    其余 hr 记失败，下次再试。hr 先归一化到 32 位，避免有符号 c_int 的负数比较陷阱。
    """
    global _initialized
    if not _initialized:
        hr = int(_ole32.CoInitializeEx(None, 0x2)) & 0xFFFFFFFF  # APARTMENTTHREADED
        _initialized = hr in (0, 1)


def _hbitmap_to_qimage(hbm):
    bm = _BITMAP()
    _gdi32.GetObjectW(c_void_p(hbm), ctypes.sizeof(bm), byref(bm))
    w, h = bm.bmWidth, bm.bmHeight
    if w <= 0 or h <= 0:
        return None
    bi = _BMI()
    bi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = (ctypes.c_ubyte * (w * h * 4))()
    hdc = _user32.GetDC(None)
    _gdi32.GetDIBits(hdc, c_void_p(hbm), 0, h, buf, byref(bi), 0)
    _user32.ReleaseDC(None, hdc)
    # Shell 给的 DIB 是【非预乘】alpha：边缘半透明像素 RGB 保持原色、
    # 只有 alpha 渐变（实测 KOOK.lnk alpha 37..227 而 RGB 恒 135,235,0）。
    # 若按 Premultiplied 解析，Qt 会再乘一次 alpha，边缘就多一圈亮边（毛边）。
    img = QtGui.QImage(bytes(buf), w, h, w * 4,
                       QtGui.QImage.Format.Format_ARGB32)
    return img.copy()


def shell_icon(path, px=256):
    """返回 QImage 或 None。走 Shell 的 IShellItemImageFactory，能拿到 jumbo 级图标。"""
    _ensure_com()
    try:
        pbi = c_void_p()
        hr = _shell32.SHCreateItemFromParsingName(
            ctypes.c_wchar_p(str(path)), None,
            byref(_IID_ISHELLITEMIMAGEFACTORY), byref(pbi))
        if hr != 0 or not pbi:
            return None
        vtbl = ctypes.cast(pbi, POINTER(POINTER(c_void_p))).contents
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtbl[2])
        get_image = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, _SIZE, c_int,
                                       POINTER(c_void_p))(vtbl[3])
        hbm = c_void_p()
        hr = get_image(pbi, _SIZE(px, px), _SIIGBF_ICONONLY, byref(hbm))
        if hr != 0 or not hbm:
            release(pbi)
            return None
        img = _hbitmap_to_qimage(hbm.value)
        _gdi32.DeleteObject(c_void_p(hbm.value))
        release(pbi)
        return img
    except Exception:
        return None


def _content_bbox(img, threshold=8):
    """图标内容（alpha>threshold）的包围盒。返回 (x, y, w, h) 或 None。"""
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    ptr = img.constBits()
    data = bytes(ptr)
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        row = y * bpl
        for x in range(w):
            if data[row + x * 4 + 3] > threshold:      # ARGB32: byte3 = alpha
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    if maxx < 0:
        return None
    return minx, miny, maxx - minx + 1, maxy - miny + 1


def normalize(img, fill=0.98):
    """把图标内容等比撑到画布的 fill，四周居中。

    为什么需要：真实图标的留白各不相同，实测包围盒比例分三档
    0.81(7 个) / 0.90(22 个) / 1.00(44 个)。不归一化就会有的显大有的显小。
    """
    if img is None or img.isNull():
        return img
    bb = _content_bbox(img)
    if bb is None:
        return img
    x, y, w, h = bb
    side = max(w, h)
    if side <= 0:
        return img
    canvas_side = img.width()
    target = canvas_side * fill          # 内容要撑到的边长
    if target <= 0:
        return img
    k = target / float(side)             # 等比缩放系数
    tw, th = max(1, int(round(w * k))), max(1, int(round(h * k)))
    canvas = QtGui.QImage(canvas_side, canvas_side,
                          QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    sub = img.copy(x, y, w, h)
    p = QtGui.QPainter(canvas)
    p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.drawImage(QRect((canvas_side - tw) // 2, (canvas_side - th) // 2, tw, th), sub)
    p.end()
    return canvas


# ---------------------------------------------------------------- 图片缩略图
IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico")


def is_image(path):
    """扩展名是否属于可直接出缩略图的图片类型。"""
    return str(path).lower().endswith(IMG_EXTS)


def _thumb_image(path, px):
    """读图并居中补成 px×px 正方形，圆角 6。读不了返回 None。

    用 QImageReader + setScaledSize 让解码器直接按目标尺寸出图：
    40MP 的手机照片若先整张解出来再缩，峰值内存能到几百 MB。
    """
    try:
        rd = QtGui.QImageReader(str(path))
        rd.setAutoTransform(True)            # 尊重 EXIF 方向
        size = rd.size()
        w, h = size.width(), size.height()
        if w <= 0 or h <= 0:
            return None
        k = float(px) / float(max(w, h))
        tw = max(1, int(round(w * k)))
        th = max(1, int(round(h * k)))
        rd.setScaledSize(QSize(tw, th))
        img = rd.read()
    except Exception:                        # noqa: BLE001 —— 损坏 / 权限 / 非图片
        return None
    if img is None or img.isNull():
        return None
    if img.format() != QtGui.QImage.Format.Format_ARGB32_Premultiplied:
        img = img.convertToFormat(QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    canvas = QtGui.QImage(px, px, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    x = (px - tw) / 2.0
    y = (px - th) / 2.0
    rad = max(3.0, 6.0 * px / 120.0)
    path_ = QtGui.QPainterPath()
    path_.addRoundedRect(QRectF(x, y, tw, th), rad, rad)
    p = QtGui.QPainter(canvas)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setClipPath(path_)
    p.drawImage(QRectF(x, y, tw, th), img)
    p.end()
    return canvas


class IconCache:
    """path -> QIcon，带 (path, px) 缓存。

    关键：向 Shell 直接请求【最终显示尺寸】的那张图，不做缩放。
    Windows 自己按目标尺寸渲染图标，跟桌面上的观感逐像素一致；
    若先取 256 再缩小，边缘会和桌面不同（更糊），也会让大小比例失真。
    """

    def __init__(self):
        self._d = {}
        self._fallback = None
        self._unify = False        # 是否把所有图标内容撑到同一尺寸

    def set_unify(self, on):
        if bool(on) != self._unify:
            self._unify = bool(on)
            self.clear()

    def icon(self, path, px=48):
        key = (str(path), int(px), self._unify)
        if key in self._d:
            return self._d[key]
        img = shell_icon(path, px)
        if img is not None and not img.isNull():
            if self._unify:
                img = normalize(img)
            ic = QtGui.QIcon(QtGui.QPixmap.fromImage(img))
        else:
            try:
                if self._fallback is None:
                    # QFileIconProvider 在 PySide6 属于 QtWidgets，QFileInfo 属于 QtCore，
                    # 都不在 QtGui —— 写错一个名字整条 fallback 就炸（正斜杠路径必走这里）。
                    from PySide6.QtWidgets import QFileIconProvider as _FIP
                    from PySide6.QtCore import QFileInfo as _QFI
                    self._fallback = _FIP()
                else:
                    from PySide6.QtCore import QFileInfo as _QFI
                ic = self._fallback.icon(_QFI(str(path)))
                ic = QtGui.QIcon(ic.pixmap(px, px))
            except Exception:              # noqa: BLE001 —— fallback 也不许把调用方带走
                ic = QtGui.QIcon()
        self._d[key] = ic
        return ic

    def thumb(self, path, px=120):
        """图片缩略图 QIcon；不是图片 / 读失败返回 None（调用方回退 shell 图标）。"""
        px = int(px)
        if px <= 0:
            return None
        key = ("thumb", str(path), px)
        if key in self._d:
            return self._d[key]
        img = _thumb_image(path, px) if is_image(path) else None
        ic = None if img is None else QtGui.QIcon(QtGui.QPixmap.fromImage(img))
        self._d[key] = ic                   # None 也缓存，避免对坏图反复解码
        return ic

    def clear(self):
        self._d.clear()
