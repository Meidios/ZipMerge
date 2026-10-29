"""视频预览控件：画面、基础播放控制、全屏与加载状态。"""

from __future__ import annotations

from pathlib import Path

# 必须先导入 waveform，让它在任何 QtMultimedia 模块加载前预加载 ffmpeg 共享库。
from .waveform import backend_available  # noqa: F401

from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaFormat, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .design import icon as design_icon
from .design import mono_font
from .design.tokens import Palette, palette_for


def video_backend_available() -> bool:
    """QtMultimedia 后端是否报告至少一种可解码视频编码。"""
    try:
        media_format = QMediaFormat()
        return bool(
            media_format.supportedVideoCodecs(QMediaFormat.ConversionMode.Decode)
        )
    except Exception:  # noqa: BLE001 - 不同 Qt 后端的能力查询可能直接失败
        return False


def _format_time(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds) // 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class VideoPreview(QWidget):
    """用 QVideoWidget 播放本地视频；选中后可直接按空格开始播放。"""

    load_requested = Signal()
    cancel_requested = Signal()
    open_external_requested = Signal()
    details_changed = Signal(int, int, int)  # width, height, duration_ms
    error_occurred = Signal(str)

    _PAGE_STATUS = 0
    _PAGE_VIDEO = 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._palette: Palette = palette_for("dark")
        self._source: Path | None = None
        self._frame_size = (0, 0)

        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._output.setVolume(0.8)
        self._player.setAudioOutput(self._output)

        self._video = QVideoWidget()
        self._video.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self._video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._player.setVideoOutput(self._video)

        self._play_button = QToolButton()
        self._play_button.setProperty("variant", "ghost")
        self._play_button.setToolTip("播放 / 暂停")
        self._play_button.clicked.connect(self.toggle_playback)

        self._position_slider = QSlider(Qt.Orientation.Horizontal)
        self._position_slider.setRange(0, 0)
        self._position_slider.sliderMoved.connect(self._player.setPosition)

        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setProperty("variant", "secondary")
        self._time_label.setProperty("role", "mono")
        self._time_label.setFont(mono_font(11))

        self._mute_button = QToolButton()
        self._mute_button.setProperty("variant", "ghost")
        self._mute_button.setToolTip("静音")
        self._mute_button.clicked.connect(self.toggle_muted)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(80)
        self._volume_slider.setToolTip("音量")
        self._volume_slider.valueChanged.connect(
            lambda value: self._output.setVolume(value / 100.0)
        )

        self._fullscreen_button = QToolButton()
        self._fullscreen_button.setProperty("variant", "ghost")
        self._fullscreen_button.setToolTip("全屏")
        self._fullscreen_button.clicked.connect(self.toggle_fullscreen)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addWidget(self._play_button)
        controls.addWidget(self._position_slider, 1)
        controls.addWidget(self._time_label)
        controls.addWidget(self._mute_button)
        controls.addWidget(self._volume_slider)
        controls.addWidget(self._fullscreen_button)

        video_page = QWidget()
        video_layout = QVBoxLayout(video_page)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(6)
        video_layout.addWidget(self._video, 1)
        video_layout.addLayout(controls)

        self._status_label = QLabel("选择视频即可预览")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setProperty("variant", "secondary")
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(True)
        self._progress.hide()

        self._load_button = QPushButton("加载视频预览")
        self._load_button.setProperty("variant", "primary")
        self._load_button.clicked.connect(self.load_requested)
        self._load_button.hide()
        self._cancel_button = QPushButton("取消")
        self._cancel_button.clicked.connect(self.cancel_requested)
        self._cancel_button.hide()
        self._open_button = QPushButton("用系统默认程序打开")
        self._open_button.clicked.connect(self.open_external_requested)
        self._open_button.hide()

        status_buttons = QHBoxLayout()
        status_buttons.addStretch(1)
        status_buttons.addWidget(self._load_button)
        status_buttons.addWidget(self._cancel_button)
        status_buttons.addWidget(self._open_button)
        status_buttons.addStretch(1)

        status_page = QWidget()
        status_layout = QVBoxLayout(status_page)
        status_layout.addStretch(1)
        status_layout.addWidget(self._status_label)
        status_layout.addWidget(self._progress)
        status_layout.addLayout(status_buttons)
        status_layout.addStretch(1)

        self._stack = QStackedWidget()
        self._stack.addWidget(status_page)
        self._stack.addWidget(video_page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.errorOccurred.connect(self._on_player_error)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        # FFmpeg 可能从解码线程发出 frame 信号。显式排队回控件所属的 UI 线程，
        # 避免在解码回调栈里读取 / 控制 QMediaPlayer 造成驱动相关死锁。
        self._video.videoSink().videoFrameChanged.connect(
            self._on_video_frame,
            Qt.ConnectionType.QueuedConnection,
        )
        self._video.fullScreenChanged.connect(self._on_fullscreen_changed)

        self._escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self._video)
        self._escape.setContext(Qt.ShortcutContext.WindowShortcut)
        self._escape.activated.connect(self.exit_fullscreen)

        self.set_palette(self._palette)
        self.clear()

    @property
    def source(self) -> Path | None:
        return self._source

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._video.setStyleSheet(f"background-color: {palette.canvas};")
        self._update_icons()

    def show_gate(self, size_text: str) -> None:
        self._reset_player()
        self._status_label.setText(
            f"视频超过自动提取上限，点击后会先解压到会话临时目录。\n大小：{size_text}"
        )
        self._show_status(load=True, external=True)

    def show_loading(self, percent: int = 0, text: str = "正在提取视频…") -> None:
        self._reset_player()
        self._status_label.setText(text or "正在提取视频…")
        self._progress.setValue(max(0, min(100, percent)))
        self._show_status(progress=True, cancel=True)

    def update_loading(self, percent: int, text: str = "") -> None:
        if self._progress.isHidden():
            return
        self._progress.setValue(max(0, min(100, percent)))
        if text:
            self._status_label.setText(text)

    def show_error(self, message: str) -> None:
        self._reset_player()
        self._status_label.setText(message or "无法播放该视频")
        self._status_label.setProperty("variant", "danger")
        self._repolish(self._status_label)
        self._show_status(external=True)

    def show_message(
        self, message: str, *, external: bool = True, load: bool = False
    ) -> None:
        self._reset_player()
        self._status_label.setText(message)
        self._status_label.setProperty("variant", "secondary")
        self._repolish(self._status_label)
        self._show_status(external=external, load=load)

    def show_file(self, path: str | Path) -> None:
        self._reset_player()
        self._source = Path(path).resolve()
        self._stack.setCurrentIndex(self._PAGE_VIDEO)
        self._set_controls_enabled(True)
        self._player.setSource(QUrl.fromLocalFile(str(self._source)))
        self._update_icons()

    def clear(self) -> None:
        self._reset_player()
        self._status_label.setText("选择视频即可预览")
        self._status_label.setProperty("variant", "secondary")
        self._repolish(self._status_label)
        self._show_status()

    def toggle_playback(self) -> None:
        if self._source is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            return
        if self._player.duration() > 0 and self._player.position() >= self._player.duration():
            self._player.setPosition(0)
        self._player.play()

    def toggle_muted(self) -> None:
        self._output.setMuted(not self._output.isMuted())
        self._update_icons()

    def toggle_fullscreen(self) -> None:
        if self._source is not None:
            self._video.setFullScreen(not self._video.isFullScreen())

    def exit_fullscreen(self) -> None:
        if self._video.isFullScreen():
            self._video.setFullScreen(False)

    def _show_status(
        self,
        *,
        load: bool = False,
        progress: bool = False,
        cancel: bool = False,
        external: bool = False,
    ) -> None:
        if self._status_label.property("variant") == "danger" and (load or progress):
            self._status_label.setProperty("variant", "secondary")
            self._repolish(self._status_label)
        self._load_button.setVisible(load)
        self._progress.setVisible(progress)
        self._cancel_button.setVisible(cancel)
        self._open_button.setVisible(external)
        self._stack.setCurrentIndex(self._PAGE_STATUS)

    def _reset_player(self) -> None:
        self.exit_fullscreen()
        # 先清引用，再让 Qt 清 source；部分后端会在 setSource(QUrl()) 内同步发错误信号。
        self._source = None
        self._player.stop()
        self._player.setSource(QUrl())
        self._video.videoSink().setVideoFrame(QVideoFrame())
        self._frame_size = (0, 0)
        self._position_slider.setRange(0, 0)
        self._position_slider.setValue(0)
        self._time_label.setText("00:00 / 00:00")
        self._set_controls_enabled(False)
        self._update_icons()

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._play_button,
            self._position_slider,
            self._mute_button,
            self._volume_slider,
            self._fullscreen_button,
        ):
            widget.setEnabled(enabled)

    @Slot(QVideoFrame)
    def _on_video_frame(self, frame: QVideoFrame) -> None:
        if not frame.isValid() or self._source is None:
            return
        self._frame_size = (frame.width(), frame.height())
        self.details_changed.emit(
            self._frame_size[0], self._frame_size[1], self._player.duration()
        )

    def _on_position_changed(self, position: int) -> None:
        if not self._position_slider.isSliderDown():
            self._position_slider.setValue(position)
        self._time_label.setText(
            f"{_format_time(position)} / {_format_time(self._player.duration())}"
        )

    def _on_duration_changed(self, duration: int) -> None:
        self._position_slider.setRange(0, max(0, duration))
        self._on_position_changed(self._player.position())
        self.details_changed.emit(self._frame_size[0], self._frame_size[1], duration)

    def _on_state_changed(self, _state) -> None:
        self._update_icons()

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if (
            status == QMediaPlayer.MediaStatus.EndOfMedia
            and self._source is not None
            and self._frame_size == (0, 0)
        ):
            self._fail_playback("文件中没有可显示的视频画面")

    def _on_player_error(self, _error, message: str) -> None:
        if self._source is not None:
            self._fail_playback(message or "当前视频编码不受支持或文件已损坏")

    def _fail_playback(self, message: str) -> None:
        self.show_error(message)
        self.error_occurred.emit(message)

    def _on_fullscreen_changed(self, fullscreen: bool) -> None:
        self._fullscreen_button.setToolTip("退出全屏" if fullscreen else "全屏")
        self._update_icons()

    def _update_icons(self) -> None:
        color = self._palette.text_primary
        playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self._play_button.setIcon(design_icon("pause" if playing else "play", 16, color))
        muted = self._output.isMuted() or self._output.volume() <= 0
        self._mute_button.setIcon(design_icon("speaker_off" if muted else "speaker_2", 16, color))
        fullscreen = self._video.isFullScreen()
        self._fullscreen_button.setIcon(
            design_icon("full_screen_minimize" if fullscreen else "full_screen_maximize", 16, color)
        )

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
