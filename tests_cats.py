# -*- coding: utf-8 -*-
"""cats.py 分类注册表自测：全程临时目录，绝不写真实 APPDATA 下的 DeskBox。

两种跑法都行：
    python tests_cats.py
    python -m unittest tests_cats
"""
import json
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="deskbox_cats_")
os.environ["DESKBOX_CATS_JSON"] = os.path.join(TMP, "_boot.json")
os.environ["DESKBOX_GROUPS_JSON"] = os.path.join(TMP, "_boot_groups.json")
os.environ["DESKBOX_ROOT"] = os.path.join(TMP, "DeskBox")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deskbox.cats as cats
import deskbox.store as store
import deskbox.groupbook as gb

_fail = []
_n = [0]

BAD = cats.BAD_CHARS


def check(name, cond, detail=""):
    _n[0] += 1
    if cond:
        print("PASS - %s" % name)
    else:
        print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        _fail.append(name)


def fresh(tag):
    """给本用例换一个全新的 cats.json 路径。"""
    p = os.path.join(TMP, "%s.json" % tag)
    os.environ["DESKBOX_CATS_JSON"] = p
    return p


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 用例
def t_default():
    p = fresh("d1")
    check("json_path 跟随 DESKBOX_CATS_JSON", cats.json_path() == p,
          cats.json_path())
    names = cats.names()
    check("默认分类 8 个", len(names) == 8, names)
    check("默认分类最后一个是未分类", names[-1] == "未分类", names)
    check("groups() 与 DEFAULT_GROUPS 一致",
          cats.groups() == cats.DEFAULT_GROUPS, cats.groups())
    check("默认表里没有待定", "待定" not in names, names)
    check("valid(未分类) 为真", cats.valid("未分类") is True)
    check("valid(全部) 为假", cats.valid("全部") is False)
    check("valid(空串) 为假", cats.valid("") is False)
    check("valid(None) 为假", cats.valid(None) is False)
    check("dir_of(图片) == images", cats.dir_of("图片") == "images")
    check("name_of_dir(images) == 图片", cats.name_of_dir("images") == "图片")
    check("dir_of(不存在) 返回空串", cats.dir_of("不存在") == "")
    check("name_of_dir(不存在) 返回空串", cats.name_of_dir("nope") == "")
    doc = cats.load()
    check("load 永远有 added/removed 两个键",
          set(doc.keys()) == {"added", "removed"}, doc)
    check("空账本时 groups() 仍是默认 8 个",
          len(cats.groups()) == 8 and cats.groups()[-1][0] == "未分类")


def t_add_errors():
    fresh("a1")
    ok, err = cats.add("")
    check("add 空名被拒", ok is False and err == "分类名不能为空", (ok, err))
    ok, err = cats.add("   ")
    check("add 纯空白被拒", ok is False and err == "分类名不能为空", (ok, err))
    ok, err = cats.add("一二三四五六七八九十十一十二")   # 13 个字
    check("add 超长被拒", ok is False and err == "分类名最多 12 个字", (ok, err))
    bad_hit = 0
    for ch in BAD:
        ok, err = cats.add("坏%s名" % ch)
        if ok is False and "不能包含" in err:
            bad_hit += 1
    check("add 七种非法字符全被拒", bad_hit == len(BAD), (bad_hit, BAD))
    ok, err = cats.add("图片")
    check("add 与现有分类重名被拒", ok is False and err == "已有同名分类",
          (ok, err))
    ok, err = cats.add("全部")
    check("add 与「全部」重名被拒", ok is False and err == "已有同名分类",
          (ok, err))
    ok, err = cats.add(" 图片 ")
    check("add 重名判断先 strip", ok is False and err == "已有同名分类",
          (ok, err))
    check("非法 add 之后分类数不变", len(cats.names()) == 8, cats.names())
    ok, err = cats.add("图" * 12)                      # 正好 12 个字，该通过
    check("add 正好 12 个字可通过", ok is True and err == "", (ok, err))
    ok, err = cats.add("图" * 13)
    check("add 第 13 个字被拒", ok is False and err == "分类名最多 12 个字",
          (ok, err))
    fresh("a2")


def t_add_ok():
    p = fresh("b1")
    ok, err = cats.add("游戏")
    check("add(游戏) 成功", ok is True and err == "", (ok, err))
    names = cats.names()
    check("add 之后分类 9 个", len(names) == 9, names)
    check("新增分类排在未分类之前", names[-2] == "游戏" and names[-1] == "未分类",
          names)
    d = cats.dir_of("游戏")
    check("新增分类有目录名", bool(d) and d.startswith("_c_"), d)
    check("目录名能反查回中文名", cats.name_of_dir(d) == "游戏", d)
    check("valid(游戏) 为真", cats.valid("游戏") is True)
    doc = cats.load()
    check("cats.json 里记了 added", doc["added"] == ["游戏"], doc)
    check("cats.json 里没塞默认表", doc["removed"] == [], doc)
    check("cats.json 真的落盘", os.path.isfile(p))
    check("cats.json 是 version 1", json.loads(read(p)).get("version") == 1,
          read(p)[:80])
    ok, err = cats.add("游戏")
    check("重复 add 同一名字被拒", ok is False and err == "已有同名分类",
          (ok, err))
    check("重复 add 之后仍是 9 个", len(cats.names()) == 9, cats.names())


