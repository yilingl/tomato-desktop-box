# -*- coding: utf-8 -*-
"""单实例守卫 —— 同一个用户会话里只允许跑一个番茄盒子。

为什么需要：同一个 exe 双击两次会起两个进程，两套主面板、两套小窗口、两个托盘
图标，而且同时对同一份 %APPDATA%\\DeskBox 账本读写，迟早写坏。

怎么做的：用 QtNetwork 的 QLocalServer / QLocalSocket（Windows 底下就是命名管道），
不起本地 TCP 端口、不写锁文件。进程被强杀也不会留下死锁 —— 管道由系统在进程
消失时回收，下次 acquire() 用 removeServer() 把可能的残留名字清掉再 listen。

三个入口（外加一个收尾）：
    _server_name()  -> 带用户名哈希的管道名，多用户同时开互不干扰
    acquire(argv)   -> (ok, sock)；ok=False 表示"已经有实例在跑，我已经把它叫醒了"
    listen(cb)      -> 挂上 newConnection，把收到的载荷交给 cb(str)
    release(srv)    -> 关服务端并把名字清掉

载荷：纯文本，第一行是来访者的工作目录，第二行起是它的 argv。
第二个实例只是想叫醒第一个，不指望它真的执行那些参数。
"""
import os
import zlib                        # 标准库，且已显式列在 build_exe.py 的 HIDDEN_IMPORTS 里

from PySide6.QtNetwork import QLocalServer, QLocalSocket

# 管道名前缀。带上程序名，避免和别的 Qt 程序撞名。
_PREFIX = "DeskBox-tomato-desktop-box"

# 第一个实例的服务端。**必须留长期引用**：QLocalServer 被 GC 掉就收不到
# newConnection 了，单实例会静默失效。
_PENDING = None

# 正在读的客户端连接。同样要留引用，否则 readyRead 还没来就被回收。
_CONNS = []

_CONNECT_MS = 500          # waitForConnected：太短会误判"没人在跑"，太长会拖慢启动
_WRITE_MS = 500            # waitForBytesWritten：载荷就几十字节，本地管道一下就走完


def _server_name():
    """同一个用户会话内唯一、又不和其它程序撞名的管道名。

    形如 DeskBox-tomato-desktop-box-1a2b3c4d。用固定字符串的话，同一台机器上
    两个用户同时开就会互相抢管道（一个把另一个顶掉），所以拿用户名做哈希后缀。

    **只用 os + zlib，不走 getpass / hashlib**：deskbox/ 是 --add-data 当数据打进
    exe 的，PyInstaller 的静态分析看不见这个文件里的 import，打包后 getpass 会
    ModuleNotFoundError（实测 2.2.1 的 exe 就是这么静默失效的）。zlib 已经在
    HIDDEN_IMPORTS 表里显式列着（cats.py 用了），所以这里只依赖它。
    getpass.getuser() 在 Windows 上本来也就是读 USERNAME，等价。
    """
    who = (os.environ.get("USERNAME") or os.environ.get("USER")
           or os.environ.get("USERPROFILE") or "user")
    # 32 位 crc32 对"同一台机器上几个用户"绰绰有余，仍然是 8 位小写 hex。
    h = "%08x" % (zlib.crc32(who.encode("utf-8", "replace")) & 0xffffffff)
    return "%s-%s" % (_PREFIX, h)


def _payload(argv=None):
    """第一行工作目录，第二行起是 argv。末尾补一个换行，收的那侧好判断读完。"""
    try:
        cwd = os.getcwd()
    except Exception:                  # noqa: BLE001
        cwd = ""
    args = [str(a) for a in (argv or [])]
    return "\n".join([cwd] + args) + "\n"


def _hello(sock, argv):
    """把手里的 socket 当"来访者"用：写载荷、冲刷、挂断。任何异常都吞掉。"""
    try:
        sock.write(_payload(argv).encode("utf-8"))
        sock.flush()
        sock.waitForBytesWritten(_WRITE_MS)
    except Exception:                  # noqa: BLE001
        pass
    try:
        sock.disconnectFromServer()
    except Exception:                  # noqa: BLE001
        pass


def _try_connect(name, argv):
    """连已有的实例。连上返回 socket（载荷已送出），连不上返回 None。"""
    sock = QLocalSocket()
    try:
        sock.connectToServer(name)
        if not sock.waitForConnected(_CONNECT_MS):
            try:
                sock.abort()
            except Exception:          # noqa: BLE001
                pass
            return None
    except Exception:                  # noqa: BLE001
        return None
    _hello(sock, argv)
    return sock


