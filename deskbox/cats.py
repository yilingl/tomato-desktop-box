# -*- coding: utf-8 -*-
"""分类注册表：默认 8 个分类可增可删，账本落 %APPDATA%\\DeskBox\\cats.json。

可用环境变量 DESKBOX_CATS_JSON 覆盖路径（测试全靠它）。
文档结构 {"version": 1, "added": ["游戏", ...], "removed": ["图片", ...]}：
默认表永远在代码里，文件里只记"加了谁""删了谁"，所以默认表将来增项能自动生效。
任何函数都不抛异常，坏文件一律当空账本。
"""
import json
import os
import zlib

VERSION = 1
FALLBACK = "未分类"                      # 兜底分类：永远存在、永远排最后、不许删

# 中文分类名 -> 目录名。目录名只用于历史上那套真搬运代码与日志，保持不动。
DEFAULT_GROUPS = [
    ("应用", "apps"),
    ("压缩包", "archives"),
    ("文档", "docs"),
    ("图片", "images"),
    ("音视频", "media"),
    ("脚本", "scripts"),
    ("文件夹", "folders"),
    (FALLBACK, "_unsorted"),
]

BAD_CHARS = '\\/:*?"<>|'
RESERVED = ("全部", "系统应用")            # 不算分类，但也不许被占用


def json_path():
    """返回当前解析出的 cats.json 路径。"""
    try:
        p = os.environ.get("DESKBOX_CATS_JSON")
        if p:
            return p
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "DeskBox", "cats.json")
    except Exception:
        return "cats.json"


def _clean_list(value):
    """列表去噪：只要字符串、strip、去空、去重（保序）、踢掉兜底分类。"""
    out = []
    try:
        if not isinstance(value, (list, tuple)):
            return []
        for v in value:
            s = str(v if v is not None else "").strip()
            if not s or s == FALLBACK or s in out:
                continue
            out.append(s)
    except Exception:
        return []
    return out


def load():
    """读账本，永远返回 {"added": [...], "removed": [...]}。坏文件当空。"""
    try:
        with open(json_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {"added": [], "removed": []}
    if not isinstance(raw, dict):
        return {"added": [], "removed": []}
    return {"added": _clean_list(raw.get("added")),
            "removed": _clean_list(raw.get("removed"))}


def save(doc):
    """原子写账本；成功 True，任何失败 False 不抛。"""
    try:
        d = doc if isinstance(doc, dict) else {}
        out = {"version": VERSION,
               "added": _clean_list(d.get("added")),
               "removed": _clean_list(d.get("removed"))}
        p = json_path()
        parent = os.path.dirname(os.path.abspath(p))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def _dir_for(name):
    """自定义分类的目录名：按名字算哈希，重排 added 也不会变，同名永远同目录。"""
    return "_c_" + format(zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF, "08x")


def groups():
    """[(中文名, 目录名)]：默认表剔掉 removed，再追加 added，未分类永远最后一个。"""
    doc = load()
    removed = {s.casefold() for s in doc["removed"]}
    out = [(cn, d) for cn, d in DEFAULT_GROUPS
           if cn == FALLBACK or cn.casefold() not in removed]
    have = {cn.casefold() for cn, _ in out}
    for name in doc["added"]:
        if name.casefold() in have:
            continue
        out.append((name, _dir_for(name)))
        have.add(name.casefold())
    tail = [(cn, d) for cn, d in out if cn == FALLBACK]
    return [(cn, d) for cn, d in out if cn != FALLBACK] + tail


def names():
    """当前有效的分类名列表（未分类在最后）。"""
    return [cn for cn, _ in groups()]


def valid(name):
    """是不是当前有效的分类名（含未分类，不含"全部"）。大小写不敏感。"""
    s = str(name if name is not None else "").strip()
    if not s:
        return False
    low = s.casefold()
    return any(cn.casefold() == low for cn in names())


def dir_of(name):
    """中文名 -> 目录名，找不到返回 ""。"""
    s = str(name if name is not None else "").strip().casefold()
    if not s:
        return ""
    for cn, d in groups():
        if cn.casefold() == s:
            return d
    return ""


def name_of_dir(d):
    """目录名 -> 中文名，找不到返回 ""。"""
    s = str(d if d is not None else "").strip().casefold()
    if not s:
        return ""
    for cn, dn in groups():
        if dn.casefold() == s:
            return cn
    return ""


def check(name):
    """纯校验不落盘：合法返回 None，否则返回中文错误文案。add 与界面共用这一套。"""
    s = str(name if name is not None else "").strip()
    if not s:
        return "分类名不能为空"
    if len(s) > 12:
        return "分类名最多 12 个字"
    for ch in BAD_CHARS:
        if ch in s:
            return '分类名不能包含 \\ / : * ? " < > |'
    low = s.casefold()
    if (any(r.casefold() == low for r in RESERVED) or
            any(cn.casefold() == low for cn in names())):
        return "已有同名分类"
    return None


def add(name):
    """新增分类；(ok, err)。被删过的分类会被恢复而不是重复添加。"""
    s = str(name if name is not None else "").strip()
    bad = check(s)
    if bad:
        return False, bad
    low = s.casefold()
    doc = load()
    for old in doc["removed"]:
        if old.casefold() == low:
            doc["removed"] = [x for x in doc["removed"]
                              if x.casefold() != low]
            return (True, "") if save(doc) else (False, "保存失败")
    doc["added"] = doc["added"] + [s]
    return (True, "") if save(doc) else (False, "保存失败")


def remove(name):
    """删除分类；(ok, err)。兜底分类与未知分类都会被拒。"""
    s = str(name if name is not None else "").strip()
    cn = s if valid(s) else name_of_dir(s)
    if not cn:
        return False, "没有这个分类"
    if cn == FALLBACK:
        return False, "「未分类」是兜底分类，不能删除"
    doc = load()
    kept = [x for x in doc["added"] if x.casefold() != cn.casefold()]
    if len(kept) != len(doc["added"]):
        doc["added"] = kept
    else:
        if any(x.casefold() == cn.casefold() for x in doc["removed"]):
            return True, ""
        doc["removed"] = doc["removed"] + [cn]
    return (True, "") if save(doc) else (False, "保存失败")
