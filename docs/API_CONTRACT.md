# Tomato Desktop Box 数据层接口契约 v1

> 两个并行子任务共同遵守。谁都不许改对方的文件。
> 数据层负责人：store-dev（写 deskbox/store.py）
> UI 层负责人：ui-dev（只改 deskbox/ui_mock.py）

## 目录与常量

收纳根目录：C:\Users\Administrator\DeskBox（与 Desktop 同级同盘）
分类子目录（中文名 -> 目录名）：

| 中文 | 目录名 |
|---|---|
| 应用 | apps |
| 压缩包 | archives |
| 文档 | docs |
| 图片 | images |
| 音视频 | media |
| 脚本 | scripts |
| 文件夹 | folders |
| 待定 | _unsorted |

桌面来源（按扫描顺序）：
1. %USERPROFILE%\Desktop
2. C:\Users\Public\Desktop

扫描时跳过：desktop.ini（不分大小写）、任何以 . 开头的名字。

## store.py 必须提供的公开 API

    ROOT: str                      # 收纳根目录绝对路径
    GROUPS: list[tuple[str, str]]  # [("应用","apps"), ...] 顺序固定，不含"全部"
    DESK_DIRS: list[str]

    class Item:
        path: str        # 绝对路径
        name: str        # 文件名
        group: str       # 中文分类名，取自 GROUPS 的第一列
        is_dir: bool
        size: int        # 目录记 0
        from_dir: str    # 来源桌面目录（DESK_DIRS 之一）
        locked: bool     # True = 只归组不搬（见下方规则）

    def scan() -> list[Item]
        """扫描两个桌面目录，返回全部 Item，按 name 排序（casefold）。"""

    def classify(path: str) -> str
        """返回中文分类名。规则见下。目录优先判为 文件夹。"""

    class Move:
        src: str
        dst: str
        group: str
        reason: str      # 空串=会搬；非空=跳过原因

    class Plan:
        moves: list[Move]        # 全部条目，含跳过的
        @property todo           -> list[Move]   # reason == ""
        @property skipped        -> list[Move]   # reason != ""
        @property by_group       -> dict[str, int]  # 中文分类 -> 会搬的数量

    def plan(items=None) -> Plan
        """生成搬运计划，不碰磁盘。items 为空则内部调 scan()。"""

    def ensure_dirs() -> None
        """在 ROOT 下创建 GROUPS 的全部子目录（已存在则跳过）。"""

    class Report:
        done: list[tuple[str, str]]    # (src, dst) 成功
        failed: list[tuple[str, str]]  # (src, 失败原因)
        @property ok -> int

    def execute(pl: Plan) -> Report
        """真搬。必须先 ensure_dirs()。同名冲突 -> dst 追加 " (2)"、" (3)"。
           每成功一项立刻写 manifest（中途崩溃也不会丢记录）。
           跨盘用 shutil.move。搬完不删除源目录本身。"""

    def manifest_path() -> str        # ROOT/manifest.json
    def manifest_all() -> list[dict] # 记录列表，最新在后
    def undo(n: int = 0) -> Report
        """按 manifest 倒序把文件搬回原位置；n>0 只回滚最近 n 条，n=0 全回滚。
           目标已存在则给回滚文件追加 " (2)"；源已不存在的记录跳过并记 failed。
           成功回滚的记录从 manifest 里删掉。"""

    def delete_to_recycle(paths: list[str]) -> tuple[int, list[str]]
        """送回收站（SHFileOperationW + FOF_ALLOWUNDO | FOF_NOCONFIRMATION）。
           返回 (成功数, 失败项列表)。绝不使用 os.remove。"""

    def open_item(path: str) -> None  # os.startfile，异常吞掉
    def reveal(path: str) -> None     # explorer /select,

    def scan_group(group: str) -> list[Item]
        """读 ROOT/<group 目录名> 下的已收纳内容，Item.path 为收纳目录内真实路径，
           Item.from_dir 为空串，Item.locked=False。目录不存在则返回空列表。"""

## 分类规则（classify，顺序不可变）

1. os.path.isdir(path) -> 文件夹
2. 扩展名小写后：
   - .lnk .exe .url .appref-ms .msi -> 应用
   - .zip .rar .7z .tgz .gz .tar .xz .bz2 -> 压缩包
   - .txt .md .pdf .doc .docx .xls .xlsx .ppt .pptx .ini .csv .json .log -> 文档
   - .png .jpg .jpeg .gif .webp .ico .bmp .svg .psd -> 图片
   - .mp4 .mkv .mp3 .wav .avi .flac .mov .webm .m4a -> 音视频
   - .bat .cmd .ps1 .py .ahk .js .vbs .reg -> 脚本
3. 其余 -> 待定

## locked（只归组不搬）规则

满足任一 -> locked = True，plan 里 Move.reason 写明原因：
- 来自 C:\Users\Public\Desktop -> reason = "公共桌面只读"
- 扩展名在 .efi .bin .ini .bat .ahk .vbs .reg -> reason = "系统/脚本文件保留原位"
- 名字是 desktop.ini -> 不产生 Item（扫描阶段就跳过）

注意：locked 只影响是否搬运，不影响分类显示。

