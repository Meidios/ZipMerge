"""音频波形与播放（PLAN 6.8）。

波形用 QAudioDecoder 解出 PCM，再按每个声道分别计算固定桶宽的 min/max 峰值；播放交给
QMediaPlayer，播放头随 positionChanged 实时跟随。峰值按文件内容指纹缓存。mono 绘制一行，
stereo 绘制 L/R 两行。长音频的每声道金字塔逐层两两合并，绘制时按控件宽度挑一层，
因此任何长度都不会退化成逐样本绘制。

QtMultimedia 的 ffmpeg 后端在部分环境下不会自行加载：Qt 的插件加载器不带
LOAD_WITH_ALTERED_SEARCH_PATH，ffmpeg 插件依赖的 av*.dll 与插件不在同一目录时找不到，
Qt 会静默退化成「无后端」（QAudioDecoder 不产生任何缓冲）。因此这里在导入
QtMultimedia 之前显式补齐 DLL 搜索目录并预加载这些库。
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import wave
from array import array
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

#: 最细一层波形桶包含的每声道音频帧数
BUCKET_SAMPLES = 256

#: 最细一层之上继续合并到的桶数上限
MAX_LEVEL_BUCKETS = 2048

#: 峰值缓存条数（LRU）
PEAK_CACHE_LIMIT = 6

#: 内容指纹读取的头 / 尾字节数
_FINGERPRINT_BYTES = 64 * 1024

#: 需要预加载的 ffmpeg 共享库（按名字模式匹配，跨 PySide6 版本可用）
_FFMPEG_PATTERNS: tuple[str, ...] = (
    "avcodec-*.dll",
    "avformat-*.dll",
    "avutil-*.dll",
    "swresample-*.dll",
    "swscale-*.dll",
)


def preload_multimedia_libs() -> int:
    """预加载 ffmpeg 共享库，返回成功加载的个数。

    必须在导入 PySide6.QtMultimedia **之前**调用：Qt 一旦尝试加载插件失败就不会重试。
    """
    if sys.platform != "win32":  # pragma: no cover - 本产品仅 Windows
        return 0
    try:
        import PySide6
    except ImportError:  # pragma: no cover - PySide6 属运行期依赖
        return 0
    root = Path(PySide6.__file__).resolve().parent
    try:
        os.add_dll_directory(str(root))
    except (AttributeError, OSError):  # pragma: no cover - 老 Python / 无权限
        pass
    loaded = 0
    for pattern in _FFMPEG_PATTERNS:
        for library in sorted(root.glob(pattern)):
            try:
                ctypes.WinDLL(str(library))
            except OSError:
                continue
            loaded += 1
    return loaded


#: 成功预加载的 ffmpeg 库个数；为 0 表示本机没有可用的多媒体后端
PRELOADED_LIBS = preload_multimedia_libs()

from PySide6.QtCore import QObject, Qt, QUrl, Signal  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPen  # noqa: E402
from PySide6.QtMultimedia import (  # noqa: E402
    QAudioDecoder,
    QAudioFormat,
    QAudioOutput,
    QMediaFormat,
    QMediaPlayer,
)
from PySide6.QtWidgets import (  # noqa: E402
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .design import glyph as design_glyph  # noqa: E402
from .design import icon as design_icon  # noqa: E402
from .design import mono_font, qcolor  # noqa: E402
from .design.tokens import Palette, palette_for  # noqa: E402


def _format_spec(sample_format) -> tuple[str, float, float]:
    """把 Qt 采样格式映射为 (array 类型码, 归一化除数, 零点)。"""
    formats = QAudioFormat.SampleFormat
    if sample_format == formats.UInt8:
        return "B", 128.0, 128.0
    if sample_format == formats.Int16:
        return "h", 32768.0, 0.0
    if sample_format == formats.Int32:
        return "i", 2147483648.0, 0.0
    if sample_format == formats.Float:
        return "f", 1.0, 0.0
    return "h", 32768.0, 0.0


@dataclass(frozen=True)
class AudioFileInfo:
    """预览信息栏使用的源音频格式信息；0 表示当前无法确定。"""

    format_name: str
    channels: int = 0
    sample_rate: int = 0
    bit_depth: int = 0


def inspect_audio_file(path: Path) -> AudioFileInfo:
    """轻量读取源音频信息；WAV 位深来自文件头，不使用解码后的 PCM 格式冒充。"""
    path = Path(path)
    format_name = path.suffix.upper().lstrip(".") or "音频"
    if path.suffix.lower() not in {".wav", ".wave"}:
        return AudioFileInfo(format_name)
    try:
        with wave.open(str(path), "rb") as source:
            return AudioFileInfo(
                format_name="WAV",
                channels=source.getnchannels(),
                sample_rate=source.getframerate(),
                bit_depth=source.getsampwidth() * 8,
            )
    except (EOFError, OSError, wave.Error):
        return AudioFileInfo("WAV")


def fingerprint(path: Path) -> str:
    """按「大小 + 头尾各 64 KiB」计算内容指纹，避免读取整个文件；读不到时返回空串。"""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    digest = hashlib.sha1(str(size).encode("ascii"))
    try:
        with path.open("rb") as handle:
            digest.update(handle.read(_FINGERPRINT_BYTES))
            if size > _FINGERPRINT_BYTES:
                handle.seek(max(_FINGERPRINT_BYTES, size - _FINGERPRINT_BYTES))
                digest.update(handle.read(_FINGERPRINT_BYTES))
    except OSError:
        return ""
    return digest.hexdigest()


def build_levels(mins: array, maxs: array) -> list[tuple[array, array]]:
    """把最细一层按两两合并展开成 min/max 金字塔。"""
    levels: list[tuple[array, array]] = [(mins, maxs)]
    while len(levels[-1][0]) > MAX_LEVEL_BUCKETS:
        previous_mins, previous_maxs = levels[-1]
        if len(previous_mins) < 2:
            break
        levels.append(
            (
                array("f", map(min, previous_mins[0::2], previous_mins[1::2])),
                array("f", map(max, previous_maxs[0::2], previous_maxs[1::2])),
            )
        )
    return levels


class PeakAccumulator:
    """把交错 PCM 按每个声道分别累积成固定帧宽度的 min/max 峰值。"""

    def __init__(
        self,
        channels: int,
        sample_rate: int,
        scale: float,
        offset: float = 0.0,
        bucket: int = BUCKET_SAMPLES,
    ) -> None:
        self.channels = max(1, int(channels))
        self.sample_rate = max(0, int(sample_rate))
        self.scale = float(scale) or 1.0
        self.offset = float(offset)
        self.bucket = max(1, int(bucket))
        self.mins = [array("f") for _ in range(self.channels)]
        self.maxs = [array("f") for _ in range(self.channels)]
        self.total = 0
        self._filled = [0] * self.channels
        self._low = [0.0] * self.channels
        self._high = [0.0] * self.channels
        self._carry: list[float | int] = []

    def feed(self, samples) -> None:
        """喂入一段与解码格式同类型的采样缓冲。"""
        if len(samples) == 0:
            return
        position = 0
        if self._carry:
            needed = self.channels - len(self._carry)
            taken = min(needed, len(samples))
            self._carry.extend(samples[:taken])
            position = taken
            if len(self._carry) < self.channels:
                return
            for channel, sample in enumerate(self._carry):
                self._feed_channel(channel, [sample])
            self.total += self.channels
            self._carry.clear()

        usable = ((len(samples) - position) // self.channels) * self.channels
        if usable:
            end = position + usable
            for channel in range(self.channels):
                self._feed_channel(
                    channel,
                    samples[position + channel : end : self.channels],
                )
            self.total += usable
            position = end
        if position < len(samples):
            self._carry.extend(samples[position:])

    def _feed_channel(self, channel: int, samples) -> None:
        """向一个声道喂入连续帧；各声道的桶边界保持对齐。"""
        position = 0
        count = len(samples)
        while position < count:
            taken = min(self.bucket - self._filled[channel], count - position)
            chunk = samples[position : position + taken]
            low = min(chunk)
            high = max(chunk)
            if self._filled[channel] == 0:
                self._low[channel] = low
                self._high[channel] = high
            else:
                if low < self._low[channel]:
                    self._low[channel] = low
                if high > self._high[channel]:
                    self._high[channel] = high
            self._filled[channel] += taken
            position += taken
            if self._filled[channel] >= self.bucket:
                self.mins[channel].append(self._low[channel])
                self.maxs[channel].append(self._high[channel])
                self._filled[channel] = 0

    def finish(self) -> "PeakData":
        for channel in range(self.channels):
            if self._filled[channel]:
                self.mins[channel].append(self._low[channel])
                self.maxs[channel].append(self._high[channel])
                self._filled[channel] = 0
        channel_levels = [
            build_levels(self.mins[channel], self.maxs[channel])
            for channel in range(self.channels)
        ]
        return PeakData(
            levels=channel_levels[0],
            frames=self.total // self.channels,
            sample_rate=self.sample_rate,
            channels=self.channels,
            scale=self.scale,
            offset=self.offset,
            channel_levels=channel_levels,
        )


class PeakData:
    """分声道波形峰值金字塔；每个声道的 levels[0] 最细。"""

    __slots__ = (
        "levels",
        "channel_levels",
        "frames",
        "sample_rate",
        "channels",
        "scale",
        "offset",
    )

    def __init__(
        self,
        levels: list[tuple[array, array]],
        frames: int,
        sample_rate: int,
        channels: int,
        scale: float,
        offset: float = 0.0,
        channel_levels: list[list[tuple[array, array]]] | None = None,
    ) -> None:
        self.channel_levels = channel_levels or [levels]
        self.levels = self.channel_levels[0]
        self.frames = int(frames)
        self.sample_rate = int(sample_rate)
        self.channels = max(1, int(channels))
        self.scale = float(scale) or 1.0
        self.offset = float(offset)

    @property
    def duration(self) -> float:
        """时长（秒）；采样率未知时返回 0。"""
        if self.sample_rate <= 0:
            return 0.0
        return self.frames / float(self.sample_rate)

    @property
    def bucket_count(self) -> int:
        return len(self.levels[0][0])

    def level_for(self, width: int, channel: int = 0) -> int:
        """选一层：桶数最接近但不少于 width 的那一层。"""
        levels = self.channel_levels[min(max(0, channel), len(self.channel_levels) - 1)]
        level = 0
        for index in range(1, len(levels)):
            if len(levels[index][0]) >= max(1, int(width)):
                level = index
            else:
                break
        return level

    def columns(self, width: int, channel: int = 0) -> list[tuple[float, float]]:
        """抽取指定声道的 width 列，并归一化为 [-1, 1] 的峰值。"""
        width = max(1, int(width))
        levels = self.channel_levels[min(max(0, channel), len(self.channel_levels) - 1)]
        lows, highs = levels[self.level_for(width, channel)]
        count = len(lows)
        if count == 0:
            return []
        if count <= width:
            return [
                (
                    (lows[index] - self.offset) / self.scale,
                    (highs[index] - self.offset) / self.scale,
                )
                for index in range(count)
            ]
        columns: list[tuple[float, float]] = []
        step = count / width
        for column in range(width):
            start = int(column * step)
            end = max(start + 1, int((column + 1) * step))
            columns.append(
                (
                    (min(lows[start:end]) - self.offset) / self.scale,
                    (max(highs[start:end]) - self.offset) / self.scale,
                )
            )
        return columns


class PeakCache:
    """按内容指纹缓存峰值数据（LRU）。"""

    def __init__(self, limit: int = PEAK_CACHE_LIMIT) -> None:
        self._limit = max(1, int(limit))
        self._items: OrderedDict[str, PeakData] = OrderedDict()

    def get(self, path: Path) -> PeakData | None:
        key = fingerprint(path)
        if not key:
            return None
        data = self._items.get(key)
        if data is not None:
            self._items.move_to_end(key)
        return data

    def put(self, path: Path, data: PeakData) -> None:
        key = fingerprint(path)
        if not key:
            return
        self._items[key] = data
        self._items.move_to_end(key)
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


class WaveformLoader(QObject):
    """用 QAudioDecoder 解出 PCM 并算出峰值；解码在主线程事件循环里异步推进。"""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._decoder: QAudioDecoder | None = None
        self._accumulator: PeakAccumulator | None = None
        self._typecode = "h"

    def start(self, path: Path) -> None:
        self.cancel()
        decoder = QAudioDecoder(self)
        self._decoder = decoder
        self._accumulator = None
        decoder.bufferReady.connect(self._on_buffer)
        decoder.finished.connect(self._on_finished)
        if hasattr(decoder, "errorOccurred"):
            decoder.errorOccurred.connect(self._on_error)
        else:  # pragma: no cover - 兼容旧版信号名
            decoder.error.connect(self._on_error)
        decoder.setSource(QUrl.fromLocalFile(str(path)))
        decoder.start()

    def cancel(self) -> None:
        decoder = self._decoder
        self._decoder = None
        self._accumulator = None
        if decoder is not None:
            decoder.stop()
            decoder.deleteLater()

    @property
    def running(self) -> bool:
        return self._decoder is not None

    def _on_buffer(self) -> None:
        decoder = self._decoder
        if decoder is None:
            return
        buffer = decoder.read()
        if not buffer.isValid():
            return
        if self._accumulator is None:
            audio_format = buffer.format()
            self._typecode, scale, offset = _format_spec(audio_format.sampleFormat())
            self._accumulator = PeakAccumulator(
                audio_format.channelCount(), audio_format.sampleRate(), scale, offset
            )
        samples = array(self._typecode)
        samples.frombytes(buffer.data())
        self._accumulator.feed(samples)

    def _on_finished(self) -> None:
        accumulator = self._accumulator
        self.cancel()
        if accumulator is None or accumulator.total == 0:
            self.failed.emit("未能从该文件解出音频数据")
            return
        self.finished.emit(accumulator.finish())

    def _on_error(self, _error) -> None:
        decoder = self._decoder
        message = decoder.errorString() if decoder is not None else ""
        self.cancel()
        self.failed.emit(message or "音频解码失败")


class WaveformView(QWidget):
    """峰值波形视图；点击或拖动可跳转播放位置。"""

    seek_requested = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(88)
        self._palette: Palette = palette_for("dark")
        self._peaks: PeakData | None = None
        self._position = 0.0
        self._columns: dict[int, list[tuple[float, float]]] = {}
        self._columns_key: tuple[int, int] | None = None

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def set_peaks(self, peaks: PeakData | None) -> None:
        self._peaks = peaks
        self._columns_key = None
        self.update()

    def set_position(self, ratio: float) -> None:
        ratio = min(1.0, max(0.0, float(ratio)))
        if abs(ratio - self._position) < 1e-4:
            return
        self._position = ratio
        self.update()

    # ---- Qt 事件 ---------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._columns_key = None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_seek(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._emit_seek(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        palette = self._palette
        painter = QPainter(self)
        painter.fillRect(self.rect(), qcolor(palette.canvas))
        width = self.width()
        height = self.height()
        lane_count = self._lane_count()
        lane_height = height / lane_count
        if lane_count > 1:
            painter.setPen(QPen(qcolor(palette.border_subtle)))
            for lane in range(1, lane_count):
                y = int(round(lane * lane_height))
                painter.drawLine(0, y, width, y)
        played = qcolor(palette.accent)
        pending = QColor(played)
        pending.setAlpha(110)
        pen_played = QPen(played)
        pen_pending = QPen(pending)
        head = int(self._position * width)
        for channel in range(lane_count):
            middle = (channel + 0.5) * lane_height
            columns = self._visible_columns(channel)
            if not columns:
                painter.setPen(QPen(qcolor(palette.border_subtle)))
                painter.drawLine(0, int(round(middle)), width, int(round(middle)))
                continue
            span = max(0.5, (lane_height - 6) / 2.0)
            step = len(columns) / width
            last = len(columns) - 1
            for x in range(width):
                low, high = columns[min(last, int(x * step))]
                top = middle - high * span
                bottom = middle - low * span
                if bottom - top < 1.0:
                    top = middle - 0.5
                    bottom = middle + 0.5
                painter.setPen(pen_played if x <= head else pen_pending)
                painter.drawLine(x, int(round(top)), x, int(round(bottom)))
        painter.setPen(QPen(qcolor(palette.text_primary)))
        painter.drawLine(head, 0, head, height)

    # ---- 内部 -------------------------------------------------------
    def _emit_seek(self, event) -> None:
        width = max(1, self.width())
        ratio = event.position().x() / width
        self.seek_requested.emit(min(1.0, max(0.0, ratio)))

    def _lane_count(self) -> int:
        """按解码得到的声道数分行；常见 mono / stereo 分别是一行 / 两行。"""
        if self._peaks is None:
            return 1
        return max(1, min(self._peaks.channels, len(self._peaks.channel_levels)))

    def _visible_columns(self, channel: int = 0) -> list[tuple[float, float]]:
        width = max(1, self.width())
        key = (width, id(self._peaks))
        if self._columns_key != key:
            self._columns.clear()
            self._columns_key = key
        if channel not in self._columns:
            self._columns[channel] = (
                self._peaks.columns(width, channel) if self._peaks is not None else []
            )
        return self._columns[channel]


class AudioPreview(QWidget):
    """音频预览：波形 + 播放 / 暂停 / 进度 / 音量（PLAN 6.8）。"""

    details_changed = Signal(int, int)  # channels, sample_rate

    def __init__(self, parent=None, cache_limit: int = PEAK_CACHE_LIMIT) -> None:
        super().__init__(parent)
        self._palette: Palette = palette_for("dark")
        self._source: Path | None = None
        self._cache = PeakCache(limit=cache_limit)

        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._output.setVolume(0.8)
        self._player.setAudioOutput(self._output)
        self._loader = WaveformLoader(self)

        self._wave = WaveformView()
        self._wave.seek_requested.connect(self._on_seek)

        self._play_button = QToolButton()
        self._play_button.setProperty("variant", "ghost")
        self._play_button.setToolTip("播放 / 暂停")
        self._play_button.clicked.connect(self.toggle_playback)

        self._position_slider = QSlider(Qt.Orientation.Horizontal)
        self._position_slider.setRange(0, 0)
        self._position_slider.sliderMoved.connect(self._on_slider_moved)

        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setProperty("variant", "secondary")
        self._time_label.setProperty("role", "mono")
        self._time_label.setFont(mono_font(11))

        self._volume_glyph = QLabel(design_glyph("speaker_2", 16))
        self._volume_glyph.setProperty("variant", "glyph")

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(90)
        self._volume_slider.setToolTip("音量")
        self._volume_slider.valueChanged.connect(self._on_volume_changed)

        self._message = QLabel("")
        self._message.setProperty("variant", "secondary")
        self._message.setWordWrap(True)
        self._message.setVisible(False)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addWidget(self._play_button)
        controls.addWidget(self._position_slider, 1)
        controls.addWidget(self._time_label)
        controls.addWidget(self._volume_glyph)
        controls.addWidget(self._volume_slider)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._wave, 1)
        layout.addWidget(self._message)
        layout.addLayout(controls)

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.errorOccurred.connect(self._on_player_error)
        self._loader.finished.connect(self._on_peaks)
        self._loader.failed.connect(self._on_load_failed)

        self.set_palette(self._palette)
        self._update_play_icon()

    # ---- 对外 -------------------------------------------------------
    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._wave.set_palette(palette)
        self._update_play_icon()

    @property
    def peaks(self) -> PeakData | None:
        return self._wave._peaks

    @property
    def source(self) -> Path | None:
        return self._source

    def show_file(self, path: Path, name: str = "") -> None:
        self.clear()
        self._source = Path(path)
        self._player.setSource(QUrl.fromLocalFile(str(self._source)))
        if not backend_available():
            self._message.setText("未检测到 QtMultimedia 解码后端，无法生成波形与播放")
            self._message.setVisible(True)
            return
        cached = self._cache.get(self._source)
        if cached is not None:
            self._apply_peaks(cached)
            return
        self._message.setText("正在解码波形…")
        self._message.setVisible(True)
        self._loader.start(self._source)

    def clear(self) -> None:
        self._loader.cancel()
        self._player.stop()
        self._player.setSource(QUrl())
        self._source = None
        self._wave.set_peaks(None)
        self._wave.set_position(0.0)
        self._position_slider.setRange(0, 0)
        self._position_slider.setValue(0)
        self._time_label.setText("00:00 / 00:00")
        self._message.setText("")
        self._message.setVisible(False)
        self._update_play_icon()

    def toggle_playback(self) -> None:
        if self._source is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    # ---- 播放器回调 -------------------------------------------------
    def _on_position_changed(self, position: int) -> None:
        duration = self._player.duration()
        if not self._position_slider.isSliderDown():
            self._position_slider.setValue(position)
        if duration > 0:
            self._wave.set_position(position / duration)
        self._time_label.setText(
            _format_time(position) + " / " + _format_time(duration)
        )

    def _on_duration_changed(self, duration: int) -> None:
        self._position_slider.setRange(0, max(0, duration))
        self._time_label.setText(
            _format_time(self._player.position()) + " / " + _format_time(duration)
        )

    def _on_state_changed(self, _state) -> None:
        self._update_play_icon()

    def _on_player_error(self, _error, message: str) -> None:
        if self._source is None:
            return
        self._message.setText(message or "无法播放该音频")
        self._message.setVisible(True)

    def _on_slider_moved(self, position: int) -> None:
        self._player.setPosition(position)

    def _on_seek(self, ratio: float) -> None:
        duration = self._player.duration()
        if duration > 0:
            self._player.setPosition(int(ratio * duration))

    def _on_volume_changed(self, value: int) -> None:
        self._output.setVolume(value / 100.0)

    def _on_peaks(self, peaks: PeakData) -> None:
        if self._source is None:
            return
        self._cache.put(self._source, peaks)
        self._apply_peaks(peaks)

    def _on_load_failed(self, message: str) -> None:
        if self._source is None:
            return
        self._message.setText(message)
        self._message.setVisible(True)

    def _apply_peaks(self, peaks: PeakData) -> None:
        self._wave.set_peaks(peaks)
        self._message.setText("")
        self._message.setVisible(False)
        self.details_changed.emit(peaks.channels, peaks.sample_rate)

    def _update_play_icon(self) -> None:
        playing = (
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        name = "pause" if playing else "play"
        self._play_button.setIcon(design_icon(name, 16, self._palette.text_primary))


def _format_time(milliseconds: int) -> str:
    """毫秒格式化成 mm:ss。"""
    if milliseconds <= 0:
        return "00:00"
    seconds = int(milliseconds) // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


_backend_available: bool | None = None


def backend_available() -> bool:
    """QtMultimedia 是否真的加载了可解码后端（结果缓存）。"""
    global _backend_available
    if _backend_available is None:
        try:
            media_format = QMediaFormat()
            _backend_available = bool(
                media_format.supportedAudioCodecs(QMediaFormat.ConversionMode.Decode)
            )
        except Exception:  # noqa: BLE001 - 后端实现差异大，统一兜底
            _backend_available = False
    return _backend_available
