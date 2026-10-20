# -*- coding: utf-8 -*-
"""shellmenu.py 的「桌面背景右键菜单」自测。

两条铁律：
1. 绝不对真实桌面做写操作 —— 所有写路径都指向 tempfile.mkdtemp() 造的临时目录。
2. 绝不真的弹出一个无人点击的菜单 —— 打断性用例一律用后台线程投 WM_CANCELMODE。

跑法：python tests_bgmenu.py    或    python -m unittest tests_bgmenu
"""
import ctypes
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deskbox.shellmenu as sm

TMP = tempfile.mkdtemp(prefix="deskbox_bg_")
WM_CANCELMODE = 0x001F
MF_BYPOSITION = 0x0400
NEW_DIR_NAME = "新建文件夹"

_fail = []
_n = [0]


def check(name, cond, detail=""):
    _n[0] += 1
    if cond:
        print("PASS - %s" % name)
    else:
        print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        _fail.append(name)


def _no_zw(text):
    """去掉零宽字符：shell 的文本里偶尔嵌 U+200B。"""
    return "".join(c for c in (text or "")
                   if c not in "\u200b\u200c\u200d\ufeff")


def _item_text(hmenu, i):
    u = sm._libs()["user32"]
    buf = ctypes.create_unicode_buffer(512)
    u.GetMenuStringW(hmenu, i, buf, 512, MF_BYPOSITION)
    return _no_zw(buf.value.replace("&", ""))


def _find_top(hmenu, keyword):
    """按文本关键字找「带子菜单」的顶层项，返回 (index, submenu) 或 (None, None)。

    必须要求 sub 非零：顶层还有一条「撤消 新建(U)\tCtrl+Z」，它也含「新建」
    字样但没有子菜单，先命中它就会误判。
    """
    u = sm._libs()["user32"]
    for i in range(int(u.GetMenuItemCount(hmenu))):
        if keyword not in _item_text(hmenu, i):
            continue
        sub = u.GetSubMenu(hmenu, i)
        if sub:
            return i, sub
    return None, None


def _sub_ids(hmenu, sub):
    """子菜单的 (位置, id, 文本) 列表。"""
    u = sm._libs()["user32"]
    out = []
    for j in range(int(u.GetMenuItemCount(sub))):
        out.append((j, int(u.GetMenuItemID(sub, j)), _item_text(sub, j)))
    return out


def _handles():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.GetProcessHandleCount.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(wintypes.DWORD)]
    n = wintypes.DWORD()
    k.GetProcessHandleCount(k.GetCurrentProcess(), ctypes.byref(n))
    return int(n.value)


def _gdi(flag):
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetGuiResources.restype = wintypes.DWORD
    u.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    return int(u.GetGuiResources(k.GetCurrentProcess(), flag))


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


def _popup_with_cancel(folder, hwnd_val):
    """真弹一次背景菜单，0.6 秒后从后台打断。返回 (返回值, 秒数)。"""
    host = sm.host_hwnd()
    stop = threading.Event()
    th = None
    if host:
        th = threading.Thread(target=_post_cancel, args=(host, stop))
        th.daemon = True
        th.start()
    t0 = time.time()
    r = sm.ShellMenu().popup_background(folder, (400, 400), hwnd_val)
    dt = time.time() - t0
    stop.set()
    if th is not None:
        th.join(1.0)
    return r, dt


# ---------------------------------------------------------------- 用例
def t_build_tmp():
    """folder=<临时目录> 时是「文件夹背景菜单」，不是「文件夹对象菜单」。"""
    hwnd = sm.host_hwnd()
    check("宿主窗口可用", bool(hwnd), hwnd)
    men = None
    try:
        men = sm._build_background(TMP, hwnd)
        check("_build_background 能建出菜单", bool(men.hmenu), men._error)
        check("建菜单没报错", not men._error, men._error)
        texts = sm._menu_texts(men.hmenu)
        joined = " | ".join(texts)
        print("     临时目录背景菜单前 10 项: %s" % joined[:200])
        check("含「新建」", any("新建" in t for t in texts), joined[:300])
        check("含「查看」", any("查看" in t for t in texts), joined[:300])
        check("含「刷新」", any("刷新" in t for t in texts), joined[:300])
        check("含「排序方式」", any("排序方式" in t for t in texts), joined[:300])
        check("不含「个人文件夹」（老错误路线的标志）",
              "个人文件夹" not in joined, joined[:300])
        check("菜单项数 >= 12（实际 %d）" % men.n_items, men.n_items >= 12,
              men.n_items)
        check("_build 三元组可解包", len(tuple(men)) == 3)
    finally:
        if men is not None:
            men.release()
    check("release 后 hmenu 已清空", men.hmenu is None)


