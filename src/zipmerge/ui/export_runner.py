"""导出执行链路：后台线程跑 :func:`run_export`，配合冲突解决与进度对话框。

引擎与核心层不感知 Qt；本模块负责把它们接到界面上：

* :class:`ExportWorker` —— ``QThread`` 包装，向外发进度 / 完成 / 失败 / 取消信号；
* :class:`ConflictDialog` —— 冲突清单 + 整体策略 + 单项指定；
* :func:`run_export_dialog` —— 组装确认、进度与结果提示的入口。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    Conflict,
    ConflictKind,
    ExportCancelled,
    ExportFailure,
    ExportOptions,
    ExportResult,
    MergeSession,
    ResolveStrategy,
    build_export_plan,
    detect_conflicts,
    run_export,
    unique_output_path,
)
from . import fmt

#: 整体 / 单项策略的显示名
_STRATEGY_LABELS: tuple[tuple[ResolveStrategy, str], ...] = (
    (ResolveStrategy.RENAME, "自动改名（name_1）"),
    (ResolveStrategy.OVERWRITE, "覆盖（后者覆盖前者）"),
    (ResolveStrategy.SKIP, "跳过（保留先到者）"),
)

#: 单项下拉里「跟随整体」的哨兵值（None 不能直接放进 itemData）
_FOLLOW_DEFAULT = "__follow__"


class ExportWorker(QThread):
    """在后台线程执行一次完整导出。

    取消通过 :meth:`cancel` 置位事件；引擎侧按行检查，通常很快返回。
    """

    progress = Signal(int, str)
    completed = Signal(object)  # ExportResult
    failed = Signal(str, str)  # message, detail
    cancelled = Signal()

    def __init__(self, session: MergeSession, options: ExportOptions, parent=None) -> None:
        super().__init__(parent)
        self._session = session
        self._options = options
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            result = run_export(
                self._session.root,
                self._session.sources,
                self._options,
                on_progress=self._emit_progress,
                cancel=self._cancel,
            )
        except ExportCancelled:
            self.cancelled.emit()
        except ExportFailure as exc:
            self.failed.emit(exc.message, exc.detail)
        except Exception as exc:  # 兜底：别让后台线程静默吞掉异常
            self.failed.emit(f"导出时发生未预期错误：{exc}", "")
        else:
            self.completed.emit(result)

    def _emit_progress(self, percent: int, text: str) -> None:
        self.progress.emit(percent, text)


class ProgressDialog(QDialog):
    """导出进度对话框；点取消只请求取消，等后台线程收尾后才关闭。"""

    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("正在导出")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._cancelling = False

        self._label = QLabel("正在准备导出…")
        self._label.setWordWrap(True)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.clicked.connect(self.request_cancel)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch(1)
        bottom.addWidget(self._cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)
        layout.addLayout(bottom)

    def set_progress(self, percent: int, text: str) -> None:
        self._bar.setValue(int(percent))
        if text:
            self._label.setText(text)

    def request_cancel(self) -> None:
        if self._cancelling:
            return
        self._cancelling = True
        self._cancel_button.setEnabled(False)
        self._label.setText("正在取消，等待当前步骤结束…")
        self.cancel_requested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 关闭按钮等价于取消：先请求取消，收尾完成后再由外部关闭
        self.request_cancel()
        event.ignore()


class ConflictDialog(QDialog):
    """冲突清单：整体策略 + 逐项覆盖。"""

    def __init__(self, conflicts: list[Conflict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出冲突")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._conflicts = conflicts

        headline = QLabel(
            f"有 {len(conflicts)} 处内容会写到同一个路径，请选择处理方式："
        )
        headline.setWordWrap(True)

        self._group = QButtonGroup(self)
        strategy_row = QHBoxLayout()
        strategy_row.setContentsMargins(0, 0, 0, 0)
        for index, (strategy, label) in enumerate(_STRATEGY_LABELS):
            button = QRadioButton(label)
            button.setChecked(index == 0)
            self._group.addButton(button, index)
            strategy_row.addWidget(button)
        strategy_row.addStretch(1)

        self._table = QTableWidget(len(conflicts), 3, self)
        self._table.setHorizontalHeaderLabels(["目标路径", "类型", "处理方式"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self._row_combos: list[QComboBox] = []
        for row, conflict in enumerate(conflicts):
            path_item = QTableWidgetItem(conflict.target_path)
            path_item.setToolTip(conflict.target_path)
            self._table.setItem(row, 0, path_item)
            self._table.setItem(row, 1, QTableWidgetItem(conflict.description))

            combo = QComboBox()
            combo.addItem("跟随整体", _FOLLOW_DEFAULT)
            for strategy, label in _STRATEGY_LABELS:
                # 目录路径冲突下覆盖不安全，直接不提供该选项
                if strategy is ResolveStrategy.OVERWRITE and conflict.kind is not ConflictKind.DUPLICATE:
                    continue
                combo.addItem(label.split("（")[0], strategy.value)
            self._table.setCellWidget(row, 2, combo)
            self._row_combos.append(combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("按所选方式导出")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(headline)
        layout.addLayout(strategy_row)
        layout.addWidget(self._table, 1)
        layout.addWidget(buttons)

    def strategy(self) -> ResolveStrategy:
        index = self._group.checkedId()
        return _STRATEGY_LABELS[max(0, index)][0]

    def overrides(self) -> dict[str, ResolveStrategy]:
        result: dict[str, ResolveStrategy] = {}
        for conflict, combo in zip(self._conflicts, self._row_combos):
            data = combo.currentData()
            if data == _FOLLOW_DEFAULT:
                continue
            result[conflict.target_path] = ResolveStrategy(data)
        return result


def prepare_options(
    parent: QWidget, session: MergeSession, options: ExportOptions
) -> ExportOptions | None:
    """导出前的询问：冲突策略与同名输出文件。用户取消返回 ``None``。"""
    try:
        mappings = build_export_plan(session.root, expand_disk_dirs=True)
    except OSError as exc:
        QMessageBox.warning(parent, "无法导出", str(exc))
        return None
    conflicts = detect_conflicts(mappings)
    if conflicts:
        dialog = ConflictDialog(conflicts, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        options.strategy = dialog.strategy()
        options.overrides = dialog.overrides()

    if options.output_path.exists():
        choice = _ask_existing(parent, options.output_path)
        if choice is None:
            return None
        if choice == "bump":
            new_path, _ = unique_output_path(
                options.output_path.parent,
                options.output_path.stem,
                options.output_path.suffix,
            )
            options.output_path = new_path
    return options


def run_export_dialog(
    parent: QWidget,
    session: MergeSession,
    options: ExportOptions,
    *,
    show_success: bool = True,
) -> ExportResult | None:
    """完整导出交互：确认 → 进度 → 结果。返回导出结果，取消或失败返回 ``None``。"""
    prepared = prepare_options(parent, session, options)
    if prepared is None:
        return None

    dialog = ProgressDialog(parent)
    worker = ExportWorker(session, prepared, parent)
    holder: dict[str, object] = {}

    worker.progress.connect(dialog.set_progress)
    dialog.cancel_requested.connect(worker.cancel)
    worker.completed.connect(lambda result: (holder.__setitem__("result", result), dialog.accept()))
    worker.failed.connect(
        lambda message, detail: (holder.__setitem__("error", (message, detail)), dialog.accept())
    )
    worker.cancelled.connect(lambda: (holder.__setitem__("cancelled", True), dialog.accept()))

    worker.start()
    dialog.exec()
    worker.wait()

    if "error" in holder:
        message, detail = holder["error"]  # type: ignore[misc]
        box = QMessageBox(QMessageBox.Icon.Critical, "导出失败", str(message), parent=parent)
        if detail:
            box.setDetailedText(str(detail))
        box.exec()
        return None
    if holder.get("cancelled"):
        QMessageBox.information(parent, "已取消", "导出已取消，未生成文件。")
        return None
    result = holder.get("result")
    if result is None:
        return None
    if show_success:
        _show_success(parent, result)
    return result  # type: ignore[return-value]


def _ask_existing(parent: QWidget, path: Path) -> str | None:
    """同名文件已存在时的三选一：递增版本 / 覆盖 / 取消。"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("目标文件已存在")
    box.setText(f"{path.name} 已存在。")
    box.setInformativeText("要递增版本号另存，还是直接覆盖？")
    bump = box.addButton("递增版本号", QMessageBox.ButtonRole.AcceptRole)
    overwrite = box.addButton("覆盖", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(bump)
    box.exec()
    clicked = box.clickedButton()
    if clicked is bump:
        return "bump"
    if clicked is overwrite:
        return "overwrite"
    return None


def _show_success(parent: QWidget, result: ExportResult) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("导出完成")
    box.setText(f"已生成 {result.output_path.name}")
    parts = [
        f"{fmt.count_label(result.file_count, '个文件')} · {fmt.count_label(result.dir_count, '个文件夹')}",
        f"体积约 {fmt.human_size(estimate_size(result))}",
    ]
    if result.renamed:
        parts.append(f"自动改名 {result.renamed} 项")
    if result.overwritten:
        parts.append(f"覆盖 {result.overwritten} 项")
    if result.skipped:
        parts.append(f"跳过 {result.skipped} 项")
    box.setInformativeText(" · ".join(parts))
    open_button = box.addButton("打开所在文件夹", QMessageBox.ButtonRole.ActionRole)
    box.addButton("关闭", QMessageBox.ButtonRole.AcceptRole)
    box.exec()
    if box.clickedButton() is open_button:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.output_path.parent)))


def estimate_size(result: ExportResult) -> int:
    """导出完成后的实际文件体积（读磁盘，失败返回 -1）。"""
    try:
        return result.output_path.stat().st_size
    except OSError:
        return -1