def t_remove_fallback():
    fresh("c1")
    ok, err = cats.remove("未分类")
    check("remove 兜底分类被拒", ok is False, (ok, err))
    check("remove 兜底分类给出原因", "兜底" in err, err)
    check("兜底分类仍在表里", cats.names()[-1] == "未分类", cats.names())
    ok, err = cats.remove("_unsorted")
    check("remove 兜底分类的目录名也被拒", ok is False and "兜底" in err,
          (ok, err))


def t_remove_unknown():
    fresh("c2")
    ok, err = cats.remove("没有这个")
    check("remove 未知分类被拒", ok is False and err == "没有这个分类",
          (ok, err))
    check("remove 未知分类后仍是 8 个", len(cats.names()) == 8, cats.names())


def t_remove_images():
    p = fresh("d2")
    ok, err = cats.remove("图片")
    check("remove(图片) 成功", ok is True and err == "", (ok, err))
    names = cats.names()
    check("删除后图片不在表里", "图片" not in names, names)
    check("删除后剩 7 个", len(names) == 7, names)
    check("删除后未分类仍在最后", names[-1] == "未分类", names)
    check("删除后 valid(图片) 为假", cats.valid("图片") is False)
    check("删除后 dir_of(图片) 为空", cats.dir_of("图片") == "")
    check("删除后 name_of_dir(images) 为空", cats.name_of_dir("images") == "")
    check("删除后 _norm_group(图片) 为 None",
          store._norm_group("图片") is None, store._norm_group("图片"))
    check("删除后 _norm_group(images) 为 None",
          store._norm_group("images") is None, store._norm_group("images"))
    check("删除图片后 png 落到未分类",
          store.classify("D:/x/照片.png") == "未分类",
          store.classify("D:/x/照片.png"))
    check("删除图片不影响其它分类",
          store.classify("D:/x/包.zip") == "压缩包", store.classify("D:/x/包.zip"))
    check("删除图片后 _norm_group(文档) 仍有效",
          store._norm_group("文档") == "文档")
    doc = cats.load()
    check("cats.json 里记了 removed", doc["removed"] == ["图片"], doc)
    check("cats.json 的 removed 落盘可读",
          "图片" in read(p), read(p)[:120])


def t_restore_images():
    fresh("d2")                        # 接着 t_remove_images 的账本
    ok, err = cats.add("图片")
    check("add 被删分类等于恢复", ok is True and err == "", (ok, err))
    names = cats.names()
    check("恢复后回到 8 个", len(names) == 8, names)
    check("恢复后图片回到默认位置", names.index("图片") == 3, names)
    check("恢复后没有重复图片", names.count("图片") == 1, names)
    check("恢复后 _norm_group(图片) 有效",
          store._norm_group("图片") == "图片", store._norm_group("图片"))
    check("恢复后 png 重新归到图片",
          store.classify("D:/x/照片.png") == "图片",
          store.classify("D:/x/照片.png"))
    doc = cats.load()
    check("恢复后 added 与 removed 都干净",
          doc["added"] == [] and doc["removed"] == [], doc)


def t_bad_json():
    p = fresh("e1")
    write(p, "{ 这不是 json ")
    check("坏 json 时 groups() 仍是默认 8 个", len(cats.groups()) == 8,
          cats.groups())
    check("坏 json 时最后仍是未分类", cats.groups()[-1][0] == "未分类")
    doc = cats.load()
    check("坏 json 时 load 返回空表",
          doc == {"added": [], "removed": []}, doc)

    write(p, "[]")
    check("顶层是数组时也当空", cats.load() == {"added": [], "removed": []},
          cats.load())
    check("顶层是数组时仍是 8 个", len(cats.groups()) == 8)

    write(p, '{"version": 1, "added": "游戏", "removed": 5}')
    check("字段类型不对时当空",
          cats.load() == {"added": [], "removed": []}, cats.load())

    write(p, '{"added": ["", "  ", null, "游戏", "游戏"], "removed": [null]}')
    doc = cats.load()
    check("load 会剔掉空名与重复名", doc["added"] == ["游戏"], doc)
    check("load 会剔掉非法 removed", doc["removed"] == [], doc)

    os.remove(p)
    check("文件不存在时仍是默认 8 个", len(cats.groups()) == 8)
    check("文件不存在时未分类仍在最后", cats.groups()[-1][0] == "未分类")


