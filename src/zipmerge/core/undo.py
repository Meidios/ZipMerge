"""撤销 / 重做栈（默认保留 50 步）。"""

from __future__ import annotations

from collections.abc import Callable

from .commands import Command

DEFAULT_LIMIT = 50


class UndoStack:
    """不依赖 Qt 的命令栈；状态变化通过回调通知 UI。"""

    def __init__(
        self,
        limit: int = DEFAULT_LIMIT,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._limit = max(1, limit)
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self._on_change = on_change

    # ---- 状态 -------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._notify()

    # ---- 操作 -------------------------------------------------------

    def do(self, command: Command) -> None:
        """执行并入栈。"""
        command.apply()
        self._undo.append(command)
        if len(self._undo) > self._limit:
            self._undo.pop(0)
        self._redo.clear()
        self._notify()

    def push_applied(self, command: Command) -> None:
        """命令已由调用方执行时，只入栈。"""
        self._undo.append(command)
        if len(self._undo) > self._limit:
            self._undo.pop(0)
        self._redo.clear()
        self._notify()

    def undo(self) -> Command | None:
        if not self._undo:
            return None
        command = self._undo.pop()
        command.revert()
        self._redo.append(command)
        self._notify()
        return command

    def redo(self) -> Command | None:
        if not self._redo:
            return None
        command = self._redo.pop()
        command.apply()
        self._undo.append(command)
        self._notify()
        return command

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()

