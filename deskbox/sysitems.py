# -*- coding: utf-8 -*-
"""桌面上的系统项（虚拟项）：此电脑、回收站 这类 ::{CLSID} 图标。

它们不是磁盘文件，不能用 os.path.exists 判断是否存在；分类固定为「系统应用」，
不进「全部」。

可见性判定（每个项各自算）：
  1. DESKBOX_HIDEICONS_JSON 指向的 JSON 存在且能解析时，只认它（键缺失就当没写）；
  2. 否则读 HKCU 的 HideDesktopIcons 下的 NewStartPanel 子键，值存在就按它
     （DWORD==1 隐藏 / 0 显示）；
  3. 两处都没有这个项，就用 CLASSIC 里的「缺省可见」标志 —— Win10 的桌面图标
     设置对话框只有 计算机/用户的文件/网络/控制面板/回收站 五项，库从 Win8 起
     就移出了对话框，所以它的键通常不存在，缺省按「不显示」，不是「显示」。

任何对外函数都不抛异常：读注册表失败时按各项目的缺省标志处理。
"""
import json
import os

CLSID_PREFIX = "::"
CATEGORY = "系统应用"

# 顺序固定：(中文名, ::{CLSID}, 注册表值名, 缺省可见)
# 缺省可见＝注册表和覆盖都没提这个项时算不算显示。前五个都在桌面图标设置
# 对话框里，只有此电脑/回收站默认勾上；库早已移出对话框，缺省不显示。
CLASSIC = [
    ("此电脑", "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}",
     "{20D04FE0-3AEA-1069-A2D8-08002B30309D}", True),
    ("回收站", "::{645FF040-5081-101B-9F08-00AA002F954E}",
     "{645FF040-5081-101B-9F08-00AA002F954E}", True),
    ("控制面板", "::{26EE0668-A00A-44D7-9371-BEB064C98683}",
     "{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}", False),
    ("网络", "::{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}",
     "{EEEEFCF7-867B-4FA2-9ABD-884CF531B602}", False),
    ("用户文件", "::{59031A47-3F72-44A7-89C5-5595FE6B30EE}",
     "{18701C81-34AB-498E-8C7F-5D26B9C36732}", False),
    ("库", "::{031E4825-7B94-4DC3-B131-E946B44C8DD5}",
     "{031E4825-7B94-4DC3-B131-E946B44C8DD5}", False),
]

# 注册表键（HKCU 下）
REGPATH = ("Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\"
           "HideDesktopIcons\\NewStartPanel")


def _keys():
    """六个注册表值名，全大写。"""
    return [ps.upper() for _n, _c, ps, _d in CLASSIC]


def _as_hidden(value):
    """注册表值 / JSON 值 -> 是否隐藏。只有 1/'1'/True 算隐藏。"""
    try:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip() == "1"
        return int(value) == 1
    except Exception:
        return False


def _override():
    """读 DESKBOX_HIDEICONS_JSON。没设返回 None；设了但读不出就当空表（全可见）。"""
    p = os.environ.get("DESKBOX_HIDEICONS_JSON")
    if not p:
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict):
            return {str(k).upper(): v for k, v in doc.items()}
    except Exception:
        pass
    return {}


def _states():
    """{注册表值名大写: True=隐藏 / False=显示 / None=覆盖与注册表都没写这一项}。"""
    keys = _keys()
    out = {k: None for k in keys}
    try:
        ov = _override()
        if ov is not None:
            for k in keys:
                if k in ov:
                    out[k] = _as_hidden(ov[k])
            return out
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGPATH) as key:
            for k in keys:
                try:
                    val, _t = winreg.QueryValueEx(key, k)
                    out[k] = _as_hidden(val)
                except Exception:
                    pass
    except Exception:
        return {k: None for k in keys}
    return out


def hidden_map():
    """{注册表值名大写: True=隐藏 / False=显示}，没写这一项按 False。排障用。"""
    try:
        st = _states()
        return {k: bool(v) for k, v in st.items()}
    except Exception:
        return {k: False for k in _keys()}


def visible_default():
    """{注册表值名大写: 缺省是否可见}，两处数据源都没提这一项时用。"""
    try:
        return {ps.upper(): bool(d) for _n, _c, ps, d in CLASSIC}
    except Exception:
        return {}


def is_virtual(path):
    """是不是 ::{CLSID} 这种虚拟项。None/空/非字符串都算 False。"""
    try:
        if not isinstance(path, str):
            return False
        s = path.strip()
        return bool(s) and s.startswith(CLSID_PREFIX)
    except Exception:
        return False


def name_of(path):
    """已知 CLSID 返回中文名（大小写不敏感），否则 None。"""
    try:
        if not is_virtual(path):
            return None
        s = path.strip().casefold()
        for nm, clsid, _ps, _d in CLASSIC:
            if clsid.casefold() == s:
                return nm
    except Exception:
        pass
    return None


def all_items():
    """全部系统项，不管可见性。"""
    try:
        return [{"path": clsid, "name": nm} for nm, clsid, _ps, _d in CLASSIC]
    except Exception:
        return []


def visible_items():
    """当前桌面上真的显示着的系统项。覆盖 > 注册表 > 该项缺省标志。"""
    try:
        st = _states()
        out = []
        for nm, clsid, ps, dflt in CLASSIC:
            v = st.get(ps.upper())
            if (dflt if v is None else not v):
                out.append({"path": clsid, "name": nm})
        return out
    except Exception:
        return []
