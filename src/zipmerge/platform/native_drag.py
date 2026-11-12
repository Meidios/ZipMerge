"""Windows OLE delayed file lists, without exposing incomplete placeholder files.

QueryGetData and EnumFormatEtc describe formats without reading files. GetData
materializes the selection before returning CF_HDROP. Windows supplies format
enumeration and drag negotiation; archive contents never enter this adapter.
"""
from __future__ import annotations

import ctypes as C
import os
import struct
import uuid
from pathlib import Path
from typing import Callable

if os.name == "nt":
    from ctypes import wintypes as W

    HRESULT = C.c_long
    PTR = C.c_void_p
    CALLBACK = C.WINFUNCTYPE
    S_OK, S_FALSE = 0, 1
    E_NOINTERFACE, E_NOTIMPL = -2147467262, -2147467263
    E_FAIL, E_ABORT = -2147467259, -2147467260
    DV_E_FORMATETC, DV_E_LINDEX, DV_E_TYMED = -2147221404, -2147221400, -2147221399
    OLE_E_ADVISENOTSUPPORTED = -2147221501
    IID_UNKNOWN = uuid.UUID("00000000-0000-0000-C000-000000000046").bytes_le
    IID_DATA = uuid.UUID("0000010e-0000-0000-C000-000000000046").bytes_le
    IID_SOURCE = uuid.UUID("00000121-0000-0000-C000-000000000046").bytes_le

    class Format(C.Structure):
        _fields_ = [("cfFormat", W.WORD), ("ptd", PTR), ("dwAspect", W.DWORD),
                    ("lindex", W.LONG), ("tymed", W.DWORD)]

    class Medium(C.Structure):
        _fields_ = [("tymed", W.DWORD), ("data", PTR), ("owner", PTR)]

    ole = C.OleDLL("ole32")
    shell = C.OleDLL("shell32")
    kernel = C.WinDLL("kernel32", use_last_error=True)
    user = C.WinDLL("user32", use_last_error=True)
    user.RegisterClipboardFormatW.argtypes = [W.LPCWSTR]
    user.RegisterClipboardFormatW.restype = W.UINT
    user.GetAsyncKeyState.argtypes = [C.c_int]
    user.GetAsyncKeyState.restype = C.c_short
    kernel.GlobalAlloc.argtypes = [W.UINT, C.c_size_t]
    kernel.GlobalAlloc.restype = PTR
    kernel.GlobalLock.argtypes = [PTR]
    kernel.GlobalLock.restype = PTR
    kernel.GlobalUnlock.argtypes = [PTR]
    kernel.GlobalFree.argtypes = [PTR]
    kernel.GlobalFree.restype = PTR
    shell.SHCreateStdEnumFmtEtc.argtypes = [W.UINT, C.POINTER(Format), C.POINTER(PTR)]
    ole.DoDragDrop.argtypes = [PTR, PTR, W.DWORD, C.POINTER(W.DWORD)]
    ole.ReleaseStgMedium.argtypes = [C.POINTER(Medium)]

    # COM owns outstanding references, including a target retaining the object
    # past DoDragDrop. Keep Python callbacks alive until its final Release.
    _live: dict[int, object] = {}

    def com_method(pointer, index, result, *arguments):
        table = C.cast(pointer, C.POINTER(C.POINTER(PTR))).contents
        return CALLBACK(result, PTR, *arguments)(table[index])

    class _ComObject:
        def __init__(self, iid, methods):
            self.refs = 1
            self.error: Exception | None = None
            self.iid = iid
            self.callbacks = [
                CALLBACK(HRESULT, PTR, PTR, C.POINTER(PTR))(self._query),
                CALLBACK(W.ULONG, PTR)(self._addref),
                CALLBACK(W.ULONG, PTR)(self._release),
            ]
            for args, function in methods:
                def guarded(*values, function=function):
                    try:
                        return function(*values)
                    except Exception as error:
                        self.error = error
                        return E_FAIL
                self.callbacks.append(CALLBACK(HRESULT, PTR, *args)(guarded))
            self.table = (PTR * len(self.callbacks))(
                *(C.cast(callback, PTR).value for callback in self.callbacks))
            self.instance = (PTR * 1)(C.addressof(self.table))
            self.pointer = C.cast(self.instance, PTR)
            _live[self.pointer.value] = self

        def _query(self, _this, iid, output):
            output[0] = None
            if C.string_at(iid, 16) not in (IID_UNKNOWN, self.iid):
                return E_NOINTERFACE
            output[0] = self.pointer.value
            self._addref(_this)
            return S_OK

        def _addref(self, _this):
            self.refs += 1
            return self.refs

        def _release(self, _this):
            self.refs -= 1
            if self.refs == 0:
                _live.pop(self.pointer.value, None)
            return self.refs

        def release(self):
            self._release(self.pointer)

    class DelayedFileDataObject(_ComObject):
        def __init__(self, private: dict[str, bytes], prepare: Callable[[], tuple[Path, ...]]):
            self.prepare = prepare
            self.attempted = False
            self.failed = False
            self.rendering = False
            self.paths: tuple[Path, ...] = ()
            effect = user.RegisterClipboardFormatW("Preferred DropEffect")
            self.payloads = {effect: struct.pack("<I", 1)}
            for name, value in private.items():
                self.payloads[user.RegisterClipboardFormatW(name)] = value
            self.formats = [Format(key, None, 1, -1, 1) for key in (*self.payloads, 15)]
            super().__init__(IID_DATA, [
                ([C.POINTER(Format), C.POINTER(Medium)], self._get),
                ([C.POINTER(Format), C.POINTER(Medium)], lambda *_: E_NOTIMPL),
                ([C.POINTER(Format)], self._can_get),
                ([C.POINTER(Format), C.POINTER(Format)], self._canonical),
                ([C.POINTER(Format), C.POINTER(Medium), W.BOOL], lambda *_: E_NOTIMPL),
                ([W.DWORD, C.POINTER(PTR)], self._enumerate),
                ([C.POINTER(Format), W.DWORD, PTR, C.POINTER(W.DWORD)], lambda *_: OLE_E_ADVISENOTSUPPORTED),
                ([W.DWORD], lambda *_: OLE_E_ADVISENOTSUPPORTED),
                ([C.POINTER(PTR)], lambda *_: OLE_E_ADVISENOTSUPPORTED),
            ])

        def _can_get(self, _this, format_pointer):
            fmt = format_pointer.contents
            if fmt.dwAspect != 1 or fmt.cfFormat not in (*self.payloads, 15):
                return DV_E_FORMATETC
            if fmt.lindex != -1:
                return DV_E_LINDEX
            return S_OK if fmt.tymed & 1 else DV_E_TYMED

        def _get(self, this, format_pointer, medium_pointer):
            C.memset(medium_pointer, 0, C.sizeof(Medium))
            result = self._can_get(this, format_pointer)
            if result != S_OK:
                return result
            fmt = format_pointer.contents
            if fmt.cfFormat == 15:
                if self.rendering:
                    return E_ABORT
                if not self.attempted:
                    self.attempted = True
                    self.rendering = True
                    try:
                        self.paths = self.prepare()
                        self.failed = not self.paths
                    except Exception:
                        self.failed = True
                        raise
                    finally:
                        self.rendering = False
                if self.failed:
                    return E_ABORT
                # DROPFILES followed by a double-null-terminated UTF-16 list.
                names = "\0".join(str(path) for path in self.paths) + "\0\0"
                data = struct.pack("<IiiII", 20, 0, 0, 0, 1) + names.encode("utf-16-le")
            else:
                data = self.payloads[fmt.cfFormat]
            handle = kernel.GlobalAlloc(2, max(1, len(data)))
            if not handle:
                return E_FAIL
            memory = kernel.GlobalLock(handle)
            if not memory:
                kernel.GlobalFree(handle)
                return E_FAIL
            C.memmove(memory, data, len(data))
            kernel.GlobalUnlock(handle)
            medium_pointer.contents.tymed = 1
            medium_pointer.contents.data = handle
            return S_OK

        def _canonical(self, _this, _input, output):
            output.contents.ptd = None
            return E_NOTIMPL

        def _enumerate(self, _this, direction, output):
            output[0] = None
            if direction != 1:
                return E_NOTIMPL
            formats = (Format * len(self.formats))(*self.formats)
            shell.SHCreateStdEnumFmtEtc(len(formats), formats, output)
            return S_OK

    class _DropSource(_ComObject):
        def __init__(self, data, process_events):
            self.data = data
            self.process_events = process_events
            super().__init__(IID_SOURCE, [
                ([W.BOOL, W.DWORD], self._continue),
                ([W.DWORD], lambda *_: 0x40102),
            ])

        def _continue(self, _this, escape, _keys):
            if escape or self.data.failed:
                return 0x40101  # DRAGDROP_S_CANCEL
            if not user.GetAsyncKeyState(1) & 0x8000:
                return 0x40100  # DRAGDROP_S_DROP
            self.process_events()
            return S_OK

    def drag_files(private, prepare, process_events) -> None:
        ole.OleInitialize(None)
        data = source = None
        try:
            data = DelayedFileDataObject(private, prepare)
            source = _DropSource(data, process_events)
            effect = W.DWORD()
            ole.DoDragDrop(data.pointer, source.pointer, 1, C.byref(effect))
            if data.error:
                raise data.error
            if source.error:
                raise source.error
        finally:
            if source is not None:
                source.release()
            if data is not None:
                data.release()
            ole.OleUninitialize()
