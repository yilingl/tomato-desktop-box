# -*- coding: utf-8 -*-
"""Tomato Desktop Box 数据层：扫描桌面、分类、搬运、清单、回滚、回收站。"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime

try:
    from . import cats                 # 作为 deskbox.store 导入
except ImportError:
    import cats                        # 直接跑脚本时兜底
try:
    from . import sysitems             # 系统项（此电脑/回收站 这类 CLSID）
except ImportError:
    import sysitems

# 收纳根目录（测试用 DESKBOX_ROOT 覆盖）
ROOT = os.environ.get("DESKBOX_ROOT") or os.path.join(
    os.path.expanduser("~"), "DeskBox")

# 中文分类名 -> 收纳目录名，顺序固定，不含"全部"
GROUPS = [
    ("应用", "apps"),
    ("压缩包", "archives"),
    ("文档", "docs"),
    ("图片", "images"),
    ("音视频", "media"),
    ("脚本", "scripts"),
    ("文件夹", "folders"),
    ("未分类", "_unsorted"),
]

_GROUP_DIR = dict(GROUPS)
_DIR_GROUP = {d: c for c, d in GROUPS}


def _is_virtual(path):
    """是不是 ::{CLSID} 这类系统虚拟项（不是磁盘文件）。"""
    try:
        return sysitems.is_virtual(path)
    except Exception:
        return False

# 桌面来源，按扫描顺序。约定【最后一项】是公共桌面（只读，不搬）。
DESK_DIRS = [
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.environ.get("PUBLIC") or
                 os.path.join(os.environ.get("SystemDrive", "C:"),
                              "Users", "Public"), "Desktop"),
]

EXT_MAP = {
    "应用": (".lnk", ".exe", ".url", ".appref-ms", ".msi"),
    "压缩包": (".zip", ".rar", ".7z", ".tgz", ".gz", ".tar", ".xz", ".bz2"),
    "文档": (".txt", ".md", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
             ".ppt", ".pptx", ".ini", ".csv", ".json", ".log"),
    "图片": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
             ".svg", ".psd"),
    "音视频": (".mp4", ".mkv", ".mp3", ".wav", ".avi", ".flac", ".mov",
               ".webm", ".m4a"),
    "脚本": (".bat", ".cmd", ".ps1", ".py", ".ahk", ".js", ".vbs", ".reg"),
}
_EXT_GROUP = {e: g for g, exts in EXT_MAP.items() for e in exts}

LOCKED_EXTS = (".efi", ".bin", ".ini", ".bat", ".ahk", ".vbs", ".reg")

MANIFEST_VERSION = 1


# ---------------------------------------------------------------- 数据模型
@dataclass
class Item:
    path: str
    name: str
    group: str
    is_dir: bool = False
    size: int = 0
    from_dir: str = ""
    locked: bool = False


@dataclass
class Move:
    src: str
    dst: str
    group: str
    reason: str = ""
    from_dir: str = ""
    size: int = 0


@dataclass
class Plan:
    moves: list = field(default_factory=list)

    @property
    def todo(self):
        return [m for m in self.moves if not m.reason]

    @property
    def skipped(self):
        return [m for m in self.moves if m.reason]

    @property
    def by_group(self):
        d = {}
        for cn, _ in GROUPS:
            n = sum(1 for m in self.todo if m.group == cn)
            if n:
                d[cn] = n
        return d


@dataclass
class Report:
    done: list = field(default_factory=list)
    failed: list = field(default_factory=list)

    @property
    def ok(self):
        return len(self.done)


# ---------------------------------------------------------------- 内部工具
def log_dir():
    """日志目录 %APPDATA%/DeskBox，与 ui.json / groups.json 同一套推导。"""
    try:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "DeskBox")
    except Exception:
        return ""


def _log(msg):
    """异常与关键动作写 %APPDATA%/DeskBox/deskbox.log，写不进去就算了。

    日志不落 ROOT：ROOT 是旧版「真搬文件」的收纳根目录，新版完全不搬文件，
    为了写一行日志去 makedirs 它，会把本该不存在的历史遗留目录复活。
    """
    try:
        d = log_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "deskbox.log"), "a",
                  encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now().isoformat(timespec="seconds"),
                                 msg))
    except Exception:
        pass


def _is_public(d):
    """DESK_DIRS 的最后一项视为公共桌面。"""
    if not d or len(DESK_DIRS) < 2:
        return False
    return os.path.normcase(os.path.abspath(d)) == \
        os.path.normcase(os.path.abspath(DESK_DIRS[-1]))


def _lock_reason(path, from_dir):
    """locked 原因；空串表示可以搬。"""
    if _is_public(from_dir):
        return "公共桌面只读"
    if os.path.splitext(os.path.basename(path))[1].lower() in LOCKED_EXTS:
        return "系统/脚本文件保留原位"
    return ""


def _free_name(path):
    """同名冲突：在扩展名前插 " (2)"、" (3)"；无扩展名直接后缀。"""
    if not os.path.exists(path):
        return path
    d, name = os.path.split(path)
    base, ext = os.path.splitext(name)
    if not ext:
        base, ext = name, ""
    i = 2
    while True:
        cand = os.path.join(d, "%s (%d)%s" % (base, i, ext))
        if not os.path.exists(cand):
            return cand
        i += 1


def _isdir(path):
    try:
        return os.path.isdir(path)
    except Exception:
        return False


def _size_of(path, is_dir):
    if is_dir:
        return 0          # 契约：目录 size 记 0
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def _norm_group(name):
    """中文分类名或目录名 -> 中文分类名；非法返回 None（分类表由 cats 决定）。"""
    s = str(name if name is not None else "").strip()
    if not s:
        return None
    if cats.valid(s):
        return s
    for cn, d in cats.groups():
        if s.casefold() == str(d).casefold():
            return cn
    return None


def _root_group(path):
    """path 若直接位于 ROOT/<分类目录> 下，返回其中文分类名；否则 None。"""
    try:
        parent = os.path.dirname(os.path.abspath(str(path)))
        if os.path.normcase(os.path.dirname(parent)) != \
                os.path.normcase(os.path.abspath(ROOT)):
            return None
        return _DIR_GROUP.get(os.path.basename(parent))
    except Exception:
        return None


def _record(src, dst, group, from_dir, size):
    """追加一条 manifest 记录并立即原子落盘（与 execute 逐项落盘同一套）。"""
    recs = manifest_all()
    recs.append({
        "orig": src,
        "now": dst,
        "group": group,
        "from": from_dir,
        "moved_at": datetime.now().isoformat(timespec="seconds"),
        "size": size,
    })
    _write_manifest(recs)


def _repoint(old, new, group):
    """把 manifest 里指向 old 的记录改指 new 并更新 group，按 now/orig 匹配。"""
    recs = manifest_all()
    for rec in reversed(recs):
        if rec.get("now") == old or rec.get("orig") == old:
            rec["now"] = new
            rec["group"] = group
            _write_manifest(recs)
            return True
    return False


# ---------------------------------------------------------------- 分类与扫描
def classify(path):
    """返回中文分类名。目录优先判为"文件夹"；分类被删掉的文件落"未分类"。"""
    try:
        if _is_virtual(path):
            return sysitems.CATEGORY
        if _isdir(path):
            return "文件夹"
        ext = os.path.splitext(os.path.basename(str(path)))[1].lower()
        g = _EXT_GROUP.get(ext, cats.FALLBACK)
        return g if cats.valid(g) else cats.FALLBACK
    except Exception:
        return cats.FALLBACK


def all_groups():
    """当前有效分类表 [(中文名, 目录名)]，来自 cats 注册表（默认在前、未分类最后）。"""
    return cats.groups()


def scan():
    """扫两个桌面目录，返回全部 Item，按 name（casefold）排序。"""
    items = []
    for d in list(DESK_DIRS):
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for name in names:
            if name.startswith(".") or name.casefold() == "desktop.ini":
                continue
            p = os.path.join(d, name)
            is_dir = _isdir(p)
            items.append(Item(
                path=p, name=name, group=classify(p), is_dir=is_dir,
                size=_size_of(p, is_dir), from_dir=d,
                locked=bool(_lock_reason(p, d))))
    items.sort(key=lambda it: it.name.casefold())
    return items


def scan_group(group):
    """读 ROOT/<group 目录名> 下的已收纳内容。"""
    out = []
    try:
        d = _GROUP_DIR.get(group, group)
        base = os.path.join(ROOT, d)
        names = os.listdir(base)
    except Exception:
        return out
    cn = _DIR_GROUP.get(d, group)
    for name in names:
        p = os.path.join(base, name)
        is_dir = _isdir(p)
        out.append(Item(path=p, name=name, group=cn, is_dir=is_dir,
                        size=_size_of(p, is_dir), from_dir="", locked=False))
    out.sort(key=lambda it: it.name.casefold())
    return out


# ---------------------------------------------------------------- 计划与执行
def plan(items=None):
    """生成搬运计划，不碰磁盘。"""
    if items is None:
        items = scan()
    p = Plan()
    for it in items:
        d = os.path.join(ROOT, _GROUP_DIR.get(it.group, "_unsorted"))
        reason = _lock_reason(it.path, it.from_dir) if it.locked else ""
        p.moves.append(Move(
            src=it.path, dst=os.path.join(d, it.name), group=it.group,
            reason=reason, from_dir=it.from_dir, size=it.size))
    return p


def ensure_dirs():
    """在 ROOT 下创建全部分类子目录。"""
    for _, d in GROUPS:
        try:
            os.makedirs(os.path.join(ROOT, d), exist_ok=True)
        except Exception as e:
            _log("ensure_dirs %s: %s" % (d, e))


def manifest_path():
    return os.path.join(ROOT, "manifest.json")


def manifest_all():
    """记录列表，最新在后；缺失或损坏返回空表。"""
    try:
        with open(manifest_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        recs = data.get("records")
        return recs if isinstance(recs, list) else []
    except Exception:
        return []


def _write_manifest(records):
    """原子写：先写 .tmp 再 os.replace。"""
    p = manifest_path()
    try:
        os.makedirs(ROOT, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": MANIFEST_VERSION, "records": records},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        _log("write manifest: %s" % e)


def execute(pl):
    """真搬。每成功一项立刻写 manifest。"""
    rep = Report()
    ensure_dirs()
    if pl is None:
        return rep
    recs = manifest_all()
    for mv in pl.todo:
        try:
            dst = _free_name(mv.dst)
            shutil.move(mv.src, dst)
            recs.append({
                "orig": mv.src,
                "now": dst,
                "group": mv.group,
                "from": mv.from_dir,
                "moved_at": datetime.now().isoformat(timespec="seconds"),
                "size": mv.size,
            })
            _write_manifest(recs)
            rep.done.append((mv.src, dst))
        except Exception as e:
            _log("execute %s: %s" % (mv.src, e))
            rep.failed.append((mv.src, str(e)))
    return rep


def undo(n=0):
    """按 manifest 倒序搬回原位置；n>0 只回滚最近 n 条。"""
    rep = Report()
    recs = manifest_all()
    remain = list(range(len(recs)))
    order = remain if n <= 0 else remain[-n:]
    for i in reversed(order):
        rec = recs[i]
        now = rec.get("now") or ""
        orig = rec.get("orig") or ""
        if not now or not os.path.exists(now):
            rep.failed.append((now, "源已不存在"))
            continue
        try:
            d = os.path.dirname(orig)
            if d:
                os.makedirs(d, exist_ok=True)
            dst = _free_name(orig)
            shutil.move(now, dst)
            remain.remove(i)
            _write_manifest([recs[j] for j in remain])
            rep.done.append((now, dst))
        except Exception as e:
            _log("undo %s: %s" % (now, e))
            rep.failed.append((now, str(e)))
    return rep


# ---------------------------------------------------------------- 手动改分类
def set_group(path, group):
    """把一项改到指定分类。返回 (ok, new_path, err)。

    ① 文件还在桌面 -> 搬进 ROOT/<目标目录> 并记 manifest
    ② 已在 ROOT/<别的目录> -> rename 到目标目录，同步更新 manifest 的 now/group
    ③ 目标位置同名 -> _free_name 改名，绝不覆盖
    """
    cn = _norm_group(group)
    if cn is None:
        return False, "", "非法的分类: %s" % group
    p = os.path.abspath(str(path or ""))
    if not os.path.exists(p):
        return False, "", "路径不存在: %s" % p
    try:
        ensure_dirs()
        src_group = _root_group(p)
        if src_group == cn:
            # 已经在这个分类里了：不搬、不改名、不记一条空记录
            return True, p, ""
        dest_dir = os.path.join(ROOT, _GROUP_DIR[cn])
        os.makedirs(dest_dir, exist_ok=True)
        dst = _free_name(os.path.join(dest_dir, os.path.basename(p)))
        size = _size_of(p, _isdir(p))
        from_dir = os.path.dirname(p)
        shutil.move(p, dst)
        if src_group is not None:
            # 已收纳项：优先改已有记录的指向；没有记录就按移动前路径补一条
            if not _repoint(p, dst, cn):
                _record(p, dst, cn, from_dir, size)
        else:
            _record(p, dst, cn, from_dir, size)
        return True, dst, ""
    except OSError as e:
        _log("set_group %s -> %s: %s" % (p, group, e))
        return False, "", "移动失败: %s" % e
    except Exception as e:
        _log("set_group %s -> %s: %s" % (p, group, e))
        return False, "", "移动失败: %s" % e


# ---------------------------------------------------------------- 拖入收纳
def ingest(paths, group=None):
    """把一批路径直接收进 Tomato Desktop Box。不查 _lock_reason（用户显式拖入即同意）。

    group=None 按 classify 自动归类；给值则强制进该分类。路径去重。
    """
    rep = Report()
    cn_force = None
    if group is not None:
        cn_force = _norm_group(group)
        if cn_force is None:
            for p in paths or []:
                rep.failed.append((str(p), "非法的分类: %s" % group))
            return rep
    seen = set()
    ensure_dirs()
    for raw in paths or []:
        try:
            p = os.path.abspath(str(raw))
        except Exception:
            rep.failed.append((str(raw), "路径非法"))
            continue
        key = os.path.normcase(p)
        if key in seen:
            continue
        seen.add(key)
        if _root_group(p) is not None:
            # 已经在收纳目录里：重复拖入不再搬（这正是「鲁大师 (2)」的来源）
            continue
        name = os.path.basename(p)
        if name.startswith(".") or name.casefold() == "desktop.ini":
            rep.failed.append((p, "desktop.ini 与隐藏项不收纳"))
            continue
        if not os.path.exists(p):
            rep.failed.append((p, "路径不存在: %s" % p))
            continue
        cn = cn_force or classify(p)
        try:
            dest_dir = os.path.join(ROOT, _GROUP_DIR.get(cn, "_unsorted"))
            os.makedirs(dest_dir, exist_ok=True)
            dst = _free_name(os.path.join(dest_dir, name))
            size = _size_of(p, _isdir(p))
            shutil.move(p, dst)
            _record(p, dst, cn, os.path.dirname(p), size)
            rep.done.append((p, dst))
        except OSError as e:
            _log("ingest %s: %s" % (p, e))
            rep.failed.append((p, "移动失败: %s" % e))
        except Exception as e:
            _log("ingest %s: %s" % (p, e))
            rep.failed.append((p, "移动失败: %s" % e))
    return rep


# ---------------------------------------------------------------- 回收站
FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def delete_to_recycle(paths):
    """送回收站，绝不 os.remove。返回 (成功数, 失败项列表)。"""
    if not sys.platform.startswith("win"):
        return 0, [str(p) for p in (paths or [])]
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    except Exception as e:
        _log("load shell32: %s" % e)
        return 0, [str(p) for p in (paths or [])]
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    shell32.SHFileOperationW.restype = ctypes.c_int
    ok, bad = 0, []
    for p in paths or []:
        if _is_virtual(p):
            bad.append(str(p))      # 系统项不能删，直接拒绝
            continue
        p = os.path.abspath(str(p))
        if not os.path.exists(p):
            bad.append(p)
            continue
        # pFrom 必须是双 null 结尾的路径串；c_wchar_p 会保住整段缓冲
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = p + "\0\0"
        op.pTo = None
        op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT |
                     FOF_NOERRORUI)
        try:
            ret = shell32.SHFileOperationW(ctypes.byref(op))
        except Exception as e:
            _log("SHFileOperationW %s: %s" % (p, e))
            bad.append(p)
            continue
        if ret != 0 or op.fAnyOperationsAborted or os.path.exists(p):
            _log("recycle %s: ret=%s aborted=%s" %
                 (p, ret, op.fAnyOperationsAborted))
            bad.append(p)
        else:
            ok += 1
    return ok, bad


# ---------------------------------------------------------------- 重命名
# shell 自己的 rename 动词要 Explorer 的文件夹视图，裸 IContextMenu 宿主动用必
# E_NOTIMPL(0x80004001)；IFileOperation::RenameItem 才是能用的一条路。
CLSID_FILE_OPERATION = "{3ad05575-8857-4850-9277-11b85bdb8e09}"
IID_FILE_OPERATION = "{947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8}"
IID_SHELL_ITEM = "{43826d1e-e718-42ee-bc55-a1e261c37bfe}"
CLSCTX_INPROC_SERVER = 1
COINIT_APARTMENTTHREADED = 0x2
S_OK = 0
S_FALSE = 1                 # 线程已经是 STA，本次调用没加引用计数
RPC_E_CHANGED_MODE = 0x80010106
FOF_RENAME_FLAGS = 0x0004 | 0x0040     # 静默 + 不确认
BAD_NAME_CHARS = '\\/:*?"<>|'


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _guid(s):
    u = uuid.UUID(s)
    return GUID(u.time_low, u.time_mid, u.time_hi_version,
                (ctypes.c_ubyte * 8)(*u.bytes[8:]))


def _vfn(ptr, idx, restype, *argtypes):
    """取 COM 虚表第 idx 项并包成可调用函数（与 shellmenu 里同款，但不互相 import）。"""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p,
                              *argtypes)(vtbl[idx])


def _release(ptr):
    """IUnknown::Release，ptr 是 c_void_p。"""
    try:
        if ptr and ptr.value:
            _vfn(ptr, 2, ctypes.c_ulong)(ptr)
    except Exception:
        pass


def _co_start(ole32):
    """调 CoInitializeEx，返回 (ok, need_uninit, hr)。

    need_uninit 的语义是「本次调用要不要配对 CoUninitialize」：
      S_OK               这次真的初始化了  -> True
      S_FALSE            线程本来就是 STA，本次没加引用计数 -> False
      RPC_E_CHANGED_MODE 别的单元模式，同样没加计数       -> False
    后两种都不能判失败。Qt 的 QApplication 已把主线程设成 STA，再调一次
    CoInitializeEx 拿到的是 S_FALSE(1)，把 1 当失败就是 GUI 下必挂的原因。
    hr 按 32 位无符号比，否则 RPC_E_CHANGED_MODE 作为 c_int 是负数、永远匹配不上。
    """
    hr = int(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)) & 0xFFFFFFFF
    if hr == S_OK:
        return True, True, hr
    if hr in (S_FALSE, RPC_E_CHANGED_MODE):
        return True, False, hr
    return False, False, hr


def rename(path, new_name):
    """把 path 改名为同目录下的 new_name。返回 (ok, err)，err 为空串表示成功。

    不移动目录、不改变所在位置。系统虚拟项（::CLSID）一律拒绝。
    走 IFileOperation::RenameItem：RenameItem 与 PerformOperations 两个 hr 都要看
    —— 非法字符这类失败编码在 PerformOperations 里，RenameItem 会返回 0。
    """
    if _is_virtual(path):
        return False, "系统图标不能重命名"
    path = os.path.abspath(str(path))
    if not os.path.exists(path):
        return False, "文件已经不在了"
    name = str(new_name or "").strip()
    if not name:
        return False, "名字不能为空"
    if any(c in BAD_NAME_CHARS for c in name) or any(ord(c) < 32 for c in name):
        return False, '名字里不能包含 \\ / : * ? " < > |'
    if len(name) > 254:
        return False, "名字太长"
    old = os.path.basename(path)
    if os.path.normcase(name) == os.path.normcase(old):
        return True, ""                  # 没变化，当成功，别去动文件
    dst = os.path.join(os.path.dirname(path), name)
    if os.path.normcase(dst) != os.path.normcase(path) and os.path.exists(dst):
        return False, "已经有同名文件了"
    if not sys.platform.startswith("win"):
        return False, "非 Windows 环境"

    try:
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    except Exception as e:
        _log("rename load dll: %s" % e)
        return False, "系统组件加载失败"
    shell32.SHCreateItemFromParsingName.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long

    co = False
    pfo = ctypes.c_void_p()
    psi = ctypes.c_void_p()
    try:
        ok_co, co, hr = _co_start(ole32)
        if not ok_co:
            _log("rename CoInitializeEx %s: hr=0x%08X" % (path, hr))
            return False, "重命名失败: hr=0x%08X" % hr
        clsid = _guid(CLSID_FILE_OPERATION)
        iid_fo = _guid(IID_FILE_OPERATION)
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None,
                                    CLSCTX_INPROC_SERVER,
                                    ctypes.byref(iid_fo), ctypes.byref(pfo))
        if hr != 0 or not pfo.value:
            _log("rename CoCreateInstance %s: hr=0x%08X" %
                 (path, hr & 0xFFFFFFFF))
            return False, "重命名失败: hr=0x%08X" % (hr & 0xFFFFFFFF)
        iid_si = _guid(IID_SHELL_ITEM)
        hr = shell32.SHCreateItemFromParsingName(
            path, None, ctypes.byref(iid_si), ctypes.byref(psi))
        if hr != 0 or not psi.value:
            _log("rename SHCreateItemFromParsingName %s: hr=0x%08X" %
                 (path, hr & 0xFFFFFFFF))
            return False, "重命名失败: hr=0x%08X" % (hr & 0xFFFFFFFF)
        _vfn(pfo, 5, ctypes.c_long, wintypes.DWORD)(pfo, FOF_RENAME_FLAGS)
        hr = _vfn(pfo, 12, ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR,
                  ctypes.c_void_p)(pfo, psi, name, None)
        if hr != 0:
            _log("rename RenameItem %s -> %s: hr=0x%08X" %
                 (path, name, hr & 0xFFFFFFFF))
            return False, "重命名失败: hr=0x%08X" % (hr & 0xFFFFFFFF)
        hr = _vfn(pfo, 21, ctypes.c_long)(pfo)
        if hr != 0:
            _log("rename PerformOperations %s -> %s: hr=0x%08X" %
                 (path, name, hr & 0xFFFFFFFF))
            return False, "重命名失败: hr=0x%08X" % (hr & 0xFFFFFFFF)
    except Exception as e:
        _log("rename %s: %s" % (path, e))
        return False, "重命名失败: %s" % e
    finally:
        _release(psi)
        _release(pfo)
        if co:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass
    if not os.path.exists(dst):
        _log("rename not applied: %s -> %s" % (path, dst))
        return False, "重命名没有生效"
    return True, ""


# ---------------------------------------------------------------- 打开
def open_item(path):
    try:
        os.startfile(str(path))     # 虚拟项也走这条路：系统会自己解析 CLSID
    except Exception as e:
        _log("open_item %s: %s" % (path, e))


def reveal(path):
    if _is_virtual(path):
        # explorer /select 对 CLSID 无效，直接打开它
        return open_item(path)
    try:
        subprocess.Popen(["explorer", "/select,", str(path)])
    except Exception as e:
        _log("reveal %s: %s" % (path, e))
