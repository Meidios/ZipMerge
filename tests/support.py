"""测试夹具：构造样本目录与样本压缩包。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from zipmerge.engine import create_archive


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_tree(root: Path) -> None:
    """构造含中文名、空格、深层嵌套与空目录的样本目录。"""
    (root / "子目录" / "更深" / "空目录").mkdir(parents=True)
    (root / "empty_dir").mkdir(parents=True)
    (root / "中文名.txt").write_text("hello 中文\n", encoding="utf-8")
    (root / "a b.txt").write_text("space name\n", encoding="utf-8")
    (root / "子目录" / "更深" / "deep.bin").write_bytes(bytes(range(256)) * 8)
    (root / "子目录" / "二重.txt").write_text("nested\n", encoding="utf-8")


def snapshot(root: Path) -> dict[str, str]:
    """返回 {相对路径(反斜杠): 内容哈希} 快照。"""
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root)).replace("/", "\\")] = sha256_file(path)
    return result


def make_sample_archive(src: Path, out: Path, fmt: str = "zip", **kwargs) -> Path:
    create_archive(out, fmt, src, **kwargs)
    return out
