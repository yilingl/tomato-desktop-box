# -*- coding: utf-8 -*-
"""appicon.py / assets/tomato.ico 自测：Qt 断言全在离屏子进程里做。

为什么走子进程：QT_QPA_PLATFORM=offscreen 必须在 import PySide6 之前设进环境；
而 discover 时本文件排在其它 tests_*.py 之前，一旦在父进程里立起 offscreen 的
QApplication，后面 tests_store / tests_shellmenu 里「QApplication 已存在」那几个
回归用例就会改在离屏平台上跑，连带整个基线。所以父进程一行 Qt 都不碰，
QIcon / QImage 的事全丢给子进程（同 tests_tray.py 的做法）。

三种状态都实测：
    正常态  —— .ico 在位，qicon() 多尺寸可用，16px 帧红+绿都在
    缺失态  —— 父进程把 .ico 临时改名（try/finally 保证改回来），qicon() 必须降级不炸
    损坏态  —— 子进程往临时目录丢一个假 .ico 并指向它，qicon() 同样必须降级

跑法：
    python tests_appicon.py
    python -m unittest tests_appicon -v
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

ICO = os.path.join(REPO, "assets", "tomato.ico")
ICO_BAK = ICO + ".bak"
EXPECTED = [16, 24, 32, 48, 64, 128, 256]
PNG_SIG = b"\x89PNG\r\n\x1a\n"

PROBE_FLAG = "--probe"
PROBE_PREFIX = "APPICON_PROBE_JSON "


def parse_ico(path):
    """标准库 struct 解 ICO 目录：返回 (原始字节, reserved, type, 条目列表)。

    条目 = (width, height, bytes, offset, 该帧字节)。256 在目录里记 0，还原成 256。
    """
    with open(path, "rb") as f:
        data = f.read()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    items = []
    for i in range(count):
        w, h, _cc, _r, planes, bpp, nbytes, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        items.append((w or 256, h or 256, nbytes, off, planes, bpp, data[off:off + nbytes]))
    return data, reserved, kind, count, items


def _px_stats(blob):
    """把一帧 PNG 读回来，数「明显偏红」「明显偏绿」的像素。"""
    from PySide6.QtGui import QImage
    img = QImage.fromData(blob, "PNG")
    if img.isNull():
        return {"w": 0, "h": 0, "opaque": 0, "red": 0, "green": 0}
    img = img.convertToFormat(QImage.Format_ARGB32)
    red = green = opaque = 0
    for y in range(img.height()):
        for x in range(img.width()):
            v = img.pixel(x, y)
            if ((v >> 24) & 0xFF) < 128:          # 透明像素不算
                continue
            opaque += 1
            r, g, b = (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
            if r > 120 and r - g > 60 and r - b > 60:
                red += 1
            elif g > 120 and g - r > 50 and g - b > 50:
                green += 1
    return {"w": img.width(), "h": img.height(), "opaque": opaque, "red": red, "green": green}


def _probe(mode):
    """子进程入口：离屏实测 appicon，结果打成一行 JSON 给父进程断言。"""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"        # 必须在 import PySide6 之前
    sys.path.insert(0, REPO)
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    import deskbox.appicon as appicon

    out = {"mode": mode}
    app = QApplication.instance() or QApplication([])
    out["platform"] = app.platformName()
    out["cwd"] = os.getcwd()

    p = appicon.path()
    out["path"] = p
    out["path_isabs"] = os.path.isabs(p)
    out["path_exists"] = os.path.isfile(p)

    ic = appicon.qicon()                                # 当前状态下的图标
    out["null"] = bool(ic.isNull())
    out["sizes"] = sorted(s.width() for s in ic.availableSizes())
    out["cached_same"] = ic is appicon.qicon()

    if out["path_exists"]:                              # 正常态：逐帧核对
        data, _res, _kind, count, items = parse_ico(p)
        out["dir_count"] = count
        out["dir_sizes"] = sorted(w for w, *_ in items)
        out["frames_png"] = [blob[:8] == PNG_SIG for *_x, blob in items]
        out["px"] = {}
        for w, _h, _n, _o, _pl, _bp, blob in items:
            if w in (16, 32):
                out["px"][str(w)] = _px_stats(blob)

    # 损坏态：临时目录里一个假 .ico，只换 path()，其余逻辑照旧
    bad = os.path.join(tempfile.gettempdir(), "deskbox_bad_appicon.ico")
    with open(bad, "wb") as f:
        f.write(b"NOT AN ICO" * 64)
    real_path = appicon.path
    appicon.path = lambda: bad
    try:
        icc = appicon.qicon()
        out["corrupt_null"] = bool(icc.isNull())
        out["corrupt_sizes"] = sorted(s.width() for s in icc.availableSizes())
    finally:
        appicon.path = real_path
        os.remove(bad)

    print(PROBE_PREFIX + json.dumps(out, ensure_ascii=False))
    return out


def _run_probe(mode):
    """跑一次离屏子进程，cwd 故意设成临时目录（证明 path() 不依赖 cwd）。"""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-u", os.path.abspath(__file__), PROBE_FLAG, mode],
                       cwd=tempfile.gettempdir(), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    line = ""
    for ln in (p.stdout or "").splitlines():
        if ln.startswith(PROBE_PREFIX):
            line = ln[len(PROBE_PREFIX):]
    return line, (p.stdout or ""), (p.stderr or ""), p.returncode


class AppIconTest(unittest.TestCase):
    """断言全部落在实测结果上：父进程解 ICO 头，子进程实测 QIcon/QImage。"""

    normal = None

    @classmethod
    def setUpClass(cls):
        line, stdout, stderr, rc = _run_probe("normal")
        if not line:
            if "ModuleNotFoundError" in (stderr or ""):
                raise unittest.SkipTest("未安装 PySide6")
            raise AssertionError("离屏子进程没回 JSON（rc=%s）\n--- stdout ---\n%s\n--- stderr ---\n%s"
                                 % (rc, stdout, stderr))
        cls.normal = json.loads(line)
        print("离屏子进程实测(正常态): " + line)

    def check(self, name, cond, detail=""):
        if cond:
            print("PASS - %s" % name)
        else:
            print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        self.assertTrue(cond, "%s %s" % (name, detail))

    # ------------------------------------------------------------ ICO 目录
    def test_01_ico_header_has_seven_sizes(self):
        data, reserved, kind, count, items = parse_ico(ICO)
        sizes = sorted(w for w, *_ in items)
        self.check("ICO 是图标类型且 reserved=0", (reserved, kind) == (0, 1), (reserved, kind))
        self.check("帧数 == 7", count == 7, count)
        self.check("尺寸集合 == %s" % EXPECTED, sizes == EXPECTED, sizes)
        self.check("每帧都是 PNG 帧（Vista+）", all(blob[:8] == PNG_SIG for *_, blob in items))
        self.check("每帧 32bpp、planes=1", all((pl, bp) == (1, 32) for _w, _h, _n, _o, pl, bp, _b in items))
        self.check("帧数据全在文件内且不越界",
                   all(off + n <= len(data) and n > 0 for _w, _h, n, off, _p, _b, _x in items),
                   [(n, off, len(data)) for _w, _h, n, off, _p, _b, _x in items])
        self.check("256 帧在目录里按惯例记 0（已还原）", 256 in sizes)

    # ------------------------------------------------------------ 正常态
    def test_02_qicon_loads_all_sizes_from_ico(self):
        d = self.normal
        self.check("子进程跑在 offscreen", d.get("platform") == "offscreen", d.get("platform"))
        self.check("path() 是绝对路径且指向存在的 .ico",
                   d.get("path_isabs") and d.get("path_exists") and d.get("path") == ICO,
                   d.get("path"))
        self.check("子进程 cwd 在临时目录，确实不靠 cwd 找图标",
                   os.path.normcase(os.path.abspath(d.get("cwd", ""))) != os.path.normcase(REPO),
                   d.get("cwd"))
        self.check("qicon() 非 null", d.get("null") is False, d.get("null"))
        self.check("availableSizes 含 16 与 32",
                   16 in d.get("sizes", []) and 32 in d.get("sizes", []), d.get("sizes"))
        self.check("availableSizes 就是 7 档", d.get("sizes") == EXPECTED, d.get("sizes"))
        self.check("qicon() 走缓存（两次同一对象）", d.get("cached_same") is True)

    # ------------------------------------------------------------ 缺失态
    def test_03_missing_ico_still_returns_icon(self):
        self.assertTrue(os.path.isfile(ICO), "开始前 .ico 必须在位")
        os.replace(ICO, ICO_BAK)
        try:
            line, stdout, stderr, rc = _run_probe("fallback")
        finally:
            os.replace(ICO_BAK, ICO)                      # 无论如何都改回来
        if not line:
            self.fail("缺失态子进程没回 JSON（rc=%s）\n--- stdout ---\n%s\n--- stderr ---\n%s"
                      % (rc, stdout, stderr))
        d = json.loads(line)
        print("离屏子进程实测(缺失态): " + line)
        self.check("改名后 path 仍指向老名字", d.get("path") == ICO, d.get("path"))
        self.check("改名后 .ico 确实不在位", d.get("path_exists") is False)
        self.check("改名后 qicon() 仍非 null（走降级画法）", d.get("null") is False, d.get("null"))
        self.check("降级图标尺寸含 32", 32 in d.get("sizes", []), d.get("sizes"))
        self.check("降级也没抛异常（JSON 正常返回）", d.get("mode") == "fallback")

        _data, _r, _k, count, items = parse_ico(ICO)      # 收尾自证：原文件回来了
        self.check("测完 .ico 已恢复原名且仍是 7 帧", count == 7 and os.path.isfile(ICO),
                   (count, os.path.isfile(ICO_BAK)))
        self.check("恢复后仍是同一份内容（7 档齐全）",
                   sorted(w for w, *_ in items) == EXPECTED, sorted(w for w, *_ in items))

    # ------------------------------------------------------------ 损坏态
    def test_04_corrupt_ico_still_returns_icon(self):
        d = self.normal
        self.check("假 .ico 时 qicon() 仍非 null", d.get("corrupt_null") is False, d.get("corrupt_null"))
        self.check("假 .ico 时降级图标含 32", 32 in d.get("corrupt_sizes", []), d.get("corrupt_sizes"))

    # ------------------------------------------------------------ 16px 帧
    def test_05_sixteen_px_frame_is_not_a_flat_blob(self):
        px = (self.normal.get("px") or {}).get("16")
        self.assertTrue(px, "子进程没报 16px 帧的像素统计")
        print("16px 帧像素统计: %s" % px)
        self.check("16px 帧明显偏红的像素 > 0", px.get("red", 0) > 0, px)
        self.check("16px 帧明显偏绿的像素 > 0", px.get("green", 0) > 0, px)
        self.check("16px 帧有不透明像素（不是空图）", px.get("opaque", 0) > 0, px)
        self.check("16px 帧确实是 16x16", (px.get("w"), px.get("h")) == (16, 16), px)


if __name__ == "__main__":
    if PROBE_FLAG in sys.argv:
        i = sys.argv.index(PROBE_FLAG)
        _probe(sys.argv[i + 1] if len(sys.argv) > i + 1 else "normal")
    else:
        unittest.main(verbosity=2)
