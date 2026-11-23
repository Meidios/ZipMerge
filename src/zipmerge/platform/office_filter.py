"""Read text from legacy Office documents using the Windows Search IFilter API."""

import ctypes as ct
from ctypes import wintypes as wt
from pathlib import Path

from ..core.document_preview import MAX_TEXT
from ..i18n import tr


class _PropertySpec(ct.Structure):
    _fields_ = [("kind", wt.ULONG), ("name_or_id", ct.c_void_p)]


class _Chunk(ct.Structure):
    _fields_ = [
        ("id", wt.ULONG), ("break_type", wt.ULONG), ("flags", wt.ULONG),
        ("locale", wt.LCID), ("property_set", ct.c_byte * 16), ("property", _PropertySpec),
        ("source_id", wt.ULONG), ("start", wt.ULONG), ("length", wt.ULONG),
    ]


def legacy_office_text(path: Path) -> str:
    ole = ct.windll.ole32
    initialized = ole.CoInitializeEx(None, 0)
    if initialized < 0 and initialized & 0xffffffff != 0x80010106:  # RPC_E_CHANGED_MODE
        raise OSError(ct.FormatError(initialized))
    ptr = ct.c_void_p()
    try:
        load = ct.windll.query.LoadIFilter
        load.argtypes = (wt.LPCWSTR, ct.c_void_p, ct.POINTER(ct.c_void_p))
        if load(str(path.resolve()), None, ct.byref(ptr)) < 0:
            raise ValueError(tr("Windows 未能读取旧版 Office 文件，请检查文件或预览筛选器"))
        vtable = ct.cast(ptr, ct.POINTER(ct.POINTER(ct.c_void_p))).contents

        def method(slot, *args):
            return ct.WINFUNCTYPE(wt.LONG, ct.c_void_p, *args)(vtable[slot])

        release = method(2)
        try:
            init = method(3, wt.ULONG, wt.ULONG, ct.c_void_p, ct.POINTER(wt.ULONG))
            flags = wt.ULONG()
            # Canonical spaces and paragraphs, content attributes, skip embedded objects.
            if init(ptr, 0x819, 0, None, ct.byref(flags)) < 0:
                raise ValueError(tr("无法读取文档，文件可能已加密或损坏"))
            get_chunk = method(4, ct.POINTER(_Chunk))
            get_text = method(5, ct.POINTER(wt.ULONG), wt.LPWSTR)
            chunk = _Chunk()
            buf = ct.create_unicode_buffer(4096)
            pieces = []
            length = 0
            while True:
                status = get_chunk(ptr, ct.byref(chunk)) & 0xffffffff
                if status == 0x80041700:  # FILTER_E_END_OF_CHUNKS
                    break
                if status & 0x80000000:
                    raise ValueError(tr("无法读取文档，文件可能已加密或损坏"))
                if not chunk.flags & 1:  # CHUNK_TEXT
                    continue
                if pieces and chunk.break_type:
                    pieces.append(" " if chunk.break_type == 1 else "\n")
                    length += 1
                while True:
                    size = wt.ULONG(len(buf))
                    status = get_text(ptr, ct.byref(size), buf) & 0xffffffff
                    if status == 0x80041701:  # FILTER_E_NO_MORE_TEXT
                        break
                    if status & 0x80000000:
                        raise ValueError(tr("无法读取文档，文件可能已加密或损坏"))
                    pieces.append(buf[:size.value].rstrip("\0"))
                    length += size.value
                    if length >= MAX_TEXT:
                        return "".join(pieces)[:MAX_TEXT] + "\n" + tr("已达到预览上限")
                    if not size.value or status == 0x41709:  # FILTER_S_LAST_TEXT
                        break
            text = "".join(pieces).strip()
            if not text:
                raise ValueError(tr("文件中没有可预览的文本"))
            return text
        finally:
            release(ptr)
    finally:
        if initialized >= 0:
            ole.CoUninitialize()
