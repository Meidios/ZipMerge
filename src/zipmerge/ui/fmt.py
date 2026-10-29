"""UI 层共用的展示格式化。"""

from __future__ import annotations

_UNITS: tuple[str, ...] = ("B", "KB", "MB", "GB", "TB")


def human_size(size: int) -> str:
    """把字节数格式化成便于阅读的字符串。"""
    if size < 0:
        return "—"
    value = float(size)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def count_label(count: int, unit: str = "项") -> str:
    return f"{count} {unit}"
