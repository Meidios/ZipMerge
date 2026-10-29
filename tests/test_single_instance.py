"""单实例 IPC 单元测试（PLAN 12.1）：JSON 编解码、分帧与具名管道收发。"""

from __future__ import annotations

import os
import struct
import unittest
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QThread  # noqa: E402
from PySide6.QtNetwork import QLocalSocket  # noqa: E402

from zipmerge.ipc import (  # noqa: E402
    ACTION_ADD_TO_SESSION,
    ACTION_OPEN,
    MAX_FRAME,
    Request,
    SingleInstanceServer,
    decode_request,
    encode_request,
    frame,
    is_running,
    send,
)
from zipmerge.ipc.single_instance import _pop_frame  # noqa: E402


def _pipe_name() -> str:
    return f"ZipMergeTest.{uuid.uuid4().hex[:8]}"


class ProtocolTests(unittest.TestCase):
    """JSON 序列化可脱离 Qt 断言（PLAN 12.1）。"""

    def test_roundtrip_preserves_unicode_paths(self) -> None:
        paths = [r"C:\包\音乐.zip", "D:/音调.7z"]
        payload = encode_request(ACTION_OPEN, paths)
        self.assertIsInstance(payload, bytes)
        self.assertEqual(decode_request(payload), Request(ACTION_OPEN, tuple(paths)))

    def test_roundtrip_add_to_session(self) -> None:
        payload = encode_request(ACTION_ADD_TO_SESSION, ["a.tar.gz"])
        self.assertEqual(
            decode_request(payload), Request(ACTION_ADD_TO_SESSION, ("a.tar.gz",))
        )

    def test_decode_accepts_str_input(self) -> None:
        payload = encode_request(ACTION_OPEN, ["a.zip"])
        self.assertEqual(
            decode_request(payload.decode("utf-8")), Request(ACTION_OPEN, ("a.zip",))
        )

    def test_decode_rejects_malformed_payloads(self) -> None:
        for bad in (
            b"",
            b"not json",
            b"[]",
            b"\"open\"",
            b'{"action": "bogus", "paths": []}',
            b'{"action": "open"}',
            b'{"action": "open", "paths": "a.zip"}',
            b'{"action": "open", "paths": [1]}',
            b"\xff\xfe",
            None,
            7,
        ):
            self.assertIsNone(decode_request(bad), bad)


class FrameTests(unittest.TestCase):
    def test_pop_frame_waits_for_complete_payload(self) -> None:
        payload = encode_request(ACTION_OPEN, ["a.zip"])
        framed = frame(payload)
        buffer = bytearray(framed[:-1])
        self.assertIsNone(_pop_frame(buffer))
        buffer.extend(framed[-1:])
        self.assertEqual(_pop_frame(buffer), payload)
        self.assertEqual(len(buffer), 0)

    def test_pop_frame_handles_back_to_back_frames(self) -> None:
        first = encode_request(ACTION_OPEN, ["a.zip"])
        second = encode_request(ACTION_ADD_TO_SESSION, ["b.7z"])
        buffer = bytearray(frame(first) + frame(second))
        self.assertEqual(_pop_frame(buffer), first)
        self.assertEqual(_pop_frame(buffer), second)
        self.assertIsNone(_pop_frame(buffer))

    def test_oversized_frame_is_dropped(self) -> None:
        buffer = bytearray(struct.pack(">I", MAX_FRAME + 1) + b"x")
        self.assertIsNone(_pop_frame(buffer))
        self.assertEqual(len(buffer), 0)


class SingleInstanceTests(unittest.TestCase):
    """服务端与客户端在真实具名管道上跑一遍。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.name = _pipe_name()
        self.server = SingleInstanceServer(self.name)
        self.addCleanup(self.server.close)

    def _wait_for(self, received, count: int = 1, timeout_ms: int = 2000) -> bool:
        waited = 0
        while waited < timeout_ms:
            self.app.processEvents()
            if len(received) >= count:
                return True
            QThread.msleep(20)
            waited += 20
        return False

    def _pump(self, timeout_ms: int = 200) -> None:
        waited = 0
        while waited < timeout_ms:
            self.app.processEvents()
            QThread.msleep(10)
            waited += 10

    def _write_raw(self, payload: bytes) -> None:
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        self.assertTrue(socket.waitForConnected(500))
        socket.write(frame(payload))
        socket.flush()
        socket.waitForBytesWritten(10)
        # 不能主动断开：客户端先断开会让服务端尚未读取的数据丢失，测试就变成空转
        self._pump()
        socket.abort()

    def test_send_delivers_request_to_server(self) -> None:
        self.assertTrue(self.server.listen())
        received = []
        self.server.request_received.connect(received.append)
        self.assertTrue(send(ACTION_OPEN, ["C:/a.zip", "中文.7z"], name=self.name))
        self.assertTrue(self._wait_for(received), "服务端未收到请求")
        self.assertEqual(received[0], Request(ACTION_OPEN, ("C:/a.zip", "中文.7z")))

    def test_send_returns_false_without_server(self) -> None:
        self.assertFalse(send(ACTION_OPEN, ["a.zip"], name=_pipe_name(), timeout_ms=300))
        self.assertFalse(is_running(_pipe_name(), timeout_ms=300))

    def test_is_running_detects_listening_server(self) -> None:
        self.assertFalse(is_running(self.name, timeout_ms=200))
        self.assertTrue(self.server.listen())
        self.assertTrue(is_running(self.name, timeout_ms=500))

    def test_malformed_payload_is_ignored_and_server_survives(self) -> None:
        self.assertTrue(self.server.listen())
        received = []
        self.server.request_received.connect(received.append)

        # 先用合法请求证明通道确实通了，非法载荷被忽略才有意义
        self.assertTrue(send(ACTION_OPEN, ["first.zip"], name=self.name))
        self.assertTrue(self._wait_for(received, 1), "首个请求未送达")

        self._write_raw(b"{not json")
        self.assertEqual(len(received), 1, "非法载荷不应产生请求")
        self.assertTrue(send(ACTION_ADD_TO_SESSION, ["ok.zip"], name=self.name))
        self.assertTrue(self._wait_for(received, 2), "非法载荷后服务端应仍可用")
        self.assertEqual(received[1], Request(ACTION_ADD_TO_SESSION, ("ok.zip",)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