def t_save_failure():
    blocker = os.path.join(TMP, "blocker.txt")
    write(blocker, "x")
    os.environ["DESKBOX_CATS_JSON"] = os.path.join(blocker, "cats.json")
    ok, err = cats.add("游戏")
    check("路径写不进去时 add 返回 False 不抛",
          ok is False and err == "保存失败", (ok, err))
    check("路径写不进去时 save 返回 False",
          cats.save({"added": [], "removed": []}) is False)
    check("路径写不进去时 groups() 仍是默认 8 个", len(cats.groups()) == 8)
    fresh("e2")


def t_fallback_not_persisted():
    p = fresh("f1")
    cats.save({"added": ["未分类", "游戏"], "removed": ["未分类", "图片"]})
    doc = cats.load()
    check("save 不会把未分类写进 added", "未分类" not in doc["added"], doc)
    check("save 不会把未分类写进 removed", "未分类" not in doc["removed"], doc)
    names = cats.names()
    check("未分类只出现一次", names.count("未分类") == 1, names)
    check("未分类永远在最后", names[-1] == "未分类", names)
    check("自定义分类仍在", "游戏" in names, names)
    check("图片仍被删", "图片" not in names, names)
    check("磁盘上的 json 里也没有未分类",
          "未分类" not in read(p), read(p))


def t_dir_stable():
    fresh("g1")
    cats.add("游戏")
    d1 = cats.dir_of("游戏")
    cats.add("工具")
    cats.remove("文档")
    check("增删别的分类不改变已有目录名", cats.dir_of("游戏") == d1,
          (d1, cats.dir_of("游戏")))
    check("两个自定义分类目录名不同", cats.dir_of("游戏") != cats.dir_of("工具"),
          (cats.dir_of("游戏"), cats.dir_of("工具")))


def t_store_surface():
    fresh("h1")
    check("store.GROUPS 最后一项是未分类",
          store.GROUPS[-1] == ("未分类", "_unsorted"), store.GROUPS[-1])
    check("store.GROUPS 里没有待定",
          all(cn != "待定" for cn, _ in store.GROUPS), store.GROUPS)
    check("store.all_groups() 等于 cats.groups()",
          store.all_groups() == cats.groups(), store.all_groups())
    check("store._norm_group 认得目录名",
          store._norm_group("images") == "图片", store._norm_group("images"))
    check("store._norm_group 认得中文名",
          store._norm_group(" 图片 ") == "图片", store._norm_group(" 图片 "))
    check("store._norm_group 空串返回 None", store._norm_group("") is None)
    check("store._norm_group 乱写返回 None",
          store._norm_group("不存在的分类") is None)
    check("classify 无扩展名落到未分类",
          store.classify("D:/x/没有扩展名") == "未分类",
          store.classify("D:/x/没有扩展名"))


def t_move_category():
    fresh("i1")
    os.environ["DESKBOX_GROUPS_JSON"] = os.path.join(TMP, "i1_groups.json")
    files = []
    for i in range(3):
        p = os.path.join(TMP, "i1_%d.png" % i)
        write(p, "x")
        files.append(p)
    gb.set_group(files[0], "图片")
    gb.set_group(files[1], "图片")
    gb.set_group(files[2], "文档")
    check("move_category 前置：账本里有 2 个图片",
          gb.get_group(files[0]) == "图片" and gb.get_group(files[1]) == "图片",
          gb.load())
    cats.remove("图片")
    n = gb.move_category("图片")
    check("move_category 返回改动条数", n == 2, n)
    check("move_category 后条目变未分类",
          gb.get_group(files[0]) == "未分类" and gb.get_group(files[1]) == "未分类",
          gb.load())
    check("move_category 不动其它分类", gb.get_group(files[2]) == "文档",
          gb.get_group(files[2]))
    check("move_category(old == to) 返回 0",
          gb.move_category("未分类", "未分类") == 0)
    check("move_category 目标非法返回 0",
          gb.move_category("未分类", "根本没有") == 0)
    check("move_category 源非法返回 0",
          gb.move_category("根本没有") == 0)
    cats.add("图片")
    n = gb.move_category("未分类", "图片")
    check("move_category 能把条目搬回图片", n == 2, n)
    check("搬回后账本里是图片", gb.get_group(files[0]) == "图片",
          gb.get_group(files[0]))


def run_checks():
    """跑全部用例，返回失败条数。"""
    del _fail[:]
    _n[0] = 0
    print("-" * 60)
    t_default()
    t_add_errors()
    t_add_ok()
    t_remove_fallback()
    t_remove_unknown()
    t_remove_images()
    t_restore_images()
    t_bad_json()
    t_save_failure()
    t_fallback_not_persisted()
    t_dir_stable()
    t_store_surface()
    t_move_category()
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
    class CatsTest(unittest.TestCase):
        """让 python -m unittest tests_cats 真的跑一遍上面这些断言。"""

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
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
