# -*- coding: utf-8 -*-
"""Tomato Desktop Box 小窗口数据层：小窗口清单只登记路径，不移动、不复制、不删除任何文件。

持久化 %APPDATA%/DeskBox/minis.json（DESKBOX_MINIS_JSON 可覆盖，测试用）。
只依赖标准库 json / os / time / random，不 import store 等兄弟模块（避免循环依赖）。
所有写操作失败一律返回 False / 0，绝不抛。
"""
import json
import os
import random
import time

VERSION = 1

DEFAULT_W = 340
DEFAULT_H = 280
MIN_W = 200
MIN_H = 160

# 屏幕可用区参考值：不查系统（本模块不 import Qt，也不碰 Win32），
# 只用来给未指定坐标的新窗口算一个居中的落点。
SCREEN_W = 1920
SCREEN_H = 1040
STEP = 28                       # 每个新窗口相对上一次的偏移

UPDATE_FIELDS = ("title", "x", "y", "w", "h", "pinned", "open")


# ---------------------------------------------------------------- 路径与类型
def json_path():
    """返回 minis.json 的完整路径。"""
    try:
        p = os.environ.get("DESKBOX_MINIS_JSON")
        if p:
            return p
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "DeskBox", "minis.json")
    except Exception:
        return "minis.json"


def _key(path):
    """去重键：normcase(abspath)，Windows 下大小写不敏感。"""
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except Exception:
        return str(path)


def _as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        pass
    try:
        return int(float(value))
    except Exception:
        return default


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def _clean_paths(value):
    """只留非空字符串，按 normcase 去重，保序。"""
    out = []
    if not isinstance(value, (list, tuple)):
        return out
    have = set()
    for p in value:
        if not isinstance(p, str) or not p.strip():
            continue
        k = _key(p)
        if k in have:
            continue
        have.add(k)
        out.append(p)
    return out


def _new_id(taken=()):
    """w + 毫秒时间戳 + 三位十六进制；撞了就重抽。"""
    while True:
        wid = "w%d%03x" % (int(time.time() * 1000), random.randrange(0x1000))
        if wid not in taken:
            return wid


def _norm(raw):
    """把一条记录规整成契约形状：缺字段补默认，坏类型就地转，尺寸夹到下限。"""
    d = raw if isinstance(raw, dict) else {}
    wid = d.get("id")
    if not isinstance(wid, str) or not wid.strip():
        wid = _new_id()
    title = d.get("title")
    return {
        "id": wid,
        "title": title if isinstance(title, str) else "",
        "x": _as_int(d.get("x"), 0),
        "y": _as_int(d.get("y"), 0),
        "w": max(MIN_W, _as_int(d.get("w"), DEFAULT_W)),
        "h": max(MIN_H, _as_int(d.get("h"), DEFAULT_H)),
        "pinned": _as_bool(d.get("pinned"), True),
        "open": _as_bool(d.get("open"), True),
        "paths": _clean_paths(d.get("paths")),
    }


def _norm_doc(data):
    """整个文档规整成 {"version":1,"windows":[...]}。"""
    doc = data if isinstance(data, dict) else {}
    raw = doc.get("windows")
    out = []
    seen = set()
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, dict):
                continue
            win = _norm(item)
            if win["id"] in seen:          # 文件里被手改成重复 id 时重抽
                win["id"] = _new_id(seen)
            seen.add(win["id"])
            out.append(win)
    return {"version": VERSION, "windows": out}


