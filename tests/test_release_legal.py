"""Release guards for source integrity and third-party license changes."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.prepare_release_legal import check_ffmpeg, obtain_source
from tools import verify_bundle


class SourceIntegrityTests(unittest.TestCase):
    def test_changed_cache_is_rejected_without_downloading(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            (cache / "source.zip").write_bytes(b"tampered source")
            item = {"filename": "source.zip", "url": "https://example.invalid/source.zip",
                    "sha256": hashlib.sha256(b"trusted source").hexdigest()}
            with mock.patch("urllib.request.urlopen") as download:
                with self.assertRaisesRegex(ValueError, "Cached source hash mismatch"):
                    obtain_source(item, cache)
                download.assert_not_called()

    def test_source_cannot_escape_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "safe path"):
                obtain_source({"filename": "../outside.zip"}, Path(temp))

    def test_failed_download_hash_never_publishes_file(self):
        import io
        with tempfile.TemporaryDirectory() as temp:
            item = {"filename": "source.zip", "url": "https://example.invalid/source.zip", "sha256": "0" * 64}
            with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(b"changed upstream")):
                with self.assertRaisesRegex(ValueError, "Source hash mismatch"):
                    obtain_source(item, Path(temp))
            self.assertEqual(list(Path(temp).iterdir()), [])


class FFmpegLicenseTests(unittest.TestCase):
    def test_rejects_license_changing_build_flags(self):
        for flag in ("--enable-gpl", "--enable-nonfree", "--enable-version3"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                check_ffmpeg("LGPL version 2.1 or later", f"--enable-shared {flag}")

    def test_rejects_unapproved_license_or_static_only_build(self):
        with self.assertRaises(ValueError):
            check_ffmpeg("GPL version 3 or later", "--enable-shared")
        with self.assertRaises(ValueError):
            check_ffmpeg("LGPL version 2.1 or later", "--enable-static")


class BundleNoticeTests(unittest.TestCase):
    def test_notice_tampering_fails_bundle_check(self):
        with tempfile.TemporaryDirectory() as temp:
            internal = Path(temp)
            legal = internal / "legal"
            legal.mkdir()
            (legal / "LICENSE").write_bytes(b"changed notice")
            (legal / "NOTICE_HASHES.json").write_text(json.dumps({"LICENSE": hashlib.sha256(b"original").hexdigest()}))
            problems = []
            with mock.patch.object(verify_bundle, "INTERNAL", internal):
                verify_bundle.check_legal(problems)
            self.assertTrue(any("notice hash mismatch" in problem for problem in problems))

    def test_removed_modules_and_plugins_are_guarded(self):
        import fnmatch
        for path in ("PySide6/Qt6VirtualKeyboard.dll", "PySide6/QtVirtualKeyboard.pyd",
                     "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
                     "PySide6/plugins/imageformats/qpdf.dll"):
            self.assertTrue(any(fnmatch.fnmatch(path, pattern) for pattern in verify_bundle.FORBIDDEN_PATTERNS), path)
