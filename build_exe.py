# -*- coding: utf-8 -*-
"""build_exe.py — 把 DeskBox 打成 Windows 单文件 exe（PyInstaller）。

用法（在仓库根跑，任何目录下跑都行，脚本自己认 __file__）：

    python build_exe.py            # 构建，产物 dist\\Tomato Desktop Box.exe
    python build_exe.py --clean    # 先删掉 build\\ 再构建（dist\\ 不删，--noconfirm 会覆盖）

为什么是这几个参数（每条都别随手删）：

  --noconsole
      桌面小工具，不能给用户弹一个黑框。代价是 exe 没有可用的 stdout/stderr，
      所以打包后的报错只能靠退出码 + 出图 + 退出码之外的旁证来查。

  --onefile
      用户要的是「双击就能跑的单文件」，不是一堆散文件。

  --add-data "assets/tomato.ico;assets"    ← 这条是必须的
      deskbox/appicon.py 的 path() 是拿自己的 __file__ 往上找 assets/tomato.ico
      （见 deskbox/appicon.py:31-34）。不把 ico 打进包里，appicon 就会走它自带的
      QPainter 降级画法，程序图标就不是用户挑的那颗番茄了。

  --add-data "<deskbox 源码>;deskbox"      ← 这条也是必须的，而且只有它还不够
      光靠上面那条，ico 会落在 <_MEIPASS>\\assets\\tomato.ico；
      但如果 deskbox 那几个模块是被 PyInstaller 编进 PYZ 的，它们的
      __file__ 就是 <_MEIPASS>\\appicon.py，再 os.pardir 往上就跳到了
      <_MEIPASS> 的**上一级**（也就是 %TEMP%\\assets\\tomato.ico），照样找不到。
      实测（PyInstaller 6.22.3 + Python 3.14）：
        编进 PYZ：appicon.__file__ = <Temp>\\_MEIxxxxxx\\appicon.py
                  -> path() = <Temp>\\assets\\tomato.ico          [不存在]
        当数据发进 _MEIPASS\\deskbox\\：
                  appicon.__file__ = <Temp>\\_MEIxxxxxx\\deskbox\\appicon.py
                  -> path() = <Temp>\\_MEIxxxxxx\\assets\\tomato.ico  [存在，正是 --add-data 放的那颗]
      而且这样装法和源码直跑是同一套：DeskBox.pyw 本来就往 sys.path 里塞
      deskbox/ 目录（见 DeskBox.pyw:3），所以包里放一个 deskbox/ 目录最贴原设计。

  --paths deskbox
      让 PyInstaller 的静态分析能找到 DeskBox.pyw 里的 import ui_mock，
      从而顺着 ui_mock 的真实 import 把 PySide6 该收的都收进来
      （QtCore/QtGui/QtWidgets + windows 平台插件 + imageformats）。
      注意 _rel() 是 importlib 动态导入，静态分析看不见 —— 所以上面那条
      「把 deskbox 整个目录当数据发进去」才是这些模块能跑起来的真正原因。

  --hidden-import <模块>                   ← 这堆也得手工点名，别删
      deskbox/ 是以「数据」进包的（见上面那条），PyInstaller 的静态分析只顺着
      入口 DeskBox.pyw -> import ui_mock 走了一遍，**看不见数据模块内部的 import**。
      凡是只出现在 deskbox/*.py 里、ui_mock 自己没直接 import 的依赖，
      都必须逐条点名，否则冻结后一开机就 ImportError。实测踩过的坑：
        RuntimeError: icons/glass 模块缺失：cannot import name 'wintypes' from 'ctypes'
      原因是 ctypes.wintypes 只在 icons/hidedesk/shellmenu/store 里 import，
      没进 PYZ，而冻结后的 ctypes/__init__.py 里就是找不到 wintypes。

  --version-file build/version_info.txt
      exe 的版本资源（Windows 专有）。里面的 CompanyName 就是用户在任务管理器
      「启动」页看到的「发布者」那一列 —— 不写这项时那一列是**空白**的。
      文件由本脚本生成到 build/（构建产物，不进仓库，也不用提交）。内容是一个
      VSVersionInfo 表达式，PyInstaller 是拿 eval() 读它的
      （PyInstaller/utils/win32/versioninfo.py:559 load_version_info_from_text_file），
      所以里面有两条死规矩：
        1. 只能是一段**表达式**，写成「VSVersionInfo = ...」这种赋值会直接报
           Failed to deserialize VSVersionInfo；
        2. 中文照字面量写就行，别手写十六进制转义 —— 文件按 UTF-8 落盘即可，
           PyInstaller 用 tokenize.detect_encoding 认编码，没有 BOM 也认 UTF-8。
      版本号跟 deskbox/ui_mock.py:73 的 VERSION 对齐（2.1.0 → 2.1.0.0）。

不写死的：distpath/workpath/specpath 都锚在仓库根，跟当前 cwd 无关。

产物：dist\\Tomato Desktop Box.exe；脚本最后会打印它的绝对路径与字节数。
"""
import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

