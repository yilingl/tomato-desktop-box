# -*- coding: utf-8 -*-
"""store.py 自测：全部在临时目录里跑，不碰真实桌面。"""
import json
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="deskbox_test_")
USER_DESK = os.path.join(TMP, "Desktop")
PUB_DESK = os.path.join(TMP, "PublicDesktop")
TEST_ROOT = os.path.join(TMP, "DeskBox")
os.makedirs(USER_DESK)
os.makedirs(PUB_DESK)
os.environ["DESKBOX_ROOT"] = TEST_ROOT
# 分类账本也指到临时目录：绝不读真实 %APPDATA%\DeskBox\cats.json
os.environ["DESKBOX_CATS_JSON"] = os.path.join(TMP, "cats.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deskbox.store as store

store.DESK_DIRS = [USER_DESK, PUB_DESK]   # 关键：把扫描源指向假桌面

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


def build_desktop():
    """造一套假桌面：.lnk/.zip/.txt/.png/.bat/.efi + 两个子目录。"""
    touch(os.path.join(USER_DESK, "Chrome.lnk"), b"L" * 10)
    touch(os.path.join(USER_DESK, "备份.zip"), b"Z" * 20)
    touch(os.path.join(USER_DESK, "笔记.txt"), b"T" * 30)
    touch(os.path.join(USER_DESK, "图片.png"), b"P" * 40)
    touch(os.path.join(USER_DESK, "运行.bat"), b"B" * 5)
    touch(os.path.join(USER_DESK, "固件.efi"), b"E" * 6)
    touch(os.path.join(USER_DESK, "desktop.ini"), b"ini")
    touch(os.path.join(USER_DESK, "DESKTOP.INI"), b"ini2")   # 大小写变体
    touch(os.path.join(USER_DESK, ".hidden"), b"h")
    for d in ("子目录A", "子目录B"):
        p = os.path.join(USER_DESK, d)
        os.makedirs(p)
        touch(os.path.join(p, "inner.txt"))
    touch(os.path.join(PUB_DESK, "公共快捷方式.lnk"), b"Q" * 7)


def names(items):
    return sorted(i.name for i in items)


def t_classify():
    j = lambda n: os.path.join(USER_DESK, n)
    check("classify Chrome.lnk -> 应用", store.classify(j("Chrome.lnk")) == "应用",
          store.classify(j("Chrome.lnk")))
    check("classify 备份.zip -> 压缩包", store.classify(j("备份.zip")) == "压缩包")
    check("classify 笔记.txt -> 文档", store.classify(j("笔记.txt")) == "文档")
    check("classify 图片.png -> 图片", store.classify(j("图片.png")) == "图片")
    check("classify 运行.bat -> 脚本", store.classify(j("运行.bat")) == "脚本")
    check("classify 固件.efi -> 未分类",
          store.classify(j("固件.efi")) == "未分类",
          store.classify(j("固件.efi")))
    check("classify 子目录A -> 文件夹", store.classify(j("子目录A")) == "文件夹")


def t_scan():
    items = store.scan()
    check("scan 数量 == 9", len(items) == 9, names(items))
    check("scan 跳过 desktop.ini（含 DESKTOP.INI 大小写变体）和 .hidden",
          not any(i.name.casefold() in ("desktop.ini", ".hidden")
                  for i in items), names(items))
    check("scan 含两个子目录",
          sum(1 for i in items if i.is_dir) == 2, names(items))
    check("scan 按 name casefold 排序",
          [i.name for i in items] == sorted((i.name for i in items),
                                            key=str.casefold))
    by = {i.name: i for i in items}
    check("scan .lnk size == 10", by["Chrome.lnk"].size == 10, by["Chrome.lnk"].size)
    check("scan 目录 size == 0", by["子目录A"].size == 0)
    check("scan 用户桌面项 from_dir 正确",
          by["Chrome.lnk"].from_dir == USER_DESK)
    check("scan 公共桌面项 from_dir 正确",
          by["公共快捷方式.lnk"].from_dir == PUB_DESK)
    check("scan 公共桌面项 locked", by["公共快捷方式.lnk"].locked)
    check("scan 运行.bat locked", by["运行.bat"].locked)
    check("scan 固件.efi locked", by["固件.efi"].locked)
    check("scan 笔记.txt 不 locked", not by["笔记.txt"].locked)
    check("scan 目录不 locked", not by["子目录A"].locked)


def t_plan():
    pl = store.plan()
    check("plan 条目数 == 9", len(pl.moves) == 9, len(pl.moves))
    check("plan todo == 6", len(pl.todo) == 6,
          [m.src for m in pl.todo])
    check("plan skipped == 3", len(pl.skipped) == 3,
          [(m.src, m.reason) for m in pl.skipped])
    reasons = sorted(m.reason for m in pl.skipped)
    check("plan skipped 原因正确", reasons == sorted(
        ["公共桌面只读", "系统/脚本文件保留原位", "系统/脚本文件保留原位"]), reasons)
    bg = pl.by_group
    check("plan by_group == 应用1/压缩包1/文档1/图片1/文件夹2",
          bg == {"应用": 1, "压缩包": 1, "文档": 1, "图片": 1, "文件夹": 2}, bg)
    check("plan by_group 合计 == todo", sum(bg.values()) == len(pl.todo))
    dst = {m.group: m.dst for m in pl.todo}
    check("plan dst 落在 ROOT/docs/笔记.txt",
          dst["文档"] == os.path.join(TEST_ROOT, "docs", "笔记.txt"), dst["文档"])
    check("plan 不碰磁盘", os.path.exists(os.path.join(USER_DESK, "笔记.txt")))


def t_execute():
    pl = store.plan()
    rep = store.execute(pl)
    check("execute ok == 6", rep.ok == 6, (rep.done, rep.failed))
    check("execute failed 为空", not rep.failed, rep.failed)
    check("execute 文件真搬走",
          os.path.exists(os.path.join(TEST_ROOT, "docs", "笔记.txt")))
    check("execute 原位置已空",
          not os.path.exists(os.path.join(USER_DESK, "笔记.txt")))
    check("execute 建全部分类目录",
          all(os.path.isdir(os.path.join(TEST_ROOT, d))
              for _, d in store.GROUPS))
    check("execute 目录也搬了",
          os.path.isdir(os.path.join(TEST_ROOT, "folders", "子目录A")))
    check("execute locked 项仍在桌面",
          os.path.exists(os.path.join(USER_DESK, "运行.bat"))
          and os.path.exists(os.path.join(PUB_DESK, "公共快捷方式.lnk")))
    recs = store.manifest_all()
    check("manifest 记录数 == 6", len(recs) == 6, len(recs))
    r = recs[-1]
    check("manifest 字段齐全",
          set(r) == {"orig", "now", "group", "from", "moved_at", "size"}, set(r))
    check("manifest orig 是原路径",
          any(x["orig"] == os.path.join(USER_DESK, "笔记.txt") for x in recs))
    check("manifest now 是收纳后路径",
          any(x["now"] == os.path.join(TEST_ROOT, "docs", "笔记.txt")
              for x in recs))
    check("manifest group 是中文名",
          any(x["group"] == "图片" for x in recs))
    with open(store.manifest_path(), "r", encoding="utf-8") as f:
        raw = json.load(f)
    check("manifest version == 1", raw.get("version") == 1, raw.get("version"))
    with open(store.manifest_path(), "r", encoding="utf-8") as f:
        txt = f.read()
    check("manifest 中文未被转义", "图片" in txt)
    check("manifest 无 .tmp 残留",
          not os.path.exists(store.manifest_path() + ".tmp"))


def t_idempotent():
    left = store.scan()
    check("execute 后 scan 只剩 locked 项",
          names(left) == sorted(["运行.bat", "固件.efi", "公共快捷方式.lnk"],
                                key=str.casefold), names(left))
    pl = store.plan()
    check("execute 后再次 plan todo == 0", len(pl.todo) == 0,
          [m.src for m in pl.todo])
    rep = store.execute(pl)
    check("execute 空计划 ok == 0", rep.ok == 0 and not rep.failed)
    check("再次 execute 后 manifest 仍是 6", len(store.manifest_all()) == 6)


def t_scan_group():
    g = store.scan_group("文件夹")
    check("scan_group(文件夹) == ['子目录A','子目录B']",
          names(g) == sorted(["子目录A", "子目录B"], key=str.casefold), names(g))
    check("scan_group path 是收纳目录真实路径",
          all(os.path.dirname(i.path) == os.path.join(TEST_ROOT, "folders")
              for i in g), [i.path for i in g])
    check("scan_group from_dir 为空、locked False",
          all(i.from_dir == "" and not i.locked for i in g))
    check("scan_group 不存在分类返回 []", store.scan_group("音视频") == [])
    check("scan_group 未知分类返回 []", store.scan_group("不存在的分类") == [])


def t_conflict():
    # 占位：让 docs 里已存在同名文件
    decoy = os.path.join(TEST_ROOT, "docs", "笔记.txt")
    touch(decoy, b"decoy")
    touch(os.path.join(USER_DESK, "笔记.txt"), b"T" * 30)
    rep = store.execute(store.plan())
    check("冲突搬移成功", rep.ok == 1 and not rep.failed, (rep.done, rep.failed))
    expect = os.path.join(TEST_ROOT, "docs", "笔记 (2).txt")
    check("同名冲突产生 (2)", os.path.exists(expect), rep.done)
    check("占位文件未被覆盖", _read(decoy) == b"decoy")
    rec = [r for r in store.manifest_all() if r["now"] == expect]
    check("manifest 记录的是 (2) 目标", len(rec) == 1, store.manifest_all())
    return expect


def t_undo(expect):
    # 先测部分回滚：最近一条是 (2) 文件，回滚应恢复原本的 orig 名
    rep = store.undo(1)
    check("undo(1) 只回滚一条", rep.ok == 1, (rep.done, rep.failed))
    check("undo(1) 回滚恢复原名 笔记.txt",
          os.path.exists(os.path.join(USER_DESK, "笔记.txt")), rep.done)
    check("undo(1) 后 manifest 剩 6", len(store.manifest_all()) == 6,
          len(store.manifest_all()))
    check("undo(1) 后 (2) 文件已消失", not os.path.exists(expect))
    os.remove(os.path.join(USER_DESK, "笔记.txt"))

    rep = store.undo()
    check("undo 全回滚 ok == 6", rep.ok == 6, (rep.done, rep.failed))
    check("undo failed 为空", not rep.failed, rep.failed)
    check("manifest 清空", store.manifest_all() == [], store.manifest_all())
    for n in ("Chrome.lnk", "备份.zip", "笔记.txt", "图片.png"):
        check("undo 还原 %s" % n, os.path.exists(os.path.join(USER_DESK, n)))
    check("undo 还原目录 子目录A",
          os.path.isdir(os.path.join(USER_DESK, "子目录A")))
    check("undo 还原目录内容",
          os.path.exists(os.path.join(USER_DESK, "子目录A", "inner.txt")))
    check("undo 后收纳目录为空",
          all(not os.listdir(os.path.join(TEST_ROOT, d))
              for _, d in store.GROUPS), "收纳目录有残留")
    check("undo 后 scan 回到 9 项", len(store.scan()) == 9, len(store.scan()))
    rep = store.undo()
    check("空 manifest 再 undo 不报错", rep.ok == 0 and not rep.failed)


def only(name):
    """只把指定名字的一项做成 Plan，避免连带搬走别的用例数据。"""
    for it in store.scan():
        if it.name == name:
            return store.plan([it])
    raise AssertionError("scan 里找不到 %s" % name)


def t_undo_conflict():
    """回滚时原位置已被别人占用 -> 追加 " (2)"。"""
    name = "冲突文件.txt"
    touch(os.path.join(USER_DESK, name), b"real")
    store.execute(only(name))
    check("回滚冲突: 已搬入 docs",
          os.path.exists(os.path.join(TEST_ROOT, "docs", name)))
    touch(os.path.join(USER_DESK, name), b"blocker")   # 占住原位
    rep = store.undo()
    expect = os.path.join(USER_DESK, "冲突文件 (2).txt")
    check("回滚冲突 ok == 1", rep.ok == 1, (rep.done, rep.failed))
    check("回滚冲突产生 (2)", os.path.exists(expect), rep.done)
    check("回滚冲突未覆盖占位文件",
          _read(os.path.join(USER_DESK, name)) == b"blocker")
    check("回滚冲突后 manifest 空", store.manifest_all() == [])
    for p in (os.path.join(USER_DESK, name), expect):
        os.remove(p)
    check("回滚冲突清理后桌面无冲突文件残留",
          not any(i.name.startswith("冲突文件") for i in store.scan()))


def t_undo_missing():
    """源已不存在 -> 进 failed，记录保留。"""
    touch(os.path.join(USER_DESK, "会丢的文件.txt"), b"gone")
    store.execute(only("会丢的文件.txt"))
    now = os.path.join(TEST_ROOT, "docs", "会丢的文件.txt")
    os.remove(now)
    rep = store.undo()
    check("undo 源不存在 ok == 0", rep.ok == 0, (rep.done, rep.failed))
    check("undo 源不存在进 failed", len(rep.failed) == 1, rep.failed)
    check("undo 源不存在时记录保留", len(store.manifest_all()) == 1,
          store.manifest_all())
    _write_empty_manifest()


def _write_empty_manifest():
    store._write_manifest([])


# ---------------------------------------------------------------- 新增 API 用例
def fresh_item(name, group_dir=None, data=b"x"):
    """在 ROOT/<group_dir> 下造一个已收纳文件（或直接给桌面造）。"""
    d = os.path.join(TEST_ROOT, group_dir) if group_dir else USER_DESK
    os.makedirs(d, exist_ok=True)
    return touch(os.path.join(d, name), data)


def t_set_group_from_desktop():
    """① 文件还在桌面 -> set_group 搬进目标分类并记 manifest。"""
    src = touch(os.path.join(USER_DESK, "其实是文档.lnk"), b"A" * 12)
    check("set_group① classify 初判为应用",
          store.classify(src) == "应用", store.classify(src))
    ok, new, err = store.set_group(src, "文档")
    check("set_group① ok", ok and not err, (ok, new, err))
    check("set_group① 新路径在 docs",
          os.path.dirname(new) == os.path.join(TEST_ROOT, "docs"), new)
    check("set_group① 源已不在桌面", not os.path.exists(src))
    recs = store.manifest_all()
    check("set_group① manifest 记一条", len(recs) == 1, recs)
    check("set_group① manifest orig 是原桌面路径",
          recs[0]["orig"] == src, recs[0])
    check("set_group① manifest group 是目标组",
          recs[0]["group"] == "文档", recs[0])
    check("set_group① manifest now 是新路径", recs[0]["now"] == new)
    return new


def t_set_group_inside_root(new):
    """② 已在 ROOT/<别的目录> -> rename，manifest 的 now/group 跟着变。"""
    ok, new2, err = store.set_group(new, "apps")
    check("set_group② 接受英文目录名", ok and not err, (ok, new2, err))
    check("set_group② 落在 apps",
          os.path.dirname(new2) == os.path.join(TEST_ROOT, "apps"), new2)
    check("set_group② 旧路径已消失", not os.path.exists(new))
    recs = store.manifest_all()
    check("set_group② manifest 仍是一条", len(recs) == 1, recs)
    check("set_group② manifest now 跟着变", recs[0]["now"] == new2, recs[0])
    check("set_group② manifest group 跟着变",
          recs[0]["group"] == "应用", recs[0])
    check("set_group② orig 保持不变", recs[0]["orig"] != new2, recs[0])
    # 再改一次，验证按 now 匹配也能持续更新
    ok, new3, err = store.set_group(new2, "图片")
    recs = store.manifest_all()
    check("set_group② 二次改分类仍是一条记录", len(recs) == 1, recs)
    check("set_group② 二次改分类 now 已更新", recs[0]["now"] == new3, recs[0])
    check("set_group② 二次改分类 group 已更新",
          recs[0]["group"] == "图片", recs[0])
    return new3


def t_set_group_conflict(new3):
    """③ 目标位置同名 -> 自动改名，绝不覆盖。"""
    occupied = os.path.join(TEST_ROOT, "docs", os.path.basename(new3))
    touch(occupied, b"occupant")
    ok, dst, err = store.set_group(new3, "文档")
    check("set_group③ ok", ok and not err, (ok, dst, err))
    check("set_group③ 自动改名 (2)",
          os.path.basename(dst).endswith(" (2).lnk"), dst)
    check("set_group③ 未覆盖占位文件", _read(occupied) == b"occupant")
    check("set_group③ manifest 的 now 指向 (2)", store.manifest_all()[0]["now"] == dst)
    os.remove(occupied)
    return dst


def t_set_group_public():
    """公共桌面项（locked）set_group 也应成功。"""
    src = touch(os.path.join(PUB_DESK, "公共其实是文档.txt"), b"P" * 9)
    item = [i for i in store.scan() if i.path == src]
    check("set_group 公共项 scan 里是 locked",
          bool(item) and item[0].locked, item and item[0].locked)
    ok, dst, err = store.set_group(src, "docs")
    check("set_group 公共桌面项成功", ok and not err, (ok, dst, err))
    check("set_group 公共项落在 docs",
          os.path.dirname(dst) == os.path.join(TEST_ROOT, "docs"), dst)
    check("set_group 公共项源已消失", not os.path.exists(src))
    return dst


def t_set_group_errors():
    ok, _, err = store.set_group(os.path.join(USER_DESK, "不存在的东西.txt"), "文档")
    check("set_group 路径不存在 -> ok False 且中文 err",
          not ok and "不存在" in err, (ok, err))
    p = touch(os.path.join(USER_DESK, "合法文件.txt"))
    ok, _, err = store.set_group(p, "不存在的分类")
    check("set_group 非法 group -> ok False 且中文 err",
          not ok and "非法" in err, (ok, err))
    ok, _, err = store.set_group(p, "全部")
    check("set_group '全部' 不是合法分类", not ok and "非法" in err, (ok, err))
    check("set_group 失败后源文件仍在", os.path.exists(p))
    os.remove(p)


def t_ingest():
    """ingest 一批 3 个（含 .bat 与 .efi）全部搬入，不查 locked。"""
    a = touch(os.path.join(USER_DESK, "拖入A.txt"), b"1" * 3)
    b = touch(os.path.join(USER_DESK, "拖入B.bat"), b"2" * 3)
    c = touch(os.path.join(PUB_DESK, "拖入C.efi"), b"3" * 3)
    locked_map = {i.path: i.locked for i in store.scan()}
    check("ingest 前置: .bat 与 .efi 是 locked（.txt 不是）",
          locked_map[b] and locked_map[c] and not locked_map[a],
          locked_map)
    rep = store.ingest([a, b, c, a])          # 末位重复，验证去重
    check("ingest ok == 3（重复路径被去重）", rep.ok == 3, (rep.done, rep.failed))
    check("ingest failed 为空", not rep.failed, rep.failed)
    check("ingest 全部源文件已消失",
          not any(os.path.exists(x) for x in (a, b, c)))
    dests = {os.path.dirname(d) for _, d in rep.done}
    check("ingest 按 classify 自动归类到 scripts/media/未分类",
          dests == {os.path.join(TEST_ROOT, "docs"),
                    os.path.join(TEST_ROOT, "scripts"),
                    os.path.join(TEST_ROOT, "_unsorted")}, dests)
    check("ingest 每个成功项都写了 manifest",
          sum(1 for r in store.manifest_all() if "拖入" in r["orig"]) == 3,
          store.manifest_all())
    # 强制分组
    d = touch(os.path.join(USER_DESK, "强制归类.lnk"), b"4" * 3)
    rep = store.ingest([d], group="文档")
    check("ingest group 覆盖 classify",
          rep.ok == 1 and os.path.dirname(rep.done[0][1]) ==
          os.path.join(TEST_ROOT, "docs"), rep.done)
    # 不存在的路径
    rep = store.ingest([os.path.join(USER_DESK, "根本没有.txt")])
    check("ingest 不存在路径进 failed 且中文原因",
          rep.ok == 0 and rep.failed and "不存在" in rep.failed[0][1], rep.failed)
    # desktop.ini 跳过
    ini = touch(os.path.join(USER_DESK, "desktop.ini"))
    rep = store.ingest([ini])
    check("ingest 跳过 desktop.ini", rep.ok == 0 and not os.path.exists(
        os.path.join(TEST_ROOT, "docs", "desktop.ini")), rep.failed)


def t_ingest_undo():
    """ingest 之后 undo 能全还原。"""
    before = len(store.manifest_all())
    a = touch(os.path.join(USER_DESK, "回滚A.jpg"), b"5" * 3)
    b = touch(os.path.join(PUB_DESK, "回滚B.cmd"), b"6" * 3)
    rep = store.ingest([a, b])
    check("ingest 回滚用例搬入 2 项", rep.ok == 2, (rep.done, rep.failed))
    check("ingest 后 manifest 增加 2 条",
          len(store.manifest_all()) == before + 2, len(store.manifest_all()))
    rep = store.undo()
    check("ingest 后 undo 全还原", rep.ok >= 2, (rep.done, rep.failed))
    check("ingest 后 undo 回到用户桌面", os.path.exists(a), rep.done)
    check("ingest 后 undo 回到公共桌面", os.path.exists(b), rep.done)
    check("ingest undo 后 manifest 清空", store.manifest_all() == [],
          store.manifest_all())


def t_set_group_undo_chain():
    """set_group 的改动也能被 undo 还原到最初位置。"""
    src = touch(os.path.join(USER_DESK, "链式还原.txt"), b"7" * 3)
    ok, p1, _ = store.set_group(src, "应用")
    ok2, p2, _ = store.set_group(p1, "图片")
    check("set_group 链式: 两跳都成功", ok and ok2, (p1, p2))
    rep = store.undo()
    check("set_group 链式 undo ok", rep.ok >= 1, rep.done)
    check("set_group 链式 undo 后文件存在", os.path.exists(src) or
          os.path.exists(p2), (src, rep.done))
    store._write_manifest([])
    for cand in (src, p1, p2):
        if os.path.exists(cand):
            os.remove(cand)


def t_recycle():
    p = touch(os.path.join(TMP, "tmp_recycle_me.txt"), b"bye")
    ok, bad = store.delete_to_recycle([p])
    check("delete_to_recycle 成功计数", ok == 1 and bad == [], (ok, bad))
    check("delete_to_recycle 文件已不在", not os.path.exists(p))
    ok, bad = store.delete_to_recycle([os.path.join(TMP, "根本没有这个文件")])
    check("delete_to_recycle 不存在项进 failed",
          ok == 0 and len(bad) == 1, (ok, bad))


def t_virtual():
    """系统虚拟项（::{CLSID}）：只分类，不进扫描，绝不送去回收站。"""
    import ctypes
    this_pc = "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
    bin_ = "::{645FF040-5081-101B-9F08-00AA002F954E}"
    check("classify(此电脑 CLSID) == 系统应用",
          store.classify(this_pc) == "系统应用", store.classify(this_pc))
    check("classify(回收站 CLSID) == 系统应用",
          store.classify(bin_) == "系统应用", store.classify(bin_))
    check("classify 对 CLSID 不看扩展名",
          store.classify(this_pc + ".txt") == "系统应用",
          store.classify(this_pc + ".txt"))
    check("scan() 不会把虚拟项当成桌面文件",
          all(not str(it.path).startswith("::") for it in store.scan()))

    # 打桩：任何 SHFileOperationW 调用都记一笔，虚拟项必须一次都不触发
    calls = []
    orig = ctypes.WinDLL

    def fake(name, *a, **kw):
        lib = orig(name, *a, **kw)
        if name.lower().startswith("shell32"):
            def boom(*_a, **_kw):
                calls.append(name)
                return 0
            lib.SHFileOperationW = boom
        return lib

    ctypes.WinDLL = fake
    try:
        ok, bad = store.delete_to_recycle([this_pc, bin_])
    finally:
        ctypes.WinDLL = orig
    print("     delete_to_recycle([此电脑, 回收站]) ->", ok, bad)
    check("delete_to_recycle 对虚拟项全部进 failed",
          ok == 0 and bad == [this_pc, bin_], (ok, bad))
    check("delete_to_recycle 对虚拟项没调 SHFileOperationW", not calls, calls)

    p = touch(os.path.join(TMP, "tmp_real_for_virtual.txt"), b"x")
    ok, bad = store.delete_to_recycle([this_pc, p])
    check("混合输入里真实文件照常删、虚拟项进 failed",
          ok == 1 and bad == [this_pc], (ok, bad))
    check("混合输入里真实文件确实没了", not os.path.exists(p))


def t_rename():
    """store.rename：IFileOperation::RenameItem，全部在临时目录里做。"""
    d = os.path.join(TMP, "rename_zone")
    os.makedirs(d, exist_ok=True)

    # ① 普通文件改名
    a = touch(os.path.join(d, "alpha.txt"), b"hello")
    ok, err = store.rename(a, "beta.txt")
    check("rename 普通文件 ok", ok and err == "", (ok, err))
    check("rename 后新名存在", os.path.exists(os.path.join(d, "beta.txt")))
    check("rename 后原名消失", not os.path.exists(a))
    check("rename 不改变所在目录",
          os.path.dirname(os.path.join(d, "beta.txt")) == d)

    # ② 改扩展名
    ok, err = store.rename(os.path.join(d, "beta.txt"), "beta.md")
    check("rename 改扩展名 ok", ok and err == "", (ok, err))
    check("rename 改扩展名后内容不变",
          _read(os.path.join(d, "beta.md")) == b"hello")

    # ③ 无扩展名
    n1 = touch(os.path.join(d, "plain"), b"p")
    ok, err = store.rename(n1, "plain2")
    check("rename 无扩展名 ok", ok and err == "", (ok, err))
    check("rename 无扩展名生效", os.path.exists(os.path.join(d, "plain2")))

    # ④ .lnk 改名（先复制一个真实桌面快捷方式，复制不到就造一个合成的）
    real_lnk = ""
    try:
        import glob
        cand = glob.glob(os.path.join(os.path.expanduser("~"),
                                      "Desktop", "*.lnk"))
        if cand:
            real_lnk = os.path.join(d, os.path.basename(cand[0]))
            shutil.copy2(cand[0], real_lnk)      # 只读真实桌面，写临时目录
    except Exception:
        real_lnk = ""
    if not real_lnk:
        real_lnk = os.path.join(d, "synth.lnk")
        touch(real_lnk, b"L\x00\x00\x00" + b"\x00" * 60)
    size_before = os.path.getsize(real_lnk)
    ok, err = store.rename(real_lnk, "改名后的快捷方式.lnk")
    check("rename .lnk ok", ok and err == "", (ok, err))
    new_lnk = os.path.join(d, "改名后的快捷方式.lnk")
    check("rename .lnk 新名存在", os.path.exists(new_lnk))
    check("rename .lnk 内容大小不变",
          os.path.getsize(new_lnk) == size_before,
          (size_before, os.path.getsize(new_lnk)))

    # ⑤ 空名 / 只空格
    ok, err = store.rename(new_lnk, "")
    check("rename 空名被拒", not ok and err == "名字不能为空", (ok, err))
    ok, err = store.rename(new_lnk, "    ")
    check("rename 只空格被拒", not ok and err == "名字不能为空", (ok, err))

    # ⑥ 非法字符逐个
    for ch in '\\/:*?"<>|':
        ok, err = store.rename(new_lnk, "a%sb" % ch)
        check("rename 非法字符 %r 被拒" % ch,
              not ok and "不能包含" in err, (ch, ok, err))
    ok, err = store.rename(new_lnk, "a\x01b")
    check("rename 控制字符被拒", not ok and "不能包含" in err, (ok, err))

    # ⑦ 超长
    ok, err = store.rename(new_lnk, "z" * 255)
    check("rename 超长被拒", not ok and err == "名字太长", (ok, err))

    # ⑧ 重名冲突：必须 False，且两个文件都还在、内容不变
    k = touch(os.path.join(d, "keep.txt"), b"KEEP")
    t = touch(os.path.join(d, "taken.txt"), b"TAKEN")
    ok, err = store.rename(k, "taken.txt")
    check("rename 重名冲突被拒", not ok and err == "已经有同名文件了", (ok, err))
    check("rename 冲突后源文件还在", os.path.exists(k))
    check("rename 冲突后目标文件还在", os.path.exists(t))
    check("rename 冲突后源内容不变", _read(k) == b"KEEP")
    check("rename 冲突后目标内容不变", _read(t) == b"TAKEN")

    # ⑨ 与原名相同 -> (True, "") 且文件不动
    mt, sz = os.path.getmtime(k), os.path.getsize(k)
    ok, err = store.rename(k, "keep.txt")
    check("rename 同名同目录直接成功", ok and err == "", (ok, err))
    check("rename 同名时文件没被动过",
          os.path.getmtime(k) == mt and os.path.getsize(k) == sz)
    ok, err = store.rename(k, "KEEP.TXT")
    check("rename 仅大小写不同视为同名", ok and err == "", (ok, err))

    # ⑩ 虚拟项与不存在
    ok, err = store.rename("::{20D04FE0-3AEA-1069-A2D8-08002B30309D}", "x")
    check("rename 虚拟项被拒", not ok and err == "系统图标不能重命名",
          (ok, err))
    ok, err = store.rename(os.path.join(d, "根本没有.txt"), "x.txt")
    check("rename 不存在被拒", not ok and err == "文件已经不在了", (ok, err))

    # 收尾：临时区删干净，别影响后面的用例
    try:
        shutil.rmtree(d)
    except Exception:
        pass


def t_rename_under_qapp():
    """回归：QApplication 已把主线程设成 STA 时 rename 仍必须成功。

    Qt 建好 QApplication 后主线程已是 STA，rename 内部再调一次
    CoInitializeEx 拿到的是 S_FALSE(1) 而不是 S_OK(0)。旧代码把 1 当失败，
    于是 GUI 里重命名 100% 报「重命名失败: hr=0x00000001」，纯控制台的
    tests_store.py 完全测不出来。这条用例专门盯这个回归。
    """
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("SKIP - 未安装 PySide6，跳过 QApplication 下的 rename 回归")
        return
    _app = QApplication.instance() or QApplication([])
    try:
        import ctypes
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        ole32.CoInitializeEx.restype = ctypes.c_long
        hr = int(ole32.CoInitializeEx(None, 2)) & 0xFFFFFFFF
        check("QApp 存在时 CoInitializeEx 返回 S_FALSE",
              hr == store.S_FALSE, "hr=0x%08X" % hr)
        check("_co_start 认 S_FALSE 但不要配对 Uninitialize",
              store._co_start(ole32) == (True, False, store.S_FALSE),
              store._co_start(ole32))
    except Exception as e:
        check("QApp 存在时 CoInitializeEx 返回 S_FALSE", False, e)

    d = tempfile.mkdtemp(prefix="deskbox_qapp_")
    os.makedirs(d, exist_ok=True)
    src = touch(os.path.join(d, "gui_src.txt"), b"gui")
    ok, err = store.rename(src, "gui_dst.txt")
    check("QApplication 存在时 rename 成功", ok and err == "", (ok, err))
    check("QApplication 存在时 rename 真生效",
          os.path.exists(os.path.join(d, "gui_dst.txt")) and not os.path.exists(src))
    # 再改一次：第二次仍在 STA 里，同样不能挂
    ok, err = store.rename(os.path.join(d, "gui_dst.txt"), "gui_dst2.txt")
    check("QApplication 存在时连续 rename 仍成功", ok and err == "", (ok, err))
    # 错误 hr 仍必须判失败（用一个假 ole32 喂死值）
    class _BadOle(object):
        def CoInitializeEx(self, p, m):
            return 0x8000FFFF
    check("错误 hr 仍判失败", store._co_start(_BadOle())[0] is False,
          store._co_start(_BadOle()))
    try:
        shutil.rmtree(d)
    except Exception:
        pass


def t_log():
    """日志必须落 %APPDATA%/DeskBox，绝不为写日志 makedirs ROOT。"""
    old_root = store.ROOT
    old_appdata = os.environ.get("APPDATA")
    probe = tempfile.mkdtemp(prefix="deskbox_log_")
    fake_root = os.path.join(probe, "DeskBoxRoot")     # 故意不创建
    fake_appdata = os.path.join(probe, "AppData")
    os.makedirs(fake_appdata)
    try:
        store.ROOT = fake_root
        os.environ["APPDATA"] = fake_appdata
        check("log_dir 跟随 APPDATA",
              os.path.normcase(store.log_dir()) ==
              os.path.normcase(os.path.join(fake_appdata, "DeskBox")),
              store.log_dir())
        store._log("日志落点测试")
        check("_log 之后 ROOT 目录不存在", not os.path.exists(fake_root),
              fake_root)
        logp = os.path.join(fake_appdata, "DeskBox", "deskbox.log")
        check("_log 之后日志落在 APPDATA 目录",
              os.path.exists(logp), logp)
        body = _read(logp).decode("utf-8") if os.path.exists(logp) else ""
        check("日志内容含调用信息", "日志落点测试" in body, body[:120])
        check("APPDATA 目录下没有 manifest.json",
              not os.path.exists(os.path.join(fake_appdata, "DeskBox",
                                              "manifest.json")))
        # 新路径下再写一次：不因为目录已存在而重复出错
        store._log("第二次")
        body = _read(logp).decode("utf-8") if os.path.exists(logp) else ""
        check("_log 可重复追加", body.count("第二次") == 1, body[:200])
        check("重复 _log 仍不建 ROOT", not os.path.exists(fake_root))
    finally:
        store.ROOT = old_root
        if old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old_appdata
        shutil.rmtree(probe, ignore_errors=True)


def main():
    print("临时工作区:", TMP)
    print("DESKBOX_ROOT:", store.ROOT)
    print("-" * 60)
    build_desktop()
    t_classify()
    t_scan()
    t_plan()
    t_execute()
    t_idempotent()
    t_scan_group()
    exp = t_conflict()
    t_undo(exp)
    t_undo_conflict()
    t_undo_missing()
    p1 = t_set_group_from_desktop()
    p2 = t_set_group_inside_root(p1)
    p3 = t_set_group_conflict(p2)
    t_set_group_public()
    t_set_group_errors()
    t_set_group_undo_chain()
    t_ingest()
    t_ingest_undo()
    t_recycle()
    t_virtual()
    t_rename()
    t_rename_under_qapp()
    t_log()
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