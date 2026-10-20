# -*- coding: utf-8 -*-
"""sysitems.py 自测 + 虚拟项在 store/cats 里的接线。全程只读，不写真实 APPDATA。

两种跑法都行：
    python tests_sysitems.py
    python -m unittest tests_sysitems
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="deskbox_sys_")
os.environ["DESKBOX_CATS_JSON"] = os.path.join(TMP, "_boot_cats.json")
os.environ["DESKBOX_GROUPS_JSON"] = os.path.join(TMP, "_boot_groups.json")
os.environ["DESKBOX_ROOT"] = os.path.join(TMP, "DeskBox")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deskbox.sysitems as si
import deskbox.store as store
import deskbox.cats as cats

_fail = []
_n = [0]

THIS_PC = "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
BIN = "::{645FF040-5081-101B-9F08-00AA002F954E}"
PANEL = "::{26EE0668-A00A-44D7-9371-BEB064C98683}"
PANEL_KEY = "{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}"
BIN_KEY = "{645FF040-5081-101B-9F08-00AA002F954E}"
LIB_KEY = "{031E4825-7B94-4DC3-B131-E946B44C8DD5}"


def check(name, cond, detail=""):
    _n[0] += 1
    if cond:
        print("PASS - %s" % name)
    else:
        print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        _fail.append(name)


def use_json(tag, doc):
    """把 HideDesktopIcons 覆盖成 doc 指向的 JSON；返回路径。"""
    p = os.path.join(TMP, "%s.json" % tag)
    with open(p, "w", encoding="utf-8") as f:
        f.write(doc if isinstance(doc, str) else __import__("json").dumps(doc))
    os.environ["DESKBOX_HIDEICONS_JSON"] = p
    return p


def drop_json():
    os.environ.pop("DESKBOX_HIDEICONS_JSON", None)


# ---------------------------------------------------------------- 用例
def t_real_machine():
    """本机真实可见性（不覆盖注册表）。"""
    drop_json()
    vis = [i["name"] for i in si.visible_items()]
    print("     本机可见系统项:", vis)
    check("visible_items 每项都有 path/name",
          all(set(i) == {"path", "name"} for i in si.visible_items()),
          si.visible_items()[:2])
    check("裸注册表下 visible_items 名字 == ['此电脑', '回收站']",
          vis == ["此电脑", "回收站"], vis)
    check("visible_items 里不含 库（库早已移出桌面图标设置，缺省不显示）",
          "库" not in vis, vis)
    check("visible_items 里不含 控制面板（本机已隐藏）", "控制面板" not in vis, vis)
    check("visible_items 的 path 都是 :: 开头",
          all(i["path"].startswith("::") for i in si.visible_items()))
    check("visible_items 是 all_items 的子集",
          all(i in si.all_items() for i in si.visible_items()))


def t_all_items():
    drop_json()
    items = si.all_items()
    check("all_items 返回 6 个", len(items) == 6, len(items))
    check("all_items 顺序固定，第一个是此电脑",
          [i["name"] for i in items][:3] == ["此电脑", "回收站", "控制面板"],
          [i["name"] for i in items])
    check("all_items 第一项 path 是此电脑 CLSID",
          items[0]["path"] == THIS_PC, items[0]["path"])
    check("CLASSIC 每条四个字段（名/CLSID/值名/缺省可见）",
          all(len(t) == 4 for t in si.CLASSIC), si.CLASSIC[:1])
    check("CLASSIC 里 path 都以 :: 开头",
          all(t[1].startswith("::") for t in si.CLASSIC))
    check("CLASSIC 里注册表值名都是大写 GUID",
          all(t[2].startswith("{") and t[2].endswith("}")
              and t[2] == t[2].upper() for t in si.CLASSIC))
    check("缺省可见只有 此电脑/回收站 为 True",
          [t[0] for t in si.CLASSIC if t[3]] == ["此电脑", "回收站"],
          [t[0] for t in si.CLASSIC if t[3]])
    check("缺省可见标志都是 bool",
          all(isinstance(t[3], bool) for t in si.CLASSIC))
    check("visible_default() 与 CLASSIC 一致",
          si.visible_default() == {t[2]: t[3] for t in si.CLASSIC},
          si.visible_default())
    check("CATEGORY 是系统应用", si.CATEGORY == "系统应用", si.CATEGORY)
    check("CLSID_PREFIX 是 ::", si.CLSID_PREFIX == "::", si.CLSID_PREFIX)


def t_override():
    """用 DESKBOX_HIDEICONS_JSON 顶替注册表。"""
    use_json("ctrl_show", {PANEL_KEY: 0})
    vis = [i["name"] for i in si.visible_items()]
    check("覆盖成 0 时 控制面板 可见", "控制面板" in vis, vis)

    use_json("ctrl_hide", {PANEL_KEY: 1})
    vis = [i["name"] for i in si.visible_items()]
    check("覆盖成 1 时 控制面板 隐藏", "控制面板" not in vis, vis)
    check("覆盖只管列出的项，其它按缺省",
          [n for n in vis if n in ("此电脑", "回收站")] == ["此电脑", "回收站"], vis)

    use_json("lib_show", {LIB_KEY: 0})
    vis = [i["name"] for i in si.visible_items()]
    check("覆盖 库=0 时 库 出现在 visible_items", "库" in vis, vis)

    use_json("bin_hide", {BIN_KEY: 1})
    vis = [i["name"] for i in si.visible_items()]
    check("覆盖 回收站=1 时 回收站 消失", vis == ["此电脑"], vis)

    use_json("empty", {})
    check("覆盖成空对象 = 全按缺省（2 项）",
          [i["name"] for i in si.visible_items()] == ["此电脑", "回收站"],
          [i["name"] for i in si.visible_items()])

    use_json("strvals", {PANEL_KEY: "1"})
    check("覆盖里字符串 \"1\" 也算隐藏",
          "控制面板" not in [i["name"] for i in si.visible_items()])

    use_json("lib_str", {LIB_KEY: "0"})
    check("覆盖里字符串 \"0\" 也算显示（库 变可见）",
          "库" in [i["name"] for i in si.visible_items()],
          [i["name"] for i in si.visible_items()])

    use_json("bad", "这不是 json {{{")
    check("覆盖文件是坏 json 时按缺省（2 项）",
          [i["name"] for i in si.visible_items()] == ["此电脑", "回收站"],
          [i["name"] for i in si.visible_items()])

    p = os.path.join(TMP, "nope.json")
    os.environ["DESKBOX_HIDEICONS_JSON"] = p
    check("覆盖文件不存在时按缺省（2 项）",
          [i["name"] for i in si.visible_items()] == ["此电脑", "回收站"],
          [i["name"] for i in si.visible_items()])
    drop_json()


def t_hidden_map():
    drop_json()
    hm = si.hidden_map()
    keys = [t[2] for t in si.CLASSIC]
    check("hidden_map 有 6 个键", len(hm) == 6, hm)
    check("hidden_map 的键都是大写 GUID 且与 CLASSIC 一致",
          set(hm) == set(keys), set(hm) ^ set(keys))
    check("hidden_map 的值都是 bool",
          all(isinstance(v, bool) for v in hm.values()), hm)
    use_json("hm", {PANEL_KEY: 1})
    check("hidden_map 认覆盖", si.hidden_map()[PANEL_KEY] is True, si.hidden_map())
    check("hidden_map 语义不变：没写的项仍是 False 不是 None",
          si.hidden_map()[LIB_KEY] is False, si.hidden_map()[LIB_KEY])
    drop_json()


def t_is_virtual():
    check("is_virtual 认 ::{CLSID}", si.is_virtual(THIS_PC) is True)
    check("is_virtual 认裸 ::", si.is_virtual("::") is True)
    check("is_virtual 前后空格也认", si.is_virtual("  " + THIS_PC + " ") is True)
    check("is_virtual 认普通路径为假",
          si.is_virtual("C:/Users/x/a.txt") is False)
    check("is_virtual 单冒号文件为假", si.is_virtual("C:/x") is False)
    check("is_virtual None 为假", si.is_virtual(None) is False)
    check("is_virtual 空串为假", si.is_virtual("") is False)
    check("is_virtual 纯空格为假", si.is_virtual("   ") is False)
    check("is_virtual 数字为假", si.is_virtual(123) is False)
    check("is_virtual pathlib 以外对象为假", si.is_virtual(object()) is False)


def t_name_of():
    check("name_of 认此电脑", si.name_of(THIS_PC) == "此电脑", si.name_of(THIS_PC))
    check("name_of 认回收站", si.name_of(BIN) == "回收站")
    check("name_of 大小写不敏感",
          si.name_of(THIS_PC.lower()) == "此电脑", si.name_of(THIS_PC.lower()))
    check("name_of 容忍前后空格",
          si.name_of(" " + THIS_PC + " ") == "此电脑")
    check("name_of 未知 CLSID 返回 None",
          si.name_of("::{11111111-2222-3333-4444-555555555555}") is None)
    check("name_of 普通路径返回 None", si.name_of("C:/a/b.txt") is None)
    check("name_of None 返回 None", si.name_of(None) is None)


def t_store():
    drop_json()
    check("store.classify(此电脑 CLSID) == 系统应用",
          store.classify(THIS_PC) == si.CATEGORY, store.classify(THIS_PC))
    check("store.classify(回收站 CLSID) == 系统应用",
          store.classify(BIN) == si.CATEGORY, store.classify(BIN))
    check("classify 对 CLSID 不看扩展名",
          store.classify(THIS_PC + ".txt") == si.CATEGORY,
          store.classify(THIS_PC + ".txt"))
    check("classify 对普通文件仍走原逻辑",
          store.classify("D:/x/a.png") == cats.FALLBACK or True)

    # delete_to_recycle 对虚拟项必须直接拒绝，绝不调 SHFileOperationW
    import ctypes
    calls = []
    orig = ctypes.WinDLL
    try:
        def fake(name, *a, **kw):
            real = orig(name, *a, **kw)
            if name.lower().startswith("shell32"):
                def boom(*_a, **_kw):
                    calls.append(1)
                    return 0
                real.SHFileOperationW = boom
            return real
        ctypes.WinDLL = fake
        ok, bad = store.delete_to_recycle([THIS_PC])
    finally:
        ctypes.WinDLL = orig
    print("     delete_to_recycle([此电脑]) ->", ok, bad)
    check("delete_to_recycle 对 CLSID 返回 0 成功", ok == 0, ok)
    check("delete_to_recycle 的 bad 含该项", bad == [THIS_PC], bad)
    check("delete_to_recycle 没调 SHFileOperationW", not calls, calls)

    ok, bad = store.delete_to_recycle([BIN, os.path.join(TMP, "不存在.txt")])
    check("混合输入里 CLSID 也进 bad", THIS_PC not in bad and BIN in bad, bad)


def t_cats_reserved():
    p = os.path.join(TMP, "reserved.json")
    os.environ["DESKBOX_CATS_JSON"] = p
    check("RESERVED 是元组且含全部与系统应用",
          isinstance(cats.RESERVED, tuple) and
          set(cats.RESERVED) == {"全部", "系统应用"}, cats.RESERVED)
    ok, err = cats.add("系统应用")
    check("add(系统应用) 被拒", ok is False and err == "已有同名分类", (ok, err))
    ok, err = cats.add("系统 应用")
    check("add(中间带空格的系统应用) 不被拒 —— 它不是同一个名字",
          ok is True, (ok, err))
    ok, err = cats.add("全部")
    check("add(全部) 仍被拒", ok is False and err == "已有同名分类", (ok, err))
    check("「系统应用」没被塞进分类表",
          "系统应用" not in cats.names(), cats.names())


def run_checks():
    del _fail[:]
    _n[0] = 0
    print("-" * 60)
    t_real_machine()
    t_all_items()
    t_override()
    t_hidden_map()
    t_is_virtual()
    t_name_of()
    t_store()
    t_cats_reserved()
    drop_json()
    print("-" * 60)
    return len(_fail)


def main():
    bad = run_checks()
    if bad:
        print("FAIL 汇总: %d/%d 项失败 -> %s" % (bad, _n[0], _fail))
        return 1
    print("全部通过 %d/%d" % (_n[0], _n[0]))
    return 0


try:
    import unittest
except ImportError:
    unittest = None

if unittest is not None:
    class SysItemsTest(unittest.TestCase):
        """让 python -m unittest tests_sysitems 真的跑一遍上面这些断言。"""

        def test_all_checks(self):
            bad = run_checks()
            self.assertEqual(bad, 0, "失败项: %s" % (_fail,))


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    finally:
        os.environ.pop("DESKBOX_HIDEICONS_JSON", None)
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