NAME = "Tomato Desktop Box"                 # exe 文件名（含空格，跑起来要有引号）
ENTRY = os.path.join(ROOT, "DeskBox.pyw")   # 入口：跟双击 .pyw 时同一个脚本
DESKBOX_SRC = os.path.join(ROOT, "deskbox")
ICON = os.path.join(ROOT, "assets", "tomato.ico")

# 见文档里 --hidden-import 那段：deskbox/ 是「数据」，它内部的 import 静态分析不到。
# 这张表就是 deskbox/*.py 里出现过、而 ui_mock 自己没直接 import 的顶层依赖
# （用 ast 扫过 14 个模块的全部 import 语句得的，含函数体里的）。
HIDDEN_IMPORTS = [
    "ctypes.wintypes",   # icons/hidedesk/shellmenu/store 声明 Win32 结构体
    "winreg",            # autostart/glass/hidedesk/sysitems 读写注册表自启项
    "zlib",              # cats.py 压分类数据
    "uuid",              # store.py 生成 id
    "dataclasses",       # store.py 的 @dataclass
    "random",            # glass.py/minibox.py 抖动
    "shutil",            # store.py 搬文件
    "subprocess",        # store.py 调外部命令
    "datetime",          # groupbook.py/store.py 时间戳
    "time",              # hidedesk.py/minibox.py
    "math",              # appicon.py 画图用三角函数
    "json", "re", "importlib",
]

DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build")
STAGE = os.path.join(WORK, "_stage", "deskbox")     # deskbox 源码的干净副本
LOG = os.path.join(WORK, "pyinstaller.log")

# 版本资源：任务管理器「启动」页的「发布者」列读的就是 CompanyName，不写就是空白。
# 生成到 build/ 里（构建产物，不进仓库），由 --version-file 交给 PyInstaller。
VERSION = "2.1.0"                       # 跟 deskbox/ui_mock.py:73 的 VERSION 对齐
PUBLISHER = "番茄炒蛋"                   # CompanyName → 任务管理器里的「发布者」
DESC = "Tomato Desktop Box 桌面图标收纳"  # FileDescription → 属性对话框里那份描述
VERFILE = os.path.join(WORK, "version_info.txt")

