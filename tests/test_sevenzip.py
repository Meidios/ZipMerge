"""引擎层测试：列表解析、解压、打包、完整性测试。"""

from __future__ import annotations

import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.support import build_tree, make_sample_archive, sha256_file, snapshot
from zipmerge.engine import (
    ArchiveCancelled,
    ArchiveError,
    create_archive,
    extract_entries,
    list_entries,
    list_with_info,
    parse_slt,
    test_archive,
)
from zipmerge.engine.sevenzip import _run


class ParseSltTests(unittest.TestCase):
    SAMPLE = (
        "7-Zip 25.01 (x64) : Copyright (c) 1999-2025 Igor Pavlov : 2025-08-03\n"
        "\n"
        "Scanning the drive for archives:\n"
        "1 file, 668 bytes (1 KiB)\n"
        "\n"
        "Listing archive: C:\\tmp\\out.zip\n"
        "\n"
        "--\n"
        "Path = C:\\tmp\\out.zip\n"
        "Type = zip\n"
        "Physical Size = 668\n"
        "\n"
        "----------\n"
        "Path = a b.txt\n"
        "Folder = -\n"
        "Size = 7\n"
        "Packed Size = 7\n"
        "Modified = 2026-09-11 00:01:33.9589164\n"
        "Attributes = A\n"
        "Encrypted = -\n"
        "CRC = DD688081\n"
        "Method = Store\n"
        "\n"
        "Path = empty_dir\n"
        "Folder = +\n"
        "Size = 0\n"
        "Packed Size = 0\n"
        "Modified = 2026-09-11 00:01:33.9435288\n"
        "Attributes = D\n"
        "Encrypted = -\n"
        "\n"
        "Path = 子目录\\中文名.txt\n"
        "Folder = -\n"
        "Size = 7\n"
        "Packed Size = 7\n"
        "Modified = 2026-09-11 00:01:33.9573519\n"
        "Attributes = A\n"
        "Encrypted = +\n"
        "CRC = 46CE8AAC\n"
    )

    def test_header_parsed(self) -> None:
        listing = parse_slt(self.SAMPLE)
        self.assertEqual(listing.archive_type, "zip")
        self.assertEqual(listing.physical_size, 668)
        self.assertEqual(listing.path, "C:\\tmp\\out.zip")

    def test_entries_parsed(self) -> None:
        listing = parse_slt(self.SAMPLE)
        self.assertEqual(len(listing.entries), 3)
        plain, empty_dir, nested = listing.entries

        self.assertEqual(plain.path, "a b.txt")
        self.assertFalse(plain.is_dir)
        self.assertEqual(plain.size, 7)
        self.assertEqual(plain.crc, "DD688081")
        self.assertFalse(plain.encrypted)
        self.assertEqual(plain.mtime.year, 2026)

        self.assertEqual(empty_dir.path, "empty_dir")
        self.assertTrue(empty_dir.is_dir)

        self.assertEqual(nested.path, "子目录\\中文名.txt")
        self.assertTrue(nested.encrypted)
        self.assertEqual(nested.name, "中文名.txt")
        self.assertEqual(nested.parent, "子目录")
        self.assertTrue(listing.has_encrypted_entries)

    def test_trailing_slash_dir(self) -> None:
        listing = parse_slt(
            "Listing archive: x.tar\n\n--\nPath = x.tar\nType = tar\n\n"
            "----------\nPath = dir/\nFolder = +\nSize = 0\n\n"
        )
        self.assertEqual(len(listing.entries), 1)
        self.assertTrue(listing.entries[0].is_dir)
        self.assertEqual(listing.entries[0].path, "dir")

    def test_directory_without_folder_line(self) -> None:
        # 7z 格式目录条目没有 Folder 行，只有 D 属性，必须靠属性位识别
        listing = parse_slt(
            "Listing archive: x.7z\n\n--\nPath = x.7z\nType = 7z\n\n"
            "----------\n"
            "Path = SFX\nSize = 0\nPacked Size = 0\nAttributes = D\nEncrypted = -\n\n"
            "Path = SFX\\boom.wav\nSize = 3\nPacked Size = 3\nAttributes = A\n\n"
        )
        self.assertEqual(len(listing.entries), 2)
        self.assertTrue(listing.entries[0].is_dir)
        self.assertEqual(listing.entries[0].path, "SFX")
        self.assertFalse(listing.entries[1].is_dir)


class EngineRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = self.tmp / "src"
        self.src.mkdir()
        build_tree(self.src)

    def test_zip_round_trip_preserves_names_and_content(self) -> None:
        archive = self.tmp / "sample.zip"
        make_sample_archive(self.src, archive, "zip")

        listing = list_with_info(archive)
        self.assertEqual(listing.archive_type, "zip")
        paths = {entry.path for entry in listing.entries}
        self.assertIn("中文名.txt", paths)
        self.assertIn("子目录\\二重.txt", paths)
        self.assertIn("子目录\\更深\\deep.bin", paths)
        self.assertIn("empty_dir", paths)
        self.assertIn("a b.txt", paths)
        # 目录条目不会被当成文件
        self.assertTrue(next(e for e in listing.entries if e.path == "empty_dir").is_dir)
        self.assertTrue(next(e for e in listing.entries if e.path == "子目录\\更深\\空目录").is_dir)

        out = self.tmp / "out"
        extract_entries(archive, None, out)
        self.assertEqual(snapshot(out), snapshot(self.src))
        self.assertTrue((out / "子目录" / "更深" / "空目录").is_dir())

    def test_extract_subset_with_chinese_names(self) -> None:
        archive = self.tmp / "sample.zip"
        make_sample_archive(self.src, archive, "zip")
        out = self.tmp / "out-subset"
        extract_entries(archive, ["中文名.txt", "a b.txt"], out)

        self.assertEqual(
            snapshot(out),
            {
                "中文名.txt": sha256_file(self.src / "中文名.txt"),
                "a b.txt": sha256_file(self.src / "a b.txt"),
            },
        )

    def test_extract_overwrite_modes_are_explicit(self) -> None:
        archive = self.tmp / "overwrite.zip"
        make_sample_archive(self.src, archive, "zip")
        out = self.tmp / "overwrite-out"
        target = out / "中文名.txt"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")

        extract_entries(archive, ["中文名.txt"], out, overwrite="skip")
        self.assertEqual(target.read_bytes(), b"old")
        extract_entries(archive, ["中文名.txt"], out, overwrite="overwrite")
        self.assertNotEqual(target.read_bytes(), b"old")

        with self.assertRaises(ValueError):
            extract_entries(archive, None, out, overwrite="invalid")  # type: ignore[arg-type]

    def test_extract_subset_of_directory_tree(self) -> None:
        archive = self.tmp / "sample.7z"
        make_sample_archive(self.src, archive, "7z")
        out = self.tmp / "out-dir"
        extract_entries(archive, ["子目录"], out)
        self.assertEqual(
            snapshot(out),
            {
                "子目录\\二重.txt": sha256_file(self.src / "子目录" / "二重.txt"),
                "子目录\\更深\\deep.bin": sha256_file(self.src / "子目录" / "更深" / "deep.bin"),
            },
        )

    def test_7z_directories_are_recognized(self) -> None:
        # 回归：7z 格式目录没有 Folder 行，曾全部被误判为文件
        archive = self.tmp / "sample.7z"
        make_sample_archive(self.src, archive, "7z")
        listing = list_with_info(archive)
        self.assertTrue(next(e for e in listing.entries if e.path == "empty_dir").is_dir)
        self.assertTrue(next(e for e in listing.entries if e.path == "子目录\\更深\\空目录").is_dir)

    def test_create_archive_has_no_absolute_prefix(self) -> None:
        archive = self.tmp / "nested" / "deep" / "levels.zip"
        create_archive(archive, "zip", self.src)
        paths = {entry.path for entry in list_entries(archive)}
        self.assertFalse(any(p.startswith(("C:", "src\\", "tmp\\")) for p in paths))

    def test_test_archive_detects_corruption(self) -> None:
        archive = self.tmp / "broken.zip"
        make_sample_archive(self.src, archive, "zip")
        self.assertTrue(test_archive(archive))

        raw = archive.read_bytes()
        archive.write_bytes(raw[: len(raw) // 2])
        self.assertFalse(test_archive(archive))

    def test_list_non_archive_raises(self) -> None:
        bogus = self.tmp / "not-an-archive.zip"
        bogus.write_text("definitely not a zip\n", encoding="utf-8")
        with self.assertRaises(ArchiveError) as ctx:
            list_entries(bogus)
        self.assertTrue(ctx.exception.hint or ctx.exception.output)

    def test_level_switch_is_accepted(self) -> None:
        archive = self.tmp / "max.zip"
        create_archive(archive, "zip", self.src, level=9)
        self.assertTrue(test_archive(archive))

    def test_7z_password_encrypts_headers(self) -> None:
        archive = self.tmp / "secret.7z"
        create_archive(archive, "7z", self.src, password="交付密码")
        with self.assertRaises(ArchiveError):
            list_entries(archive)
        self.assertTrue(list_entries(archive, password="交付密码"))

    def test_zip_entry_names_use_utf8_flag(self) -> None:
        # PLAN 12.6：含非 ASCII 的条目名必须按 UTF-8 写入并置通用标志位 0x800
        archive = self.tmp / "utf8.zip"
        create_archive(archive, "zip", self.src)
        raw = archive.read_bytes()
        eocd = raw.rfind(b"PK\x05\x06")
        count, _size, offset = struct.unpack_from("<HIH", raw, eocd + 10)
        decoded: set[str] = set()
        pos = offset
        for _ in range(count):
            (flags,) = struct.unpack_from("<H", raw, pos + 8)
            nlen, elen, clen = struct.unpack_from("<HHH", raw, pos + 28)
            name = raw[pos + 46 : pos + 46 + nlen]
            if not name.isascii():
                self.assertTrue(flags & 0x800, name)
                decoded.add(name.decode("utf-8"))
            pos += 46 + nlen + elen + clen
        self.assertIn("中文名.txt", decoded)
        self.assertIn("子目录/更深/空目录/", decoded)


class CancellationTests(unittest.TestCase):
    def test_cancel_kills_silent_process_promptly(self) -> None:
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        started = time.perf_counter()
        command = [sys.executable, "-c", "import time; time.sleep(30)"]
        try:
            with mock.patch("zipmerge.engine.sevenzip._base_command", return_value=command):
                with self.assertRaises(ArchiveCancelled):
                    _run([], cancel=cancel)
        finally:
            timer.cancel()
        self.assertLess(time.perf_counter() - started, 2.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
