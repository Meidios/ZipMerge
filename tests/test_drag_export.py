"""树拖出控制器：本地 URL、内部 MIME 与缓存复用。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from zipmerge.core import MergeSession, build_target_drag_root  # noqa: E402
from zipmerge.ui.drag_export import DragExportController, LazyFileMimeData  # noqa: E402
from zipmerge.ui.target_model import MIME_NODES  # noqa: E402

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802
    global _app
    _app = QApplication.instance() or QApplication([])


class _FakeDrag:
    instances: list["_FakeDrag"] = []
    request_urls = True
    pump_events = False
    in_exec = False

    def __init__(self, _parent) -> None:
        self.mime = None
        self.prefetched_paths: tuple[Path, ...] = ()
        self.__class__.instances.append(self)

    def setMimeData(self, mime) -> None:  # noqa: N802
        self.mime = mime

    def exec(self, _supported, _default):
        self.__class__.in_exec = True
        try:
            # 模拟 Explorer 在拖拽刚开始时预取并缓存 CF_HDROP。
            if self.__class__.request_urls:
                self.prefetched_paths = tuple(
                    Path(url.toLocalFile()) for url in self.mime.urls()
                )
            if self.__class__.pump_events:
                QTest.qWait(40)
            return Qt.DropAction.CopyAction
        finally:
            self.__class__.in_exec = False


class DragExportControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-drag-ui-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="拖出UI", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)
        self.view = QWidget()
        self.addCleanup(self.view.deleteLater)
        _FakeDrag.instances = []
        _FakeDrag.request_urls = True
        _FakeDrag.pump_events = False
        _FakeDrag.in_exec = False

    def test_initial_uri_is_placeholder_and_external_resolution_runs_once(self) -> None:
        placeholder = self.tmp / "占位 文件.txt"
        placeholder.touch()
        local = self.tmp / "真实 文件.txt"
        local.write_text("payload", encoding="utf-8")
        source = QMimeData()
        source.setData(MIME_NODES, b"[1]")
        prepare = mock.Mock(return_value=(local,))

        mime = LazyFileMimeData(source, prepare, (placeholder,))

        self.assertTrue(mime.hasFormat(MIME_NODES))
        self.assertTrue(mime.hasFormat("text/uri-list"))
        self.assertEqual(prepare.call_count, 0)
        self.assertEqual(Path(mime.urls()[0].toLocalFile()), placeholder)
        self.assertEqual(prepare.call_count, 0)
        self.assertEqual(mime.resolve_urls(), (local,))
        self.assertEqual(Path(mime.urls()[0].toLocalFile()), local)
        self.assertEqual(mime.resolve_urls(), (local,))
        prepare.assert_called_once_with()

    def test_direct_external_drag_without_target_signal_materializes_and_reuses_cache(self) -> None:
        source = self.tmp / "中文 文件.txt"
        source.write_text("payload", encoding="utf-8")
        node = self.session.import_disk_paths([source])[0]
        root = build_target_drag_root([node])
        controller = DragExportController(self.view, self.session)
        original_run = controller._run
        _FakeDrag.pump_events = True

        def assert_drag_active(request):
            self.assertTrue(_FakeDrag.in_exec)
            return original_run(request)

        with mock.patch.object(controller, "_run", side_effect=assert_drag_active) as run, \
             mock.patch("zipmerge.ui.drag_export.QDrag", _FakeDrag), \
             mock.patch("zipmerge.ui.drag_export.QApplication.widgetAt", return_value=None):
            for _ in range(2):
                mime = QMimeData()
                mime.setData(MIME_NODES, b"[1]")
                controller.start(
                    root, mime, Qt.DropAction.CopyAction, Qt.DropAction.CopyAction
                )

        self.assertEqual(len(_FakeDrag.instances), 2)
        first = _FakeDrag.instances[0].mime
        second = _FakeDrag.instances[1].mime
        self.assertTrue(first.hasFormat(MIME_NODES))
        self.assertEqual(len(first.urls()), 1)
        local = Path(first.urls()[0].toLocalFile())
        self.assertEqual(_FakeDrag.instances[0].prefetched_paths, (local,))
        self.assertEqual(local.read_text(encoding="utf-8"), "payload")
        self.assertEqual(second.urls()[0].toLocalFile(), first.urls()[0].toLocalFile())
        run.assert_called_once()

    def test_windows_uri_probe_uses_placeholder_without_materializing_internal_drag(self) -> None:
        source = self.tmp / "内部.txt"
        source.write_text("payload", encoding="utf-8")
        root = build_target_drag_root(self.session.import_disk_paths([source]))
        controller = DragExportController(self.view, self.session)
        _FakeDrag.pump_events = True
        # Explorer 会在目标明确前读取并缓存 CF_HDROP；缓存的是已存在的空占位
        # 路径，而不是触发真实物化。手势始终留在应用内时不会弹进度或复制数据。
        with mock.patch.object(controller, "_run") as run, \
             mock.patch("zipmerge.ui.drag_export.QDrag", _FakeDrag), \
             mock.patch("zipmerge.ui.drag_export.QApplication.widgetAt", return_value=self.view):
            mime = QMimeData()
            mime.setData(MIME_NODES, b"[1]")
            controller.start(root, mime, Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)

        run.assert_not_called()
        self.assertEqual(_FakeDrag.instances[0].mime.data(MIME_NODES), b"[1]")
        cached_path = Path(_FakeDrag.instances[0].mime.urls()[0].toLocalFile())
        self.assertFalse(cached_path.exists())


if __name__ == "__main__":
    unittest.main()
