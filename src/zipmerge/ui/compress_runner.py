"""Cancellable progress for the standalone Explorer ZIP command."""

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from zipmerge.core.compress_here import compress_here
from zipmerge.engine import ArchiveCancelled
from zipmerge.i18n import tr
from .export_runner import ProgressDialog


class CompressWorker(QThread):
    progress = Signal(int, str)

    def __init__(self, source: Path) -> None:
        super().__init__()
        self.source = source
        self.cancel = threading.Event()
        self.output: Path | None = None
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            self.output = compress_here(
                self.source, cancel=self.cancel, on_progress=self.report_progress,
            )
        except ArchiveCancelled:
            pass
        except Exception as exc:
            self.error = exc

    def report_progress(self, percent: int, _raw: str) -> None:
        self.progress.emit(min(percent, 99), "")


def run_compress_dialog(path: str) -> Path | None:
    source = Path(path)
    dialog = ProgressDialog(title=tr("正在压缩"), message=source.name)
    worker = CompressWorker(source)
    worker.progress.connect(dialog.set_progress)
    dialog.cancel_requested.connect(worker.cancel.set)
    def finish() -> None:
        if worker.output is None:
            dialog.accept()
        else:
            dialog.show_complete(worker.output.name)

    worker.finished.connect(finish)
    worker.start()
    dialog.exec()
    worker.wait()
    if worker.error is not None:
        raise worker.error
    return worker.output
