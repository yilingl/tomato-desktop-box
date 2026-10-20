# -*- coding: utf-8 -*-
"""shellmenu.py 自测：绝不真的弹菜单（会卡死无人点击），只走 _build 链路。

句柄断言说明：QueryContextMenu 会让 shell 逐个加载第三方扩展 DLL，每个扩展
都会常驻若干句柄——这是 shell 扩展自身的行为，Explorer 里也一样，不是本模块
的泄漏。所以这里不测「绝对增长」，而是对比「裸 shell 基线」与「我们的 _build」：
两者增量必须同量级，多出来的那部分才算我们的泄漏。
"""
import ctypes
import os
import sys
import tempfile
import shutil
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deskbox.shellmenu as sm
import deskbox.store as store

_fail = []
_n = [0]
THIRD = ("火绒", "360", "WPS", "百度网盘", "ToDesk", "QQ", "夸克", "豆包",
         "NVIDIA", "7-Zip", "Bandizip", "Git")
WM_CANCELMODE = 0x001F
IID_SF = "{000214e6-0000-0000-c000-000000000046}"
IID_CM = "{000214e4-0000-0000-c000-000000000046}"


def check(name, cond, detail=""):
    _n[0] += 1
    if cond:
        print("PASS - %s" % name)
    else:
        print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        _fail.append(name)


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.GetProcessHandleCount.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(wintypes.DWORD)]
    return k


def _handles():
    k = _k32()
    n = wintypes.DWORD()
    k.GetProcessHandleCount(k.GetCurrentProcess(), ctypes.byref(n))
    return int(n.value)


def _gdi(flag):
    """GDI(0) / USER(1) 对象数。"""
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetGuiResources.restype = wintypes.DWORD
    u.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    return int(u.GetGuiResources(_k32().GetCurrentProcess(), flag))


def _desk_files():
    """真实桌面上的文件（只读，不动它们）。"""
    out = []
    for d in list(store.DESK_DIRS):
        try:
            for n in sorted(os.listdir(d)):
                p = os.path.join(d, n)
                if os.path.isfile(p):
                    out.append(p)
        except Exception:
            continue
    return out


def _pick(paths):
    for p in paths:
        if os.path.basename(p).lower() == "008.png":
            return p
    return paths[0] if paths else ""


def _raw_shell(path, hwnd):
    """只调到 QueryContextMenu 就收手、资源全释放的裸基线。返回菜单项数。"""
    libs = sm._libs()
    shell32, user32, ole32 = libs["shell32"], libs["user32"], libs["ole32"]
    iid_sf = sm._guid(IID_SF)
    iid_cm = sm._guid(IID_CM)
    pf = ctypes.c_void_p()
    shell32.SHParseDisplayName(ctypes.c_wchar_p(os.path.dirname(path)), None,
                               ctypes.byref(pf), 0, ctypes.byref(wintypes.ULONG()))
    full = ctypes.c_void_p()
    shell32.SHParseDisplayName(ctypes.c_wchar_p(path), None, ctypes.byref(full),
                               0, ctypes.byref(wintypes.ULONG()))
    ps = ctypes.c_void_p()
    ch = ctypes.c_void_p()
    shell32.SHBindToParent(full, ctypes.byref(iid_sf), ctypes.byref(ps),
                           ctypes.byref(ch))
    arr = (ctypes.c_void_p * 1)(ch.value)
    dcm = sm.DEFCONTEXTMENU()
    dcm.hwnd = hwnd
    dcm.pidlFolder = pf.value
    dcm.psf = ps.value
    dcm.cidl = 1
    dcm.apidl = arr
    pcm = ctypes.c_void_p()
    shell32.SHCreateDefaultContextMenu(ctypes.byref(dcm), ctypes.byref(iid_cm),
                                       ctypes.byref(pcm))
    n = 0
    if pcm.value:
        h = user32.CreatePopupMenu()
        # flags 必须与 _build 完全一致：少一个 CMF_CANRENAME 会少加载一个扩展，
        # 那一次性加载会被错算成我们的泄漏
        sm._vfn(pcm, sm.VT_QUERY_CONTEXT_MENU, ctypes.c_long, wintypes.HMENU,
                wintypes.UINT, wintypes.UINT, wintypes.UINT,
                wintypes.UINT)(pcm, h, 0, 1, 0x7FFF,
                               sm.CMF_EXPLORE | sm.CMF_CANRENAME |
                               sm.CMF_EXTENDEDVERBS)
        n = int(user32.GetMenuItemCount(h))
        user32.DestroyMenu(h)
        sm._release(pcm)
    sm._release(ps)
    ole32.CoTaskMemFree(full)
    ole32.CoTaskMemFree(pf)
    return n


