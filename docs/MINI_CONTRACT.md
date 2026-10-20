# 小窗口（MiniWindow）接口契约 v1

主面板（MainWindow）之后新增的第二层界面：桌面上可以开任意多个**小窗口**，每个小窗口里放一批用户自己挑的图标。

## 产品边界（用户已确认）

1. **共存**：主面板继续当总入口；小窗口是独立的顶层窗口，可以开到任意多个，互不影响。主面板关掉小窗口还在。
2. **放图标的两条路**：从主面板把图标拖进来；或直接从 Windows 桌面把文件拖进来。两种都只是「登记进这个小窗口」，**不移动、不复制、不删除任何文件**。
3. **操作与主面板完全一样**：双击打开、右键同一套菜单（打开 / 在资源管理器中显示 / 重命名 / 删除到回收站 / 更多操作（原生菜单）/ 移到分类 / 复制路径），外加一条小窗口专属的「从小窗口移除」。
4. **自动网格**：图标列数随窗口宽度自动增减，超出就滚动。用户只调窗口大小。
5. **外观**：跟随全局五档（玻璃 / 黑玻璃 / 深色 / 浅色 / 纯色），每个小窗口有自己的「固定位置」开关（**新建时默认关**，顶栏直接能拖；锁住后拖不动，改大小照样能改）。
   顶栏右侧是一个 **···** 按钮（不再是黄/绿/红三个交通灯），点开只有两项：「关闭这个小窗口 / 删除这个小窗口」（用户明确不要最小化和最大化）；
   `MiniWindow._on_more_menu()` 负责弹它，`main._dbg_menupop` 为真时只 popup 不阻塞，探针靠这个取菜单。
6. **内容固定**：一个窗口里放哪几个就是哪几个，跟「分类」无关。主面板删分类不影响它。
7. **关闭＝隐藏但记住**：点 ··· → 关闭这个小窗口只是关掉，窗口和清单都留着，能从主面板重新打开，位置尺寸不变。要彻底删掉走 ··· → 删除这个小窗口。
8. **新建入口**：主面板顶栏加一个按钮，点一下直接开一个新的空白小窗口。
9. **永远待在桌面这一层**：小窗口不置顶，而且始终压在 Z 序最底下 —— 点它、激活它都不会盖到别的程序上面。
   实现：MiniWindow._apply_flags() 带 Qt.WindowStaysOnBottomHint；MiniWindow._keep_bottom() 用
   SetWindowPos(hwnd, HWND_BOTTOM, 0,0,0,0, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE) 压回底层，
   在 showEvent 和 changeEvent(ActivationChange) 里各补一次；_open_mini() 不再 raise_() /
   activateWindow()（任务栏按钮照常可用，没有用 WS_EX_NOACTIVATE 那种硬招）。主面板不受影响。
10. **互相吸附**：拖动时跟别的小窗口自动贴齐 —— 12px 内吸上，并排贴死（`左 = 邻右 + 1`）、上下贴死、
   同侧边齐平、拐角一起吸。吸住之后拉尺寸，**贴着右边 / 下边的邻居跟着一起挪**，两块永远贴死：
   不重叠、不留缝。
11. **拉尺寸也吸（顿一下）**：快跟邻居一样长、一样宽，或者底边 / 右边正好贴住邻居的边的时候，
   尺寸直接吸到正正好，光标要再走超过 12px 才拽得开 —— 这一下就是「长度一样了」的反馈。
   右下角拖动是自己接管的（`MiniWindow._grip_event()`，在 `_grip` 上装了 eventFilter），
   因为磁吸得按「按下那一刻的尺寸 + 光标绝对位移」算，不能跟 QSizeGrip 内部抢锚点。
   实现：`SNAP_PX = 12`、`_snap_pos()`（拖位置）、`_snap_size()`（拖右下角尺寸）、`_snap_side()`（拖单边尺寸）、`_follow()`（推邻居）。
  **五条边都能拉**：右下沿用 `QSizeGrip`，左 / 左下 / 下 / 右 是四个透明热区（`_edges` 字典 + `_place_edges()`），
  全部走同一个 `_grip_event(ev, edge)`；动哪条边只算哪条边，其它边钉死在按下那一刻的位置（`setGeometry` 而不是 `resize`）。
  设置里关掉「小窗口自动粘连」（`main._snap_on = False`，存 ui.json 的 `snap` 键，默认开）时，
  `_snap_pos` / `_snap_size` / `_snap_side` 一律原样返回，`_follow()` 也不推邻居。
   小窗口之间 + 屏幕四边都吸（左边贴屏幕左边、右边贴屏幕右边，上下同理，`_screen_rect()` 取可用区域避开任务栏），
   主面板不参与吸附。

