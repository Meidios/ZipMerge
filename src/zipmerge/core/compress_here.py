"""Create a sibling ZIP without changing inputs or overwriting existing output."""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from zipmerge.i18n import tr
from ..engine import ArchiveCancelled, create_archive
from ..engine.sevenzip import ProgressCallback


def compress_here(
    path: str | Path, *, on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    source = Path(path).absolute()
    if not source.exists() or source.is_symlink():
        raise ValueError(tr("文件或目录不存在，或是符号链接：{0}", source))
    if not source.is_file() and not source.is_dir():
        raise ValueError(tr("不支持的文件类型：{0}", source))
    if source.parent == source:
        raise ValueError(tr("不能直接打包磁盘根目录"))
    directory = source.is_dir()
    name = source.name if directory else source.parent.name or source.stem
    with tempfile.TemporaryDirectory(prefix=".zipmerge-pack-", dir=source.parent) as staging:
        candidate = Path(staging) / "output.zip"
        create_archive(
            candidate, "zip", source if directory else source.parent,
            paths=None if directory else [f".\\{source.name}"],
            on_progress=on_progress, cancel=cancel,
        )
        number = 1
        while True:
            if cancel is not None and cancel.is_set():
                raise ArchiveCancelled(tr("操作已取消"))
            suffix = "" if number == 1 else f"_{number}"
            destination = source.parent / f"{name}{suffix}.zip"
            if destination.exists():
                number += 1
                continue
            try:
                # Windows rename refuses existing targets, even across concurrent
                # invocations. POSIX link provides the same no-clobber rule.
                if os.name == "nt":
                    candidate.rename(destination)
                else:
                    os.link(candidate, destination)
                return destination
            except FileExistsError:
                number += 1
