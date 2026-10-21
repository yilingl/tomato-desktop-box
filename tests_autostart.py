# -*- coding: utf-8 -*-
"""autostart.py 自测：只读写「测试专用值名」，绝不碰生产值 "Tomato Desktop Box"。

两种跑法都行：
    python tests_autostart.py
    python -m unittest tests_autostart
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deskbox.autostart as auto

TEST_VALUE = auto.VALUE_NAME + " (自测)"
IS_WIN = os.name == "nt"

if IS_WIN:
    import winreg


def _read_reg(value_name):
    """直接读 HKCU Run 里的原始值；返回 (存在?, 原始内容)。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, auto.RUN_KEY) as key:
            data, _kind = winreg.QueryValueEx(key, value_name)
        return True, data
    except OSError:
        return False, None


class AutostartTest(unittest.TestCase):
    """注册表只用自测值名；生产值名前后各读一次做对照。"""

    def setUp(self):
        self.n = 0
        self.assertNotEqual(TEST_VALUE, auto.VALUE_NAME)
        self.prod_before = _read_reg(auto.VALUE_NAME) if IS_WIN else (False, None)
        self.assertEqual(TEST_VALUE.strip(), TEST_VALUE)
        auto.disable(TEST_VALUE)          # 上一轮跑挂留下的残留先清掉

    def tearDown(self):
        auto.disable(TEST_VALUE)          # 兜底删除自测值
        if IS_WIN:
            self.assertFalse(_read_reg(TEST_VALUE)[0], "自测值没删干净")
            self.assertEqual(_read_reg(auto.VALUE_NAME), self.prod_before,
                             "生产值名被这次测试动过")

    # ------------------------------------------------------------ 工具
    def check(self, name, cond, detail=""):
        self.n += 1
        if cond:
            print("PASS - %s" % name)
        else:
            print("FAIL - %s%s" % (name, ("  << " + str(detail)) if detail else ""))
        self.assertTrue(cond, "%s %s" % (name, detail))

    # ------------------------------------------------------------ 用例
    def test_constants(self):
        self.check("RUN_KEY 就是 HKCU 的 Run 键",
                   auto.RUN_KEY == r"Software\Microsoft\Windows\CurrentVersion\Run",
                   auto.RUN_KEY)
        self.check("VALUE_NAME", auto.VALUE_NAME == "Tomato Desktop Box", auto.VALUE_NAME)

    def test_pythonw_path(self):
        p = auto.pythonw_path()
        self.check("pythonw_path 非空", bool(p), p)
        self.check("pythonw_path 是绝对路径", os.path.isabs(p), p)
        if IS_WIN:
            sib = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if os.path.isfile(sib):
                self.check("有 pythonw.exe 时优先用它", p.lower() == sib.lower(), p)
            else:
                self.check("没有 pythonw.exe 时退回 sys.executable",
                           p == sys.executable, p)

    def test_script_path(self):
        p = auto.script_path()
        self.check("script_path 是绝对路径", os.path.isabs(p), p)
        self.check("script_path 指向 DeskBox.pyw",
                   os.path.basename(p) == "DeskBox.pyw", p)
        self.check("该文件真实存在", os.path.isfile(p), p)
        old = os.getcwd()
        tmp = tempfile.mkdtemp(prefix="deskbox_autostart_")
        try:
            os.chdir(tmp)
            self.check("换掉 cwd 后 script_path 不变", auto.script_path() == p,
                       auto.script_path())
            self.check("换掉 cwd 后 command() 不变",
                       auto.command() == '"%s" "%s"' % (auto.pythonw_path(), p),
                       auto.command())
        finally:
            os.chdir(old)
            os.rmdir(tmp)

    def test_command_quoting(self):
        cmd = auto.command()
        self.check("command() 两段都带引号",
                   cmd == '"%s" "%s"' % (auto.pythonw_path(), auto.script_path()), cmd)
        self.check("command() 里出现 pythonw / sys.executable 的路径段",
                   os.path.basename(auto.pythonw_path()) in cmd, cmd)
        self.check("command() 含 DeskBox.pyw 且被引号包住",
                   '"' + auto.script_path() + '"' in cmd, cmd)

    def test_non_windows_safe_degrade(self):
        old = auto._IS_WINDOWS
        auto._IS_WINDOWS = False
        try:
            self.check("非 Windows: is_enabled() False", auto.is_enabled() is False)
            self.check("非 Windows: enable() False", auto.enable() is False)
            self.check("非 Windows: disable() False", auto.disable() is False)
            self.check("非 Windows: 纯函数照常可用",
                       auto.command() == '"%s" "%s"' % (auto.pythonw_path(),
                                                        auto.script_path()))
        finally:
            auto._IS_WINDOWS = old

    @unittest.skipUnless(IS_WIN, "注册表读写只在 Windows 上测")
    def test_registry_roundtrip(self):
        self.check("起手自测值不存在", _read_reg(TEST_VALUE)[0] is False)
        self.check("起手 is_enabled(自测值) False",
                   auto.is_enabled(TEST_VALUE) is False)

        self.check("enable(自测值) 成功", auto.enable(TEST_VALUE) is True)
        exists, raw = _read_reg(TEST_VALUE)
        print("    [注册表原文] %s = %r" % (TEST_VALUE, raw))
        self.check("注册表里真的有这条值", exists is True)
        self.check("读回字符串 == command()", raw == auto.command(), raw)
        self.check("is_enabled(自测值) True", auto.is_enabled(TEST_VALUE) is True)

        self.check("disable(自测值) 成功", auto.disable(TEST_VALUE) is True)
        exists2, raw2 = _read_reg(TEST_VALUE)
        print("    [注册表原文] %s 已删, exists=%s raw=%r" % (TEST_VALUE, exists2, raw2))
        self.check("值消失了", exists2 is False)
        self.check("删完 is_enabled False", auto.is_enabled(TEST_VALUE) is False)
        self.check("再删一次（值不存在）也算成功", auto.disable(TEST_VALUE) is True)

        self.check("整轮跑完生产值名没被动过",
                   _read_reg(auto.VALUE_NAME) == self.prod_before,
                   (_read_reg(auto.VALUE_NAME), self.prod_before))

    def test_non_frozen_stays_two_segments(self):
        self.check("非冻结态 _frozen() False", auto._frozen() is False)
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.isfile(pyw):
            pyw = sys.executable
        expect_script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(auto.__file__))),
            "DeskBox.pyw")
        cmd = auto.command()
        print("    [非冻结] %s" % cmd)
        self.check("非冻结态 pythonw_path 还是 pythonw/解释器", auto.pythonw_path() == pyw, cmd)
        self.check("非冻结态 script_path 还是仓库里的 DeskBox.pyw",
                   auto.script_path() == expect_script, auto.script_path())
        self.check("非冻结态仍是两段带引号：\"pyw\" \"DeskBox.pyw\"",
                   cmd == '"%s" "%s"' % (pyw, expect_script), cmd)
        self.check("非冻结态命令行里有两个引号段",
                   cmd.count('"') == 4 and cmd.startswith('"'), cmd)

    def test_frozen_command_points_at_exe(self):
        exe = r"C:\Program Files\Tomato\Tomato Desktop Box.exe"
        old_exe = sys.executable
        had_frozen = hasattr(sys, "frozen")
        old_frozen = getattr(sys, "frozen", None)
        try:
            sys.frozen = True
            sys.executable = exe
            cmd = auto.command()
            print("    [冻结] %s" % cmd)
            self.check("冻结态 _frozen() True", auto._frozen() is True)
            self.check("冻结态 pythonw_path() 就是 exe", auto.pythonw_path() == exe,
                       auto.pythonw_path())
            self.check("冻结态 script_path() 就是 exe", auto.script_path() == exe,
                       auto.script_path())
            self.check("冻结态 command() == exe 路径 + --tray（开机进托盘）",
                       cmd == '"C:\\Program Files\\Tomato\\Tomato Desktop Box.exe" --tray',
                       cmd)
            self.check("冻结态仍只有一段带引号的路径（2 个引号），参数在引号外",
                       cmd.count('"') == 2 and cmd.endswith(" --tray"), cmd)
            self.check("冻结态不带 DeskBox.pyw", "DeskBox.pyw" not in cmd, cmd)
        finally:
            if had_frozen:
                sys.frozen = old_frozen
            else:
                del sys.frozen
            sys.executable = old_exe
        self.check("收尾后 _frozen() False 且解释器复原",
                   auto._frozen() is False and sys.executable == old_exe,
                   (auto._frozen(), sys.executable))
        self.check("收尾后 command() 回到老的两段格式",
                   auto.command() == '"%s" "%s"' % (auto.pythonw_path(),
                                                    auto.script_path()),
                   auto.command())

    @unittest.skipUnless(IS_WIN, "注册表只在 Windows 上测")
    def test_frozen_registry_value_is_exe(self):
        exe = r"C:\Program Files\Tomato\Tomato Desktop Box.exe"
        old_exe = sys.executable
        had_frozen = hasattr(sys, "frozen")
        old_frozen = getattr(sys, "frozen", None)
        try:
            sys.frozen = True
            sys.executable = exe
            self.check("冻结态 enable 成功", auto.enable(TEST_VALUE) is True)
            exists, raw = _read_reg(TEST_VALUE)
            print("    [注册表原文·冻结态] %s = %r" % (TEST_VALUE, raw))
            self.check("注册表里存的就是 exe 一段 + --tray",
                       raw == '"C:\\Program Files\\Tomato\\Tomato Desktop Box.exe" --tray',
                       raw)
            self.check("冻结态 is_enabled True", auto.is_enabled(TEST_VALUE) is True)
        finally:
            if had_frozen:
                sys.frozen = old_frozen
            else:
                del sys.frozen
            sys.executable = old_exe
        self.check("解冻后同一条值变成「待更新」→ is_enabled False",
                   auto.is_enabled(TEST_VALUE) is False)
        self.check("冻结态值已清理", auto.disable(TEST_VALUE) is True)

    @unittest.skipUnless(IS_WIN, "路径变了要视为待更新")
    def test_stale_path_is_not_enabled(self):
        auto.enable(TEST_VALUE)
        # 手动塞一条旧路径（模拟换过 Python / 挪过仓库）
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, auto.RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, TEST_VALUE, 0, winreg.REG_SZ,
                              '"C:\\old\\pythonw.exe" "C:\\old\\DeskBox.pyw"')
        self.check("路径变了 → is_enabled False",
                   auto.is_enabled(TEST_VALUE) is False)

    def test_tray_flag_is_the_one_the_app_reads(self):
        """自启动写进去的参数必须真的有人认。

        两边写岔了（这边 --tray、那边 --tray-start）就是静默失效：注册表看着挺好，
        开机照旧弹面板。所以拿 command() 里那截参数去主程序源码里对一遍。
        """
        exe = r"C:\Program Files\Tomato\Tomato Desktop Box.exe"
        old_exe = sys.executable
        had_frozen = hasattr(sys, "frozen")
        old_frozen = getattr(sys, "frozen", None)
        try:
            sys.frozen = True
            sys.executable = exe
            cmd = auto.command()
        finally:
            if had_frozen:
                sys.frozen = old_frozen
            else:
                del sys.frozen
            sys.executable = old_exe
        flags = cmd.split('"')[-1].split()
        self.check("冻结态只多一个 --tray 参数", flags == ["--tray"], cmd)
        src_path = os.path.join(
            os.path.dirname(os.path.abspath(auto.__file__)), "ui_mock.py")
        with open(src_path, encoding="utf-8") as fh:
            src = fh.read()
        for f in flags:
            self.check("主程序里有 '%s in sys.argv' 这道闸" % f,
                       ('"%s" in sys.argv' % f) in src
                       or ('"%s" not in sys.argv' % f) in src, f)


if __name__ == "__main__":
    unittest.main(verbosity=2)