## 不做（用户明确说不用学）

天气、日历、搜索框、音乐播放器。图片里那几块一律不做。

## 数据层：deskbox/minibox.py（store-dev 负责）

持久化文件 `%APPDATA%\DeskBox\minis.json`（环境变量 `DESKBOX_MINIS_JSON` 可覆盖，测试用）。

```json
{
  "version": 1,
  "windows": [
    {
      "id": "w1719000000a3f",
      "title": "我的桌面",
      "x": 120, "y": 120, "w": 340, "h": 280,
      "pinned": false,
      "open": true,
      "paths": ["C:\\Users\\Administrator\\Desktop\\QQ.lnk"]
    }
  ]
}
```

字段语义：

- `id`：唯一字符串，创建后永不变。`create()` 生成，形如 `w<毫秒时间戳><3位十六进制>`
- `title`：窗口标题，允许空串（UI 显示时回落成「小窗口」+序号）
- `x/y/w/h`：屏幕坐标与尺寸（整数）
- `pinned`：是否固定位置（**新建默认 false**；老记录里缺这个键时按 true 读，保持旧行为）
- `open`：是否显示着。**关闭只把 open 置 false，不删条目**
- `paths`：图标绝对路径列表，顺序即显示顺序

### 公开 API

| 签名 | 语义 |
|---|---|
| `json_path() -> str` | minis.json 的完整路径（APPDATA 缺失退 ~/DeskBox） |
| `DEFAULT_W = 340` / `DEFAULT_H = 280` / `MIN_W = 200` / `MIN_H = 160` | 尺寸常量 |
| `load() -> dict` | 永远返回 `{"version":1,"windows":[...]}`，文件不存在/坏了返回空表，绝不 None、绝不抛 |
| `save(data) -> bool` | 原子写（.tmp → os.replace），失败返回 False 不抛 |
| `windows() -> list[dict]` | 规整后的窗口列表（补齐缺省字段、类型转 int/bool/list），顺序＝文件顺序 |
| `get(wid) -> dict or None` | 单个窗口的规整副本 |
| `create(title="", x=None, y=None, w=DEFAULT_W, h=DEFAULT_H) -> dict` | 新建并落盘，返回该窗口 dict。x/y 为 None 时按「屏幕可用区居中 + 每个新窗口偏移 28px」算 |
| `update(wid, **fields) -> bool` | 只认白名单字段 title/x/y/w/h/pinned/open，非法字段忽略；w/h 不小于 MIN_W/MIN_H；不存在返回 False |
| `remove(wid) -> bool` | 真删这一条（UI 的「删除这个小窗口」用） |
| `add_paths(wid, paths) -> (added:int, skipped:list[str])` | 追加到末尾；按 `os.path.normcase(os.path.abspath(p))` 去重（窗口内已有的、同批次重复的都算 skipped）；不存在的路径、`desktop.ini`、以 `.` 开头的文件名进 skipped（返回被跳过的原始字符串） |
| `set_paths(wid, paths) -> int` | 整体替换，返回最终条数 |
| `remove_path(wid, path) -> bool` | 从该窗口移除一个 |
| `prune() -> int` | 清账本：删掉已不存在的路径，并整条删掉「没路径也没标题」的空壳窗口，返回清掉的条数 |
| `open_ids() -> list[str]` | 所有 `open` 为真的窗口 id |

约束：

- 只依赖标准库 `json` / `os` / `time` / `random`，**不 import store / groupbook / cats**（避免循环依赖）。
- 所有写操作失败一律返回 False/0 + 不抛异常。
- 不碰桌面，不碰 ROOT（`C:\Users\Administrator\DeskBox`）。

## UI 层：deskbox/ui_mock.py（ui-dev 负责）

### 1. `MiniWindow(QWidget)` 新类

