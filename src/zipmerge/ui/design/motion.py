"""动效工具：``QPropertyAnimation`` + ``QEasingCurve`` 封装。

系统「在 Windows 中显示动画」关闭时全部跳过，直接落终值（见 PLAN 6.7）。
动画对象以控件为 parent，避免被 Python GC 回收。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Callable

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

SPI_GETCLIENTAREAANIMATION = 0x1042

_enabled_override: bool | None = None
_enabled_cache: bool | None = None


def animations_enabled() -> bool:
    """系统是否允许界面动效。"""
    global _enabled_cache
    if _enabled_override is not None:
        return _enabled_override
    if _enabled_cache is not None:
        return _enabled_cache
    if sys.platform != "win32":
        _enabled_cache = True
        return True
    enabled = True
    try:
        value = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0
        )
        if ok:
            enabled = bool(value.value)
    except (OSError, AttributeError):
        enabled = True
    _enabled_cache = enabled
    return enabled


def set_animations_enabled(value: bool | None) -> None:
    """测试或设置页覆盖开关；``None`` 表示回到系统默认。"""
    global _enabled_override, _enabled_cache
    _enabled_override = value
    _enabled_cache = None


def animate(
    target,
    prop: bytes,
    start,
    end,
    duration_ms: int,
    *,
    curve: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
    on_finished: Callable[[], None] | None = None,
) -> QPropertyAnimation | None:
    """对 ``target`` 的 Qt 属性做插值；返回 ``None`` 表示已直接落终值。"""
    if not animations_enabled() or duration_ms <= 0:
        target.setProperty(prop.decode("ascii"), end)
        if on_finished is not None:
            QTimer.singleShot(0, on_finished)
        return None
    animation = QPropertyAnimation(target, prop, target)
    animation.setDuration(duration_ms)
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setEasingCurve(curve)
    if on_finished is not None:
        animation.finished.connect(on_finished)
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def fade_in(widget: QWidget, duration_ms: int, *, on_finished: Callable[[], None] | None = None):
    """淡入控件；动效关闭时保持不透明。"""
    if not animations_enabled() or duration_ms <= 0:
        if on_finished is not None:
            QTimer.singleShot(0, on_finished)
        return None
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = animate(effect, b"opacity", 0.0, 1.0, duration_ms)
    if animation is not None:
        animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    if on_finished is not None:
        QTimer.singleShot(duration_ms, on_finished)
    return animation


def animate_height(
    widget: QWidget,
    start: int,
    end: int,
    duration_ms: int,
    *,
    on_finished: Callable[[], None] | None = None,
) -> QPropertyAnimation | None:
    """面板展开 / 折叠用的高度动画。"""
    return animate(widget, b"maximumHeight", start, end, duration_ms, on_finished=on_finished)