# ---------------------------------------------------------------- 用例
_QAPP = None


def t_available():
    ok, why = sm.available()
    check("available() 返回可用", ok is True, why)
    check("available() 原因非空字符串", isinstance(why, str) and bool(why), why)


def t_available_qapp(files):
    """回归：QApplication 已把主线程设成 STA 时 available() 仍必须成功且稳定。

    Qt 建好 QApplication 后主线程已是 STA，available() 内部再调一次
    CoInitializeEx 拿到的是 S_FALSE(1)。旧代码 if ...: pass 是空语句，
    等于每次调用都无条件 CoUninitialize，把 Qt 持有的 STA 计数减掉一次。
    这里连调 5 次，再真建一次菜单，证明 STA 没被破坏。
    """
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("SKIP - 未安装 PySide6，跳过 QApplication 下的 available() 回归")
        return
    global _QAPP
    _QAPP = QApplication.instance() or QApplication([])
    ole32 = sm._libs()["ole32"]
    hr = int(ole32.CoInitializeEx(None, sm.COINIT_APARTMENTTHREADED)) & 0xFFFFFFFF
    check("QApp 存在时 CoInitializeEx 返回 S_FALSE", hr == sm.S_FALSE,
          "hr=0x%08X" % hr)
    check("_co_start 认 S_FALSE 但不要配对 Uninitialize",
          sm._co_start(ole32) == (True, False, sm.S_FALSE), sm._co_start(ole32))

    results = []
    for i in range(5):
        ok, why = sm.available()
        results.append((ok, why))
        check("QApp 存在时第 %d 次 available() 成功" % (i + 1), ok is True, why)
    check("QApp 存在时 available() 连调 5 次结果一致",
          len(set(results)) == 1, results)
    check("QApp 存在时 available() 原因非空",
          isinstance(results[0][1], str) and bool(results[0][1]), results[0][1])
    # STA 若被误 Uninitialize，后面建菜单会失败——用它兜底
    if files:
        men = None
        try:
            men = sm._build([_pick(files)], sm.host_hwnd())
            check("available() 连调 5 次后菜单仍能建出来", men.n_items > 0,
                  men._error)
        except Exception as e:
            check("available() 连调 5 次后菜单仍能建出来", False, e)
        finally:
            if men is not None:
                men.release()
    # 宿主窗口创建/销毁复跑一遍：QApp 存活时销毁旧宿主窗口曾直接崩 0xc0000096
    # （窗口类记着第一个 WNDPROC 的地址，旧回调对象被 GC 后就是野指针）
    h1 = sm.host_hwnd()
    sm.ShellMenu().close()
    h2 = sm.host_hwnd()
    check("QApp 存在时宿主窗口可销毁并重建", bool(h1) and bool(h2) and h1 != h2,
          (h1, h2))


def t_bad_input():
    r = None
    try:
        r = sm.popup([], (0, 0))
        check("popup([]) 不抛异常", True)
    except Exception as e:
        check("popup([]) 不抛异常", False, e)
    check("popup([]) 返回 None", r is None, r)

    ghost = os.path.join(tempfile.gettempdir(), "deskbox_no_such_9527.xyz")
    r = None
    try:
        r = sm.popup([ghost], (0, 0))
        check("popup 不存在路径不抛异常", True)
    except Exception as e:
        check("popup 不存在路径不抛异常", False, e)
    check("popup 不存在路径返回 None", r is None, r)

    m = sm._build([ghost], 0)
    check("_build 不存在路径返回 0 项", m.n_items == 0, m.n_items)
    m.release()
    check("_build 失败后 release 幂等", m.release() is None)


