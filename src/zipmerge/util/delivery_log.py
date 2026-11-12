"""Bounded, process-local observations about temporary deliveries. Export is explicit."""

from collections import deque
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from zipmerge import __version__


def _read_one(path: Path) -> int:
    if os.name == "nt":
        # The C runtime collapses Win32 sharing violations and ACL failures into EACCES.
        # Use the native error and allow concurrent reads, writes and deletes.
        import _winapi
        handle = _winapi.CreateFile(str(path), _winapi.GENERIC_READ, 7, 0,
                                    _winapi.OPEN_EXISTING, 0, 0)
        try:
            data, _ = _winapi.ReadFile(handle, 1)
            return len(data)
        finally:
            _winapi.CloseHandle(handle)
    with path.open("rb", buffering=0) as source:
        return len(source.read(1))


def error_codes(exc: OSError) -> dict:
    win = getattr(exc, "winerror", None)
    if win in (32, 33):
        kind = "sharing_or_lock_violation"
    elif win == 5 or isinstance(exc, PermissionError):
        kind = "access_denied"
    elif win in (2, 3) or exc.errno in (errno.ENOENT, errno.ENOTDIR):
        kind = "not_found"
    else:
        kind = "os_error"
    return {"status": kind, "errno": exc.errno, "winerror": win}


class DeliveryLog:
    def __init__(self):
        self._events = deque(maxlen=512)
        self._seq = 0
        self._key = os.urandom(16)

    def token(self, path: Path) -> str:
        # No username, project name, real path, or stable cross-run identifier.
        raw = os.fsencode(os.path.normcase(str(path)))
        return hashlib.blake2s(raw, key=self._key, digest_size=8).hexdigest()

    def event(self, action, path=None, **details):
        self._seq += 1
        row = {"seq": self._seq, "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "event": action}
        if path is not None:
            path = Path(path)
            row.update(path_id=self.token(path), parent_id=self.token(path.parent))
        self._events.append(dict(row, **details))

    def observe(self, action, path, **details):
        """Report independent metadata and one-byte read probes; never retain the byte."""
        path = Path(path)
        try:
            info = path.stat()
        except OSError as exc:
            metadata = error_codes(exc)
        else:
            metadata = {"status": "file" if stat.S_ISREG(info.st_mode) else "not_regular",
                        "size": info.st_size, "mtime_ns": info.st_mtime_ns}
        read = {"status": "not_attempted"}
        if metadata["status"] != "not_regular":
            try:
                count = _read_one(path)
            except OSError as exc:
                read = error_codes(exc)
            else:
                read = {"status": "readable" if count else "empty"}
        if metadata["status"] != "file" or read["status"] not in ("readable", "empty"):
            # Only test membership; never record any directory entries.
            try:
                with os.scandir(path.parent) as entries:
                    listed = any(os.path.normcase(e.name) == os.path.normcase(path.name) for e in entries)
                details["parent_entry"] = {"status": "listed" if listed else "not_listed"}
            except OSError as exc:
                details["parent_entry"] = error_codes(exc)
        self.event(action, path, stat=metadata, read=read, **details)
        return metadata

    def report(self) -> str:
        version = sys.getwindowsversion() if sys.platform == "win32" else None
        return json.dumps({"format": 1, "app": "ZipMerge", "version": __version__,
                           "windows_build": version.build if version else None,
                           "scope": "Current process only; recipient access is not observable.",
                           "dropped_older_events": self._seq - len(self._events),
                           "events": list(self._events)}, ensure_ascii=False, indent=2)


delivery_log = DeliveryLog()
