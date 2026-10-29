"""ZipMerge 界面层（Qt Widgets）。

对外公开主窗口、各面板与导出执行入口，方便入口脚本与测试引用。
设计系统（令牌 / QSS / 图标 / 主题）见 :mod:`zipmerge.ui.design`。
"""

from __future__ import annotations

from .export_bar import ExportBar
from .export_runner import (
    ConflictDialog,
    ExportWorker,
    ProgressDialog,
    prepare_options,
    run_export_dialog,
)
from .extraction_runner import (
    ExtractionProgressDialog,
    ExtractionResult,
    ExtractionWorker,
    run_extraction_dialog,
)
from .main_window import LAYOUT_BOTTOM, LAYOUT_COLUMNS, MainWindow, WorkspaceTab
from .preview_panel import PreviewPanel
from .settings_dialog import SettingsDialog
from .source_panel import SourcePanel
from .target_panel import TargetPanel
from .temporary_archive import TemporaryArchiveCard
from .video_preview import VideoPreview

__all__ = [
    "ConflictDialog",
    "ExportBar",
    "ExportWorker",
    "ExtractionProgressDialog",
    "ExtractionResult",
    "ExtractionWorker",
    "LAYOUT_BOTTOM",
    "LAYOUT_COLUMNS",
    "MainWindow",
    "ProgressDialog",
    "PreviewPanel",
    "SettingsDialog",
    "SourcePanel",
    "TargetPanel",
    "TemporaryArchiveCard",
    "VideoPreview",
    "WorkspaceTab",
    "prepare_options",
    "run_export_dialog",
    "run_extraction_dialog",
]
