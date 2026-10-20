# -*- coding: utf-8 -*-
"""Windows 开机自启动 —— HKCU 的 Run 键里放一条 REG_SZ 值。

只写这一个值，不碰任何别的注册表键；非 Windows 上全部安全降级（import 不炸、
每个函数都老实返回 False）。全程不移动 / 复制 / 删除任何桌面文件。
"""
import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Tomato Desktop Box"

_SCRIPT_NAME = "DeskBox.pyw"
_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:
    import winreg


def _frozen():
    """运行时判断跑没跑在打包好的 exe 里（PyInstaller 会设 sys.frozen）。

    刻意每次现算，不做 import 期常量，测试才好 monkeypatch。
    """
    return bool(getattr(sys, "frozen", False))


def pythonw_path():
    """冻结态就是 exe 自己；否则优先 sys.executable 同目录的 pythonw.exe。"""
    exe = sys.executable or ""
    if _frozen():
        return exe
    if exe:
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(cand):
            return cand
    return exe


def script_path():
    """冻结态是 exe 自己；否则本仓库 DeskBox.pyw 的绝对路径（与 cwd 无关）。"""
    if _frozen():
        return sys.executable or ""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), _SCRIPT_NAME)


def command():
    """RUN 项的完整命令行，路径含空格 / 中文都不能出错。"""
    if _frozen():
        # 单文件 exe：只有它自己一段，没有第二个参数
        return '"%s"' % (sys.executable or "")
    return '"%s" "%s"' % (pythonw_path(), script_path())


def is_enabled(value_name=VALUE_NAME):
    """读注册表实际值并与 command() 比对；路径变了算未启用 / 待更新。

    任何读失败都返回 False，绝不抛。
    """
    if not _IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            data, _kind = winreg.QueryValueEx(key, value_name)
    except OSError:
        return False
    return isinstance(data, str) and data == command()


def enable(value_name=VALUE_NAME):
    """写入 / 覆盖 RUN 项。失败返回 False，绝不抛。"""
    if not _IS_WINDOWS:
        return False
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command())
    except OSError:
        return False
    return True


def disable(value_name=VALUE_NAME):
    """删掉 RUN 项；值本来就不在也算成功。失败返回 False，绝不抛。"""
    if not _IS_WINDOWS:
        return False
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except OSError as exc:
        # ERROR_FILE_NOT_FOUND(2) = 值不存在，视为已关闭
        return getattr(exc, "winerror", None) == 2
    return True
