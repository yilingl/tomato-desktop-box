# Tomato Desktop Box 原生右键菜单 — 探针结论（2026-10-19）

目标：面板里右键图标，弹出**和桌面右键完全一样**的菜单（含火绒 / 360 / WPS / 百度网盘 / ToDesk / QQ / 夸克 / 豆包等第三方扩展）。

## 失败路径（不要再走）
| 路线 | 结果 |
|---|---|
| `IShellItem::BindToHandler(BHID_SFObject/SFUIObject/SFViewObject/Storage)` | 全部 `hr=0x800401E5` REGDB_E_CLASSNOTREG |
| `IShellFolder::GetUIObjectOf(riid=IContextMenu)` | 只有系统内置 10–12 项，**第三方扩展一个都没有** |
| 手工转发 `WM_INITMENUPOPUP` 给 IContextMenu2::HandleMenuMsg / IContextMenu3::HandleMenuMsg2 | `forwarded=0`，项数 11→11 完全不变 |

第三方扩展挂在 `SHCreateDefaultContextMenu` 的完整宿主环境上（site / 多线程 / association info），纯手工 `GetUIObjectOf` 拿不到。

## 可行路径（已实测 PASS）

```
CoInitializeEx(None, COINIT_APARTMENTTHREADED=0x2)

# 1. 父目录 pidl
pidlFolder = SHParseDisplayName(dirname(path))

# 2. 逐项
full = SHParseDisplayName(path)
SHBindToParent(full, IID_IShellFolder, &psf, &child_pidl)

# 3. DEFCONTEXTMENU
DEFCONTEXTMENU{ hwnd, pcmcb=None, pidlFolder, psf, cidl, apidl[],
                punkAssociationInfo=None, cKeys=0, aKeys=None }

# 4. 建菜单对象
SHCreateDefaultContextMenu(&dcm, IID_IContextMenu, &pcm)   # hr=0

# 5. 填菜单
hmenu = CreatePopupMenu()
IContextMenu::QueryContextMenu(hmenu, indexMenu=0, idCmdFirst=1, idCmdLast=0x7FFF,
                              flags = CMF_EXPLORE(0x04) | CMF_EXTENDEDVERBS(0x100))

# 6. 弹菜单（原生，DPI / 主题 / 阴影全由系统负责）
cmd = TrackPopupMenuEx(hmenu, TPM_RETURNCMD|TPM_RIGHTBUTTON, x, y, hwnd, &tpm)

# 7. 执行
CMINVOKECOMMANDINFO{ cbSize=56, fMask=0, hwnd, lpVerb=(void*)(cmd-idCmdFirst), nShow=SW_SHOWNORMAL }
IContextMenu::InvokeCommand(&cmi)
```

### 实测数据
- 008.png：`menuItems=53`（顶层）+ 子菜单展开共 79 项，火绒 3 项 / 360 压缩 5 项 / WPS 3 组 / 百度网盘 3 项 / ToDesk 1 项 / QQ 3 项 / 夸克 / 豆包全部出现
- 2.0.zip：`menuItems=40`，解压到 / 解压到当前文件夹 / WPS解压 全部出现
- `InvokeCommand`（复制为路径，cbSize=56 fMask=0）`hr=0x00000000`，剪贴板真的变成 `"C:\\Users\\Administrator\\Desktop\\008.png"`
- 耗时：首次 bind 5.8ms + create 1.0ms + query 223.8ms（DLL 首次加载）；第二次 bind 1.2ms + create 0.4ms + query 48.2ms

## 两个致命细节（踩了 4 轮才对）

1. **`cbSize` 必须是 56**（`sizeof(CMINVOKECOMMANDINFO)`），不是 `CMINVOKECOMMANDINFOEX` 的 96。
   - `cbSize=96 + fMask=0` → hr=0 但**什么都没执行**（静默失败）
   - `cbSize=96 + fMask=CMIC_MASK_UNICODE(0x4000)` → `hr=0x80070057`
   - `cbSize=56, fMask=0, lpVerb=(void*)(cmd-1)` → `hr=0` 真实生效 ✅
2. **`lpVerb` 低字放的是 `cmd - idCmdFirst`**（idCmdFirst 必须给 1，不是 0x7FFF）。
   - 给 `cmd` 本身 → `hr=0x80004005 / 0x80070057`
   - 给 `cmd - 1` → `hr=0` ✅

## vtable 索引备忘
| 接口 | 索引 |
|---|---|
| IUnknown | 0 QueryInterface / 1 AddRef / 2 Release |
| IShellFolder | 10 GetUIObjectOf |
| IContextMenu | 3 QueryContextMenu / 4 InvokeCommand / 5 GetCommandString |
| IContextMenu2 | 6 HandleMenuMsg |
| IContextMenu3 | 7 HandleMenuMsg2 |

`GetCommandString(cmd, GCS_VERBW=4, NULL, buf, 512)` 签名共 5 个参数（this 之外 5 个？错传会 access violation）。

## 子菜单填充
`TrackPopupMenuEx` 阻塞期间，系统向菜单所属窗口发 `WM_INITMENUPOPUP(0x0117)` / `WM_DRAWITEM` / `WM_MEASUREITEM`。
必须把这些消息转发给 `IContextMenu3::HandleMenuMsg2`（拿不到就退回 `IContextMenu2::HandleMenuMsg`），子菜单才会填出内容。
Qt 里用 `QAbstractNativeEventFilter` 拦 `nativeEvent`，菜单存活期间不要释放 `pcm2/pcm3`。

## 风险
`TrackPopupMenuEx` 必须在 UI 线程调用；菜单弹出期间 Tomato Desktop Box 主窗口不应被销毁。
`InvokeCommand` 可能弹第三方 UAC / 浏览器窗口，属正常行为。
