"""Real 7-Zip regression coverage for the Explorer compression action."""

import shutil
import tempfile
import threading
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from zipmerge.core.compress_here import compress_here
from zipmerge.engine import ArchiveCancelled, extract_entries, list_with_info


class CompressHereTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-compress-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_chinese_directory_packs_contents_including_empty_directory(self):
        source = self.tmp / "中文 目录"
        (source / "空目录").mkdir(parents=True)
        (source / "文件.txt").write_bytes(b"selected content")
        (self.tmp / "unselected.txt").write_bytes(b"excluded")
        output = compress_here(source)
        self.assertEqual(output, self.tmp / "中文 目录.zip")
        names = {entry.path for entry in list_with_info(output).entries}
        self.assertEqual(names, {"空目录", "文件.txt"})
        extract_entries(output, None, self.tmp / "verify")
        self.assertEqual((self.tmp / "verify" / "文件.txt").read_bytes(), b"selected content")

    def test_empty_directory_creates_empty_zip(self):
        source = self.tmp / "空目录"
        source.mkdir()
        listing = list_with_info(compress_here(source))
        self.assertEqual((listing.archive_type, listing.entries), ("zip", []))

    def test_cancel_during_real_7zip_progress_discards_candidate(self):
        source = self.tmp / "进度测试.bin"
        source.write_bytes(os.urandom(6 * 1024 * 1024))
        stop = threading.Event()
        def cancel_on_progress(percent, _):
            self.assertIn(percent, range(101))
            stop.set()
        with self.assertRaises(ArchiveCancelled):
            compress_here(source, on_progress=cancel_on_progress, cancel=stop)
        self.assertTrue(stop.is_set())
        self.assertEqual(list(self.tmp.iterdir()), [source])

    def test_file_name_collision_and_archive_source_are_preserved(self):
        source = self.tmp / "@配音.final.wav"
        source.write_bytes(b"audio")
        output = compress_here(source)
        self.assertEqual(output.name, self.tmp.name + ".zip")
        original = output.read_bytes()
        self.assertEqual(compress_here(source).name, self.tmp.name + "_2.zip")
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(compress_here(output).name, self.tmp.name + "_3.zip")
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual([e.path for e in list_with_info(output).entries], [source.name])

    def test_failure_leaves_no_final_archive_or_staging_directory(self):
        source = self.tmp / "文件.txt"
        source.write_bytes(b"original")
        with mock.patch("zipmerge.core.compress_here.create_archive", side_effect=OSError("failed")):
            with self.assertRaises(OSError):
                compress_here(source)
        self.assertEqual(list(self.tmp.iterdir()), [source])

    def test_missing_source_creates_nothing(self):
        with self.assertRaises(ValueError):
            compress_here(self.tmp / "missing")
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_existing_directory_at_output_name_is_preserved(self):
        source = self.tmp / "sample.txt"
        source.write_bytes(b"selected")
        existing = self.tmp / (self.tmp.name + ".zip")
        existing.mkdir()
        self.assertEqual(compress_here(source).name, self.tmp.name + "_2.zip")
        self.assertTrue(existing.is_dir())

    def test_concurrent_shell_invocations_do_not_overwrite_each_other(self):
        source = self.tmp / "录音 [01].txt"
        source.write_bytes(b"selected file")
        with ThreadPoolExecutor(max_workers=2) as executor:
            outputs = list(executor.map(compress_here, [source, source]))
        self.assertEqual({p.name for p in outputs}, {self.tmp.name + ".zip", self.tmp.name + "_2.zip"})
        for output in outputs:
            self.assertEqual([e.path for e in list_with_info(output).entries], [source.name])