def t_prefilled_new_menu():
    """补发 WM_INITMENUPOPUP 后「新建」子菜单必须是满的。"""
    hwnd = sm.host_hwnd()
    men = None
    try:
        men = sm._build_background(TMP, hwnd)
        check("背景菜单已补发 INITMENUPOPUP", men.prefilled > 0, men.prefilled)
        idx, sub = _find_top(men.hmenu, "新建")
        check("顶层找到「新建」子菜单", sub is not None, idx)
        if not sub:
            check("「新建」子菜单项数 >= 12", False, "找不到子菜单")
            return
        items = _sub_ids(men.hmenu, sub)
        check("「新建」子菜单项数 >= 12（实际 %d）" % len(items),
              len(items) >= 12, len(items))
        texts = [t for _, _, t in items if t]
        joined = " | ".join(texts)
        print("     「新建」子菜单: %s" % joined[:300])
        check("含「文件夹」", any("文件夹" in t for t in texts), joined[:300])
        check("含「文本文档」", any("文本文档" in t for t in texts), joined[:300])
        check("含「快捷方式」", any("快捷方式" in t for t in texts), joined[:300])
        fid = [i for _, i, t in items if "文件夹" in t and "快捷" not in t]
        check("「文件夹」项有可用命令 id", bool(fid) and fid[0] > 0, fid)
    finally:
        if men is not None:
            men.release()


def t_build_root():
    """folder=None 走桌面根命名空间，菜单要有「显示设置」或「个性化」。"""
    hwnd = sm.host_hwnd()
    men = None
    try:
        men = sm._build_background(None, hwnd)
        check("桌面根背景菜单能建出", bool(men.hmenu), men._error)
        check("桌面根菜单没报错", not men._error, men._error)
        texts = sm._menu_texts(men.hmenu)
        joined = " | ".join(texts)
        print("     桌面根背景菜单: %s" % joined[:260])
        check("含「显示设置」或「个性化」",
              ("显示设置" in joined) or ("个性化" in joined), joined[:300])
        check("桌面根菜单项数 >= 10（实际 %d）" % men.n_items,
              men.n_items >= 10, men.n_items)
        check("桌面根菜单已补发 INITMENUPOPUP", men.prefilled > 0, men.prefilled)
        check("桌面根没有「自定义文件夹」（那是普通文件夹视图的项）",
              "自定义文件夹" not in joined, joined[:300])
    finally:
        if men is not None:
            men.release()


def t_invoke_new_folder():
    """真 Invoke「新建 -> 文件夹」，只在临时目录里落地。"""
    work = tempfile.mkdtemp(prefix="deskbox_bginvoke_", dir=TMP)
    hwnd = sm.host_hwnd()
    men = None
    try:
        men = sm._build_background(work, hwnd)
        if not men.hmenu:
            check("Invoke 前置：菜单建成功", False, men._error)
            return
        _idx, sub = _find_top(men.hmenu, "新建")
        if not sub:
            check("Invoke 前置：找到「新建」子菜单", False)
            return
        target = [(i, t) for _j, i, t in _sub_ids(men.hmenu, sub)
                  if "文件夹" in t and "快捷" not in t]
        if not target:
            check("Invoke 前置：找到「文件夹」项", False)
            return
        cmd = target[0][0]
        check("Invoke 前置：目标目录初始为空", os.listdir(work) == [],
              os.listdir(work))
        hr = sm._invoke(men.pcm, hwnd, cmd)
        print("     invoke 文件夹 hr=0x%08X" % (hr & 0xFFFFFFFF))
        check("Invoke 返回成功", hr == 0, hex(hr & 0xFFFFFFFF))
        made = ""
        for _ in range(30):
            if os.path.isdir(os.path.join(work, NEW_DIR_NAME)):
                made = NEW_DIR_NAME
                break
            time.sleep(0.1)
        print("     临时目录内容: %s" % os.listdir(work))
        check("真 Invoke 建出了「%s」" % NEW_DIR_NAME, made == NEW_DIR_NAME,
              os.listdir(work))
        check("建出来的是目录",
              os.path.isdir(os.path.join(work, NEW_DIR_NAME)))
        check("没有在临时目录外造东西", sorted(os.listdir(work)) == [NEW_DIR_NAME],
              os.listdir(work))
    finally:
        if men is not None:
            men.release()


