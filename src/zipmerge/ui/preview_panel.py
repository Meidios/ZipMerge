"""预览面板：文本 / 图片 / 音频 / 视频 / 元信息。

预览按需从源包提取单个条目到会话临时目录，并用 LRU 缓存复用已提取的文件；
磁盘散文件直接读取原路径，不复制。音频波形见 :mod:`waveform`，视频播放见
:mod:`video_preview`。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, QThread, Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QMovie, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import MergeSession, NodeKind, TargetNode
from ..engine import ArchiveError, extract_entries
from ..util.paths import safe_relative_path
from ..util.settings import (
    DEFAULT_CACHE_LIMIT,
    DEFAULT_IMAGE_PIXELS,
    DEFAULT_TEXT_LIMIT,
    DEFAULT_VIDEO_AUTO_LIMIT,
    KEY_PREVIEW_CACHE_LIMIT,
    KEY_PREVIEW_IMAGE_PIXELS,
    KEY_PREVIEW_TEXT_LIMIT,
    KEY_PREVIEW_VIDEO_AUTO_LIMIT,
    Settings,
)
from ..util.tempdir import remove_dir
from . import fmt
from .design import mono_font
from .design.tokens import Palette, palette_for
from .target_model import AUDIO_EXT, IMAGE_EXT, TEXT_EXT, VIDEO_EXT
from .video_preview import VideoPreview, video_backend_available
from .waveform import AudioFileInfo, AudioPreview, backend_available, inspect_audio_file

#: 文本预览大小上限（PLAN 6.8）
TEXT_LIMIT = DEFAULT_TEXT_LIMIT

#: Pillow 解码像素上限，避免超大图撑爆内存
IMAGE_MAX_PIXELS = DEFAULT_IMAGE_PIXELS

#: 预览临时文件的 LRU 条数上限
CACHE_LIMIT = 8

#: 文本编码尝试顺序（PLAN 6.8 要求自动识别 UTF-8 / GBK）
_TEXT_ENCODINGS: tuple[str, ...] = ("utf-8", "gbk")


@dataclass(frozen=True)
class PreviewExtraction:
    """一次后台视频提取的独立候选目录与最终路径。"""

    key: tuple[str, str]
    output_dir: Path
    destination: Path
    cleanup_root: Path


def _setting_int(settings: Settings | None, key: str, default: int) -> int:
    """读取设置里的整数上限；缺设置或值非法时回落到默认值。"""
    if settings is None:
        return default
    try:
        return int(settings.value(key, default, int))
    except (TypeError, ValueError):
        return default


def _format_media_duration(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds) // 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _format_sample_rate(sample_rate: int) -> str:
    if sample_rate <= 0:
        return ""
    khz = sample_rate / 1000.0
    return f"{khz:g}kHz"


def _format_audio_subtitle(info: AudioFileInfo, size: int | None) -> str:
    pieces = [info.format_name or "音频"]
    if info.channels == 1:
        pieces.append("mono")
    elif info.channels == 2:
        pieces.append("stereo")
    elif info.channels > 2:
        pieces.append(f"{info.channels} ch")
    if info.sample_rate > 0:
        pieces.append(_format_sample_rate(info.sample_rate))
    if info.bit_depth > 0:
        pieces.append(f"{info.bit_depth}bit")
    if size is not None:
        pieces.append(fmt.human_size(size))
    return " · ".join(pieces)


class PreviewCache:
    """把源包条目按需提取到临时目录，LRU 淘汰。"""

    def __init__(self, root: Path, limit: int = CACHE_LIMIT) -> None:
        self._root = Path(root)
        self._limit = max(1, limit)
        self._entries: OrderedDict[tuple[str, str], Path] = OrderedDict()

    def local_path(
        self, node: TargetNode, session: MergeSession, *, on_error=None
    ) -> Path | None:
        """返回节点的本地可读路径；无法获取时返回 ``None``。"""
        ref = node.source
        if ref is None:
            return None
        if node.kind is NodeKind.DISK_FILE and ref.disk_path:
            path = Path(ref.disk_path)
            return path if path.is_file() else None
        if ref.source_id is None or ref.entry_path is None:
            return None

        cached = self.cached_path(node)
        if cached is not None:
            return cached

        source = session.sources.get(ref.source_id)
        if source is None:
            return None

        entry_dir = self._entry_dir(ref.source_id)
        destination = entry_dir / safe_relative_path(ref.entry_path)
        try:
            extract_entries(
                source.archive_path,
                [ref.entry_path],
                entry_dir,
                password=source.password,
            )
        except ArchiveError as exc:
            if on_error is not None:
                on_error(exc.message)
            return None
        if not destination.is_file():
            return None

        key = (ref.source_id, ref.entry_path)
        self._entries[key] = destination
        self._entries.move_to_end(key)
        self._evict()
        return destination

    def cached_path(self, node: TargetNode) -> Path | None:
        ref = node.source
        if ref is None or ref.source_id is None or ref.entry_path is None:
            return None
        key = (ref.source_id, ref.entry_path)
        cached = self._entries.get(key)
        if cached is None or not cached.is_file():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return cached

    def prepare_video_extraction(self, node: TargetNode, token: int) -> PreviewExtraction:
        ref = node.source
        if ref is None or ref.source_id is None or ref.entry_path is None:
            raise ValueError("视频节点没有可提取的源包条目")
        cleanup_root = self._root / f"video-load-{token:08d}"
        output_dir = cleanup_root / safe_relative_path(ref.source_id)
        destination = output_dir / safe_relative_path(ref.entry_path)
        return PreviewExtraction(
            key=(ref.source_id, ref.entry_path),
            output_dir=output_dir,
            destination=destination,
            cleanup_root=cleanup_root,
        )

    def commit(self, extraction: PreviewExtraction) -> Path:
        if not extraction.destination.is_file():
            raise ValueError("视频提取完成但目标文件不存在")
        previous = self._entries.pop(extraction.key, None)
        if previous is not None and previous != extraction.destination:
            _remove_file(previous)
        self._entries[extraction.key] = extraction.destination
        self._entries.move_to_end(extraction.key)
        self._evict()
        return extraction.destination

    def discard(self, extraction: PreviewExtraction) -> None:
        remove_dir(extraction.cleanup_root)

    def clear(self) -> None:
        for path in self._entries.values():
            _remove_file(path)
        self._entries.clear()

    def _entry_dir(self, source_id: str) -> Path:
        return self._root / safe_relative_path(source_id)

    def _evict(self) -> None:
        while len(self._entries) > self._limit:
            _key, path = self._entries.popitem(last=False)
            _remove_file(path)


class VideoExtractWorker(QThread):
    """后台提取单个包内视频；信号带请求 token，避免迟到结果覆盖当前预览。"""

    progress = Signal(int, int, str)
    completed = Signal(int, object)
    failed = Signal(int, str)
    cancelled = Signal(int)

    def __init__(
        self,
        token: int,
        archive: Path,
        entry_path: str,
        extraction: PreviewExtraction,
        password: str | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.token = token
        self.archive = Path(archive)
        self.entry_path = entry_path
        self.extraction = extraction
        self.password = password
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            extract_entries(
                self.archive,
                [self.entry_path],
                self.extraction.output_dir,
                password=self.password,
                on_progress=lambda percent, text: self.progress.emit(
                    self.token, percent, text
                ),
                cancel=self._cancel,
            )
        except ArchiveError as exc:
            if self._cancel.is_set():
                self.cancelled.emit(self.token)
            else:
                self.failed.emit(self.token, exc.message)
            return
        if self._cancel.is_set():
            self.cancelled.emit(self.token)
        elif not self.extraction.destination.is_file():
            self.failed.emit(self.token, "视频提取完成但目标文件不存在")
        else:
            self.completed.emit(self.token, self.extraction.destination)


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


class ImageView(QLabel):
    """按比例缩放到可用空间的图片视图。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(120)
        self._source: QPixmap | None = None
        self._movie: QMovie | None = None
        self._movie_size = QSize()

    def set_image(self, pixmap: QPixmap | None) -> None:
        self.stop_animation()
        self._source = pixmap
        self._rescale()

    def set_animation(self, path: Path, width: int, height: int) -> bool:
        self.stop_animation()
        self._source = None
        movie = QMovie(str(path))
        movie.setCacheMode(QMovie.CacheMode.CacheNone)
        if not movie.isValid():
            movie.deleteLater()
            return False
        self._movie = movie
        self._movie_size = QSize(width, height)
        self.setMovie(movie)
        self._rescale()
        movie.start()
        return True

    def stop_animation(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self.setMovie(None)
            self._movie.deleteLater()
            self._movie = None
        self._movie_size = QSize()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._movie is not None and not self._movie_size.isEmpty():
            available = self.size()
            scaled = QSize(self._movie_size)
            scaled.scale(available, Qt.AspectRatioMode.KeepAspectRatio)
            if not scaled.isEmpty():
                self._movie.setScaledSize(scaled)
            return
        if self._source is None or self._source.isNull():
            self.clear()
            return
        available = self.size()
        if available.width() <= 0 or available.height() <= 0:
            return
        scaled = self._source.scaled(
            available,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        super().setPixmap(scaled)


class PreviewPanel(QWidget):
    """展示当前选中节点的内容预览与元信息。"""

    #: 文本 / 图片之外的节点请求用系统默认程序打开
    open_external_requested = Signal(object)

    _PAGE_EMPTY = 0
    _PAGE_TEXT = 1
    _PAGE_IMAGE = 2
    _PAGE_INFO = 3
    _PAGE_MESSAGE = 4
    _PAGE_AUDIO = 5
    _PAGE_VIDEO = 6

    def __init__(
        self, session: MergeSession, settings: Settings | None = None, parent=None
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("previewPanel")
        self.setProperty("role", "panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._text_limit = _setting_int(settings, KEY_PREVIEW_TEXT_LIMIT, DEFAULT_TEXT_LIMIT)
        self._image_pixels = _setting_int(settings, KEY_PREVIEW_IMAGE_PIXELS, DEFAULT_IMAGE_PIXELS)
        self._cache_limit = _setting_int(settings, KEY_PREVIEW_CACHE_LIMIT, DEFAULT_CACHE_LIMIT)
        self._video_auto_limit = _setting_int(
            settings, KEY_PREVIEW_VIDEO_AUTO_LIMIT, DEFAULT_VIDEO_AUTO_LIMIT
        )
        self._palette: Palette = palette_for("dark")
        self._cache = PreviewCache(session.temp_dir / "preview")
        self._node: TargetNode | None = None
        self._audio_info: AudioFileInfo | None = None
        self._video_token = 0
        self._video_jobs: dict[int, tuple[VideoExtractWorker, PreviewExtraction]] = {}

        self._title = QLabel("—")
        self._title.setProperty("variant", "subtitle")
        self._title.setWordWrap(True)
        self._subtitle = QLabel("选中一个文件即可预览")
        self._subtitle.setProperty("variant", "secondary")

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_empty_page())
        self._stack.addWidget(self._build_text_page())
        self._stack.addWidget(self._build_image_page())
        self._stack.addWidget(self._build_info_page())
        self._stack.addWidget(self._build_message_page())
        self._stack.addWidget(self._build_audio_page())
        self._stack.addWidget(self._build_video_page())

        # 普通预览舞台透明落在面板表面；视频 / 波形等内容控件自行绘制暗画布。
        self._stage = QFrame()
        self._stage.setProperty("role", "preview")
        stage_layout = QVBoxLayout(self._stage)
        stage_layout.setContentsMargins(6, 6, 6, 6)
        stage_layout.setSpacing(0)
        stage_layout.addWidget(self._stack)

        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(0)
        header.addWidget(self._title)
        header.addWidget(self._subtitle)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self._stage, 1)

        self.set_palette(self._palette)
        self.clear()

        # 空格播放：焦点在本面板（含子控件）时生效，不抢文本输入控件的空格
        self._shortcut_space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._shortcut_space.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut_space.activated.connect(self.toggle_playback)

    # ---- 对外 -------------------------------------------------------
    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._text_view.setStyleSheet(
            f"background: transparent; color: {palette.text_primary};"
        )
        self._message.setStyleSheet(f"color: {palette.text_secondary};")
        self._audio.set_palette(palette)
        self._video.set_palette(palette)

    def reload_settings(self, settings: Settings) -> None:
        """设置对话框确认后刷新已打开标签页的预览上限。"""
        self._text_limit = _setting_int(settings, KEY_PREVIEW_TEXT_LIMIT, DEFAULT_TEXT_LIMIT)
        self._image_pixels = _setting_int(
            settings, KEY_PREVIEW_IMAGE_PIXELS, DEFAULT_IMAGE_PIXELS
        )
        self._video_auto_limit = _setting_int(
            settings, KEY_PREVIEW_VIDEO_AUTO_LIMIT, DEFAULT_VIDEO_AUTO_LIMIT
        )

    def show_node(self, node: TargetNode | None) -> None:
        self._begin_request()
        self._node = node
        if node is None:
            self._title.setText("—")
            self._subtitle.setText("选中一个文件即可预览")
            self._stack.setCurrentIndex(self._PAGE_EMPTY)
            return
        if node.is_dir:
            self._show_info(node, "文件夹：导出时会保留其目录结构")
            return
        suffix = Path(node.name).suffix.lower()
        if suffix in IMAGE_EXT:
            self._show_image(node)
        elif suffix in VIDEO_EXT:
            self._show_video(node)
        elif suffix in AUDIO_EXT:
            self._show_audio(node)
        elif suffix in TEXT_EXT or not suffix:
            self._show_text(node)
        else:
            self._show_info(node, "该类型不支持内嵌预览")

    def clear(self) -> None:
        self._begin_request()
        self._node = None
        self._title.setText("—")
        self._subtitle.setText("选中一个文件即可预览")
        self._stack.setCurrentIndex(self._PAGE_EMPTY)

    def shutdown(self) -> None:
        """关闭标签前停止媒体与后台提取，保证会话目录可安全清理。"""
        self._video_token += 1
        for worker, _extraction in self._video_jobs.values():
            worker.cancel()
        for worker, extraction in list(self._video_jobs.values()):
            worker.wait()
            self._cache.discard(extraction)
            worker.deleteLater()
        self._video_jobs.clear()
        self._audio.clear()
        self._video.clear()
        self._image_view.stop_animation()
        self._cache.clear()

    def toggle_playback(self) -> None:
        """空格快捷键：当前预览音频或视频时播放 / 暂停。"""
        if self._stack.currentIndex() == self._PAGE_AUDIO:
            self._audio.toggle_playback()
        elif self._stack.currentIndex() == self._PAGE_VIDEO:
            self._video.toggle_playback()

    # ---- 页面 -------------------------------------------------------
    def _build_empty_page(self) -> QWidget:
        hint = QLabel("选中目标结构里的文件即可在这里预览内容")
        hint.setProperty("variant", "disabled")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _build_text_page(self) -> QWidget:
        self._text_view = QPlainTextEdit()
        self._text_view.setReadOnly(True)
        self._text_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text_view.setFont(mono_font(12))
        return self._text_view

    def _build_image_page(self) -> QWidget:
        self._image_view = ImageView()
        page = QFrame()
        page.setProperty("role", "canvas")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._image_view)
        return page

    def _build_info_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self._info_form = QFormLayout()
        self._info_form.setContentsMargins(4, 4, 4, 4)
        self._info_form.setSpacing(6)
        self._info_note = QLabel("")
        self._info_note.setProperty("variant", "secondary")
        self._info_note.setWordWrap(True)
        outer.addLayout(self._info_form)
        outer.addWidget(self._info_note)
        outer.addStretch(1)
        return page

    def _build_message_page(self) -> QWidget:
        self._message = QLabel("")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        layout.addWidget(self._message)
        layout.addStretch(1)
        return page

    def _build_audio_page(self) -> QWidget:
        self._audio = AudioPreview(cache_limit=self._cache_limit)
        self._audio.details_changed.connect(self._on_audio_details)
        return self._audio

    def _build_video_page(self) -> QWidget:
        self._video = VideoPreview()
        self._video.load_requested.connect(self._load_pending_video)
        self._video.cancel_requested.connect(self._cancel_current_video_load)
        self._video.open_external_requested.connect(self._request_open_external)
        self._video.details_changed.connect(self._on_video_details)
        self._video.error_occurred.connect(self._on_video_playback_error)
        return self._video

    # ---- 各类型渲染 -------------------------------------------------
    def _show_text(self, node: TargetNode) -> None:
        path = self._cache.local_path(node, self.session, on_error=self._show_error)
        if path is None:
            self._show_error("无法从源包中读取该文件")
            return
        try:
            size = path.stat().st_size
        except OSError as exc:
            self._show_error(str(exc))
            return
        if size > self._text_limit:
            self._show_info(
                node, f"文件超过文本预览上限（{fmt.human_size(self._text_limit)}），不在内嵌预览"
            )
            return
        data = path.read_bytes()
        text, encoding = _decode_text(data)
        self._title.setText(node.name)
        self._subtitle.setText(f"文本 · {encoding.upper()} · {fmt.human_size(size)}")
        self._text_view.setPlainText(text)
        self._stack.setCurrentIndex(self._PAGE_TEXT)

    def _show_image(self, node: TargetNode) -> None:
        path = self._cache.local_path(node, self.session, on_error=self._show_error)
        if path is None:
            self._show_error("无法从源包中读取该图片")
            return
        info, error = inspect_image(path, self._image_pixels)
        if info is None:
            self._show_error(error or "图片无法解码")
            return
        width, height, animated = info
        if Path(node.name).suffix.lower() == ".gif" and animated:
            if not self._image_view.set_animation(path, width, height):
                self._show_error("GIF 动图无法解码")
                return
            self._title.setText(node.name)
            self._subtitle.setText(
                f"GIF 动图 · {width}×{height} · {fmt.human_size(path.stat().st_size)}"
            )
            self._stack.setCurrentIndex(self._PAGE_IMAGE)
            return
        pixmap, error = load_pixmap(path, self._image_pixels)
        if pixmap is None:
            self._show_error(error or "图片无法解码")
            return
        self._title.setText(node.name)
        self._subtitle.setText(f"图片 · {pixmap.width()}×{pixmap.height()} · {fmt.human_size(path.stat().st_size)}")
        self._image_view.set_image(pixmap)
        self._stack.setCurrentIndex(self._PAGE_IMAGE)

    def _show_audio(self, node: TargetNode) -> None:
        path = self._cache.local_path(node, self.session, on_error=self._show_error)
        if path is None:
            self._show_error("无法从源包中读取该音频")
            return
        if not backend_available():
            self._show_info(node, "未检测到 QtMultimedia 解码后端，无法生成波形与播放")
            return
        try:
            size = path.stat().st_size
        except OSError as exc:
            self._show_error(str(exc))
            return
        self._audio_info = inspect_audio_file(path)
        self._title.setText(node.name)
        self._subtitle.setText(_format_audio_subtitle(self._audio_info, size))
        self._stack.setCurrentIndex(self._PAGE_AUDIO)
        self._audio.show_file(path, node.name)

    def _show_video(self, node: TargetNode) -> None:
        self._title.setText(node.name)
        size = _entry_size(node, self.session)
        self._subtitle.setText(
            "视频" + (f" · {fmt.human_size(size)}" if size is not None else "")
        )
        self._stack.setCurrentIndex(self._PAGE_VIDEO)
        if not video_backend_available():
            self._video.show_error("未检测到 QtMultimedia 视频解码后端")
            return

        ref = node.source
        if node.kind is NodeKind.DISK_FILE and ref is not None and ref.disk_path:
            path = Path(ref.disk_path)
            if path.is_file():
                self._video.show_file(path)
            else:
                self._video.show_error(f"文件不存在：{path}")
            return

        cached = self._cache.cached_path(node)
        if cached is not None:
            self._video.show_file(cached)
            return
        if size is not None and size > self._video_auto_limit:
            self._video.show_gate(fmt.human_size(size))
            return
        self._start_video_extract(node)

    def _load_pending_video(self) -> None:
        node = self._node
        if node is None or Path(node.name).suffix.lower() not in VIDEO_EXT:
            return
        ref = node.source
        if node.kind is NodeKind.DISK_FILE and ref is not None and ref.disk_path:
            path = Path(ref.disk_path)
            if path.is_file():
                self._video.show_file(path)
            else:
                self._video.show_error(f"文件不存在：{path}")
            return
        cached = self._cache.cached_path(node)
        if cached is not None:
            self._video.show_file(cached)
            return
        self._start_video_extract(node)

    def _start_video_extract(self, node: TargetNode) -> None:
        ref = node.source
        if ref is None or ref.source_id is None or ref.entry_path is None:
            self._video.show_error("该视频没有可读取的源包条目")
            return
        source = self.session.sources.get(ref.source_id)
        if source is None:
            self._video.show_error("源包已卸载，无法读取该视频")
            return
        token = self._video_token
        if token in self._video_jobs:
            return
        try:
            extraction = self._cache.prepare_video_extraction(node, token)
        except ValueError as exc:
            self._video.show_error(str(exc))
            return
        worker = VideoExtractWorker(
            token,
            source.archive_path,
            ref.entry_path,
            extraction,
            source.password,
            self,
        )
        worker.progress.connect(self._on_video_extract_progress)
        worker.completed.connect(self._on_video_extracted)
        worker.failed.connect(self._on_video_extract_failed)
        worker.cancelled.connect(self._on_video_extract_cancelled)
        worker.finished.connect(worker.deleteLater)
        self._video_jobs[token] = (worker, extraction)
        self._video.show_loading()
        worker.start()

    def _cancel_current_video_load(self) -> None:
        job = self._video_jobs.get(self._video_token)
        if job is not None:
            job[0].cancel()
            self._video.show_message("正在取消视频加载…", external=False)

    def _on_video_extract_progress(self, token: int, percent: int, _text: str) -> None:
        if token == self._video_token:
            self._video.update_loading(percent, f"正在提取视频… {percent}%")

    def _on_video_extracted(self, token: int, _path: Path) -> None:
        job = self._video_jobs.pop(token, None)
        if job is None:
            return
        _worker, extraction = job
        if token != self._video_token or self._node is None:
            self._cache.discard(extraction)
            return
        try:
            path = self._cache.commit(extraction)
        except ValueError as exc:
            self._video.show_error(str(exc))
            return
        self._video.show_file(path)

    def _on_video_extract_failed(self, token: int, message: str) -> None:
        job = self._video_jobs.pop(token, None)
        if job is None:
            return
        _worker, extraction = job
        self._cache.discard(extraction)
        if token == self._video_token:
            self._subtitle.setText("视频 · 预览失败")
            self._video.show_error(message or "无法从源包中读取该视频")

    def _on_video_extract_cancelled(self, token: int) -> None:
        job = self._video_jobs.pop(token, None)
        if job is None:
            return
        _worker, extraction = job
        self._cache.discard(extraction)
        if token == self._video_token:
            self._video.show_message("已取消视频加载", load=True)

    def _on_video_details(self, width: int, height: int, duration: int) -> None:
        node = self._node
        if node is None or self._stack.currentIndex() != self._PAGE_VIDEO:
            return
        pieces = ["视频"]
        if width > 0 and height > 0:
            pieces.append(f"{width}×{height}")
        if duration > 0:
            pieces.append(_format_media_duration(duration))
        size = _entry_size(node, self.session)
        if size is not None:
            pieces.append(fmt.human_size(size))
        self._subtitle.setText(" · ".join(pieces))

    def _on_video_playback_error(self, _message: str) -> None:
        self._subtitle.setText("视频 · 预览失败")

    def _on_audio_details(self, channels: int, sample_rate: int) -> None:
        node = self._node
        info = self._audio_info
        if (
            node is None
            or info is None
            or self._stack.currentIndex() != self._PAGE_AUDIO
        ):
            return
        self._audio_info = AudioFileInfo(
            format_name=info.format_name,
            channels=channels if channels > 0 else info.channels,
            sample_rate=sample_rate if sample_rate > 0 else info.sample_rate,
            bit_depth=info.bit_depth,
        )
        self._subtitle.setText(
            _format_audio_subtitle(self._audio_info, _entry_size(node, self.session))
        )

    def _request_open_external(self) -> None:
        if self._node is not None:
            self.open_external_requested.emit(self._node)

    def _begin_request(self) -> None:
        self._video_token += 1
        for worker, _extraction in self._video_jobs.values():
            worker.cancel()
        self._audio_info = None
        self._audio.clear()
        self._video.clear()
        self._image_view.stop_animation()

    def _show_info(self, node: TargetNode, note: str) -> None:
        self._title.setText(node.name)
        if node.is_dir:
            self._subtitle.setText("文件夹")
        else:
            self._subtitle.setText(Path(node.name).suffix.upper().lstrip(".") or "文件")

        while self._info_form.rowCount():
            self._info_form.removeRow(0)
        for label, value in _node_rows(node, self.session):
            key = QLabel(label)
            key.setProperty("variant", "secondary")
            value_label = QLabel(value)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._info_form.addRow(key, value_label)
        self._info_note.setText(note)
        self._stack.setCurrentIndex(self._PAGE_INFO)

    def _show_error(self, message: str) -> None:
        self._title.setText(self._node.name if self._node else "—")
        self._subtitle.setText("预览失败")
        self._message.setText(message)
        self._stack.setCurrentIndex(self._PAGE_MESSAGE)


def _decode_text(data: bytes) -> tuple[str, str]:
    """按 UTF-8 → GBK 顺序解码；都失败时用 UTF-8 替换字符兜底。"""
    for encoding in _TEXT_ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8"


def load_pixmap(path: Path, max_pixels: int = IMAGE_MAX_PIXELS) -> tuple[QPixmap | None, str]:
    """用 Pillow 解码图片；限制解码尺寸，失败返回原因。"""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow 属运行期依赖
        return None, "未安装 Pillow，无法预览图片"
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            if width * height > max_pixels:
                return None, f"图片尺寸过大（{width}×{height}），不在内嵌预览"
            rgba = image.convert("RGBA")
            payload = rgba.tobytes("raw", "RGBA")
    except Exception as exc:  # Pillow 抛出的异常类型很多，统一兜底
        return None, f"图片解码失败：{exc}"
    buffer = QImage(payload, width, height, width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(buffer.copy()), ""


def inspect_image(
    path: Path, max_pixels: int = IMAGE_MAX_PIXELS
) -> tuple[tuple[int, int, bool] | None, str]:
    """只读取图片元数据，供 GIF 播放前做尺寸和动画帧检查。"""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return None, "未安装 Pillow，无法预览图片"
    try:
        with Image.open(path) as image:
            width, height = image.size
            if width * height > max_pixels:
                return None, f"图片尺寸过大（{width}×{height}），不在内嵌预览"
            animated = bool(getattr(image, "is_animated", False) and getattr(image, "n_frames", 1) > 1)
            return (width, height, animated), ""
    except Exception as exc:
        return None, f"图片解码失败：{exc}"


def _node_rows(node: TargetNode, session: MergeSession) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [("名称", node.name), ("类型", _kind_label(node))]
    ref = node.source
    if ref is not None and ref.source_id:
        source = session.sources.get(ref.source_id)
        if source is not None:
            rows.append(("来源包", source.archive_path.name))
        if ref.entry_path:
            rows.append(("包内路径", ref.entry_path))
    if ref is not None and ref.disk_path:
        rows.append(("磁盘路径", ref.disk_path))
    if node.is_dir:
        rows.append(("含文件", fmt.count_label(node.total_file_count(), "个文件")))
    else:
        size = _entry_size(node, session)
        if size is not None:
            rows.append(("大小", f"{fmt.human_size(size)}（{size} 字节）"))
    return rows


def _kind_label(node: TargetNode) -> str:
    if node.kind is NodeKind.VIRTUAL_DIR:
        return "新建文件夹"
    if node.kind is NodeKind.ARCHIVE_ENTRY:
        return "源包条目"
    if node.kind is NodeKind.DISK_FILE:
        return "磁盘文件"
    if node.kind is NodeKind.DISK_DIR:
        return "磁盘文件夹"
    return str(node.kind)


def _entry_size(node: TargetNode, session: MergeSession) -> int | None:
    ref = node.source
    if ref is not None and ref.source_id and ref.entry_path:
        source = session.sources.get(ref.source_id)
        if source is not None:
            entry = source.entry(ref.entry_path)
            if entry is not None:
                return entry.size
    if ref is not None and ref.disk_path:
        try:
            return Path(ref.disk_path).stat().st_size
        except OSError:
            return None
    return None