def acquire(argv=None):
    """抢单实例权。

    返回 (ok, sock)：
        (True,  None) —— 我是第一个，名字已占住（服务端留在 _PENDING，等 listen() 来接）
        (False, sock) —— 已经有实例在跑，我把载荷送过去并已挂断，调用方应当直接退出
        (True,  None) —— 抢不到也连不上（极罕见的竞态）时降级：照常启动，绝不把程序卡死
    """
    global _PENDING
    name = _server_name()

    sock = _try_connect(name, argv)
    if sock is not None:
        return False, sock            # 已有实例在跑：叫醒它，自己收工

    # 连不上：要么真没人在跑，要么上次被强杀留了个死管道名。先清残留再抢。
    try:
        QLocalServer.removeServer(name)
    except Exception:                  # noqa: BLE001
        pass
    srv = QLocalServer()
    try:
        if srv.listen(name):
            _PENDING = srv             # 交给 listen() 挂回调；这里只是把名字占住
            return True, None
    except Exception:                  # noqa: BLE001
        pass
    try:
        srv.close()
    except Exception:                  # noqa: BLE001
        pass

    # listen 失败：可能别的进程刚抢到，再连一次试试。
    sock = _try_connect(name, argv)
    if sock is not None:
        return False, sock
    # 谁也没抢到，谁也连不上 —— 降级成照常启动，别让用户点了没反应。
    return True, None


def _drain(server, on_message):
    """把排队的连接逐个接下来，读满载荷后交给 on_message(str)。

    载荷以换行结尾，所以攒到见 "\n" 就算读全；对端挂断还没见换行（异常情况）
    也把攒到的东西交出去 —— 宁可按"叫醒"处理，也不要静默丢弃。
    """
    while True:
        try:
            if not server.hasPendingConnections():
                return
            conn = server.nextPendingConnection()
        except Exception:              # noqa: BLE001
            return
        if conn is None:
            return
        _CONNS.append(conn)
        state = {"buf": b"", "fired": False}

        def _fire(st):
            if st["fired"]:
                return
            st["fired"] = True
            try:
                on_message(st["buf"].decode("utf-8", "replace"))
            except Exception:          # noqa: BLE001 —— 回调里的错绝不能冒到事件循环
                pass

        def _ready(c=conn, st=state):
            if st["fired"]:
                return
            try:
                st["buf"] += bytes(c.readAll())
            except Exception:          # noqa: BLE001
                return
            if b"\n" in st["buf"]:
                _fire(st)

        def _gone(c=conn, st=state):
            _fire(st)                  # 对端已挂断：把攒到的交出去，然后等它自己回收

        try:
            conn.readyRead.connect(_ready)
            conn.disconnected.connect(_gone)
        except Exception:              # noqa: BLE001
            pass


def listen(on_message):
    """把 newConnection 挂到服务端上。返回的 server 调用方必须留着引用。

    acquire() 已经建好服务端时直接复用它（不然名字被自己占着，再 listen 会失败）；
    没经过 acquire() 时自己建一个 —— 单独用 listen() 也能工作。
    """
    global _PENDING
    srv = _PENDING
    if srv is None:
        name = _server_name()
        try:
            QLocalServer.removeServer(name)
        except Exception:              # noqa: BLE001
            pass
        srv = QLocalServer()
        try:
            if not srv.listen(name):
                try:
                    srv.close()
                except Exception:      # noqa: BLE001
                    pass
                return None
        except Exception:              # noqa: BLE001
            return None
        _PENDING = srv
    if not callable(on_message):
        return srv
    try:
        srv.newConnection.connect(lambda: _drain(srv, on_message))
    except Exception:                  # noqa: BLE001
        return srv
    return srv


def release(server=None):
    """退出时收尾：关服务端 + 清掉管道名。任何异常都吞掉（退出路径上绝不能抛）。"""
    global _PENDING
    srv = server if server is not None else _PENDING
    if srv is not None:
        try:
            srv.close()
        except Exception:              # noqa: BLE001
            pass
    try:
        QLocalServer.removeServer(_server_name())
    except Exception:                  # noqa: BLE001
        pass
    for conn in list(_CONNS):
        try:
            conn.abort()
        except Exception:              # noqa: BLE001
            pass
    _CONNS.clear()
    _PENDING = None
