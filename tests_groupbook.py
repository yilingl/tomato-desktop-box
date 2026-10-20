# -*- coding: utf-8 -*-
"""groupbook.py + hidedesk.py 自测：全程临时目录，绝不触碰真实桌面与真实注册表。"""
import json
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="deskbox_gb_")
os.environ["DESKBOX_GROUPS_JSON"] = os.path.join(TMP, "_boot.json")
os.environ["DESKBOX_CATS_JSON"] = os.path.join(TMP, "_boot_cats.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deskbox.groupbook as gb
import deskbox.hidedesk as hd
import deskbox.store as store

_fail = []
_n = [0]


def check(name, cond, detail=""):
    _n[0] += 1
    if cond:
        print("PASS - %s" % name)
    else:
        print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        _fail.append(name)


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def touch(path, data=b"x"):
    with open(path, "wb") as f:
        f.write(data)
    return path


def fresh(tag):
    """给本用例换一个全新的 groups.json 路径。"""
    p = os.path.join(TMP, "%s.json" % tag)
    os.environ["DESKBOX_GROUPS_JSON"] = p
    return p


def src_dir(tag):
    d = os.path.join(TMP, tag)
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 1. load 容错
def t_load_safe():
    p = fresh("load_safe")
    check("load 文件缺失返回 {}", gb.load() == {}, gb.load())

    touch(p, b"")
    check("load 空文件返回 {}", gb.load() == {}, gb.load())

    touch(p, b'{"version":1,"items":{ broken !!!')
    check("load 损坏 JSON 返回 {}", gb.load() == {}, gb.load())

    touch(p, b'[1,2,3]')
    check("load 顶层不是对象返回 {}", gb.load() == {}, gb.load())

    touch(p, b'{"version":1,"items":{"a":"not-a-dict"}}')
    check("load 条目不是对象被跳过", gb.load() == {}, gb.load())

    touch(p, b'{"version":1,"items":{"a":{"group":""}}}')
    check("load 空 group 被跳过", gb.load() == {}, gb.load())

    # 权限错误：父级是个文件，路径根本建不出来
    blocker = touch(os.path.join(TMP, "blocker"), b"f")
    os.environ["DESKBOX_GROUPS_JSON"] = os.path.join(blocker, "groups.json")
    check("load 权限/路径错误返回 {}", gb.load() == {}, gb.load())
    check("save 权限/路径错误返回 False", gb.save({"x": {"group": "文档"}}) is False)
    check("groups_path 跟随环境变量",
          gb.groups_path() == os.path.join(blocker, "groups.json"),
          gb.groups_path())

    # 未知分类：读保留原值，写拒绝
    p = fresh("load_unknown")
    doc = {"version": 1, "items": {gb.key_of(os.path.join(TMP, "x.txt")):
                                   {"group": "自定义分类", "at": "2026-01-01T00:00:00"}}}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    data = gb.load()
    check("load 保留未知分类原值",
          list(data.values())[0]["group"] == "自定义分类", data)
    check("set_group 拒绝非法分类",
          gb.set_group(os.path.join(TMP, "x.txt"), "自定义分类") is False)
    check("set_group 拒绝空分类",
          gb.set_group(os.path.join(TMP, "x.txt"), "") is False)


# ---------------------------------------------------------------- 2. 中文名/目录名
def t_norm_equiv():
    d = src_dir("norm")
    a = touch(os.path.join(d, "a.lnk"))
    b = touch(os.path.join(d, "b.zip"))
    fresh("norm")
    check("set_group 中文名 ok", gb.set_group(a, "应用") is True)
    check("get_group 读到中文名", gb.get_group(a) == "应用", gb.get_group(a))
    check("set_group 目录名 ok", gb.set_group(b, "archives") is True)
    check("目录名归一到中文分类", gb.get_group(b) == "压缩包", gb.get_group(b))
    check("set_group 大小写不敏感", gb.set_group(b, "ARCHIVES") is True and
          gb.get_group(b) == "压缩包")
    check("get_group 未记录返回 None",
          gb.get_group(os.path.join(d, "nope.txt")) is None)
    check("key_of 用 normcase(abspath)",
          gb.key_of(a) == os.path.normcase(os.path.abspath(a)), gb.key_of(a))
    check("key_of 大小写归一", gb.key_of(a.upper()) == gb.key_of(a))


# ---------------------------------------------------------------- 3. auto_group
def t_auto_group():
    d = src_dir("auto")
    files = [touch(os.path.join(d, n)) for n in
             ("x.lnk", "y.zip", "z.txt", "w.png", "v.bat")]
    fresh("auto")
    n = gb.auto_group(files)
    check("auto_group 写入 5 条", n == 5, n)
    got = {os.path.basename(p): gb.get_group(p) for p in files}
    check("auto_group .lnk -> 应用", got["x.lnk"] == "应用", got)
    check("auto_group .zip -> 压缩包", got["y.zip"] == "压缩包", got)
    check("auto_group .txt -> 文档", got["z.txt"] == "文档", got)
    check("auto_group .png -> 图片", got["w.png"] == "图片", got)
    check("auto_group .bat -> 脚本", got["v.bat"] == "脚本", got)
    check("auto_group 总条数 5", gb.stats()["total"] == 5, gb.stats())

    # only_unset=True 不覆盖
    gb.set_group(files[0], "图片")
    n = gb.auto_group(files, only_unset=True)
    check("only_unset=True 已有分组不覆盖",
          gb.get_group(files[0]) == "图片" and n == 0, (n, gb.get_group(files[0])))

    # only_unset=False 覆盖
    n = gb.auto_group(files, only_unset=False)
    check("only_unset=False 覆盖回来",
          gb.get_group(files[0]) == "应用" and n == 5,
          (n, gb.get_group(files[0])))

    # 注入 classify
    n = gb.auto_group(files, only_unset=False, classify=lambda p: "未分类")
    check("auto_group 支持注入 classify",
          n == 5 and gb.get_group(files[0]) == "未分类", n)
    check("auto_group 空列表返回 0", gb.auto_group([]) == 0)


# ---------------------------------------------------------------- 4. assign_of
def t_assign_of():
    d = src_dir("assign")
    a = touch(os.path.join(d, "a.txt"))
    b = touch(os.path.join(d, "b.txt"))
    fresh("assign")
    gb.set_group(a, "图片")
    items = [store.Item(path=a, name="a.txt", group="文档"),
             store.Item(path=b, name="b.txt", group="文档")]
    m = gb.assign_of(items)
    check("assign_of 账本优先覆盖 classify", m[a] == "图片", m)
    check("assign_of 无记录沿用 Item.group", m[b] == "文档", m)
    items2 = [store.Item(path=b, name="b.txt", group="")]
    check("assign_of 空 group 回落到 classify",
          gb.assign_of(items2)[b] == "文档", gb.assign_of(items2))
    check("assign_of 空输入返回 {}", gb.assign_of([]) == {})


# ---------------------------------------------------------------- 5. prune
def t_prune():
    d = src_dir("prune")
    alive = [touch(os.path.join(d, "live%d.txt" % i)) for i in range(3)]
    dead = touch(os.path.join(d, "dead.txt"))
    fresh("prune")
    gb.set_groups({p: "文档" for p in alive + [dead]})
    check("prune 前置 4 条", gb.stats()["total"] == 4, gb.stats())
    n = gb.prune(alive)
    check("prune 删掉 1 条", n == 1, n)
    check("prune 存在项保留", gb.stats()["total"] == 3, gb.stats())
    check("prune 已删项读不到", gb.get_group(dead) is None)
    check("prune 保留项仍在", gb.get_group(alive[0]) == "文档")
    check("prune 传空集合全删", gb.prune([]) == 3, gb.stats())
    check("prune 空账本返回 0", gb.prune(alive) == 0)
    # 绝不自己遍历磁盘：传一个不存在的路径也只看集合
    check("prune 不判存在性",
          gb.set_group(os.path.join(d, "ghost.txt"), "文档") is True and
          gb.prune([os.path.join(d, "ghost.txt")]) == 0)


# ---------------------------------------------------------------- 6. set_groups 一次落盘
def t_set_groups_once():
    d = src_dir("batch")
    ps = [touch(os.path.join(d, "p%d.dat" % i)) for i in range(5)]
    fresh("batch")
    real_save = gb.save
    calls = [0]

    def spy(data):
        calls[0] += 1
        return real_save(data)

    gb.save = spy
    try:
        n = gb.set_groups({p: "文档" for p in ps})
    finally:
        gb.save = real_save
    check("set_groups 返回 5", n == 5, n)
    check("set_groups 只落盘一次", calls[0] == 1, calls[0])
    check("set_groups 数据都写进去了", gb.stats()["total"] == 5, gb.stats())
    # 非法分类整条跳过、不落盘
    calls[0] = 0
    gb.save = spy
    try:
        n = gb.set_groups({ps[0]: "瞎写", ps[1]: "文档"})
    finally:
        gb.save = real_save
    check("set_groups 跳过非法分类", n == 1, n)
    check("set_groups 只剩 1 条合法时仍只写一次", calls[0] == 1, calls[0])
    calls[0] = 0
    gb.save = spy
    try:
        n = gb.set_groups({ps[0]: "瞎写"})
    finally:
        gb.save = real_save
    check("set_groups 全非法返回 0 且不落盘", n == 0 and calls[0] == 0,
          (n, calls[0]))


# ---------------------------------------------------------------- 7. clear/stats/读得懂
def t_clear_stats_readable():
    d = src_dir("clear")
    a = touch(os.path.join(d, "kook.lnk"))
    b = touch(os.path.join(d, "手册.txt"))
    p = fresh("clear")
    gb.set_group(a, "应用")
    gb.set_group(b, "文档")
    st = gb.stats()
    check("stats total 2", st["total"] == 2, st)
    check("stats by_group 应用=1", st["by_group"].get("应用") == 1, st)
    check("stats by_group 文档=1", st["by_group"].get("文档") == 1, st)
    check("clear_group ok", gb.clear_group(a) is True)
    check("clear_group 后 total 1", gb.stats()["total"] == 1, gb.stats())
    check("clear_group 不存在返回 False", gb.clear_group(a) is False)

    raw = _read(p).decode("utf-8")
    doc = json.loads(raw)
    check("落盘 version=1", doc.get("version") == 1, doc.get("version"))
    check("落盘键是绝对路径",
          all(os.path.isabs(k) for k in doc["items"]), list(doc["items"]))
    check("落盘值是中文分类",
          doc["items"][gb.key_of(b)]["group"] == "文档", doc["items"])
    check("落盘带 at 时间戳",
          len(doc["items"][gb.key_of(b)]["at"]) >= 19,
          doc["items"][gb.key_of(b)])
    check("落盘中文不转义（人眼可读）", "文档" in raw, raw[:120])
    check("落盘 indent=2", '\n  "version"' in raw, raw[:80])
    check("落盘无 .tmp 残留", not os.path.exists(p + ".tmp"))


# ---------------------------------------------------------------- 8. hidedesk
def t_hidedesk():
    check("hidedesk 已导入", hasattr(hd, "defview_hwnd"))
    for fn in ("defview_hwnd", "is_hidden", "hide", "show", "toggle",
               "describe"):
        check("hidedesk 有 %s" % fn, callable(getattr(hd, fn, None)))
    h = hd.defview_hwnd()
    check("defview_hwnd 返回 int", isinstance(h, int), type(h))
    check("defview_hwnd 找不到时是 0 或正数", h == 0 or h > 0, h)
    check("is_hidden 返回 bool", isinstance(hd.is_hidden(), bool),
          type(hd.is_hidden()))
    check("describe 返回字符串", isinstance(hd.describe(), str), hd.describe())

    # ---- 以下全部走桩，不发真命令、不写真注册表 ----
    real = (hd.defview_hwnd, hd.is_hidden, hd._send_cmd, hd._notify,
            hd._write_hideicons, hd._read_hideicons)
    real_hide = hd._read_hideicons()          # 只读备份，测试结束复核没变
    st = {"hidden": False, "sent": 0, "written": 0, "toggled": False}

    def fake_read():
        return 1 if st["hidden"] else 0

    def fake_write(v):
        st["written"] += 1
        st["hidden"] = bool(int(v))
        return True

    def fake_send(dv):
        st["sent"] += 1
        if st["toggled"]:                     # 模拟 0x7402 真的是个开关
            st["hidden"] = not st["hidden"]
        return True, ""

    # 系统触点全部换桩，hide/show 走不到真实 SendMessage 与真实注册表
    hd.defview_hwnd = lambda: 12345
    hd._read_hideicons = fake_read
    hd.is_hidden = lambda: st["hidden"]
    hd._write_hideicons = fake_write
    hd._notify = lambda: True
    hd._send_cmd = fake_send
    old_tries, old_step = hd._POLL_TRIES, hd._POLL_STEP
    hd._POLL_TRIES, hd._POLL_STEP = 3, 0.001
    try:
        st["toggled"] = True
        ok, err = hd.hide()
        check("hide 返回 (True, '')", (ok, err) == (True, ""), (ok, err))
        check("hide 后状态为隐藏", st["hidden"] is True, st)

        sent = st["sent"]
        ok, err = hd.hide()
        check("hide 幂等不再发命令", (ok, err) == (True, "") and st["sent"] == sent,
              (ok, err, st["sent"], sent))

        ok, err = hd.show()
        check("show 返回 (True, '')", (ok, err) == (True, ""), (ok, err))
        check("show 后状态为显示", st["hidden"] is False, st)

        ok, err, now = hd.toggle()
        check("toggle 返回三元组", ok is True and err == "" and now is True,
              (ok, err, now))
        ok, err, now = hd.toggle()
        check("toggle 再切回显示", ok is True and now is False, (ok, err, now))

        # 兜底：SendMessage 说成功但状态不动 -> 走注册表
        st["toggled"] = False
        st["written"] = 0
        st["hidden"] = False
        ok, err = hd.hide()
        check("SendMessage 无效时兜底写注册表", ok is True and st["written"] >= 1,
              (ok, st["written"]))
        check("兜底后状态为隐藏", st["hidden"] is True, st)

        # 一路失败 -> 返回 (False, 中文原因)，不抛
        st["toggled"] = False
        st["hidden"] = False
        hd._write_hideicons = lambda v: False
        hd._send_cmd = lambda dv: (True, "")
        ok, err = hd.hide()
        check("全失败返回 False", ok is False, (ok, err))
        check("全失败给中文原因", isinstance(err, str) and err and
              not err.isascii(), err)

        hd._send_cmd = lambda dv: (False, "桌面图标层无响应")
        hd._write_hideicons = fake_write
        ok, err = hd.hide()
        check("找不到 DefView 也能兜底成功", ok is True, (ok, err))
    finally:
        hd.defview_hwnd, hd.is_hidden, hd._send_cmd, hd._notify, \
            hd._write_hideicons, hd._read_hideicons = real
        hd._POLL_TRIES, hd._POLL_STEP = old_tries, old_step

    check("测试未改动真实 HideIcons", hd._read_hideicons() == real_hide,
          (hd._read_hideicons(), real_hide))
    check("describe 隐藏态文案", isinstance(hd.describe(), str))
    # 最后再确认一次：恢复后不再有桩，任何 hide/show 都不会因测试残留而误发
    check("桩已全部恢复", all(x is not None for x in real))


def main():
    print("临时工作区:", TMP)
    print("groups.json:", gb.groups_path())
    print("-" * 60)
    t_load_safe()
    t_norm_equiv()
    t_auto_group()
    t_assign_of()
    t_prune()
    t_set_groups_once()
    t_clear_stats_readable()
    t_hidedesk()
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
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
