# -*- coding: utf-8 -*-
"""虚拟分组账本：只记「哪个文件属于哪个分类」，文件系统零改动。

存储位置 %APPDATA%\\DeskBox\\groups.json，可用环境变量 DESKBOX_GROUPS_JSON 覆盖。
与 store.py 的 ingest 去重键保持一致：os.path.normcase(os.path.abspath(p))。
"""
import json
import os
from datetime import datetime

try:
    from . import store                # 作为 deskbox.groupbook 导入
except ImportError:
    import store                       # 直接跑脚本时兜底

VERSION = 1


def groups_path():
    """返回当前解析出的 groups.json 路径。"""
    try:
        p = os.environ.get("DESKBOX_GROUPS_JSON")
        if p:
            return p
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "DeskBox", "groups.json")
    except Exception:
        return "groups.json"


def key_of(path):
    """归一化键：normcase(abspath)，Windows 下大小写不敏感。"""
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except Exception:
        return str(path)


def load():
    """读账本，返回 {normkey: {"group": str, "at": str}}；坏了一律 {}。"""
    try:
        with open(groups_path(), "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return {}
    try:
        items = doc.get("items") if isinstance(doc, dict) else None
        if not isinstance(items, dict):
            return {}
        out = {}
        for k, v in items.items():
            if not isinstance(v, dict):
                continue
            g = v.get("group")
            if not isinstance(g, str) or not g:
                continue
            out[str(k)] = {"group": g, "at": str(v.get("at") or "")}
        return out
    except Exception:
        return {}


def save(data):
    """原子写：先写 path + ".tmp" 再 os.replace；失败返回 False 不抛。"""
    p = groups_path()
    tmp = p + ".tmp"
    try:
        doc = {"version": VERSION, "items": dict(data or {})}
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False


def get_group(path):
    """账本里记的分类；没有返回 None。"""
    rec = load().get(key_of(path))
    return rec["group"] if rec else None


def set_group(path, group):
    """写一条分组。group 可传中文名或目录名；非法分类返回 False。"""
    g = store._norm_group(group)
    if not g:
        return False
    data = load()
    data[key_of(path)] = {"group": g, "at": _now()}
    return save(data)


def set_groups(mapping):
    """批量写 {path: group}，一次落盘；返回成功条数。"""
    if not isinstance(mapping, dict):
        return 0
    data = load()
    n = 0
    for p, g in mapping.items():
        cn = store._norm_group(g)
        if not cn:
            continue
        data[key_of(p)] = {"group": cn, "at": _now()}
        n += 1
    if n:
        save(data)
    return n


def clear_group(path):
    """移除该条；本来就没有返回 False。"""
    data = load()
    k = key_of(path)
    if k not in data:
        return False
    del data[k]
    return save(data)


def move_category(old, to="未分类"):
    """把账本里 group == old 的条目全部改成 to，一次落盘；返回改动条数。

    「at」保持原值不动——移动分类不该让文件的加入时间凭空变新。
    to 非法、old == to、old 不是分类名都返回 0。
    """
    b = store._norm_group(to)
    if not b:
        return 0
    # 源分类可能刚被 cats.remove 删掉，此时 _norm_group 已经不认它，
    # 所以源只做 strip 后按字面匹配，不校验有效性。
    a = store._norm_group(old) or str(old if old is not None else "").strip()
    if not a or a == b:
        return 0
    data = load()
    n = 0
    for rec in data.values():
        if rec.get("group") == a:
            rec["group"] = b
            n += 1
    if n:
        save(data)
    return n


def auto_group(paths, only_unset=True, classify=None):
    """一键整理：按分类写入账本，返回写入条数。only_unset 时不覆盖已有分组。"""
    fn = classify or store.classify
    data = load()
    n = 0
    for p in paths or []:
        k = key_of(p)
        if only_unset and k in data:
            continue
        try:
            g = fn(p)
        except Exception:
            continue
        cn = store._norm_group(g)
        if not cn:
            continue
        data[k] = {"group": cn, "at": _now()}
        n += 1
    if n:
        save(data)
    return n


def assign_of(items):
    """给一批 store.Item 回填 group：账本优先，没有就用 classify。"""
    data = load()
    out = {}
    for it in items or []:
        p = getattr(it, "path", it)
        rec = data.get(key_of(p))
        if rec:
            out[p] = rec["group"]
            continue
        g = getattr(it, "group", None)
        out[p] = g or store.classify(p)
    return out


def prune(known_paths):
    """删掉账本里不在 known_paths 的条目；返回删除条数。绝不自己遍历磁盘。"""
    try:
        known = set(key_of(p) for p in (known_paths or ()))
    except Exception:
        known = set()
    data = load()
    dead = [k for k in data if k not in known]
    if not dead:
        return 0
    for k in dead:
        del data[k]
    save(data)
    return len(dead)


def stats():
    """{"total": 条数, "by_group": {分类: 数量}}。"""
    data = load()
    by = {}
    for rec in data.values():
        g = rec["group"]
        by[g] = by.get(g, 0) + 1
    return {"total": len(data), "by_group": by}


def _now():
    return datetime.now().isoformat(timespec="seconds")
