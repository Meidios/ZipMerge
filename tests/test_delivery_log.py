"""Evidence produced for temporary-archive incidents."""

import ctypes
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zipmerge.core import MergeSession
from zipmerge.util.delivery_log import DeliveryLog, delivery_log


class DeliveryLogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "客户名称.zip"
        self.path.write_bytes(b"secret audio")
        self.log = DeliveryLog()

    def sample(self):
        self.log.observe("test", self.path)
        return json.loads(self.log.report())["events"][-1]

    def test_disappearance_and_privacy(self):
        before = self.sample()
        self.path.unlink()
        after = self.sample()
        self.assertEqual(before["stat"]["size"], 12)
        self.assertEqual(before["read"]["status"], "readable")
        self.assertEqual(before["path_id"], after["path_id"])
        self.assertEqual(after["stat"]["status"], "not_found")
        self.assertEqual(after["parent_entry"]["status"], "not_listed")
        for value in (self.path.name, self.root.name, "secret audio"):
            self.assertNotIn(value, self.log.report())

    def test_access_denied_is_not_reported_as_missing(self):
        with patch("zipmerge.util.delivery_log._read_one", side_effect=PermissionError(13, "secret filename")):
            row = self.sample()
        self.assertEqual(row["stat"]["status"], "file")
        self.assertEqual(row["read"]["status"], "access_denied")
        self.assertEqual(row["parent_entry"]["status"], "listed")
        self.assertNotIn("secret filename", self.log.report())

    @unittest.skipUnless(os.name == "nt", "Windows sharing modes")
    def test_windows_exclusive_handle_is_distinguished_from_acl_denial(self):
        from ctypes import wintypes
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        create = dll.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        dll.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = create(str(self.path), 0x80000000, 0, None, 3, 0, None)
        self.assertNotEqual(h, ctypes.c_void_p(-1).value)
        try:
            row = self.sample()
            self.assertEqual(row["read"]["status"], "sharing_or_lock_violation")
            self.assertEqual(row["read"]["winerror"], 32)
            self.assertEqual(row["parent_entry"]["status"], "listed")
        finally:
            dll.CloseHandle(h)
        self.assertEqual(self.sample()["read"]["status"], "readable")

    def test_cleanup_timeline_identifies_the_replaced_generation(self):
        session = MergeSession(temp_root=self.root)
        self.addCleanup(session.close)
        a = session.create_temporary_output(self.path.name)
        a.write_bytes(b"one")
        session.commit_temporary_archive(a)
        b = session.create_temporary_output(self.path.name)
        b.write_bytes(b"two")
        session.commit_temporary_archive(b)
        rows = json.loads(delivery_log.report())["events"]
        after = next(r for r in reversed(rows) if r["event"] == "cleanup.after")
        self.assertEqual(after["path_id"], delivery_log.token(a.resolve()))
        self.assertEqual(after["stat"]["status"], "not_found")
        self.assertEqual(rows[-1]["read"]["status"], "readable")
