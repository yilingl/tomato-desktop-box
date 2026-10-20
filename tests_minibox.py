# -*- coding: utf-8 -*-
"""minibox.py 自测：全部在临时目录里跑，不碰真实 APPDATA，也不碰任何桌面文件。

两种跑法都行：
    python tests_minibox.py
    python -m unittest tests_minibox
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deskbox.minibox as mb


def _read(path):
    with open(path, "rb") as f:
        return f.read()


class MiniBoxTest(unittest.TestCase):
    """每条用例一个独立临时目录，互不污染。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="deskbox_mini_")
        self.js = os.path.join(self.tmp, "minis.json")
        self._old = os.environ.get("DESKBOX_MINIS_JSON")
        os.environ["DESKBOX_MINIS_JSON"] = self.js
        self.n = 0

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DESKBOX_MINIS_JSON", None)
        else:
            os.environ["DESKBOX_MINIS_JSON"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ 工具
    def check(self, name, cond, detail=""):
        self.n += 1
        if cond:
            print("PASS - %s" % name)
        else:
            print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        self.assertTrue(cond, "%s %s" % (name, detail))

    def touch(self, *names):
        out = []
        for name in names:
            p = os.path.join(self.tmp, name)
            with open(p, "wb") as f:
                f.write(b"x")
            out.append(p)
        return out

    def write_raw(self, text):
        with open(self.js, "w", encoding="utf-8") as f:
            f.write(text)

    # ------------------------------------------------------------ 用例
    def test_json_path_env(self):
        self.check("json_path 受 DESKBOX_MINIS_JSON 影响",
                   mb.json_path() == self.js, mb.json_path())
        old = os.environ.pop("DESKBOX_MINIS_JSON")
        try:
            p = mb.json_path()
            self.check("无环境变量时落在 APPDATA/DeskBox/minis.json",
                       p.lower().endswith(os.path.join("deskbox", "minis.json")),
                       p)
            self.check("无环境变量时文件名是 minis.json",
                       os.path.basename(p) == "minis.json", p)
        finally:
            os.environ["DESKBOX_MINIS_JSON"] = old
        self.check("环境变量恢复后 json_path 仍指向临时文件",
                   mb.json_path() == self.js, mb.json_path())

    def test_load_bad_inputs(self):
        self.check("文件不存在时返回空表",
                   mb.load() == {"version": 1, "windows": []}, mb.load())
        self.write_raw("")
        self.check("空文件返回空表",
                   mb.load() == {"version": 1, "windows": []}, mb.load())
        self.write_raw("{ this is not json ")
        self.check("坏 JSON 返回空表",
                   mb.load() == {"version": 1, "windows": []}, mb.load())
        self.write_raw("[1, 2, 3]")
        self.check("顶层是 list 返回空表",
                   mb.load() == {"version": 1, "windows": []}, mb.load())
        self.write_raw(json.dumps({"version": 1, "windows": "nope"}))
        self.check("windows 是 str 返回空表",
                   mb.load() == {"version": 1, "windows": []}, mb.load())
        self.check("load 结果版本号恒为 1", mb.load()["version"] == 1)
        os.remove(self.js)
        os.makedirs(self.js)          # 目标是个目录，读必然失败
        self.check("路径是目录时也返回空表不抛",
                   mb.load() == {"version": 1, "windows": []}, mb.load())
        shutil.rmtree(self.js)

    def test_load_norms_records(self):
        self.write_raw(json.dumps({"version": 1, "windows": [
            {"id": "w1", "paths": ["a", 3, "", "b"], "w": 10, "h": 5,
             "pinned": 1, "open": 0},
        ]}))
        w = mb.windows()[0]
        self.check("w 被夹到 MIN_W", w["w"] == mb.MIN_W, w["w"])
        self.check("h 被夹到 MIN_H", w["h"] == mb.MIN_H, w["h"])
        self.check("pinned 转成 bool", w["pinned"] is True, w["pinned"])
        self.check("open 转成 bool", w["open"] is False, w["open"])
        self.check("title 缺字段补空串", w["title"] == "", w["title"])
        self.check("x/y 缺字段补 0", (w["x"], w["y"]) == (0, 0), (w["x"], w["y"]))
        self.check("paths 过滤掉非字符串与空串", w["paths"] == ["a", "b"],
                   w["paths"])
        self.check("load 返回的 windows 是 list",
                   isinstance(mb.load()["windows"], list))

    def test_constants(self):
        self.check("DEFAULT_W == 340", mb.DEFAULT_W == 340, mb.DEFAULT_W)
        self.check("DEFAULT_H == 280", mb.DEFAULT_H == 280, mb.DEFAULT_H)
        self.check("MIN_W == 200", mb.MIN_W == 200, mb.MIN_W)
        self.check("MIN_H == 160", mb.MIN_H == 160, mb.MIN_H)
        self.check("缩放常量满足 MIN_W < DEFAULT_W", mb.MIN_W < mb.DEFAULT_W)
        gp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "deskbox", "minibox.py")
        src = _read(gp).decode("utf-8")
        imports = [ln.strip() for ln in src.splitlines()
                   if ln.startswith("import ") or ln.startswith("from ")]
        self.check("minibox 只 import 标准库四件套",
                   sorted(imports) == ["import json", "import os",
                                       "import random", "import time"],
                   imports)
        self.check("minibox 的 import 里没有 store/cats/groupbook",
                   not [ln for ln in imports
                        if "store" in ln or "groupbook" in ln or "cats" in ln],
                   imports)

    def test_create(self):
        w1 = mb.create()
        self.check("create 返回 dict", isinstance(w1, dict), type(w1))
        self.check("create 落盘", os.path.exists(self.js), self.js)
        self.check("create 默认标题为空串", w1["title"] == "", w1["title"])
        self.check("create 默认尺寸是 DEFAULT", (w1["w"], w1["h"]) ==
                   (mb.DEFAULT_W, mb.DEFAULT_H), (w1["w"], w1["h"]))
        self.check("create 默认 pinned=False（新窗口默认不固定）", w1["pinned"] is False)
        self.check("create 默认 open=True", w1["open"] is True)
        self.check("create 默认 paths 为空", w1["paths"] == [], w1["paths"])
        self.check("id 形如 w<数字><hex3>",
                   w1["id"].startswith("w") and len(w1["id"]) >= 6, w1["id"])
        self.check("create 的窗口回到 load() 里",
                   len(mb.windows()) == 1, mb.windows())
        self.check("文件里真是 1 条",
                   len(json.loads(_read(self.js).decode("utf-8"))["windows"]) == 1)

        w2 = mb.create("第二个")
        w3 = mb.create("第三个")
        ids = [w1["id"], w2["id"], w3["id"]]
        self.check("连续 create 三次 id 互不相同",
                   len(set(ids)) == 3, ids)
        self.check("连续 create 后文件里 3 条", len(mb.windows()) == 3)
        self.check("title 生效", w2["title"] == "第二个", w2["title"])

    def test_create_coords(self):
        w = mb.create(x=100, y=200)
        self.check("create 用指定坐标", (w["x"], w["y"]) == (100, 200),
                   (w["x"], w["y"]))
        n = len(mb.windows())
        self.check("指定坐标落盘", (mb.get(w["id"])["x"],
                                  mb.get(w["id"])["y"]) == (100, 200))
        w2 = mb.create()
        self.check("未指定坐标时 x 在可用区内",
                   0 <= w2["x"] and w2["x"] + w2["w"] <= mb.SCREEN_W,
                   (w2["x"], w2["w"], mb.SCREEN_W))
        self.check("未指定坐标时 y 在可用区内",
                   0 <= w2["y"] and w2["y"] + w2["h"] <= mb.SCREEN_H,
                   (w2["y"], w2["h"], mb.SCREEN_H))
        self.check("默认落点大致居中（横向）",
                   abs(w2["x"] - (mb.SCREEN_W - w2["w"]) // 2)
                   <= mb.STEP * (n % 8) + mb.STEP, (w2["x"], n))
        self.check("create 传 w/h 生效",
                   mb.create(w=500, h=400)["w"] == 500)
        self.check("create 传小尺寸被夹到 MIN",
                   (mb.create(w=10, h=10)["w"], mb.create(h=10)["h"]) ==
                   (mb.MIN_W, mb.MIN_H))

    def test_update(self):
        w = mb.create("原名")
        wid = w["id"]
        self.check("update title 返回 True", mb.update(wid, title="新名") is True)
        self.check("update title 生效", mb.get(wid)["title"] == "新名")
        mb.update(wid, x=11, y=22)
        g = mb.get(wid)
        self.check("update x/y 生效", (g["x"], g["y"]) == (11, 22), (g["x"], g["y"]))
        mb.update(wid, w=400, h=300)
        g = mb.get(wid)
        self.check("update w/h 生效", (g["w"], g["h"]) == (400, 300), (g["w"], g["h"]))
        mb.update(wid, pinned=True)
        self.check("update pinned 生效", mb.get(wid)["pinned"] is True)
        mb.update(wid, open=False)
        self.check("update open 生效", mb.get(wid)["open"] is False)
        self.check("update 全部落盘",
                   json.loads(_read(self.js).decode("utf-8"))["windows"][0]["title"]
                   == "新名")

        before = mb.get(wid)
        mb.update(wid, xxxxx=1)
        self.check("update 非法字段被忽略（不新增键）",
                   "xxxxx" not in mb.get(wid), mb.get(wid).keys())
        self.check("update 非法字段不影响其他字段",
                   mb.get(wid) == before, (before, mb.get(wid)))
        self.check("update 传 0 个合法字段仍返回 True",
                   mb.update(wid) is True)

        mb.update(wid, w=10)
        self.check("update w=10 夹到 MIN_W", mb.get(wid)["w"] == mb.MIN_W,
                   mb.get(wid)["w"])
        mb.update(wid, h=10)
        self.check("update h=10 夹到 MIN_H", mb.get(wid)["h"] == mb.MIN_H,
                   mb.get(wid)["h"])
        self.check("update 不存在的 wid 返回 False",
                   mb.update("w_bogus", title="x") is False)

    def test_remove(self):
        w = mb.create("待删")
        wid = w["id"]
        mb.create("留着")
        self.check("remove 返回 True", mb.remove(wid) is True)
        self.check("remove 后 get 返回 None", mb.get(wid) is None)
        self.check("remove 后文件少一条", len(mb.windows()) == 1, mb.windows())
        self.check("remove 不存在的 id 返回 False", mb.remove(wid) is False)
        self.check("remove 后落盘也少一条",
                   len(json.loads(_read(self.js).decode("utf-8"))["windows"]) == 1)

    def test_add_paths(self):
        files = self.touch("a.txt", "b.txt", "c.txt")
        ini = self.touch("desktop.ini")[0]
        dot = self.touch(".gitignore")[0]
        w = mb.create()
        wid = w["id"]
        added, skipped = mb.add_paths(wid, files)
        self.check("add_paths 正常追加", added == 3 and skipped == [],
                   (added, skipped))
        self.check("add_paths 落盘", len(mb.get(wid)["paths"]) == 3)
        self.check("add_paths 保持顺序", mb.get(wid)["paths"] == files,
                   mb.get(wid)["paths"])

        added, skipped = mb.add_paths(wid, [files[0]])
        self.check("重复路径进 skipped", added == 0 and skipped == [files[0]],
                   (added, skipped))

        batch = [files[1], files[1]]
        added, skipped = mb.add_paths(wid, batch)
        self.check("同批次重复只算一次并进 skipped",
                   added == 0 and skipped == batch, (added, skipped))

        missing = os.path.join(self.tmp, "不存在的.txt")
        added, skipped = mb.add_paths(wid, [missing])
        self.check("不存在路径进 skipped", added == 0 and skipped == [missing],
                   (added, skipped))

        added, skipped = mb.add_paths(wid, [ini])
        self.check("desktop.ini 进 skipped", added == 0 and skipped == [ini],
                   (added, skipped))
        added, skipped = mb.add_paths(wid, [dot])
        self.check("点开头文件进 skipped", added == 0 and skipped == [dot],
                   (added, skipped))

        added, skipped = mb.add_paths(wid, [""])
        self.check("空串进 skipped", added == 0 and skipped == [""],
                   (added, skipped))
        added, skipped = mb.add_paths(wid, [None, 123])
        self.check("非字符串进 skipped 且保留原值",
                   added == 0 and skipped == [None, 123], (added, skipped))
        self.check("skipped 返回的是原始输入字符串",
                   files[0] in mb.add_paths(wid, [files[0]])[1])

        self.check("窗口不存在返回 (0, 全部输入)",
                   mb.add_paths("w_bogus", files) == (0, files),
                   mb.add_paths("w_bogus", files))

    def test_add_paths_normcase(self):
        self.touch("A.TXT")
        real = os.path.join(self.tmp, "A.TXT")
        lower = os.path.join(self.tmp.lower(), "a.txt")
        self.check("大小写不同写法指向同一文件",
                   os.path.normcase(os.path.abspath(real)) ==
                   os.path.normcase(os.path.abspath(lower)), (real, lower))
        w = mb.create()
        added, _ = mb.add_paths(w["id"], [real])
        self.check("首次加入成功", added == 1)
        added, skipped = mb.add_paths(w["id"], [lower])
        self.check("normcase 语义下算重复（大小写不同）",
                   added == 0 and skipped == [lower], (added, skipped))
        self.check("窗口里只有一条", len(mb.get(w["id"])["paths"]) == 1,
                   mb.get(w["id"])["paths"])

    def test_set_paths(self):
        files = self.touch("s1.txt", "s2.txt", "s3.txt")
        w = mb.create()
        wid = w["id"]
        mb.add_paths(wid, files)
        n = mb.set_paths(wid, [files[2]])
        self.check("set_paths 整体替换返回最终条数", n == 1, n)
        self.check("set_paths 后只剩新的一条",
                   mb.get(wid)["paths"] == [files[2]], mb.get(wid)["paths"])
        self.check("set_paths 传空表清空",
                   mb.set_paths(wid, []) == 0 and mb.get(wid)["paths"] == [])
        self.check("set_paths 去重",
                   mb.set_paths(wid, [files[0], files[0]]) == 1,
                   mb.get(wid)["paths"])
        self.check("set_paths 对不存在窗口返回 0",
                   mb.set_paths("w_bogus", files) == 0)

    def test_remove_path(self):
        files = self.touch("r1.txt", "r2.txt")
        w = mb.create()
        wid = w["id"]
        mb.add_paths(wid, files)
        self.check("remove_path 返回 True", mb.remove_path(wid, files[0]) is True)
        self.check("remove_path 后只剩一条",
                   mb.get(wid)["paths"] == [files[1]], mb.get(wid)["paths"])
        self.check("remove_path 不存在的路径返回 False",
                   mb.remove_path(wid, files[0]) is False)
        self.check("remove_path 对不存在窗口返回 False",
                   mb.remove_path("w_bogus", files[1]) is False)
        self.check("remove_path 落盘生效",
                   len(json.loads(_read(self.js).decode("utf-8"))
                       ["windows"][0]["paths"]) == 1)

    def test_prune(self):
        files = self.touch("p1.txt", "p2.txt", "p3.txt")
        w = mb.create()
        wid = w["id"]
        mb.add_paths(wid, files)
        w2 = mb.create()
        mb.add_paths(w2["id"], [files[0]])
        os.remove(files[1])
        os.remove(files[2])
        n = mb.prune()
        self.check("prune 返回删除总条数", n == 2, n)
        self.check("prune 保留仍存在的路径",
                   mb.get(wid)["paths"] == [files[0]], mb.get(wid)["paths"])
        self.check("prune 另一个窗口的残留路径也保留",
                   mb.get(w2["id"])["paths"] == [files[0]],
                   mb.get(w2["id"])["paths"])
        self.check("prune 已一次落盘",
                   len(json.loads(_read(self.js).decode("utf-8"))
                       ["windows"][0]["paths"]) == 1)
        self.check("没有可删的再调返回 0", mb.prune() == 0)

    def test_prune_drops_shells(self):
        # 点完「新建小窗口」不放东西留下的是空壳：没路径、没标题 → 该整条清掉。
        files = self.touch("s1.txt")
        shell = mb.create()
        named = mb.create("起过名字")          # 空但用户起过名字 → 留着
        keep = mb.create()
        mb.add_paths(keep["id"], files)
        n = mb.prune()
        self.check("prune 清掉空壳并计入条数", n == 1, n)
        self.check("空壳窗口整条消失", mb.get(shell["id"]) is None)
        self.check("起过名字的空窗口保留", mb.get(named["id"]) is not None)
        self.check("有路径的窗口保留", mb.get(keep["id"]) is not None)
        self.check("空壳清理已落盘",
                   len(json.loads(_read(self.js).decode("utf-8"))
                       ["windows"]) == 2)

    def test_open_ids(self):
        a = mb.create("a")
        b = mb.create("b")
        c = mb.create("c")
        mb.update(b["id"], open=False)
        self.check("open_ids 只返回 open 为真的",
                   sorted(mb.open_ids()) == sorted([a["id"], c["id"]]),
                   mb.open_ids())
        self.check("全关时返回空表",
                   all(mb.update(v["id"], open=False)
                       for v in mb.windows()) and mb.open_ids() == [],
                   mb.open_ids())
        self.check("重新打开后回到列表",
                   mb.update(b["id"], open=True) and
                   mb.open_ids() == [b["id"]], mb.open_ids())

    def test_no_shared_memory(self):
        w = mb.create("共享内存")
        wid = w["id"]
        a = mb.load()
        b = mb.load()
        self.check("两次 load 不是同一个对象", a is not b)
        self.check("两次 load 的 windows 不是同一个 list",
                   a["windows"] is not b["windows"])
        self.check("两次 load 的窗口 dict 不是同一个对象",
                   a["windows"][0] is not b["windows"][0])
        a["windows"][0]["title"] = "被改了"
        a["windows"].append({"id": "w_x"})
        self.check("改一份不影响另一份", b["windows"][0]["title"] == "共享内存",
                   b["windows"][0]["title"])
        self.check("改一份不影响落盘数据",
                   mb.get(wid)["title"] == "共享内存", mb.get(wid)["title"])
        w["title"] = "改返回的 dict"
        self.check("改 create 返回值不影响落盘",
                   mb.get(wid)["title"] == "共享内存", mb.get(wid)["title"])

    def test_save_failure(self):
        f = os.path.join(self.tmp, "blocker")
        with open(f, "wb") as fh:
            fh.write(b"x")
        os.environ["DESKBOX_MINIS_JSON"] = os.path.join(f, "minis.json")
        self.check("save 目标父路径是文件时返回 False",
                   mb.save({"version": 1, "windows": []}) is False)
        self.check("save 失败不抛，load 仍能返回空表",
                   mb.load() == {"version": 1, "windows": []}, mb.load())
        self.check("create 在写失败时仍返回 dict 不抛",
                   isinstance(mb.create(), dict))
        os.environ["DESKBOX_MINIS_JSON"] = self.js
        self.check("恢复可写路径后 save 返回 True",
                   mb.save({"version": 1, "windows": []}) is True)


def main():
    print("=" * 60)
    argv = [sys.argv[0], "-v"]
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MiniBoxTest)
    runner = unittest.TextTestRunner(verbosity=1, stream=sys.stdout)
    res = runner.run(suite)
    print("=" * 60)
    cases = res.testsRun
    print("用例数: %d, 失败: %d, 错误: %d" % (cases,
                                         len(res.failures), len(res.errors)))
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    sys.exit(code)
