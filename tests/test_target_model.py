"""目标树 Qt 模型测试：列显示、来源列、拖放 mime 编解码与落点。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QModelIndex, Qt, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.core import MergeSession, ResolveStrategy  # noqa: E402
from zipmerge.engine import create_archive  # noqa: E402
from zipmerge.ui.drag_export import LazyFileMimeData  # noqa: E402
from zipmerge.ui.target_model import (  # noqa: E402
    MIME_NODES,
    MIME_SOURCE,
    TargetTreeModel,
    icon_name_for,
)
from zipmerge.ui.design.tokens import palette_for  # noqa: E402

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


def write_files(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class TargetTreeModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-model-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="建模", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)

        src = self.tmp / "src_a"
        write_files(src, {"SFX/boom.wav": b"A", "SFX/深层/inner.txt": b"T", "readme.txt": b"R"})
        archive = self.tmp / "包A.zip"
        create_archive(archive, "zip", src)
        self.wrapper = self.session.import_archive(archive)

        self.model = TargetTreeModel(self.session)

    # ---- 基本映射 ---------------------------------------------------
    def test_headers_and_columns(self) -> None:
        self.assertEqual(self.model.columnCount(), 2)
        self.assertEqual(
            self.model.headerData(0, Qt.Orientation.Horizontal), "名称"
        )
        self.assertEqual(
            self.model.headerData(1, Qt.Orientation.Horizontal), "来源"
        )
        self.assertIsNone(self.model.headerData(0, Qt.Orientation.Vertical))

    def test_rowcount_index_and_parent(self) -> None:
        root = QModelIndex()
        self.assertEqual(self.model.rowCount(root), 1)
        wrapper_index = self.model.index(0, 0, root)
        self.assertEqual(self.model.data(wrapper_index), "包A")
        self.assertEqual(self.model.rowCount(wrapper_index), 2)  # readme.txt + SFX

        # 找到 SFX 目录并验证 parent 链回到 wrapper
        sfx_row = next(
            row
            for row in range(self.model.rowCount(wrapper_index))
            if self.model.data(self.model.index(row, 0, wrapper_index)) == "SFX"
        )
        sfx_index = self.model.index(sfx_row, 0, wrapper_index)
        self.assertEqual(self.model.parent(sfx_index), wrapper_index)
        self.assertEqual(self.model.parent(wrapper_index), QModelIndex())
        self.assertEqual(self.model.node(sfx_index).name, "SFX")

    def test_index_for_node_roundtrip(self) -> None:
        inner = next(n for n in self.session.root.walk() if n.name == "inner.txt")
        index = self.model.index_for_node(inner)
        self.assertTrue(index.isValid())
        self.assertIs(self.model.node(index), inner)
        self.assertFalse(self.model.index_for_node(self.session.root).isValid())

    def test_source_column_and_icon(self) -> None:
        wrapper_index = self.model.index(0, 0)
        self.assertEqual(self.model.data(self.model.index(0, 1, wrapper_index)), "包A")
        self.assertEqual(self.model.data(wrapper_index, Qt.ItemDataRole.ToolTipRole).splitlines()[0], "包A")
        self.assertIsNotNone(self.model.data(wrapper_index, Qt.ItemDataRole.DecorationRole))

        boom = next(n for n in self.session.root.walk() if n.name == "boom.wav")
        self.assertEqual(icon_name_for(boom), "music_note")
        self.assertIsNotNone(self.model.data(self.model.index_for_node(boom), Qt.ItemDataRole.DecorationRole))
        boom.name = "镜头.mp4"
        self.assertEqual(icon_name_for(boom), "video")

    # ---- 改名 -------------------------------------------------------
    def test_setdata_renames_and_persists(self) -> None:
        readme = next(n for n in self.session.root.children[0].children if n.name == "readme.txt")
        index = self.model.index_for_node(readme)
        self.assertTrue(self.model.setData(index, "说明.txt", Qt.ItemDataRole.EditRole))
        self.assertEqual(readme.name, "说明.txt")
        self.assertEqual(self.model.data(index), "说明.txt")
        # 撤销后退回旧名
        self.session.undo.undo()
        self.assertEqual(next(n for n in self.session.root.walk() if n.name == "readme.txt").name, "readme.txt")

    def test_setdata_rejects_illegal_name(self) -> None:
        readme = next(n for n in self.session.root.walk() if n.name == "readme.txt")
        index = self.model.index_for_node(readme)
        seen: list[str] = []
        self.model.rename_rejected.connect(seen.append)
        self.assertFalse(self.model.setData(index, "a/b.txt", Qt.ItemDataRole.EditRole))
        self.assertTrue(seen)
        self.assertEqual(readme.name, "readme.txt")

    # ---- 拖放 -------------------------------------------------------
    def test_mime_roundtrip_and_move(self) -> None:
        wrapper_index = self.model.index(0, 0)
        sfx = next(n for n in self.session.root.children[0].children if n.is_dir)
        readme = next(n for n in self.session.root.children[0].children if not n.is_dir)

        data = self.model.mimeData([self.model.index_for_node(readme)])
        self.assertTrue(data.hasFormat(MIME_NODES))
        self.assertEqual(json.loads(bytes(data.data(MIME_NODES)).decode()), [readme.uid])

        target_index = self.model.index_for_node(sfx)
        self.assertTrue(self.model.canDropMimeData(data, Qt.DropAction.MoveAction, -1, 0, target_index))
        self.assertTrue(self.model.dropMimeData(data, Qt.DropAction.MoveAction, -1, 0, target_index))
        self.assertEqual(readme.parent, sfx)
        self.assertEqual(readme.path(), "包A\\SFX\\readme.txt")
        self.assertEqual(wrapper_index.isValid(), True)

    def test_cannot_drop_into_self_or_descendant(self) -> None:
        sfx = next(n for n in self.session.root.children[0].children if n.is_dir)
        deeper = next(n for n in sfx.children if n.is_dir)
        data = self.model.mimeData([self.model.index_for_node(sfx)])
        self.assertFalse(
            self.model.canDropMimeData(data, Qt.DropAction.MoveAction, -1, 0, self.model.index_for_node(deeper))
        )

    def test_drop_source_entry_subtree(self) -> None:
        source_id = self.wrapper.source.source_id
        assert source_id is not None
        payload = QMimeData()
        payload.setData(MIME_SOURCE, json.dumps({"source_id": source_id, "entry_path": "SFX"}).encode())
        self.assertTrue(self.model.canDropMimeData(payload, Qt.DropAction.CopyAction, -1, 0, QModelIndex()))
        self.assertTrue(self.model.dropMimeData(payload, Qt.DropAction.CopyAction, -1, 0, QModelIndex()))
        dropped = next(child for child in self.session.root.children if child.name == "SFX")
        self.assertTrue(dropped.is_dir)
        self.assertEqual([child.name for child in dropped.children], ["boom.wav", "深层"])
        self.assertEqual(dropped.source.entry_path, "SFX")

    def test_internal_source_drop_uses_custom_payload_without_materializing_urls(self) -> None:
        source_id = self.wrapper.source.source_id
        assert source_id is not None
        internal = QMimeData()
        internal.setData(
            MIME_SOURCE,
            json.dumps({"source_id": source_id, "entry_path": "SFX\\boom.wav"}).encode(),
        )
        prepare = mock.Mock(return_value=())
        payload = LazyFileMimeData(internal, prepare)

        self.assertTrue(
            self.model.canDropMimeData(
                payload, Qt.DropAction.CopyAction, -1, 0, QModelIndex()
            )
        )
        self.assertTrue(
            self.model.dropMimeData(
                payload, Qt.DropAction.CopyAction, -1, 0, QModelIndex()
            )
        )

        prepare.assert_not_called()
        self.assertIn("boom.wav", [child.name for child in self.session.root.children])

    def test_drop_source_single_file(self) -> None:
        source_id = self.wrapper.source.source_id
        assert source_id is not None
        payload = QMimeData()
        payload.setData(
            MIME_SOURCE,
            json.dumps({"source_id": source_id, "entry_path": "SFX\\boom.wav"}).encode(),
        )
        self.assertTrue(self.model.dropMimeData(payload, Qt.DropAction.CopyAction, -1, 0, QModelIndex()))
        node = next(child for child in self.session.root.children if child.name == "boom.wav")
        self.assertFalse(node.is_dir)
        self.assertEqual(node.source.entry_path, "SFX\\boom.wav")

    def test_drop_source_whole_archive_wraps_name(self) -> None:
        source_id = self.wrapper.source.source_id
        assert source_id is not None
        payload = QMimeData()
        payload.setData(
            MIME_SOURCE,
            json.dumps({"source_id": source_id, "entry_path": "", "whole": True}).encode(),
        )
        self.assertTrue(self.model.dropMimeData(payload, Qt.DropAction.CopyAction, -1, 0, QModelIndex()))
        self.assertEqual(
            [child.name for child in self.session.root.children],
            ["包A", "包A"],
        )

    def test_drop_unknown_source_is_rejected(self) -> None:
        payload = QMimeData()
        payload.setData(MIME_SOURCE, json.dumps({"source_id": "不存在", "entry_path": ""}).encode())
        self.assertFalse(self.model.dropMimeData(payload, Qt.DropAction.CopyAction, -1, 0, QModelIndex()))

    def test_drop_urls_imports_disk_file(self) -> None:
        loose = self.tmp / "散文件.txt"
        loose.write_text("loose", encoding="utf-8")
        payload = QMimeData()
        payload.setUrls([QUrl.fromLocalFile(str(loose))])
        self.assertTrue(self.model.canDropMimeData(payload, Qt.DropAction.CopyAction, -1, 0, QModelIndex()))
        self.assertTrue(self.model.dropMimeData(payload, Qt.DropAction.CopyAction, -1, 0, QModelIndex()))
        self.assertIn("散文件.txt", [child.name for child in self.session.root.children])

    def test_drop_batch_prompts_once_and_renames_all_duplicate_files(self) -> None:
        paths = []
        for folder_name in ("old", "new-1", "new-2"):
            folder = self.tmp / folder_name
            folder.mkdir()
            path = folder / "Take.wav"
            path.write_bytes(folder_name.encode())
            paths.append(path)
        self.session.import_disk_paths([paths[0]])
        seen: list[int] = []
        self.model.conflict_resolver = (
            lambda count: seen.append(count) or ResolveStrategy.RENAME
        )
        payload = QMimeData()
        payload.setUrls([QUrl.fromLocalFile(str(path)) for path in paths[1:]])

        self.assertTrue(
            self.model.dropMimeData(
                payload, Qt.DropAction.CopyAction, -1, 0, QModelIndex()
            )
        )

        self.assertEqual(seen, [2])
        names = [child.name for child in self.session.root.children if not child.is_dir]
        self.assertEqual(names, ["Take.wav", "Take_1.wav", "Take_2.wav"])

    def test_inline_rename_can_replace_existing_file(self) -> None:
        first = self.tmp / "first.txt"
        second = self.tmp / "second.txt"
        first.write_text("old", encoding="utf-8")
        second.write_text("new", encoding="utf-8")
        old, incoming = self.session.import_disk_paths([first, second])
        self.model.conflict_resolver = lambda _count: ResolveStrategy.OVERWRITE

        self.assertTrue(
            self.model.setData(
                self.model.index_for_node(incoming),
                "FIRST.txt",
                Qt.ItemDataRole.EditRole,
            )
        )

        self.assertNotIn(old, self.session.root.children)
        self.assertIn(incoming, self.session.root.children)
        self.assertEqual(incoming.name, "FIRST.txt")

    def test_flags_dir_droppable_file_not(self) -> None:
        wrapper = self.session.root.children[0]
        sfx = next(n for n in wrapper.children if n.is_dir)
        readme = next(n for n in wrapper.children if not n.is_dir)
        self.assertTrue(self.model.flags(self.model.index_for_node(sfx)) & Qt.ItemFlag.ItemIsDropEnabled)
        self.assertFalse(self.model.flags(self.model.index_for_node(readme)) & Qt.ItemFlag.ItemIsDropEnabled)
        self.assertTrue(self.model.flags(QModelIndex()) & Qt.ItemFlag.ItemIsDropEnabled)

    def test_refresh_keeps_tree_consistent(self) -> None:
        self.model.refresh()
        self.assertEqual(self.model.rowCount(QModelIndex()), len(self.session.root.children))
        self.model.set_palette(palette_for("light", "#0078D4"))  # 不抛异常即可
        self.assertIsNotNone(self.model.data(self.model.index(0, 0), Qt.ItemDataRole.DecorationRole))

    def test_pending_refresh_is_cancelled_with_model_destruction(self) -> None:
        model = TargetTreeModel(self.session)
        model.request_refresh()
        model.deleteLater()
        _app.processEvents()
        _app.processEvents()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