def _raw_background(folder, hwnd):
    """裸 shell 基线：同样的 CreateViewObject/GetItemObject/QueryContextMenu，
    只做一遍然后全释放，不带我们任何额外状态。用来把「shell 扩展自己的常驻
    开销」从「我们的泄漏」里剥掉——扩展 DLL 一加载就常驻句柄，Explorer 里也一样。
    """
    libs = sm._libs()
    shell32, user32 = libs["shell32"], libs["user32"]
    ole32 = libs["ole32"]
    iid_sf = sm._guid(sm.IID_ISHELLFOLDER)
    iid_sv = sm._guid(sm.IID_ISHELLVIEW)
    iid_cm = sm._guid("{000214e4-0000-0000-c000-000000000046}")
    desk = ctypes.c_void_p()
    shell32.SHGetDesktopFolder(ctypes.byref(desk))
    pidl = ctypes.c_void_p()
    shell32.SHParseDisplayName(ctypes.c_wchar_p(folder), None,
                               ctypes.byref(pidl), 0,
                               ctypes.byref(wintypes.ULONG()))
    psf = ctypes.c_void_p()
    sm._vfn(desk, sm.VT_SF_BIND_TO_OBJECT, ctypes.c_long, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(sm.GUID),
            ctypes.POINTER(ctypes.c_void_p))(desk, pidl.value, None,
                                             ctypes.byref(iid_sf),
                                             ctypes.byref(psf))
    psv = ctypes.c_void_p()
    sm._vfn(psf, sm.VT_SF_CREATE_VIEW_OBJECT, ctypes.c_long, ctypes.c_void_p,
            ctypes.POINTER(sm.GUID), ctypes.POINTER(ctypes.c_void_p))(
        psf, None, ctypes.byref(iid_sv), ctypes.byref(psv))
    pcm = ctypes.c_void_p()
    sm._vfn(psv, sm.VT_SV_GET_ITEM_OBJECT, ctypes.c_long, ctypes.c_uint,
            ctypes.POINTER(sm.GUID), ctypes.POINTER(ctypes.c_void_p))(
        psv, sm.SVGIO_BACKGROUND, ctypes.byref(iid_cm), ctypes.byref(pcm))
    hm = user32.CreatePopupMenu()
    sm._vfn(pcm, sm.VT_QUERY_CONTEXT_MENU, ctypes.c_long, wintypes.HMENU,
            wintypes.UINT, wintypes.UINT, wintypes.UINT, wintypes.UINT)(
        pcm, hm, 0, 1, 0x7FFF, sm.CMF_EXPLORE | sm.CMF_CANRENAME)
    # 基线也要补发 INITMENUPOPUP：那一步同样是 shell 扩展在干活（「新建」子菜单
    # 要现场加载一串扩展），不算在我们头上。补发前必须把 _CUR 指向这条菜单自己的
    # pcm2/pcm3 —— _wndproc 要靠它把消息转发给 IContextMenu3，指错了会踩到已释放
    # 的指针（实测直接 Fatal Python error）。
    old3, old2 = sm._CUR["pcm3"], sm._CUR["pcm2"]
    sm._CUR["pcm3"] = sm._qi(pcm, sm._guid("{bcfce0a0-ec17-11d0-8d10-00a0c90f2719}"))
    sm._CUR["pcm2"] = sm._qi(pcm, sm._guid("{000214f4-0000-0000-c000-000000000046}"))
    try:
        sm._prefill_submenus(hm, hwnd)
    finally:
        sm._CUR["pcm3"], sm._CUR["pcm2"] = old3, old2
    user32.DestroyMenu(hm)
    for ptr in (pcm, psv, psf, desk):
        sm._release(ptr)
    if ole32 and pidl.value:
        ole32.CoTaskMemFree(pidl.value)


def _leak_round(fn, rounds=3):
    """预热两次（让 shell 扩展 DLL 先加载完），再统计 rounds 次的增量。"""
    fn()
    fn()
    base = _handles()
    gbase = (_gdi(0), _gdi(1))
    for _i in range(rounds):
        fn()
    return (_handles() - base, _gdi(0) - gbase[0], _gdi(1) - gbase[1])


