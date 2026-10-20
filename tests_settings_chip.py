# -*- coding: utf-8 -*-
"""设置面板五块文字底色（chip）与背景行宽度的实测验收。

为什么走子进程：QT_QPA_PLATFORM=offscreen 必须在 import PySide6 之前进环境，
    否则 discover 跑到这里时可能已有 windows 平台 QApplication，会真往屏幕上弹设置框。

测什么：
    1. 五个 chip（背景 / 开机自启动 / 最小化到托盘 / 点关闭 / 小窗口自动粘连）
       尺寸完全一致，且都等于模块级 _CHIP_W x _CHIP_H；
    2. 每个 chip 的文字宽度留有余量：horizontalAdvance(text) <= width - 12；
    3. 每个 chip 都在对话框内容区内：x + width <= 400 - 18；
    4. 对话框里**所有**子控件都不越界（findChildren(QWidget) 全扫）；
    5. 背景行那个 5 段 Segmented 已从 224 压回内容区，且每段宽度仍容得下
       最长标签「黑玻璃」+ 8px 内边距；
    6. 藏掉任一个 chip，它那块矩形里的像素真的变（证明底色是 chip 自己画的，
       不是继承 box 背景）；
    7. 顺手存一张 _probe/p_settings_chip_fixed.png 供人眼复核。

跑法：
    python tests_settings_chip.py
    python -m unittest tests_settings_chip -v
"""
import json
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

PROBE_FLAG = "--probe"
PROBE_PREFIX = "CHIP_PROBE_JSON "

CHIP_TEXTS = ("背景", "开机自启动", "最小化到托盘", "点关闭", "小窗口自动粘连")
SEG_LABELS = ("玻璃", "黑玻璃", "深色", "浅色", "纯色")
SHOT = os.path.join(REPO, "_probe", "p_settings_chip_fixed.png")


def _probe():
    """子进程入口：离屏构造设置对话框，量出 chip 与背景行的全部几何，打成一行 JSON。"""
    import tempfile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"      # 必须在 import PySide6 之前设
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    tmp = tempfile.mkdtemp(prefix="dsh_chip_")
    for key in ("UI", "MINIS", "GROUPS", "CATS", "HIDEICONS"):
        os.environ["DESKBOX_%s_JSON" % key] = os.path.join(tmp, key.lower() + ".json")
    os.environ["DESKBOX_ROOT"] = os.path.join(tmp, "root")
    os.makedirs(os.environ["DESKBOX_ROOT"], exist_ok=True)
    sys.path.insert(0, REPO)

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import QApplication, QLabel, QWidget
    import deskbox.ui_mock as U

    app = QApplication.instance() or QApplication([])
    U.NO_HIDE_PROMPT = True                          # 压掉启动自检弹窗

    out = {"chips": {}, "errors": [], "all_children": [], "seg": {}}

    # GlassDialog.exec 会开模态事件循环，离屏跑就永远不返回 —— 换成"显示一下再返回 0"。
    made = []
    real_dialog = U.GlassDialog

    class _Fake(real_dialog):
        def exec(self):
            made.append(self)
            self.show()
            app.processEvents()
            return 0

    U.GlassDialog = _Fake
    try:
        win = U.MainWindow()
        app.processEvents()
        win._on_settings()
        app.processEvents()
    finally:
        U.GlassDialog = real_dialog

    if not made:
        out["errors"].append("设置对话框没被构造出来")
        return out
    dlg = made[-1]
    dlg.show()
    app.processEvents()

    out["dlg_w"] = dlg.width()
    out["dlg_h"] = dlg.height()
    out["const_w"] = U._CHIP_W
    out["const_h"] = U._CHIP_H
    out["content_limit"] = dlg.width() - 18

    font = QFont(U.FAMILY)
    font.setPixelSize(11)
    fm = QFontMetrics(font)

    labels = {lb.text(): lb for lb in dlg.findChildren(QLabel) if lb.text() in CHIP_TEXTS}
    out["found_chips"] = sorted(labels)

    for text in CHIP_TEXTS:
        lb = labels.get(text)
        if lb is None:
            out["errors"].append("找不到 chip：%s" % text)
            continue
        pt = lb.mapTo(dlg, QPoint(0, 0))
        out["chips"][text] = {
            "w": lb.width(),
            "h": lb.height(),
            "x": pt.x(),
            "y": pt.y(),
            "adv": fm.horizontalAdvance(lb.text()),
            "right": pt.x() + lb.width(),
        }

    # 背景行那个 5 段分段控件：必须是标签全对的那个，别拿错成两段开关
    seg = None
    for cand in dlg.findChildren(U.Segmented):
        if tuple(cand.labels) == SEG_LABELS:
            seg = cand
            break
    if seg is None:
        out["errors"].append("找不到背景行的 5 段 Segmented")
    else:
        spt = seg.mapTo(dlg, QPoint(0, 0))
        n = len(SEG_LABELS)
        out["seg"] = {
            "w": seg.width(),
            "x": spt.x(),
            "right": spt.x() + seg.width(),
            "per": (seg.width() - 4) / float(n),
            "need": fm.horizontalAdvance("黑玻璃") + 8,
            "adv_beige": fm.horizontalAdvance("黑玻璃"),
        }

    # 全对话框子控件越界扫描（内容右边界 = 宽度 - 18）
    limit = out["content_limit"]
    for wid in dlg.findChildren(QWidget):
        pt = wid.mapTo(dlg, QPoint(0, 0))
        right = pt.x() + wid.width()
        if right > limit + 1:
            label = wid.text() if hasattr(wid, "text") else ""
            out["all_children"].append([type(wid).__name__, label, pt.x(), wid.width(), right])

    # 藏掉单个 chip，看它矩形里的像素是否真的跟着变（底色是它自己画的）
    full = dlg.grab().toImage()
    for text in CHIP_TEXTS:
        lb = labels.get(text)
        if lb is None:
            continue
        pt = lb.mapTo(dlg, QPoint(0, 0))
        probes = [(lb.width() // 2, lb.height() // 2),
                  (4, 4),
                  (lb.width() - 5, lb.height() - 5)]
        lb.hide()
        app.processEvents()
        gone = dlg.grab().toImage()
        lb.show()
        app.processEvents()
        changed = 0
        for px, py in probes:
            a = full.pixelColor(pt.x() + px, pt.y() + py)
            b = gone.pixelColor(pt.x() + px, pt.y() + py)
            if (abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())) > 6:
                changed += 1
        out["chips"][text]["changed"] = changed
        out["chips"][text]["probes"] = len(probes)

    # 存图供人眼复核
    try:
        os.makedirs(os.path.dirname(SHOT), exist_ok=True)
        dlg.grab().save(SHOT)
        out["shot"] = SHOT
    except Exception as exc:                         # noqa: BLE001
        out["errors"].append("截图没存下来：%s: %s" % (type(exc).__name__, exc))

    dlg.close()
    win.close()
    app.processEvents()
    return out


