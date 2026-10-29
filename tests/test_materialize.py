"""资源管理器拖出所用目录物化与选择结构测试。"""

from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from zipmerge.core import (
    ExportCancelled,
    ExportFailure,
    MaterializeRequest,
    MergeSession,
    NodeKind,
    TargetNode,
    build_source_drag_root,
    build_target_drag_root,
    materialize_directory,
)
from zipmerge.engine import create_archive


class MaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-materialize-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="拖出", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)

    def test_target_mixed_content_and_empty_dir(self) -> None:
        loose = self.tmp / "中文 文件.txt"
        loose.write_text("ok", encoding="utf-8")
        node = self.session.import_disk_paths([loose])[0]
        self.session.rename(node, "改名.txt")
        empty = self.session.new_folder(self.session.root, "空目录")
        root = build_target_drag_root([node, empty])
        result = materialize_directory(
            MaterializeRequest(root, self.session.sources, self.tmp / "out")
        )
        self.assertEqual((result.destination / "改名.txt").read_text(encoding="utf-8"), "ok")
        self.assertTrue((result.destination / "空目录").is_dir())
        self.assertEqual({path.name for path in result.paths}, {"改名.txt", "空目录"})

    def test_archive_folder_preserves_selected_structure(self) -> None:
        source_dir = self.tmp / "source"
        (source_dir / "SFX").mkdir(parents=True)
        (source_dir / "SFX" / "音效.wav").write_bytes(b"wave")
        archive = self.tmp / "包A.zip"
        create_archive(archive, "zip", source_dir)
        wrapper = self.session.import_archive(archive)
        sfx = next(node for node in wrapper.walk() if node.name == "SFX")
        root = build_source_drag_root([sfx], self.session.sources)
        result = materialize_directory(
            MaterializeRequest(root, self.session.sources, self.tmp / "archive-out")
        )
        self.assertEqual((result.destination / "SFX" / "音效.wav").read_bytes(), b"wave")

    def test_archive_dll_materializes_without_extension_filter(self) -> None:
        source_dir = self.tmp / "dll-source"
        source_dir.mkdir()
        payload = b"MZ" + bytes(range(64))
        (source_dir / "plugin.dll").write_bytes(payload)
        archive = self.tmp / "插件包.zip"
        create_archive(archive, "zip", source_dir)
        wrapper = self.session.import_archive(archive)
        dll = next(node for node in wrapper.walk() if node.name == "plugin.dll")

        root = build_source_drag_root([dll], self.session.sources)
        result = materialize_directory(
            MaterializeRequest(root, self.session.sources, self.tmp / "dll-out")
        )

        self.assertEqual([path.name for path in result.paths], ["plugin.dll"])
        self.assertEqual((result.destination / "plugin.dll").read_bytes(), payload)

    def test_parent_selection_removes_child(self) -> None:
        parent = TargetNode("文件夹", True)
        child = TargetNode("a.txt", False, NodeKind.VIRTUAL_DIR)
        parent.add(child)
        root = build_target_drag_root([parent, child])
        self.assertEqual(len(root.children), 1)
        self.assertEqual(root.children[0].name, "文件夹")

    def test_windows_case_collision_is_rejected_and_cleaned(self) -> None:
        a = self.tmp / "a"
        b = self.tmp / "b"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        nodes = self.session.import_disk_paths([a, b])
        self.session.rename(nodes[0], "Name.txt")
        self.session.rename(nodes[1], "name.txt")
        destination = self.tmp / "collision"
        with self.assertRaises(ExportFailure):
            materialize_directory(
                MaterializeRequest(build_target_drag_root(nodes), self.session.sources, destination)
            )
        self.assertFalse(destination.exists())

    def test_cancel_before_start_leaves_no_candidate(self) -> None:
        loose = self.tmp / "x.txt"
        loose.write_text("x", encoding="utf-8")
        root = build_target_drag_root(self.session.import_disk_paths([loose]))
        cancel = threading.Event()
        cancel.set()
        destination = self.tmp / "cancelled"
        with self.assertRaises(ExportCancelled):
            materialize_directory(
                MaterializeRequest(root, self.session.sources, destination), cancel=cancel
            )
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