def t_build_one(files):
    if not files:
        check("桌面有可测文件", False, "桌面没有文件")
        return
    path = _pick(files)
    hwnd = sm.host_hwnd()
    check("宿主窗口创建成功", bool(hwnd), hwnd)
    men = None
    try:
        men = sm._build([path], hwnd)
        check("_build 可解包成三元组", len(tuple(men)) == 3)
        check("单文件菜单项 > 20（%s，实际 %d）"
              % (os.path.basename(path), men.n_items), men.n_items > 20,
              men.n_items)
        texts = sm._menu_texts(men.hmenu)
        joined = " | ".join(texts)
        hit = [k for k in THIRD if k in joined]
        check("菜单含第三方扩展项", bool(hit),
              "只看到 %d 项：%s" % (len(texts), joined[:400]))
        print("     第三方项实测: %s" % (", ".join(hit) if hit else "(无)"))
    finally:
        if men is not None:
            men.release()
    check("release 后 pcm/hmenu 已清空", men.pcm is None and men.hmenu is None)
    check("release 后 pidl 表已清空", men._pidls == [], men._pidls)
    check("重复 release 不抛异常", men.release() is None)


def t_build_multi(files):
    if len(files) < 2:
        check("桌面至少两个文件", False, len(files))
        return
    hwnd = sm.host_hwnd()
    men = None
    try:
        men = sm._build(files[:2], hwnd)
        check("多选 2 个文件能建菜单", men.n_items > 0, men.n_items)
        check("多选 pidl 记录了 3 个（目录 + 2 项）", len(men._pidls) == 3,
              men._pidls)
        check("多选菜单项文本非空", len(sm._menu_texts(men.hmenu)) > 0)
    finally:
        if men is not None:
            men.release()


def _no_zw(text):
    """去掉零宽字符，shell 的「清空回收站」里嵌了两个 U+200B。"""
    return "".join(c for c in (text or "") if c not in "\u200b\u200c\u200d\ufeff")


def t_virtual(files):
    """CLSID 虚拟项：此电脑 / 回收站 走 SHBindToParent 路线。"""
    this_pc = "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
    bin_ = "::{645FF040-5081-101B-9F08-00AA002F954E}"
    hwnd = sm.host_hwnd()
    check("虚拟项前置：宿主窗口可用", bool(hwnd), hwnd)

    men = None
    try:
        men = sm._build([this_pc], hwnd)
        check("此电脑 n_items >= 5（实际 %d）" % men.n_items, men.n_items >= 5,
              men.n_items)
        texts = sm._menu_texts(men.hmenu)
        joined = " | ".join(texts)
        print("     此电脑菜单: %s" % joined[:300])
        check("此电脑菜单含「属性」", any("属性" in t for t in texts), joined[:300])
    finally:
        if men is not None:
            men.release()

    men = None
    try:
        men = sm._build([bin_], hwnd)
        check("回收站 n_items >= 5（实际 %d）" % men.n_items, men.n_items >= 5,
              men.n_items)
        texts = sm._menu_texts(men.hmenu)
        # 回收站那一条被 shell 写成「清空\u200b\u200b回收站」（两个零宽空格），
        # 断言前必须先把零宽字符去掉，否则看得见却匹配不上。
        clean = " | ".join(_no_zw(t) for t in texts)
        print("     回收站菜单: %s" % clean[:300])
        check("回收站菜单含「清空回收站」", "清空回收站" in clean, clean[:300])
    finally:
        if men is not None:
            men.release()

    # 混合多选：以第一项所属 parent 为准，不许崩
    mixed = ([files[0]] if files else []) + [this_pc]
    men = None
    try:
        men = sm._build(mixed, hwnd)
        check("混选（文件 + CLSID）不崩", men.n_items >= 0, men.n_items)
    finally:
        if men is not None:
            men.release()
    men = None
    try:
        men = sm._build([this_pc, bin_], hwnd)
        check("混选（两个不同 CLSID）不崩且以第一项为准", men.n_items >= 5,
              men.n_items)
    finally:
        if men is not None:
            men.release()
    check("_build 纯垃圾路径仍返回 0 项",
          sm._build(["::not-a-clsid"], hwnd).n_items == 0)

    r, dt = _popup_with_cancel(this_pc, 0)
    print("     CLSID popup 打断耗时 %.2fs" % dt)
    check("CLSID popup 3 秒内被打断", dt < 3.0, "%.2fs" % dt)
    check("CLSID popup 被打断返回 None", r is None, r)


