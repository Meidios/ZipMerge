"""Create a sibling ZIP without changing inputs or overwriting existing output."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..engine import create_archive


def compress_here(path: str | Path) -> Path:
    source = Path(path).absolute()
    if not source.exists() or source.is_symlink():
        raise ValueError(f"文件或目录不存在，或是符号链接：{source}")
    if not source.is_file() and not source.is_dir():
        raise ValueError(f"不支持的文件类型：{source}")
    if source.parent == source:
        raise ValueError("不能直接打包磁盘根目录")
    name = source.name if source.is_dir() else source.stem
    with tempfile.TemporaryDirectory(prefix=".zipmerge-pack-", dir=source.parent) as staging:
        candidate = Path(staging) / "output.zip"
        create_archive(candidate, "zip", source.parent, paths=[source.name])
        number = 1
        while True:
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
