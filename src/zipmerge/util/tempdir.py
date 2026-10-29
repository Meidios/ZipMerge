"""临时目录管理：按会话分子目录，关闭时清理，启动时扫除残留。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .paths import long_path

STALE_MARKER = "session-"


def default_root() -> Path:
    """默认临时根目录：%TEMP%\\ZipMerge。"""
    return Path(tempfile.gettempdir()) / "ZipMerge"


def create_session_dir(root: Path, token: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    suffix = token or "session"
    return Path(tempfile.mkdtemp(prefix=f"{STALE_MARKER}{suffix}-", dir=str(root)))


def dir_size(path: Path) -> int:
    """递归统计目录占用字节数。"""
    total = 0
    if not path.exists():
        return 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os_scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def os_scandir(path: Path):
    import os

    return os.scandir(long_path(path))


def clear_dir(path: Path) -> bool:
    """清空目录内容但保留目录本身。返回是否成功。"""
    if not path.exists():
        return True
    ok = True
    for child in path.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(long_path(child), ignore_errors=False)
            else:
                child.unlink()
        except OSError:
            ok = False
    return ok


def remove_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(long_path(path), ignore_errors=True)


def purge_stale(root: Path, keep: set[Path] | None = None) -> int:
    """删除上次运行残留的会话目录，返回清理数量。"""
    if not root.exists():
        return 0
    keep = {path.resolve() for path in (keep or set())}
    removed = 0
    for child in root.iterdir():
        if not child.is_dir() or not child.name.startswith(STALE_MARKER):
            continue
        if child.resolve() in keep:
            continue
        remove_dir(child)
        removed += 1
    return removed