def t_no_extra_leak(files):
    """我们的 _build 相对裸 shell 基线不得额外泄漏句柄。"""
    if not files:
        check("泄漏检测前置：桌面有文件", False)
        return
    path = _pick(files)
    hwnd = sm.host_hwnd()
    rounds = 10
    # 预热，把首次 DLL 加载的一次性句柄吃掉
    _raw_shell(path, hwnd)
    sm._build([path], hwnd).release()
    # 裸基线本身的波动就很大（扩展惰性加载时一轮能差十几个），所以两轮交替
    # 各测一次，基线取观测到的最大成本、我们的取最小值——这才是「同量级」。
    raw_seen, our_seen = [], []
    for _ in range(2):
        base = _handles()
        for _ in range(rounds):
            _raw_shell(path, hwnd)
        raw_seen.append(_handles() - base)
        base = _handles()
        for _ in range(rounds):
            sm._build([path], hwnd).release()
        our_seen.append(_handles() - base)
    raw_delta = max(raw_seen)
    our_delta = min(our_seen)
    print("     %d 次裸 shell 基线增量 %s，我们的 _build 增量 %s"
          % (rounds, raw_seen, our_seen))
    check("_build 相对裸 shell 基线无额外句柄泄漏",
          our_delta <= max(raw_delta, 0) + 5,
          "raw=%d ours=%d" % (raw_delta, our_delta))

    # GDI/USER 对象：菜单与图标占的是这两类，泄漏会在这里显形
    _raw_shell(path, hwnd)
    sm._build([path], hwnd).release()
    g0 = (_gdi(0), _gdi(1))
    for _ in range(rounds):
        _raw_shell(path, hwnd)
    g_raw = (_gdi(0) - g0[0], _gdi(1) - g0[1])
    g0 = (_gdi(0), _gdi(1))
    for _ in range(rounds):
        sm._build([path], hwnd).release()
    g_our = (_gdi(0) - g0[0], _gdi(1) - g0[1])
    print("     GDI/USER 增量：裸基线 %s，我们的 %s" % (g_raw, g_our))
    check("_build 相对裸 shell 基线无额外 GDI 泄漏",
          g_our[0] <= max(g_raw[0], 0) + 20, "raw=%d ours=%d"
          % (g_raw[0], g_our[0]))
    check("宿主窗口不重复创建", sm.host_hwnd() == sm.host_hwnd() == hwnd,
          sm._HOST["hwnd"])


def _post_cancel(hwnd, stop, delay=0.6, period=0.15):
    """后台线程：等 delay 秒后向 hwnd 投 WM_CANCELMODE，之后每 period 补一次。"""
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                               wintypes.LPARAM]
    u.PostMessageW.restype = wintypes.BOOL
    if stop.wait(delay):
        return
    while not stop.is_set():
        u.PostMessageW(hwnd, WM_CANCELMODE, 0, 0)
        stop.wait(period)


def _popup_with_cancel(path, hwnd_val):
    """真弹一次菜单，0.6 秒后从后台打断。返回 (返回值, 秒数)。"""
    host = sm.host_hwnd()
    stop = threading.Event()
    th = None
    if host:
        th = threading.Thread(target=_post_cancel, args=(host, stop))
        th.daemon = True
        th.start()
    t0 = time.time()
    r = sm.ShellMenu().popup([path], (400, 400), hwnd_val)
    dt = time.time() - t0
    stop.set()
    if th is not None:
        th.join(1.0)
    return r, dt


