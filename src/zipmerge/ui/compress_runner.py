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

    def __init__(self, sources: list[Path]) -> None:
        super().__init__()
        self.sources = sources
        self.cancel = threading.Event()
        self.output: Path | None = None
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            self.output = compress_here(
                self.sources, cancel=self.cancel, on_progress=self.report_progress,
            )
        except ArchiveCancelled:
            pass
        except Exception as exc:
            self.error = exc

    def report_progress(self, percent: int, _raw: str) -> None:
        self.progress.emit(min(percent, 99), "")


def run_compress_dialog(paths: list[str]) -> Path | None:
    sources = [Path(path) for path in paths]
    message = sources[0].name if len(sources) == 1 else tr("已选 {0} 个项目", len(sources))
    dialog = ProgressDialog(title=tr("正在压缩"), message=message)
    worker = CompressWorker(sources)
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