# 版本资源文件的内容模板（% 占位，见 _write_version_file）。
# 语言/代码页用 Windows 那边最通行的 040904B0 + Translation [1033, 1200]：
# 字符串是以 UTF-16 存的，所以 CompanyName 写中文一样能显示对。
VERSION_TEMPLATE = """\
# 本文件由 build_exe.py 生成（build/ 是构建产物，不进仓库）——别手改这里，改模板。
# PyInstaller 用 eval() 读它，所以整个文件是一段表达式，不是赋值语句。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%(v4)s), prodvers=(%(v4)s),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '%(pub)s'),           # 任务管理器「发布者」
        StringStruct('FileDescription', '%(desc)s'),
        StringStruct('ProductName', '%(name)s'),
        StringStruct('FileVersion', '%(ver)s'),
        StringStruct('ProductVersion', '%(ver)s'),
        StringStruct('InternalName', '%(name)s'),
        StringStruct('OriginalFilename', '%(name)s.exe'),
      ]),
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""

# 构建日志末尾的回看行数：PyInstaller 一屏几百行，只在最后回看十几行就够定位
TAIL_LINES = 25


def _fail(msg):
    """构建前置条件不满足 —— 直接说清楚缺什么，别让用户去看 PyInstaller 的长日志。"""
    sys.stderr.write("build_exe.py: %s\n" % msg)
    raise SystemExit(2)


def _stage_deskbox():
    """把 deskbox/*.py 复制一份到 build/_stage/deskbox，丢掉 __pycache__。

    为什么不留 __pycache__：那是本机 Python 编出来的字节码，跟 exe 里的解释器
    未必是一回事，白占体积还容易让人误判；运行期 Python 自己会从 .py 编。
    """
    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(os.path.dirname(STAGE), exist_ok=True)
    shutil.copytree(DESKBOX_SRC, STAGE,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return STAGE


def _fingerprint(stage):
    """给这次要打进去的 deskbox 源码算个短指纹。

    打包最烦的一种情况是「exe 是哪版打的」说不清：deskbox/ 是构建时复制的副本，
    别人改了源码、忘了重打，现象对不上还查不出来。把这行指纹抄进报告，
    下次对一下就知道手上的 exe 是不是这份源码。
    """
    h = hashlib.sha256()
    for fn in sorted(os.listdir(stage)):
        if fn.endswith(".py"):
            with open(os.path.join(stage, fn), "rb") as fp:
                h.update(fn.encode("utf-8") + b"\0" + fp.read())
    return h.hexdigest()[:16]


def _write_version_file():
    """生成 exe 的版本资源文件（build/version_info.txt），返回它的路径。

    为什么非得有这个文件：任务管理器「启动」页有一列「发布者」= exe 版本资源里的
    CompanyName，没有版本资源的那一列就是空白。PyInstaller 只在给了 --version-file
    时才往 exe 里写版本资源，所以要自己生成一份。

    两条不能违反的规矩（见 versioninfo.py:559 load_version_info_from_text_file）：
      * 整个文件必须是一段可 eval 的**表达式**，写成「VSVersionInfo = ...」赋值会报
        Failed to deserialize VSVersionInfo；
      * 中文直接写字面量，文件按 UTF-8 落盘（走 tokenize.detect_encoding 认编码），
        不需要 BOM、也不要手写十六进制转义。
    """
    parts = (VERSION.split(".") + ["0", "0"])[:4]
    text = VERSION_TEMPLATE % {
        "v4": ", ".join(parts),          # FixedFileInfo 要的 4 段数字：2, 1, 0, 0
        "ver": ".".join(parts),          # 字符串型的 4 段：2.1.0.0
        "pub": PUBLISHER,
        "desc": DESC,
        "name": NAME,
    }
    os.makedirs(WORK, exist_ok=True)
    with open(VERFILE, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(text)
    return VERFILE


def _pyinstaller_args(stage):
    """PyInstaller 命令行。用 subprocess 调 python -m PyInstaller（二选一里挑这个）。

    挑 subprocess 而不是 from PyInstaller.__main__ import run 的原因：构建日志
    完整地落在子进程里，收得到、能 tee 进文件，也不会把 PyInstaller 的
    import 副作用留在本进程里；失败时退出码天然可判。
    """
    sep = ";"                                   # Windows 上 --add-data 的源/目标分隔符
    return [
        sys.executable, "-m", "PyInstaller",
        "--noconsole",
        "--onefile",
        "--name", NAME,
        "--icon", ICON,
        # Windows 专有：把版本资源（发布者等）写进 exe，没有它任务管理器那列是空白。
        "--version-file", VERFILE,
        "--add-data", "%s%sassets" % (ICON, sep),
        "--add-data", "%s%sdeskbox" % (stage, sep),
        "--paths", DESKBOX_SRC,
    ] + [
        a for mod in HIDDEN_IMPORTS for a in ("--hidden-import", mod)
    ] + [
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", WORK,
        "--noconfirm",
        ENTRY,
    ]


def _run(args):
    """跑 PyInstaller，输出一边打到屏上一边攒起来，最后回看末尾若干行。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    tail = []
    proc = subprocess.Popen(
        args, cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, encoding="utf-8", errors="replace",
    )
    with open(LOG, "w", encoding="utf-8") as fp:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            print(line, flush=True)
            fp.write(line + "\n")
            tail.append(line)
            if len(tail) > TAIL_LINES:
                del tail[0]
    return proc.wait(), tail


def _check_warn():
    """看一眼 PyInstaller 的 warn 文件：入口的 import ui_mock 没被解析到就白打了。

    真发生了的话 PYZ 里不会带走 PySide6，exe 一开机就 ImportError，
    而且 --noconsole 把报错吞了，现象只能是「双击没反应」—— 所以在这里拦住。
    """
    warn = os.path.join(WORK, NAME, "warn-%s.txt" % NAME)
    if not os.path.isfile(warn):
        return []
    hits = []
    with open(warn, "r", encoding="utf-8", errors="replace") as fp:
        for line in fp:
            if "ui_mock" in line or "appicon" in line:
                hits.append(line.strip())
    return hits


def _check_archive(exe):
    """开产物里的 CArchive 目录，确认图标和 deskbox 真躺在它们该在的位置。

    这条替代不了跑一遍 exe，但拦的是最阴的一种坏法：appicon.path() 是拿自己的
    __file__ 往上找 assets/tomato.ico 的，包里布局一变（比如 deskbox 被编进 PYZ
    而不是当数据发），ico 明明还在包里、程序也不报错，只是图标**静默**换成
    appicon 里 QPainter 画的那颗简化番茄 —— 不盯着图标看根本发现不了。
    所以这里按源码清单逐个数，少一个就红。
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except Exception as exc:                     # API 变了就别拦住构建
        return "（跳过：%s）" % exc
    try:
        arc = CArchiveReader(exe)
        names = set(arc.toc)
    except Exception as exc:
        return "（跳过：%s）" % exc

    want = ["assets%s%s" % (os.sep, os.path.basename(ICON))]
    for fn in sorted(os.listdir(DESKBOX_SRC)):
        if fn.endswith(".py"):
            want.append("deskbox%s%s" % (os.sep, fn))
    missing = [w for w in want if w not in names]
    if missing:
        _fail("产物里少了这些文件（图标会静默降级成画出来的那颗番茄）：%s"
              % "、".join(missing))
    return "assets/tomato.ico + deskbox/ 共 %d 个 .py 都在" % (len(want) - 1)


def _check_version(exe):
    """把版本资源从产物里读回来，确认「发布者」真的是番茄炒蛋。

    跟 _check_archive 是同一类兜底：版本资源写失败不一定会让退出码非零，
    而现象只是任务管理器里那一列空白 —— 没人会为了它去翻构建日志，
    所以在这里读回来对一遍，对不上直接红。
    """
    try:
        from PyInstaller.utils.win32.versioninfo import \
            read_version_info_from_executable
    except Exception as exc:                     # API 变了就别拦住构建
        return "（跳过：%s）" % exc
    try:
        info = read_version_info_from_executable(exe)
        got = {}
        for kid in info.kids:                    # StringFileInfo / VarFileInfo
            for table in getattr(kid, "kids", []):
                for entry in getattr(table, "kids", []):
                    if getattr(entry, "name", None) and hasattr(entry, "val"):
                        got[entry.name] = entry.val
    except Exception as exc:
        return "（跳过：%s）" % exc

    if got.get("CompanyName") != PUBLISHER:
        _fail("产物里的 CompanyName 是 %r，不是 %r —— 任务管理器「发布者」会不对"
              % (got.get("CompanyName"), PUBLISHER))
    return "发布者=%s / ProductName=%s / FileDescription=%s / 版本=%s（%d 个字符串字段）" % (
        got.get("CompanyName"), got.get("ProductName"), got.get("FileDescription"),
        got.get("FileVersion"), len(got))


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    clean = "--clean" in argv
    if clean and os.path.isdir(WORK):
        print("[build_exe] --clean：删掉 %s" % WORK)
        shutil.rmtree(WORK, ignore_errors=True)

    for path, what in ((ENTRY, "入口脚本"), (ICON, "图标文件"),
                       (DESKBOX_SRC, "deskbox 目录")):
        if not os.path.exists(path):
            _fail("%s不存在：%s" % (what, path))

    try:
        import PyInstaller                                    # noqa: F401
    except ImportError:
        _fail("当前解释器里没有 PyInstaller，先装：%s -m pip install pyinstaller"
              % sys.executable)

    print("[build_exe] 解释器：%s" % sys.executable)
    print("[build_exe] 仓库根：%s" % ROOT)
    stage = _stage_deskbox()
    print("[build_exe] deskbox 源码副本：%s（已剔除 __pycache__）" % stage)
    print("[build_exe] 源码指纹：%s（deskbox/*.py 汇总，用于核对 exe 是哪个版本打的）"
          % _fingerprint(stage))

    vfile = _write_version_file()
    print("[build_exe] 版本资源：%s（发布者 CompanyName=%s，版本 %s.0）"
          % (vfile, PUBLISHER, VERSION))

    args = _pyinstaller_args(stage)
    print("[build_exe] 命令：%s" % " ".join(
        '"%s"' % a if " " in a else a for a in args[1:]))
    code, tail = _run(args)

    print("")
    print("========== PyInstaller 日志最后 %d 行 ==========" % TAIL_LINES)
    for line in tail:
        print(line)
    print("========== 日志全文：%s ==========" % LOG)

    if code != 0:
        _fail("PyInstaller 退出码 %d，构建失败（看上面这段和 %s）" % (code, LOG))

    hits = _check_warn()
    if hits:
        _fail("PyInstaller 没解析到入口的 import（会有 ImportError 风险）：\n  "
              + "\n  ".join(hits))

    exe = os.path.join(DIST, NAME + ".exe")
    if not os.path.isfile(exe):
        _fail("构建报了成功但找不到产物：%s" % exe)

    print("[build_exe] 包内布局：%s" % _check_archive(exe))
    print("[build_exe] 版本资源：%s" % _check_version(exe))

    size = os.path.getsize(exe)
    print("")
    print("[build_exe] exe 绝对路径：%s" % exe)
    print("[build_exe] exe 字节数：%d（%.1f MiB）" % (size, size / 1048576.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