def t_interruptible(files):
    """所有者固定为宿主窗口后，菜单必须能被 WM_CANCELMODE 打断。"""
    if not files:
        check("可打断性前置：桌面有文件", False)
        return
    path = _pick(files)
    host = sm.host_hwnd()
    check("可打断性前置：宿主窗口可用", bool(host), host)
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetDesktopWindow.restype = wintypes.HWND
    desktop = int(u.GetDesktopWindow() or 0)
    cases = [(0, "hwnd=0"), (desktop, "hwnd=外部窗口句柄"),
             (987654321, "hwnd=随机整数")]
    for val, label in cases:
        r, dt = _popup_with_cancel(path, val)
        print("     %s 打断耗时 %.2fs" % (label, dt))
        check("%s 时 popup 2 秒内返回" % label, dt < 2.0, "%.2fs" % dt)
        check("%s 时被打断返回 None" % label, r is None, r)


def t_owner_split(files):
    """菜单内容与 hwnd 无关：owner 恒为宿主窗口。"""
    if not files:
        check("owner 拆分前置：桌面有文件", False)
        return
    path = _pick(files)
    host = sm.host_hwnd()
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetDesktopWindow.restype = wintypes.HWND
    other = int(u.GetDesktopWindow() or 0)
    m1 = sm._build([path], host)
    try:
        n1 = m1.n_items
        t1 = " | ".join(sm._menu_texts(m1.hmenu))
    finally:
        m1.release()
    m2 = sm._build([path], other)
    try:
        n2 = m2.n_items
        t2 = " | ".join(sm._menu_texts(m2.hmenu))
    finally:
        m2.release()
    check("菜单项数不受 owner 影响（%d vs %d）" % (n1, n2), n1 == n2 and n1 > 20,
          "%d/%d" % (n1, n2))
    hit = [k for k in THIRD if k in t1 and k in t2]
    check("两种 owner 下第三方扩展项一致", len(hit) >= 6, hit)


def t_invoke_hwnd(files):
    """CMINVOKECOMMANDINFO.hwnd 必须是调用方传入的窗口，而非菜单 owner。"""
    if not files:
        check("命令窗口前置：桌面有文件", False)
        return
    path = _pick(files)
    host = sm.host_hwnd()
    libs = sm._libs()
    user32 = libs["user32"]
    real_track = user32.TrackPopupMenuEx
    real_invoke = sm._invoke
    seen = {"owner": [], "hwnd": []}

    def fake_track(hmenu, flags, x, y, owner, params):
        seen["owner"].append(int(owner or 0))
        return 1                        # 假装用户点了第一项

    def fake_invoke(pcm, hwnd, cmd):
        seen["hwnd"].append(int(hwnd or 0))
        return 0

    try:
        user32.TrackPopupMenuEx = fake_track
        sm._invoke = fake_invoke
        for val in (0, 424242):
            sm.ShellMenu().popup([path], (400, 400), val)
    finally:
        sm._invoke = real_invoke
        user32.TrackPopupMenuEx = real_track
    check("TrackPopupMenuEx 的 owner 恒为宿主窗口",
          seen["owner"] == [host, host], seen["owner"])
    check("hwnd=0 时命令窗口退化为宿主窗口", seen["hwnd"][:1] == [host],
          seen["hwnd"])
    check("hwnd=424242 时命令窗口用调用方句柄", seen["hwnd"][1:2] == [424242],
          seen["hwnd"])


