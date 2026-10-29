"""应用路径解析与长路径处理。

同时服务于源码运行（仓库根）与 PyInstaller onedir 打包（exe 同目录）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Windows 上单个路径组件的实用上限；超过时给 7-Zip 传 \\?\ 前缀。
LONG_PATH_THRESHOLD = 250


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """返回随程序分发资源所在的根目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[3]


def vendor_path(*parts: str) -> Path:
    return app_root().joinpath("vendor", *parts)


def sevenzip_exe() -> Path:
    return vendor_path("7zip", "7z.exe")


def asset_path(*parts: str) -> Path:
    return app_root().joinpath("assets", *parts)


def long_path(path: str | os.PathLike[str]) -> str:
    r"""在路径过长时补上 Windows 扩展长度前缀（\\?\ 或 \\?\UNC\）。"""
    text = os.path.abspath(os.fspath(path))
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    if len(text) >= LONG_PATH_THRESHOLD:
        return "\\\\?\\" + text
    return text


def to_backslashes(path: str) -> str:
    """归档条目路径统一使用反斜杠，便于与 7-Zip 输出对齐。"""
    return path.replace("/", "\\")


def to_forward_slashes(path: str) -> str:
    return path.replace("\\", "/")


def normalize_entry_path(path: str) -> str:
    """规范化包内条目路径：反斜杠、去掉前导 ./ 与首尾多余分隔符。"""
    text = to_backslashes(path.strip())
    while text.startswith(".\\"):
        text = text[2:]
    if text == ".":
        return ""
    return text.strip("\\")


def safe_relative_path(value: str) -> Path:
    """把包内相对路径 / 源包 ID 收敛为安全的本地相对路径。

    丢弃空段、``.`` 与 ``..``，避免源包里的绝对路径或上跳路径写到临时目录之外。
    """
    parts = [
        part
        for part in to_backslashes(str(value)).split("\\")
        if part and part not in (".", "..")
    ]
    return Path(*parts) if parts else Path("entry")


#: 默认识别为压缩包的扩展名。这是 PLAN 8.2 所说的「程序内的格式识别表」的唯一来源，
#: 系统集成的关联候选、源包面板的导入判定与目标树图标都从这里取。
ARCHIVE_SUFFIXES: tuple[str, ...] = (
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".tgz",
    ".cab",
    ".iso",
)

#: 复合后缀：单段后缀已覆盖匹配，这里只用于展示与命名处理
ARCHIVE_COMPOUND_SUFFIXES: tuple[str, ...] = (".tar.gz", ".tar.bz2", ".tar.xz")

_extra_archive_suffixes: set[str] = set()


def normalize_suffix(value: str) -> str:
    """规范化用户输入的扩展名：小写、补前导点；非法或长度不合理时返回空串。"""
    text = str(value).strip().lower()
    if not text:
        return ""
    if not text.startswith("."):
        text = "." + text
    if not 2 <= len(text) <= 32 or text.endswith("."):
        return ""
    allowed = "._+-"
    if not all(ch.isalnum() or ch in allowed for ch in text[1:]):
        return ""
    return text


def set_extra_archive_suffixes(values) -> None:
    """设置用户在设置页里新增的自定义后缀（进程内生效，见 PLAN 8.2）。"""
    global _extra_archive_suffixes
    _extra_archive_suffixes = {suffix for suffix in (normalize_suffix(v) for v in values) if suffix}


def extra_archive_suffixes() -> frozenset[str]:
    return frozenset(_extra_archive_suffixes)


def archive_suffixes() -> tuple[str, ...]:
    """内置 + 自定义识别后缀。"""
    return ARCHIVE_SUFFIXES + tuple(sorted(_extra_archive_suffixes))


def is_archive_path(path: str | os.PathLike[str]) -> bool:
    name = os.fspath(path).lower()
    return name.endswith(archive_suffixes() + ARCHIVE_COMPOUND_SUFFIXES)


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """按 Windows 文件名规则替换非法字符，并去除尾部点与空格。"""
    illegal = '\\/:*?"<>|'
    cleaned = "".join(replacement if ch in illegal or ord(ch) < 32 else ch for ch in name)
    return cleaned.rstrip(" .")
