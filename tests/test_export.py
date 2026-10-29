"""导出链路端到端集成测试：双源包 → 整理 → 冲突 → 导出 → 逐文件校验。"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from zipmerge.core import ResolveStrategy
from zipmerge.core.exporter import (
    ExportCancelled,
    ExportOptions,
    build_resolution,
    estimate_export_size,
    run_export,
)
from zipmerge.core.session import MergeSession
from zipmerge.engine import ArchiveCancelled, create_archive, extract_entries, list_with_info

# 源包 A：中文名 / 空格名 / 深层嵌套 / 包内空目录 / 顶层空目录
ARCHIVE_A = {
    "SFX/boom.wav": b"boom-A",
    "SFX/\u7a7a\u683c \u540d\u5b57.wav": b"space-A",
    "SFX/\u6df1\u5c42/\u5185\u5c42/deep-A.txt": b"deep-A",
    "readme.txt": b"readme-A",
}
ARCHIVE_A_DIRS = ("SFX/empty_in_sfx", "\u9876\u5c42\u7a7a\u76ee\u5f55")

# 源包 B：与 A 在 SFX/boom.wav 上重名（导出冲突来源）
ARCHIVE_B = {
    "VO/line01.wav": b"line01-B",
    "VO/\u4e2d\u6587\u5bf9\u767d.wav": b"duibai-B",
    "SFX/boom.wav": b"boom-B",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_files(root: Path, files: dict[str, bytes], dirs: tuple[str, ...] = ()) -> None:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for rel in dirs:
        (root / rel).mkdir(parents=True, exist_ok=True)


def snapshot(root: Path) -> dict[str, str]:
    """返回 {相对路径（反斜杠）: 内容 SHA256}。"""
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            key = str(path.relative_to(root)).replace("/", "\\")
            result[key] = digest(path.read_bytes())
    return result


class ExportIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-export-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="\u96c6\u6210", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)

    def _build_sources(self) -> tuple[Path, Path]:
        a_src = self.tmp / "src_a"
        b_src = self.tmp / "src_b"
        write_files(a_src, ARCHIVE_A, ARCHIVE_A_DIRS)
        write_files(b_src, ARCHIVE_B)
        a_zip = self.tmp / "\u9879\u76eexxx_SFX_20260910_Demo_V1.zip"
        b_zip = self.tmp / "0910\u9879\u76eexxx_vo_demo.7z"
        create_archive(a_zip, "zip", a_src)
        create_archive(b_zip, "7z", b_src)
        return a_zip, b_zip

    def test_two_archives_merge_end_to_end(self) -> None:
        a_zip, b_zip = self._build_sources()

        wrapper_a = self.session.import_archive(a_zip)
        wrapper_b = self.session.import_archive(b_zip)
        self.assertEqual(wrapper_a.name, "\u9879\u76eexxx_SFX_20260910_Demo_V1")
        self.assertEqual(wrapper_b.name, "0910\u9879\u76eexxx_vo_demo")
        # 同一路径重复导入直接复用已有项，不产生重复树
        self.assertIs(self.session.import_archive(a_zip), wrapper_a)

        # 摊平两个「包名文件夹」层，让内容直接进入目标根
        self.assertEqual(self.session.flatten(wrapper_a, 0), 3)
        self.assertEqual(self.session.flatten(wrapper_b, 0), 2)

        # 重命名 + 新建空文件夹 + 移动
        readme = next(n for n in self.session.root.children if n.name == "readme.txt")
        self.session.rename(readme, "\u8bf4\u660e.txt")
        extra = self.session.new_folder(self.session.root, "\u9644\u52a0")
        self.session.move([readme], extra, 0)
        self.assertEqual(readme.path(), "\u9644\u52a0\\\u8bf4\u660e.txt")

        # 撤销一步再重做，确认整理链路可回滚
        self.session.undo.undo()
        self.assertEqual(readme.path(), "\u8bf4\u660e.txt")
        self.session.undo.redo()
        self.assertEqual(readme.path(), "\u9644\u52a0\\\u8bf4\u660e.txt")

        out = self.tmp / "out" / "\u9879\u76eexxx_\u9700\u6c42\u540d_SFX&VO_20260910_V1.zip"
        options = ExportOptions(
            output_path=out,
            strategy=ResolveStrategy.RENAME,
            stage_dir=self.tmp / "stage",
        )
        progress: list[int] = []
        result = run_export(
            self.session.root,
            self.session.sources,
            options,
            on_progress=lambda pct, _text: progress.append(pct),
        )

        self.assertTrue(out.exists())
        self.assertTrue(progress and progress[-1] == 100)
        self.assertEqual(result.renamed, 1)  # SFX/boom.wav 重名 → boom_1.wav
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.file_count, 7)
        self.assertEqual(result.dir_count, 8)

        # 重新列表：空目录（含包内与顶层）必须保留
        listing = list_with_info(out)
        dirs = {entry.path for entry in listing.entries if entry.is_dir}
        self.assertTrue(
            {"SFX", "VO", "\u9644\u52a0", "\u9876\u5c42\u7a7a\u76ee\u5f55", "SFX\\empty_in_sfx", "SFX\\\u6df1\u5c42\\\u5185\u5c42"} <= dirs
        )

        # 全量解压后逐文件 SHA256 比对
        verify = self.tmp / "verify"
        extract_entries(out, None, verify)
        expected = {
            "SFX\\boom.wav": digest(ARCHIVE_A["SFX/boom.wav"]),
            "SFX\\boom_1.wav": digest(ARCHIVE_B["SFX/boom.wav"]),
            "SFX\\\u7a7a\u683c \u540d\u5b57.wav": digest(ARCHIVE_A["SFX/\u7a7a\u683c \u540d\u5b57.wav"]),
            "SFX\\\u6df1\u5c42\\\u5185\u5c42\\deep-A.txt": digest(ARCHIVE_A["SFX/\u6df1\u5c42/\u5185\u5c42/deep-A.txt"]),
            "VO\\line01.wav": digest(ARCHIVE_B["VO/line01.wav"]),
            "VO\\\u4e2d\u6587\u5bf9\u767d.wav": digest(ARCHIVE_B["VO/\u4e2d\u6587\u5bf9\u767d.wav"]),
            "\u9644\u52a0\\\u8bf4\u660e.txt": digest(ARCHIVE_A["readme.txt"]),
        }
        self.assertEqual(snapshot(verify), expected)

    def test_disk_paths_export_and_estimate(self) -> None:
        loose = self.tmp / "disk" / "\u6563\u6587\u4ef6 \u8bf4\u660e.txt"
        loose.parent.mkdir(parents=True, exist_ok=True)
        loose.write_bytes(b"loose-data")
        folder = self.tmp / "disk" / "\u7d20\u6750\u5939"
        (folder / "\u5185\u5c42").mkdir(parents=True)
        (folder / "\u5185\u5c42" / "a.bin").write_bytes(b"bin-A")

        nodes = self.session.import_disk_paths([loose, folder, self.tmp / "\u7f3a\u5931.txt"])
        self.assertEqual([node.name for node in nodes], ["\u6563\u6587\u4ef6 \u8bf4\u660e.txt", "\u7d20\u6750\u5939"])
        self.assertEqual(estimate_export_size(self.session.root, self.session.sources), 15)

        out = self.tmp / "out2" / "disk.zip"
        result = run_export(
            self.session.root,
            self.session.sources,
            ExportOptions(output_path=out, stage_dir=self.tmp / "stage2"),
        )
        self.assertEqual(result.file_count, 2)
        self.assertEqual(result.dir_count, 2)

        verify = self.tmp / "verify2"
        extract_entries(out, None, verify)
        self.assertEqual(
            snapshot(verify),
            {
                "\u6563\u6587\u4ef6 \u8bf4\u660e.txt": digest(b"loose-data"),
                "\u7d20\u6750\u5939\\\u5185\u5c42\\a.bin": digest(b"bin-A"),
            },
        )

    def test_export_cancelled(self) -> None:
        a_zip, _ = self._build_sources()
        self.session.import_archive(a_zip)

        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ExportCancelled):
            run_export(
                self.session.root,
                self.session.sources,
                ExportOptions(output_path=self.tmp / "cancel.zip", stage_dir=self.tmp / "stage3"),
                cancel=cancel,
            )

    def test_overwrite_rebuilds_archive_without_old_entries(self) -> None:
        old_src = self.tmp / "old-src"
        write_files(old_src, {"old-only.txt": b"old"})
        output = self.tmp / "replace.zip"
        create_archive(output, "zip", old_src)

        new_file = self.tmp / "new-only.txt"
        new_file.write_bytes(b"new")
        self.session.import_disk_paths([new_file])
        run_export(
            self.session.root,
            self.session.sources,
            ExportOptions(output_path=output, stage_dir=self.tmp / "replace-stage"),
        )

        verify = self.tmp / "replace-verify"
        extract_entries(output, None, verify)
        self.assertEqual(snapshot(verify), {"new-only.txt": digest(b"new")})

    def test_cancelled_candidate_preserves_existing_output_and_cleans_stage(self) -> None:
        source = self.tmp / "source.txt"
        source.write_bytes(b"source")
        self.session.import_disk_paths([source])
        output = self.tmp / "existing.zip"
        output.write_bytes(b"original")
        stage = self.tmp / "cancel-stage"

        def cancel_during_pack(path, *_args, **_kwargs):
            Path(path).write_bytes(b"partial")
            raise ArchiveCancelled("操作已取消")

        with mock.patch("zipmerge.core.exporter.create_archive", cancel_during_pack):
            with self.assertRaises(ExportCancelled):
                run_export(
                    self.session.root,
                    self.session.sources,
                    ExportOptions(output_path=output, stage_dir=stage),
                )

        self.assertEqual(output.read_bytes(), b"original")
        self.assertFalse(stage.exists())
        self.assertEqual(list(self.tmp.glob(".existing.zip.zipmerge-*.tmp")), [])

    def test_disk_directory_contents_participate_in_conflict_detection(self) -> None:
        folder = self.tmp / "assets"
        folder.mkdir()
        (folder / "same.txt").write_bytes(b"folder")
        explicit = self.tmp / "same.txt"
        explicit.write_bytes(b"explicit")
        disk_dir, disk_file = self.session.import_disk_paths([folder, explicit])
        disk_file.name = "same.txt"
        self.session.move([disk_file], disk_dir, 0)

        _mappings, resolution = build_resolution(
            self.session.root,
            ExportOptions(output_path=self.tmp / "conflict.zip"),
        )
        self.assertEqual(len(resolution.conflicts), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