## Plan 里 dst 的算法

dst = ROOT / <该 group 的目录名> / name
目录名映射用 GROUPS 里的第二列。

## 错误约定

- 所有函数不得抛异常给 UI（除参数明显非法）。内部 try/except，失败信息进 Report.failed。
- 日志写 %APPDATA%/DeskBox/deskbox.log（append，utf-8）。目录推导与 ui.json / groups.json 一致（APPDATA 缺失时退回 ~/DeskBox），`store.log_dir()` 可查。**日志永不写 ROOT**，也不为写日志 makedirs ROOT。

## autostart.py / tray.py / appicon.py 必须提供的公开 API

    # deskbox/autostart.py —— 开机自启动（只写 HKCU 的 Run 值）
    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    VALUE_NAME = "Tomato Desktop Box"

    pythonw_path() -> str        # 同目录 pythonw.exe，没有就退 python.exe
    script_path() -> str         # 要自启的脚本绝对路径
    command() -> str             # 写进注册表的那串命令行（带引号）
    is_enabled(value_name=VALUE_NAME) -> bool   # 现读注册表实际值并与 command() 比对；读不到 -> False
    enable(value_name=VALUE_NAME) -> bool       # 写 Run 值；成功 True
    disable(value_name=VALUE_NAME) -> bool      # 删 Run 值；成功 True

    # deskbox/tray.py —— 通知区图标
    class Tray(QSystemTrayIcon):
        __init__(self, parent, icon, on_toggle_panel=None, on_show_panel=None,
                 on_show_all_minis=None, on_settings=None, on_quit=None,
                 on_tb_mode=None, tb_mode=None, on_hide_all_minis=None)
        available() -> bool          # 系统有没有通知区
        show() / hide() / is_visible() -> bool / set_icon(icon)
    # 菜单六项固定顺序：显示主面板(-> on_show_panel，只显不隐) / 显示全部小窗口(-> on_show_all_minis)
    #   / 隐藏全部小窗口(-> on_hide_all_minis，只 hide 不 close、不写 minis.json) / 任务栏透明 ▸ 子菜单
    #   (-> on_tb_mode，三档磨砂/透明/关，默认关) / 设置(-> on_settings) / 退出(-> on_quit)
    # 回调按中文文案 key 取，不用下标 —— 往后插项也不会错位
    # 左键 Trigger -> on_toggle_panel（可见就收，不可见就放）

    # deskbox/appicon.py —— 程序图标
    path() -> str                    # 图标文件路径（可能不存在，交给调用方降级）
    qicon() -> QIcon                 # 缓存 QIcon；16/24/32/48/64/128/256 七档；文件缺失 / 损坏时自有降级画法，绝不返回 null

## 降级约定（三模块共同遵守）

- 三个模块都不向 UI 抛异常：失败一律 False / None / 空转。
- ui_mock 用现成的 `_rel()` 载入，允许缺失：

      AUTO, _AUTO_ERR = _rel("autostart")
      TRAY, _TRAY_ERR = _rel("tray")
      APPICON, _APPICON_ERR = _rel("appicon")

- 模块缺失或不可用时：设置面板里对应的那一行隐藏或置灰并写明原因；托盘不建（`_setup_tray()` 返回 None）；程序图标跳过。
  **绝不假装可用**：开关不许拿本地标志冒充注册表，托盘不许明明没有通知区还给「已收进托盘」的错觉。
- 黄点（最小化）分支：`ui.json` 的 `tray` 键为真 且 `TRAY.available()` 为真 -> 面板 `hide()` 收进托盘；
  否则照旧 `showMinimized()`。**托盘不可用时绝不隐藏面板**，否则用户找不回窗口。
- `ui.json` 新增 `tray` 键（默认 `true`，即「最小化到托盘」）；`groups.json` / `minis.json` 不受影响。
- main() 里 `app.setWindowIcon(APPICON.qicon())`（APPICON 为 None 就跳过）；主面板建好后 `w._setup_tray()`，
  托盘对象挂在 `w._tray`（不留引用会被 GC），可用才 `show()`。
- 托盘「退出」是真退出：小窗口一起关 + `QApplication.quit()`（`setQuitOnLastWindowClosed(False)` 已是 False，必须显式退）。

## UI 侧接线约定（ui-dev 遵守）

- import store，不自己实现任何扫描/分类/移动逻辑。当前 ui_mock._load_items 与 _classify 要改为调 store.scan()，_classify 直接删掉。
- 底部「整理桌面」按钮 -> store.plan() -> 弹预览对话框（列出 各分类 x 个、跳过项及原因）-> 确认后 store.execute() -> 刷新网格。
- 底部「还原」按钮 -> store.undo()（先弹确认框，说明会全部回滚）。
- 顶部 ⟳ 刷新 -> 重新 store.scan() 并重建网格与侧栏计数。
- 网格双击 -> store.open_item()；右键菜单：打开 / 在资源管理器中显示 / 送回收站。
- 网格要能显示收纳后的内容：侧栏选中某分类时，数据源 = 桌面扫描结果（未搬的）+ store.scan_group(该分类)。
- UI 不改 glass.py / icons.py。store-dev 不改 ui_mock.py。
