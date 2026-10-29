"""预览面板测试：提取缓存、文本 / 图片解码、页面分发。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QMovie  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.core import MergeSession  # noqa: E402
from zipmerge.engine import create_archive  # noqa: E402
from zipmerge.ui import preview_panel as preview_module  # noqa: E402
from zipmerge.ui.preview_panel import (  # noqa: E402
    PreviewCache,
    PreviewPanel,
    _decode_text,
    load_pixmap,
)

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


def _make_png(path: Path, size: tuple[int, int] = (12, 8)) -> None:
    from PIL import Image

    Image.new("RGB", size, (200, 40, 40)).save(path, format="PNG")


def _make_gif(path: Path, size: tuple[int, int] = (12, 8)) -> None:
    from PIL import Image

    frames = [Image.new("RGB", size, color) for color in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=40, loop=0)


class DecodeTests(unittest.TestCase):
    def test_utf8_then_gbk(self) -> None:
        self.assertEqual(_decode_text("中文".encode("utf-8")), ("中文", "utf-8"))
        self.assertEqual(_decode_text("中文".encode("gbk")), ("中文", "gbk"))

    def test_invalid_bytes_fall_back(self) -> None:
        text, encoding = _decode_text(b"\xff\xfe\x00")
        self.assertEqual(encoding, "utf-8")
        self.assertTrue(text)

    def test_load_pixmap(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="zipmerge-pix-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        png = tmp / "a.png"
        _make_png(png, (12, 8))
        pixmap, error = load_pixmap(png)
        self.assertEqual(error, "")
        self.assertIsNotNone(pixmap)
        self.assertEqual((pixmap.width(), pixmap.height()), (12, 8))

    def test_load_pixmap_bad_file(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="zipmerge-pix-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        broken = tmp / "broken.png"
        broken.write_bytes(b"not an image")
        pixmap, error = load_pixmap(broken)
        self.assertIsNone(pixmap)
        self.assertTrue(error)


class PreviewCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-pcache-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="预览", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)

        src = self.tmp / "src"
        (src / "子").mkdir(parents=True)
        (src / "逐层" / "deep").mkdir(parents=True)
        (src / "逐层" / "deep" / "笔记.txt").write_text("深层的文本\n", encoding="utf-8")
        (src / "子" / "中文.txt").write_text("多层路径内容\n", encoding="utf-8")
        archive = self.tmp / "包A.zip"
        create_archive(archive, "zip", src)
        self.session.import_archive(archive)
        self.cache = PreviewCache(self.tmp / "cache")

    def _node(self, name: str):
        return next(n for n in self.session.root.walk() if n.name == name)

    def test_extracts_entry_preserving_subpath(self) -> None:
        node = self._node("笔记.txt")
        path = self.cache.local_path(node, self.session)
        self.assertIsNotNone(path)
        self.assertEqual(path.read_text(encoding="utf-8"), "深层的文本\n")
        self.assertEqual(path.name, "笔记.txt")
        self.assertEqual(path.parent.name, "deep")

        # 再取一次命中缓存（同一个 Path 对象）
        self.assertEqual(self.cache.local_path(node, self.session), path)

    def test_disk_file_uses_original_path(self) -> None:
        loose = self.tmp / "散.txt"
        loose.write_text("loose\n", encoding="utf-8")
        self.session.import_disk_paths([loose])
        node = self._node("散.txt")
        self.assertEqual(self.cache.local_path(node, self.session), loose)


class PreviewPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-preview-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="面板", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)

        src = self.tmp / "src"
        src.mkdir(parents=True)
        (src / "说明.txt").write_text("正文内容\n", encoding="utf-8")
        _make_png(src / "图片.png", (10, 10))
        _make_gif(src / "动画.gif", (9, 7))
        (src / "音频.wav").write_bytes(b"RIFF")
        (src / "数据.bin").write_bytes(b"\x00\x01\x02")
        archive = self.tmp / "包A.zip"
        create_archive(archive, "zip", src)
        self.session.import_archive(archive)
        self.panel = PreviewPanel(self.session)
        self.addCleanup(self.panel.deleteLater)

    def _node(self, name: str):
        return next(n for n in self.session.root.walk() if n.name == name)

    def test_empty_state(self) -> None:
        self.panel.show_node(None)
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_EMPTY)

    def test_text_node(self) -> None:
        self.panel.show_node(self._node("说明.txt"))
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_TEXT)
        self.assertIn("正文内容", self.panel._text_view.toPlainText())

    def test_image_node(self) -> None:
        self.panel.show_node(self._node("图片.png"))
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_IMAGE)
        self.assertIn("10×10", self.panel._subtitle.text())

    def test_gif_node_autoplays_and_stops_on_switch(self) -> None:
        self.panel.show_node(self._node("动画.gif"))
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_IMAGE)
        self.assertIn("GIF 动图", self.panel._subtitle.text())
        movie = self.panel._image_view._movie
        self.assertIsNotNone(movie)
        self.assertEqual(movie.state(), QMovie.MovieState.Running)
        self.panel.show_node(self._node("说明.txt"))
        self.assertIsNone(self.panel._image_view._movie)

    def test_directory_node_shows_info(self) -> None:
        self.panel.show_node(self.session.root.children[0])
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_INFO)
        self.assertGreater(self.panel._info_form.rowCount(), 0)

    def test_unknown_type_shows_info(self) -> None:
        self.panel.show_node(self._node("数据.bin"))
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_INFO)
        self.assertTrue(self.panel._info_note.text())

    def test_audio_node_without_backend_shows_info(self) -> None:
        original = preview_module.backend_available
        preview_module.backend_available = lambda: False
        try:
            self.panel.show_node(self._node("音频.wav"))
        finally:
            preview_module.backend_available = original
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_INFO)
        self.assertIn("后端", self.panel._info_note.text())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