def t_rename_menu(files):
    """「重命名」项：菜单里要有，且 popup 选中它时不调 _invoke、直接把文本交回。"""
    if not files:
        check("重命名前置：桌面有文件", False)
        return
    path = _pick(files)
    host = sm.host_hwnd()
    men = None
    try:
        men = sm._build([path], host)
        items = sm._walk(men.hmenu)
        hit = [(d, i, mid, t) for d, i, mid, t in items
               if t.replace("&", "").startswith("重命名")]
        check("_build 菜单里有「重命名」（n_items=%d）" % men.n_items,
              bool(hit), " | ".join(t for _, _, _, t in items)[:400])
        if not hit:
            return
        depth, _idx, mid, text = hit[0]
        print("     重命名项: depth=%d id=%s text=%r" % (depth, mid, text))
        check("重命名项在顶层菜单", depth == 0, depth)
        check("_is_rename_text 认得出重命名", sm._is_rename_text(text), text)
        check("_is_rename_text 不误伤其它项",
              not sm._is_rename_text("属性(R)") and
              not sm._is_rename_text("打开(O)"))
        check("_is_rename_text 容忍空值", not sm._is_rename_text(None))
    finally:
        if men is not None:
            men.release()

    # popup 选中「重命名」：_invoke 必须一次都不调
    libs = sm._libs()
    user32 = libs["user32"]
    real_track = user32.TrackPopupMenuEx
    real_invoke = sm._invoke
    calls = {"invoke": 0}
    got = {"text": None, "err": None}

    def fake_track(hmenu, flags, x, y, owner, params):
        for _d, _i, m, t in sm._walk(hmenu):
            if t.replace("&", "").startswith("重命名"):
                return int(m)
        return 0

    def boom(*a, **kw):
        calls["invoke"] += 1
        raise AssertionError("重命名不该调 _invoke")

    inst = sm.ShellMenu()
    try:
        user32.TrackPopupMenuEx = fake_track
        sm._invoke = boom
        got["text"] = inst.popup([path], (500, 500), 0)
        got["err"] = inst.last_error
    finally:
        sm._invoke = real_invoke
        user32.TrackPopupMenuEx = real_track
    check("popup 选中重命名没调 _invoke", calls["invoke"] == 0, calls)
    check("popup 把重命名文本交回上层",
          str(got["text"] or "").replace("&", "").startswith("重命名"),
          got["text"])
    check("popup 处理重命名后 last_error 为空", got["err"] == "", got["err"])


def t_close():
    inst = sm.ShellMenu()
    try:
        inst.close()
        inst.close()
        check("ShellMenu.close() 幂等", True)
    except Exception as e:
        check("ShellMenu.close() 幂等", False, e)
    check("close 后 host 归零", sm._HOST["hwnd"] == 0, sm._HOST["hwnd"])
    check("close 后可重建宿主窗口", bool(sm.host_hwnd()), sm._HOST["hwnd"])


def t_pos():
    class Pt(object):
        def x(self):
            return 12

        def y(self):
            return 34

    check("_pos 接受 (x, y) 元组", sm._pos((12, 34)) == (12, 34))
    check("_pos 接受 QPoint 式对象", sm._pos(Pt()) == (12, 34))
    check("_pos 非法输入退化为 (0, 0)", sm._pos(None) == (0, 0),
          sm._pos(None))


def main():
    print("-" * 60)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoUninitialize.argtypes = []
    ole32.CoInitializeEx(None, sm.COINIT_APARTMENTTHREADED)
    try:
        files = _desk_files()
        print("桌面可测文件数:", len(files))
        t_available()
        t_pos()
        t_bad_input()
        t_build_one(files)
        t_build_multi(files)
        t_owner_split(files)
        t_invoke_hwnd(files)
        t_rename_menu(files)
        t_interruptible(files)
        t_no_extra_leak(files)
        t_virtual(files)
        t_close()
        t_available_qapp(files)     # 放最后：QApplication 会干扰句柄基线
    finally:
        sm._CUR["pcm3"] = sm._CUR["pcm2"] = None
        ole32.CoUninitialize()
    print("-" * 60)
    if _fail:
        print("FAIL 汇总: %d/%d 项失败 -> %s" % (len(_fail), _n[0], _fail))
        return 1
    print("全部通过 %d/%d" % (_n[0], _n[0]))
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    finally:
        try:
            sm.ShellMenu().close()
        except Exception:
            pass
    sys.exit(code)
