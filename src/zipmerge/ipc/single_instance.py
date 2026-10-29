"""单实例服务与客户端（PLAN 8.4）。

第二次启动时用 `QLocalSocket` 连上已运行实例，发送 JSON 参数后退出；已运行实例通过
`SingleInstanceServer` 收到 `Request` 再分发。

JSON 编解码是纯函数（`encode_request` / `decode_request`），可脱离 Qt 单测；socket 层额外加
4 字节大端长度前缀分帧，避免 `readAll` 截断导致动作丢失。
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

__all__ = [
    "ACTION_ADD_TO_SESSION",
    "ACTION_OPEN",
    "ACTIONS",
    "MAX_FRAME",
    "SERVER_NAME",
    "Request",
    "SingleInstanceServer",
    "decode_request",
    "encode_request",
    "frame",
    "is_running",
    "send",
]

#: 具名管道名（PLAN 8.4）
SERVER_NAME = "ZipMerge.SingleInstance"

ACTION_OPEN = "open"
ACTION_ADD_TO_SESSION = "addToSession"
ACTIONS = (ACTION_OPEN, ACTION_ADD_TO_SESSION)

#: 单帧上限，防止异常客户端撑爆内存
MAX_FRAME = 8 * 1024 * 1024

_HEADER = struct.Struct(">I")

#: 心跳步长：客户端等待送达确认时的轮询间隔
_STEP_MS = 10


def _pump() -> None:
    """转一圈本进程的事件循环，让 socket 通知器有机会收发数据。"""
    app = QCoreApplication.instance()
    if app is not None:
        app.processEvents()


@dataclass(frozen=True)
class Request:
    """第二次启动发来的动作请求。"""

    action: str
    paths: tuple[str, ...]


def encode_request(action: str, paths) -> bytes:
    """把动作与路径列表编码成 JSON 字节串。"""
    return json.dumps(
        {"action": str(action), "paths": [str(p) for p in paths]},
        ensure_ascii=False,
    ).encode("utf-8")


def decode_request(payload) -> Request | None:
    """解析 JSON 载荷；格式不合法时返回 None（不抛异常）。"""
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except ValueError:
            return None
    elif isinstance(payload, (bytes, bytearray, memoryview)):
        try:
            data = json.loads(bytes(payload).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
    else:
        return None
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    paths = data.get("paths")
    if action not in ACTIONS:
        return None
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return None
    return Request(action=action, paths=tuple(paths))


def frame(payload: bytes) -> bytes:
    """给载荷加 4 字节大端长度前缀。"""
    return _HEADER.pack(len(payload)) + payload


def _pop_frame(buffer: bytearray):
    """从缓冲区头部取出一整帧；不足一帧或长度异常时返回 None。"""
    if len(buffer) < _HEADER.size:
        return None
    (size,) = _HEADER.unpack_from(buffer)
    if size > MAX_FRAME:
        buffer.clear()
        return None
    end = _HEADER.size + size
    if len(buffer) < end:
        return None
    payload = bytes(buffer[_HEADER.size : end])
    del buffer[:end]
    return payload


def is_running(name: str = SERVER_NAME, timeout_ms: int = 300) -> bool:
    """是否已有实例在监听该管道名。"""
    socket = QLocalSocket()
    socket.connectToServer(name)
    connected = bool(socket.waitForConnected(timeout_ms))
    if connected:
        socket.disconnectFromServer()
    else:
        socket.abort()
    return connected


def send(action: str, paths, *, name: str = SERVER_NAME, timeout_ms: int = 2000) -> bool:
    """把请求发给已运行实例；没有实例可连时返回 False。"""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        socket.abort()
        return False
    socket.write(frame(encode_request(action, paths)))
    socket.flush()
    # 先让本进程事件循环把缓冲真正推进管道（极短载荷下 waitForBytesWritten 可能返回 False，
    # 因此只借它驱动一次事件循环，不据此判断成败）。
    socket.waitForBytesWritten(_STEP_MS)
    # 服务端读完整帧后主动断开，客户端以此确认送达；不可主动 disconnectFromServer，
    # 否则 Windows 会丢弃服务端尚未读取的管道数据。
    remaining = timeout_ms
    while remaining > 0:
        _pump()
        if socket.waitForDisconnected(_STEP_MS) or socket.state() == QLocalSocket.UnconnectedState:
            break
        remaining -= _STEP_MS
    socket.abort()
    return True


class SingleInstanceServer(QObject):
    """监听具名管道，收到请求后发 request_received(Request)。"""

    request_received = Signal(object)

    def __init__(self, name: str = SERVER_NAME, parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._buffers = {}
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)

    @property
    def name(self) -> str:
        return self._name

    def listen(self) -> bool:
        """开始监听；先清掉上次异常退出残留的管道名。"""
        QLocalServer.removeServer(self._name)
        return bool(self._server.listen(self._name))

    def is_listening(self) -> bool:
        return bool(self._server.isListening())

    def close(self) -> None:
        self._server.close()
        QLocalServer.removeServer(self._name)

    def _on_new_connection(self) -> None:
        socket = self._server.nextPendingConnection()
        while socket is not None:
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))
            self._on_ready_read(socket)
            socket = self._server.nextPendingConnection()

    def _on_ready_read(self, socket) -> None:
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        while True:
            payload = _pop_frame(buffer)
            if payload is None:
                return
            request = decode_request(payload)
            if request is not None:
                self.request_received.emit(request)
            # 一次连接只处理一帧：读完主动断开，既清理资源也作为客户端的送达确认。
            self._buffers.pop(socket, None)
            socket.disconnectFromServer()
            return

    def _on_disconnected(self, socket) -> None:
        self._buffers.pop(socket, None)
        socket.deleteLater()