- 无边框 + `WA_TranslucentBackground`，`paintEvent` 调 `glass.GLASS.paint(p, local, global_rect, RADIUS, density=<主面板当前浓度>, mode=bg_mode())` —— 与主面板完全同款玻璃，切档时跟着变
- 顶栏 34px：左边标题（双击弹 `NameDialog` 改名，最多 20 字），右边 🔒 固定位置（复用 `RoundButton` + `set_dim`；开 = 🔒 / 关 = 🔓）+ **`···` 按钮**（`RoundButton("···", "更多")` → `_on_more_menu()`），**没有交通灯**
  - **`···` → 关闭这个小窗口 = `minibox.update(wid, open=False)` + `hide()`，不删数据；删除这个小窗口 = 真删条目（二次确认）**
- 主体：复用 `DropGrid` + `IconDelegate`，自动网格（IconMode，`gridSize` 按窗口可用宽度算列）
- 右下角 `QSizeGrip` 可拖拽改大小（参照主面板 `_grip`）；左 / 左下 / 下 / 右四条边是透明热区，都走 `_grip_event(ev, edge)`，**没有最小化 / 最大化**；`resizeEvent`/`moveEvent` 都写回 minibox（300ms 防抖，参照主面板 `_queue_save`）
- 双击图标 → `STORE.open_item`
- 右键图标 → 与主面板一模一样的那套菜单 + 末尾加一条「从小窗口移除」+ 「图标大小」（小 / 中 / 大，勾在当前的档位上，跟主面板共用同一个设置；顶栏右键里也有一份，两处都改同一个档位）
- 右键窗口空白处 → 桌面的原生背景菜单（复用 `_shell_bg_menu` 同款逻辑，走 `SM.popup_background`）
- 右键顶栏 → 小窗口自己的菜单：重命名窗口 / 固定位置 / 图标大小（跟随主面板）/ 打开主面板 / **删除这个小窗口**
- 空状态：灰字「把图标拖进来，或者从主面板拖」
- 拖入：接受 `DRAG_MIME`（主面板拖来的）与 `urls`（桌面拖来的），两者都调 `minibox.add_paths(wid, paths)` 然后刷新网格；**绝不做任何文件操作**
- 拖出：**只带 DRAG_MIME，不带 urls**（避免把用户的桌面文件真的拖到别的文件夹里）；往外拖不产生文件操作

### 2. 主面板接入

- 顶栏在 📌 右边加一颗 `RoundButton("▤", "小窗口")`
  - **左键单击 = 直接 `minibox.create()` 并打开一个空白 MiniWindow**
  - **右键 = 管理菜单**：列出所有窗口（开着的打勾，关着的不打勾，点一下切换显示），末尾「新建小窗口」
- `MainWindow.closeEvent`：关主面板 = 整个程序收工。还开着的小窗口**跟着一起 `hide()`，但绝不 `close()`** ——
  `MiniWindow.closeEvent` 会把 `open` 写成 `false`，那样下次开主面板就再也看不到它们了。所以
  `MainWindow.closeEvent` 先把它们的 wid 记进 `self._hidden_minis`，只隐藏、不改账本，再走正常退出流程。
- 启动时（`MainWindow.__init__` 之后）把 `minibox.open_ids()` 里的窗口都恢复出来；顺带跑一次 `minibox.prune()`
  清掉已删除的文件与空壳窗口（没路径也没标题的那种）。
- `MainWindow.showEvent`：**不是**第一次 show 就调 `_resume_minis()`，把 `_hidden_minis` 里的窗口原样开回来 ——
  这就是「主面板关掉时跟着关、主面板再打开时跟着开」的实现。第一次 show 交给 `__init__` 末尾的
  `_restore_minis()`，由 `self._shown_once` 区分。

### 3. 主面板关闭后仍要能重开

`MiniWindow` 的顶栏右键菜单里必须有「打开主面板」，调 `MainWindow.show()` + `raise_()` + `activateWindow()`。

## 验收口径

- `tests_minibox.py` 全绿（store-dev 出）
- UI 侧探针用 tempfile + `DESKBOX_MINIS_JSON` / `DESKBOX_GROUPS_JSON` / `DESKBOX_UI_JSON` 隔离，断言：新建 / 拖动加入 / 去重 / 关闭再打开 / 位置尺寸持久化 / 三档玻璃都能绘制 / 图标可点 / 菜单结构 / 删除窗口 / 主面板关闭后小窗口仍在
- 真实桌面零改动（DESK0 / DESK1 项数不变，无 `(2)` 残留，`C:\Users\Administrator\DeskBox` 不存在）