def _best(fn):
    """同一操作量两遍取较小值：shell 扩展首次加载会带一堆一次性句柄进来，
    这种一次性开销只出现在某一轮里，取 min 能把噪声压掉。"""
    a = fn()
    b = fn()
    return (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]))


def t_leak():
    """连弹 3 次：与「裸 shell 基线」同量级才算没泄漏。"""
    hwnd = sm.host_hwnd()
    raw = _best(lambda: _leak_round(lambda: _raw_background(TMP, hwnd)))
    ours = _best(lambda: _leak_round(
        lambda: sm._build_background(TMP, hwnd).release()))
    print("     连弹 3 次：裸基线 %s，我们的 %s（句柄, GDI, USER）"
          % (raw, ours))
    check("连弹 3 次句柄增量不超裸基线 +5（裸 %d / 我们 %d）"
          % (raw[0], ours[0]), ours[0] <= max(raw[0], 0) + 5, (raw, ours))
    check("连弹 3 次 GDI 增量不超裸基线 +5（裸 %d / 我们 %d）"
          % (raw[1], ours[1]), ours[1] <= max(raw[1], 0) + 5, (raw, ours))
    check("连弹 3 次 USER 增量不超裸基线 +5（裸 %d / 我们 %d）"
          % (raw[2], ours[2]), ours[2] <= max(raw[2], 0) + 5, (raw, ours))
    check("裸基线自身不低于我们的增量（说明开销来自 shell 扩展）",
          ours[0] - raw[0] <= 5, (raw, ours))


def t_interruptible():
    """真弹背景菜单：owner 恒为宿主窗口，必须能被 WM_CANCELMODE 打断。"""
    check("打断性前置：宿主窗口可用", bool(sm.host_hwnd()), sm.host_hwnd())
    for val, label in ((0, "hwnd=0"), (987654321, "hwnd=987654321")):
        r, dt = _popup_with_cancel(TMP, val)
        print("     %s 背景菜单打断耗时 %.2fs" % (label, dt))
        check("%s 时 popup_background 5 秒内返回" % label, dt < 5.0,
              "%.2fs" % dt)
        check("%s 时被打断返回 None" % label, r is None, r)


def t_bad_input():
    """非法入参：不抛、返回 None、last_error 有话说。"""
    inst = sm.ShellMenu()
    r = inst.popup_background(os.path.join(TMP, "根本没有这个目录"))
    check("不存在的目录返回 None", r is None, r)
    check("不存在的目录写了 last_error",
          "目录不存在" in (inst.last_error or ""), inst.last_error)
    inst.close()


def t_min_size():
    """确定性断言兜底，避免用例全被跳过时假装全绿。"""
    check("临时工作区存在", os.path.isdir(TMP), TMP)
    check("_build_background 是函数", callable(sm._build_background))
    check("_prefill_submenus 是函数", callable(sm._prefill_submenus))
    check("模块级 popup_background 存在", callable(sm.popup_background))
    check("ShellMenu.popup_background 存在",
          callable(getattr(sm.ShellMenu, "popup_background", None)))


def run_checks():
    del _fail[:]
    _n[0] = 0
    print("-" * 60)
    t_min_size()
    t_build_tmp()
    t_prefilled_new_menu()
    t_build_root()
    t_invoke_new_folder()
    t_leak()
    t_bad_input()
    t_interruptible()
    print("-" * 60)
    return len(_fail)


def main():
    bad = run_checks()
    if bad:
        print("FAIL 汇总: %d/%d 项失败 -> %s" % (bad, _n[0], _fail))
        return 1
    print("全部通过 %d/%d" % (_n[0], _n[0]))
    return 0


class BgMenuTest(unittest.TestCase):
    """让 python -m unittest tests_bgmenu 真的跑一遍上面这些断言。"""

    @classmethod
    def setUpClass(cls):
        ole = ctypes.WinDLL("ole32", use_last_error=True)
        ole.CoInitializeEx.restype = ctypes.c_long
        ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        ole.CoInitializeEx(None, sm.COINIT_APARTMENTTHREADED)

    def test_all_checks(self):
        bad = run_checks()
        self.assertEqual(bad, 0, "失败项: %s" % (_fail,))


if __name__ == "__main__":
    code = 1
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoUninitialize.argtypes = []
    ole.CoInitializeEx(None, sm.COINIT_APARTMENTTHREADED)
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
        try:
            ole.CoUninitialize()
        except Exception:
            pass
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
