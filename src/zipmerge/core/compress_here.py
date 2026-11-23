"""Create a sibling ZIP without changing inputs or overwriting existing output."""

from __future__ import annotations

import os
import stat
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path

from zipmerge.i18n import tr
from ..engine import ArchiveCancelled, create_archive
from ..engine.sevenzip import ProgressCallback


def compress_here(
    path: str | Path | Sequence[str | Path], *, on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    paths = (path,) if isinstance(path, (str, Path)) else path
    sources = list(dict.fromkeys(Path(item).absolute() for item in paths))
    if not sources:
        raise ValueError(tr("没有收到要打包的文件或目录。"))
    first = sources[0]
    parent = first.parent
    directories = set()
    for source in sources:
        if cancel is not None and cancel.is_set():
            raise ArchiveCancelled(tr("操作已取消"))
        try:
            mode = source.lstat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            mode = 0
        if not mode or stat.S_ISLNK(mode):
            raise ValueError(tr("文件或目录不存在，或是符号链接：{0}", source))
        if not stat.S_ISREG(mode) and not stat.S_ISDIR(mode):
            raise ValueError(tr("不支持的文件类型：{0}", source))
        if source.parent == source:
            raise ValueError(tr("不能直接打包磁盘根目录"))
        if stat.S_ISDIR(mode):
            directories.add(source)
    contents_only = len(sources) == 1 and first in directories
    name = first.name if contents_only else parent.name or first.stem
    workdir = first if contents_only else parent
    members = None
    staging_parent = parent
    if not contents_only:
        # Search results may include both a folder and one of its descendants.
        # Stage outside every selected folder, even when the first item is inside it.
        for ancestor in (parent, *parent.parents):
            if ancestor in directories:
                staging_parent = ancestor.parent
        selected = [item for item in sources if directories.isdisjoint(item.parents)]
        try:
            workdir = Path(os.path.commonpath([item.parent for item in selected]))
        except ValueError:
            # Different volumes have no common root. 7-Zip stores the basenames
            # of absolute operands and rejects conflicting archive member names.
            members = [str(item) for item in selected]
        else:
            members = [str(item.relative_to(workdir)) for item in selected]
    with tempfile.TemporaryDirectory(prefix=".zipmerge-pack-", dir=staging_parent) as staging:
        candidate = Path(staging) / "output.zip"
        create_archive(
            candidate, "zip", workdir, paths=members,
            on_progress=on_progress, cancel=cancel,
        )
        number = 1
        while True:
            if cancel is not None and cancel.is_set():
                raise ArchiveCancelled(tr("操作已取消"))
            suffix = "" if number == 1 else f"_{number}"
            destination = parent / f"{name}{suffix}.zip"
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
