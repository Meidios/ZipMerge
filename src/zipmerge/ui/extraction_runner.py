"""面向用户的后台解压、冲突确认与结果提示。"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import ExtractionPlan, OverwriteMode, inspect_extraction_conflicts
from ..engine import ArchiveError, extract_entries
from . import fmt


@dataclass(frozen=True)
class ExtractionResult:
    destination: Path
    file_count: int
    dir_count: int
    skipped: int = 0


class ExtractionWorker(QThread):
    """顺序执行多个源包任务，并把每包进度折算为整体进度。"""

    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str, str, bool)
    cancelled = Signal(bool)

    def __init__(
        self,
        plan: ExtractionPlan,
        overwrite: OverwriteMode,
        existing_count: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._plan = plan
        self._overwrite = overwrite
        self._existing_count = existing_count
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102 - QThread 入口
        completed_jobs = 0
        total = max(1, len(self._plan.jobs))
        try:
            for index, job in enumerate(self._plan.jobs):
                if self._cancel.is_set():
                    self.cancelled.emit(completed_jobs > 0)
                    return

                def report(percent: int, _text: str, *, offset=index, name=job.archive_name) -> None:
                    overall = int((offset + max(0, min(100, percent)) / 100) * 100 / total)
                    self.progress.emit(overall, f"正在解压 {name}…")

                self.progress.emit(int(index * 100 / total), f"正在解压 {job.archive_name}…")
                extract_entries(
                    job.archive_path,
                    job.paths,
                    job.output_dir,
                    password=job.password,
                    overwrite=self._overwrite,
                    on_progress=report,
                    cancel=self._cancel,
                )
                completed_jobs += 1
        except ArchiveError as exc:
            if self._cancel.is_set():
                self.cancelled.emit(completed_jobs > 0)
            else:
                message = f"解压失败：{exc.message}"
                if exc.hint:
                    message += f"（{exc.hint}）"
                self.failed.emit(message, exc.output, True)
            return
        except Exception as exc:  # 防止线程异常静默丢失
            self.failed.emit(f"解压时发生未预期错误：{exc}", "", True)
            return

        if self._cancel.is_set():
            self.cancelled.emit(completed_jobs > 0)
            return
        skipped = self._existing_count if self._overwrite == "skip" else 0
        self.progress.emit(100, "解压完成")
        self.completed.emit(
            ExtractionResult(
                destination=self._plan.destination,
                file_count=max(0, self._plan.file_count - skipped),
                dir_count=self._plan.dir_count,
                skipped=skipped,
            )
        )


class ExtractionProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("正在解压")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._cancelling = False
        self._label = QLabel("正在准备解压…")
        self._label.setWordWrap(True)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.clicked.connect(self.request_cancel)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self._cancel_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)
        layout.addLayout(bottom)

    def set_progress(self, percent: int, text: str) -> None:
        self._bar.setValue(percent)
        if text:
            self._label.setText(text)

    def request_cancel(self) -> None:
        if self._cancelling:
            return
        self._cancelling = True
        self._cancel_button.setEnabled(False)
        self._label.setText("正在取消，已经写入的文件会保留…")
        self.cancel_requested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.request_cancel()
        event.ignore()


def _show_blocking_conflicts(parent: QWidget, structural: tuple[str, ...], internal: tuple[str, ...]) -> None:
    lines = [*structural]
    lines.extend(f"包内路径大小写或类型冲突：{path}" for path in internal)
    box = QMessageBox(QMessageBox.Icon.Critical, "无法安全解压", "目标路径存在结构冲突。", parent=parent)
    box.setInformativeText("请清理冲突路径或选择其他目录后重试。")
    box.setDetailedText("\n".join(lines))
    box.exec()


def _ask_overwrite(parent: QWidget, count: int) -> OverwriteMode | None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("目标中已有同名文件")
    box.setText(f"发现 {count} 个已存在的同名文件。")
    box.setInformativeText("请选择本次解压的统一处理方式。")
    skip = box.addButton("跳过已有", QMessageBox.ButtonRole.AcceptRole)
    overwrite = box.addButton("覆盖已有", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(skip)
    box.exec()
    if box.clickedButton() is skip:
        return "skip"
    if box.clickedButton() is overwrite:
        return "overwrite"
    return None


def run_extraction_dialog(parent: QWidget, plan: ExtractionPlan) -> ExtractionResult | None:
    """执行冲突预检、后台解压和最终结果提示。"""
    conflicts = inspect_extraction_conflicts(plan)
    if conflicts.blocking:
        _show_blocking_conflicts(parent, conflicts.structural, conflicts.internal)
        return None
    overwrite: OverwriteMode = "overwrite"
    if conflicts.existing_files:
        choice = _ask_overwrite(parent, len(conflicts.existing_files))
        if choice is None:
            return None
        overwrite = choice

    dialog = ExtractionProgressDialog(parent)
    worker = ExtractionWorker(plan, overwrite, len(conflicts.existing_files), parent)
    holder: dict[str, object] = {}
    worker.progress.connect(dialog.set_progress)
    dialog.cancel_requested.connect(worker.cancel)
    worker.completed.connect(lambda result: (holder.__setitem__("result", result), dialog.accept()))
    worker.failed.connect(
        lambda message, detail, partial: (
            holder.__setitem__("error", (message, detail, partial)), dialog.accept()
        )
    )
    worker.cancelled.connect(
        lambda partial: (holder.__setitem__("cancelled", partial), dialog.accept())
    )
    worker.start()
    dialog.exec()
    worker.wait()

    if "error" in holder:
        message, detail, partial = holder["error"]  # type: ignore[misc]
        box = QMessageBox(QMessageBox.Icon.Critical, "解压失败", str(message), parent=parent)
        if partial:
            box.setInformativeText("已经成功或部分写入的文件会保留在目标目录中。")
        if detail:
            box.setDetailedText(str(detail))
        box.exec()
        return None
    if "cancelled" in holder:
        QMessageBox.information(parent, "已取消", "解压已取消；已经写入的文件会保留在目标目录中。")
        return None
    result = holder.get("result")
    if not isinstance(result, ExtractionResult):
        return None
    _show_success(parent, result)
    return result


def _show_success(parent: QWidget, result: ExtractionResult) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("解压完成")
    box.setText(f"已解压到 {result.destination}")
    parts = [
        fmt.count_label(result.file_count, "个文件"),
        fmt.count_label(result.dir_count, "个文件夹"),
    ]
    if result.skipped:
        parts.append(f"跳过已有 {result.skipped} 项")
    box.setInformativeText(" · ".join(parts))
    open_button = box.addButton("打开目标文件夹", QMessageBox.ButtonRole.ActionRole)
    box.addButton("关闭", QMessageBox.ButtonRole.AcceptRole)
    box.exec()
    if box.clickedButton() is open_button:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.destination)))