# ---------------------------------------------------------------- 读写
def load():
    """永远返回 {"version":1,"windows":[...]}；文件缺失/坏/结构不对都是空表。"""
    empty = {"version": VERSION, "windows": []}
    try:
        with open(json_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    if not isinstance(data.get("windows"), list):
        return empty
    return _norm_doc(data)


def save(data):
    """原子写：先 .tmp 再 os.replace；失败返回 False 不抛。"""
    try:
        p = json_path()
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_norm_doc(data), f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def windows():
    """规整后的窗口列表，顺序＝文件顺序。"""
    return load()["windows"]


def get(wid):
    """单个窗口的规整副本；没有返回 None。"""
    for win in windows():
        if win["id"] == wid:
            return win
    return None


def _find(doc, wid):
    for win in doc["windows"]:
        if win["id"] == wid:
            return win
    return None


# ---------------------------------------------------------------- 增删改
def create(title="", x=None, y=None, w=DEFAULT_W, h=DEFAULT_H):
    """新建一个窗口并落盘，返回该窗口 dict。新窗口默认不固定位置（pinned=False）。"""
    doc = load()
    ws = doc["windows"]
    width = max(MIN_W, _as_int(w, DEFAULT_W))
    height = max(MIN_H, _as_int(h, DEFAULT_H))
    n = len(ws) % 8
    if x is None:
        x = (SCREEN_W - width) // 2 + n * STEP
    if y is None:
        y = (SCREEN_H - height) // 2 + n * STEP
    win = _norm({
        "id": _new_id({v["id"] for v in ws}),
        "title": title if isinstance(title, str) else str(title),
        "x": _as_int(x, 0),
        "y": _as_int(y, 0),
        "w": width,
        "h": height,
        "pinned": False,          # 新窗口默认不固定：顶栏直接能拖
        "open": True,
        "paths": [],
    })
    ws.append(win)
    save(doc)
    return win


def update(wid, **fields):
    """只认白名单字段；w/h 夹到下限；窗口不存在返回 False。"""
    doc = load()
    win = _find(doc, wid)
    if win is None:
        return False
    for k, v in fields.items():
        if k not in UPDATE_FIELDS:
            continue
        if k == "title":
            win["title"] = v if isinstance(v, str) else str(v)
        elif k in ("x", "y"):
            win[k] = _as_int(v, win[k])
        elif k == "w":
            win["w"] = max(MIN_W, _as_int(v, win["w"]))
        elif k == "h":
            win["h"] = max(MIN_H, _as_int(v, win["h"]))
        else:                                   # pinned / open
            win[k] = _as_bool(v, win[k])
    return save(doc)


def remove(wid):
    """真删这一条（UI 的「删除这个小窗口」）。"""
    doc = load()
    keep = [v for v in doc["windows"] if v["id"] != wid]
    if len(keep) == len(doc["windows"]):
        return False
    doc["windows"] = keep
    return save(doc)


# ---------------------------------------------------------------- 图标清单
def _reject(path):
    """返回跳过原因；空串表示可以加。"""
    if not isinstance(path, str):
        return "不是字符串"
    if not path.strip():
        return "空路径"
    name = os.path.basename(path.rstrip("\\/"))
    if name.casefold() == "desktop.ini":
        return "desktop.ini"
    if name.startswith("."):
        return "点开头"
    if not os.path.exists(path):
        return "不存在"
    return ""


def add_paths(wid, paths):
    """追加到末尾，返回 (新加了几条, 被跳过的原始输入列表)。"""
    try:
        items = list(paths)
    except Exception:
        return 0, []
    doc = load()
    win = _find(doc, wid)
    if win is None:
        return 0, list(items)
    have = {_key(p) for p in win["paths"]}
    added = 0
    skipped = []
    for p in items:
        if _reject(p):
            skipped.append(p)
            continue
        k = _key(p)
        if k in have:
            skipped.append(p)
            continue
        have.add(k)
        win["paths"].append(p)
        added += 1
    if added:
        save(doc)
    return added, skipped


def set_paths(wid, paths):
    """整体替换，返回最终条数。"""
    try:
        items = list(paths)
    except Exception:
        return 0
    doc = load()
    win = _find(doc, wid)
    if win is None:
        return 0
    keep = []
    have = set()
    for p in items:
        if _reject(p):
            continue
        k = _key(p)
        if k in have:
            continue
        have.add(k)
        keep.append(p)
    win["paths"] = keep
    save(doc)
    return len(keep)


def remove_path(wid, path):
    """从该窗口移除一个；没这条返回 False。"""
    doc = load()
    win = _find(doc, wid)
    if win is None:
        return False
    k = _key(path)
    keep = [p for p in win["paths"] if _key(p) != k]
    if len(keep) == len(win["paths"]):
        return False
    win["paths"] = keep
    return save(doc)


def prune():
    """清理账本，一次落盘，返回清掉的条数（失效路径 + 被整条删掉的空壳窗口）。

    两类条目要清：
      1. 路径已经不在磁盘上的图标；
      2. 一个路径都没有、标题也是空的空壳窗口 —— 用户点了「新建小窗口」
         却没往里放东西，留下的就是这样一条记录，再留着只是碍事。
    还有路径、或者用户起过名字的窗口一律保留。
    """
    doc = load()
    total = 0
    keep_wins = []
    for win in doc["windows"]:
        keep = []
        for p in win["paths"]:
            if os.path.exists(p):
                keep.append(p)
            else:
                total += 1
        win["paths"] = keep
        if not keep and not (win.get("title") or "").strip():
            total += 1                 # 空壳：没路径也没名字
            continue
        keep_wins.append(win)
    if total:
        doc["windows"] = keep_wins
        save(doc)
    return total


def open_ids():
    """所有 open 为真的窗口 id。"""
    return [v["id"] for v in windows() if v["open"]]
