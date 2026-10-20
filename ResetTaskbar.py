# -*- coding: utf-8 -*-
"""把 Windows 10 任务栏外观还原成系统默认 —— 双击就完事的小兜底程序。

为什么需要它：盒子里的「任务栏透明」是直接把 accent 策略打到任务栏窗口上的，
盒子要是被强杀 / 任务管理器结束进程，来不及走 release()，任务栏就会一直留在
透明或磨砂的状态。双击这个程序（或者打包出来的 ResetTaskbar.exe）就回到原样。

刻意不 import PySide6 / tkinter / 任何 GUI 库、不建窗口：靠 PyInstaller onefile
打包出来只有几十 KB，出问题时最不容易连它自己也起不来的东西，才有资格当兜底。
唯一的界面是一个 ctypes 直接调的 MessageBoxW，够用了。

用法：
    python ResetTaskbar.py            # 还原，然后弹一个框告诉你结果
    python ResetTaskbar.py --quiet    # 还原，但**什么都不显示**（不弹框、不打印）
                                      # 给批处理 / 快捷方式用

为什么默认要弹框：进压缩包的那个 exe 是用 --noconsole 打包的（避免双击时闪一个
黑框），print 出去的字用户一个字都看不见 —— 双击之后「到底成功没有」就完全没底。
所以默认走 MessageBoxW；--quiet 才把反馈整个关掉。

退出码：0 = 还原成功，1 = 没成功（非 Windows、任务栏窗口没找到、Win32 拒绝）。
弹不弹框不影响退出码。
"""
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deskbox.taskbarfx as taskbarfx

TITLE = "Tomato Desktop Box"
OK_TEXT = "任务栏外观已经还原成系统默认了。"
FAIL_TEXT = "没能还原任务栏外观。可能不在 Windows 10 上，或者任务栏窗口没找到。"

MB_ICONINFORMATION = 0x00000040
MB_ICONWARNING = 0x00000030


def _alert(text, warning=False):
    """把结果告诉用户：Windows 上弹框，其它系统退回 print。绝不抛。

    单独拎成一个函数是有意的 —— 测试直接 patch 它，就不用去碰 windll
    （没有 windll 的平台，属性访问本身就会炸）。
    """
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(
                0, text, TITLE, MB_ICONWARNING if warning else MB_ICONINFORMATION)
        except Exception:               # noqa: BLE001 —— 弹不出框也照常返回退出码
            return False
        return True
    try:
        print(text)                     # 非 Windows：没有任务栏可还原，给行字就行
    except Exception:                   # noqa: BLE001
        return False
    return True


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in args            # 什么都不显示：不弹框、不打印
    try:
        ok = bool(taskbarfx.reset_default())
    except Exception:                   # noqa: BLE001 —— 兜底程序自己绝不能崩
        ok = False
    if not quiet:
        _alert(OK_TEXT if ok else FAIL_TEXT, warning=not ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