class ChipTest(unittest.TestCase):
    """设置面板五个 chip 尺寸统一 + 背景行不越界。"""

    @classmethod
    def setUpClass(cls):
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run([sys.executable, os.path.abspath(__file__), PROBE_FLAG],
                              cwd=REPO, env=env, capture_output=True, timeout=300)
        cls.proc = proc
        cls.data = None
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith(PROBE_PREFIX):
                cls.data = json.loads(line[len(PROBE_PREFIX):])
        if cls.data is None:
            raise AssertionError("探针没吐结果行：rc=%s\nSTDOUT=\n%s\nSTDERR=\n%s"
                                 % (proc.returncode,
                                    proc.stdout.decode("utf-8", "replace")[-3000:],
                                    proc.stderr.decode("utf-8", "replace")[-3000:]))

    def check(self, name, cond, detail=""):
        self.assertTrue(cond, "%s —— %s" % (name, detail))

    def test_00_no_probe_errors(self):
        self.check("探针无错误", not self.data["errors"], str(self.data["errors"]))

    def test_01_five_chips_found(self):
        self.check("五个 chip 都找到", self.data["found_chips"] == sorted(CHIP_TEXTS),
                   str(self.data["found_chips"]))

    def test_02_sizes_identical_and_equal_constants(self):
        cw, ch = self.data["const_w"], self.data["const_h"]
        for text in CHIP_TEXTS:
            info = self.data["chips"].get(text)
            self.check("%s 尺寸 == 常量" % text, info is not None, "chip 缺失")
            self.check("%s 宽 == _CHIP_W" % text, info["w"] == cw, "%s != %s" % (info["w"], cw))
            self.check("%s 高 == _CHIP_H" % text, info["h"] == ch, "%s != %s" % (info["h"], ch))
        self.check("常量本身 > 最长文本", cw - 12 >= 77, "chip 宽 %s 放不下 77px 的「小窗口自动粘连」" % cw)

    def test_03_text_fits_with_padding(self):
        for text in CHIP_TEXTS:
            info = self.data["chips"][text]
            self.check("%s 文字留 12px 余量" % text,
                       info["adv"] <= info["w"] - 12,
                       "adv=%s width=%s 余量=%s" % (info["adv"], info["w"], info["w"] - info["adv"]))

    def test_04_chips_inside_content(self):
        limit = self.data["content_limit"]
        for text in CHIP_TEXTS:
            info = self.data["chips"][text]
            self.check("%s 在内容区内" % text, info["right"] <= limit,
                       "right=%s limit=%s" % (info["right"], limit))

    def test_05_no_child_widget_overflows(self):
        self.check("对话框内没有子控件越界",
                   not self.data["all_children"],
                   "越界(类名, 文本, x, w, right)：%s，内容右边界 %s"
                   % (self.data["all_children"], self.data["content_limit"]))

    def test_06_background_row_narrowed(self):
        seg = self.data["seg"]
        self.check("背景行有 5 段 Segmented", bool(seg), "没找到")
        self.check("宽度已小于改动前 224", seg["w"] < 224, "现在是 %s" % seg["w"])
        self.check("每段容得下「黑玻璃」+8px",
                   seg["per"] >= seg["need"],
                   "每段 %.1f < 需要 %s（黑玻璃 %s）" % (seg["per"], seg["need"], seg["adv_beige"]))
        self.check("背景行右端在内容区内", seg["right"] <= self.data["content_limit"],
                   "right=%s limit=%s" % (seg["right"], self.data["content_limit"]))

    def test_07_hiding_chip_changes_pixels(self):
        for text in CHIP_TEXTS:
            info = self.data["chips"][text]
            self.check("%s 藏起来后像素变化" % text,
                       info.get("changed", 0) >= 2,
                       "采样 %s 点只变了 %s 点" % (info.get("probes"), info.get("changed")))

    def test_08_screenshot_saved(self):
        path = self.data.get("shot") or ""
        self.check("截图存在", path and os.path.isfile(path), str(path))
        self.check("截图不是空文件", os.path.getsize(path) > 1000, str(os.path.getsize(path)))


if __name__ == "__main__":
    if PROBE_FLAG in sys.argv:
        payload = _probe()
        sys.stdout.write(PROBE_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    else:
        unittest.main(verbosity=2)
