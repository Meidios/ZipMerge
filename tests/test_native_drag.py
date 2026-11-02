"""Exercise the actual Windows IDataObject ABI and Shell copy consumer."""
from __future__ import annotations

import ctypes as C
import os
import struct
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests.support import snapshot


@unittest.skipUnless(os.name == "nt", "Windows OLE only")
class NativeDragTests(unittest.TestCase):
    def setUp(self):
        from zipmerge.platform import native_drag as native
        self.n = native
        native.ole.OleInitialize(None)
        self.addCleanup(native.ole.OleUninitialize)
        self.temp = tempfile.TemporaryDirectory(prefix="zipmerge-native-drag-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def object(self, prepare):
        obj = self.n.DelayedFileDataObject({"application/x-zipmerge-nodes": b"[1]"}, prepare)
        self.addCleanup(obj.release)
        return obj

    def get(self, obj, fmt):
        n = self.n
        medium = n.Medium()
        method = n.com_method(obj.pointer, 3, n.HRESULT, C.POINTER(n.Format), C.POINTER(n.Medium))
        result = method(obj.pointer, C.byref(fmt), C.byref(medium))
        if result:
            return result, b""
        try:
            data = n.kernel.GlobalLock(medium.data)
            if fmt.cfFormat == 15:
                n.kernel.GlobalSize.argtypes = [n.PTR]
                n.kernel.GlobalSize.restype = C.c_size_t
                size = n.kernel.GlobalSize(medium.data)
            else:
                size = len(obj.payloads[fmt.cfFormat])
            return result, C.string_at(data, size)
        finally:
            n.kernel.GlobalUnlock(medium.data)
            n.ole.ReleaseStgMedium(C.byref(medium))

    def test_query_enumeration_and_internal_payload_never_materialize(self):
        n = self.n
        prepare = mock.Mock(side_effect=AssertionError("internal drag must not extract"))
        obj = self.object(prepare)
        fmt = n.Format(15, None, 1, -1, 1)
        query = n.com_method(obj.pointer, 5, n.HRESULT, C.POINTER(n.Format))
        self.assertEqual(query(obj.pointer, C.byref(fmt)), 0)
        enumerator = n.PTR()
        enum = n.com_method(obj.pointer, 8, n.HRESULT, n.W.DWORD, C.POINTER(n.PTR))
        self.assertEqual(enum(obj.pointer, 1, C.byref(enumerator)), 0)
        n.com_method(enumerator, 2, n.W.ULONG)(enumerator)
        cf = n.user.RegisterClipboardFormatW("application/x-zipmerge-nodes")
        self.assertEqual(self.get(obj, n.Format(cf, None, 1, -1, 1)), (0, b"[1]"))
        prepare.assert_not_called()

    def test_first_hdrop_contains_complete_unicode_files_and_only_prepares_once(self):
        n = self.n
        directory = self.root / "中文目录"
        file = directory / "音效.txt"
        def materialize():
            directory.mkdir()
            file.write_bytes(b"actual bytes")
            return (directory,)
        prepare = mock.Mock(side_effect=materialize)
        obj = self.object(prepare)
        for _ in range(2):
            result, data = self.get(obj, n.Format(15, None, 1, -1, 1))
            self.assertEqual(result, 0)
            self.assertEqual(struct.unpack_from("<IiiII", data), (20, 0, 0, 0, 1))
            self.assertEqual(data[20:].decode("utf-16-le"), str(directory) + "\0\0")
            self.assertEqual(file.read_bytes(), b"actual bytes")
        prepare.assert_called_once()

    def test_cancel_and_failure_never_supply_placeholder_paths(self):
        n = self.n
        for effect in (lambda: (), mock.Mock(side_effect=OSError("materialization failed"))):
            obj = self.object(effect)
            code, data = self.get(obj, n.Format(15, None, 1, -1, 1))
            self.assertLess(code, 0)
            self.assertEqual(data, b"")
            self.assertEqual(list(self.root.iterdir()), [])

    def test_reentrant_request_cannot_observe_unprepared_urls(self):
        n = self.n
        fmt = n.Format(15, None, 1, -1, 1)
        def prepare():
            code, data = self.get(obj, fmt)
            self.assertLess(code, 0)
            self.assertEqual(data, b"")
            file = self.root / "ready.txt"
            file.write_bytes(b"complete")
            return (file,)
        obj = self.object(prepare)
        self.assertEqual(self.get(obj, fmt)[0], 0)

    def test_native_data_request_uses_real_archive_worker_and_session_cache(self):
        from PySide6.QtWidgets import QApplication, QWidget
        from zipmerge.core import MergeSession, build_target_drag_root
        from zipmerge.engine import create_archive
        from zipmerge.ui.drag_export import DragExportController
        self.app = QApplication.instance() or QApplication([])
        source = self.root / "样本内容"
        (source / "子目录" / "空目录").mkdir(parents=True)
        (source / "音效.txt").write_text("real archive content", encoding="utf-8")
        archive = self.root / "中文加密.7z"
        create_archive(archive, "7z", source, password="验证口令")
        session = MergeSession(temp_root=self.root / "session")
        self.addCleanup(session.close)
        wrapper = session.import_archive(archive, password="验证口令")
        root = build_target_drag_root([wrapper])
        view = QWidget()
        self.addCleanup(view.deleteLater)
        controller = DragExportController(view, session)
        signature = controller._signature(root)
        fmt = self.n.Format(15, None, 1, -1, 1)
        with mock.patch.object(controller, "_run", wraps=controller._run) as run:
            for _ in range(2):
                obj = self.object(lambda: controller._prepare_paths(root, signature))
                self.assertEqual(self.get(obj, fmt)[0], 0)
                self.assertEqual(snapshot(obj.paths[0]), snapshot(source))
                self.assertTrue((obj.paths[0] / "子目录" / "空目录").is_dir())
                self.assertTrue(view.isEnabled())
            run.assert_called_once()

    def test_real_windows_shell_copies_first_delayed_hdrop_and_content(self):
        n = self.n
        # IFileOperation is the native Shell copy engine also used by Explorer.
        def guid(value):
            return (C.c_byte * 16).from_buffer_copy(uuid.UUID(value).bytes_le)
        clsid = guid("3ad05575-8857-4850-9277-11b85bdb8e09")
        iid = guid("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")
        operation = n.PTR()
        n.ole.CoCreateInstance.argtypes = [n.PTR, n.PTR, n.W.DWORD, n.PTR, C.POINTER(n.PTR)]
        n.ole.CoCreateInstance(C.byref(clsid), None, 1, C.byref(iid), C.byref(operation))
        self.addCleanup(n.com_method(operation, 2, n.W.ULONG), operation)
        flags = n.com_method(operation, 5, n.HRESULT, n.W.DWORD)
        self.assertEqual(flags(operation, 0x4 | 0x10 | 0x200 | 0x400), 0)  # no UI, new test destination
        target = self.root / "接收目录"
        target.mkdir()
        shell_item = n.PTR()
        item_iid = guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")
        n.shell.SHCreateItemFromParsingName.argtypes = [n.W.LPCWSTR, n.PTR, n.PTR, C.POINTER(n.PTR)]
        n.shell.SHCreateItemFromParsingName(str(target), None, C.byref(item_iid), C.byref(shell_item))
        self.addCleanup(n.com_method(shell_item, 2, n.W.ULONG), shell_item)
        source = self.root / "物化目录"
        prepare = mock.Mock()
        def materialize():
            (source / "子目录" / "空目录").mkdir(parents=True)
            (source / "中文音效.txt").write_bytes(bytes(range(256)) * 5)
            (source / "子目录" / "更多.bin").write_bytes(b"nested")
            return (source,)
        prepare.side_effect = materialize
        obj = self.object(prepare)
        copy = n.com_method(operation, 17, n.HRESULT, n.PTR, n.PTR)
        self.assertEqual(copy(operation, obj.pointer, shell_item), 0)
        perform = n.com_method(operation, 21, n.HRESULT)
        self.assertEqual(perform(operation), 0)
        aborted = n.W.BOOL()
        n.com_method(operation, 22, n.HRESULT, C.POINTER(n.W.BOOL))(operation, C.byref(aborted))
        self.assertFalse(aborted.value)
        prepare.assert_called_once()
        self.assertEqual(snapshot(target / source.name), snapshot(source))
        self.assertTrue((target / source.name / "子目录" / "空目录").is_dir())
