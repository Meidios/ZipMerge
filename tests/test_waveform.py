"""音频波形测试：峰值金字塔、指纹缓存、视图交互与预览面板接入。"""

from __future__ import annotations

import math
import os
import shutil
import struct
import tempfile
import unittest
from array import array
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QPoint, QTimer, Qt  # noqa: E402
from PySide6.QtMultimedia import QAudioFormat  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.core import MergeSession  # noqa: E402
from zipmerge.ui import preview_panel as preview_module  # noqa: E402
from zipmerge.ui import waveform  # noqa: E402
from zipmerge.ui.preview_panel import PreviewPanel  # noqa: E402
from zipmerge.ui.waveform import (  # noqa: E402
    AudioPreview,
    PeakAccumulator,
    PeakCache,
    PeakData,
    WaveformView,
    _format_spec,
    _format_time,
    build_levels,
    fingerprint,
    inspect_audio_file,
)

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


def write_wav(
    path: Path,
    seconds: float = 0.3,
    rate: int = 44100,
    freq: float = 440.0,
    *,
    channels: int = 1,
    bits: int = 16,
) -> None:
    """写一个 PCM WAV 样本，覆盖 mono / stereo 与 16 / 24 bit。"""
    width = bits // 8
    frames = bytearray()
    for index in range(int(seconds * rate)):
        for channel in range(channels):
            value = math.sin(2 * math.pi * (freq + channel * 110) * index / rate)
            integer = int(((1 << (bits - 1)) - 1) * 0.5 * value)
            if bits == 16:
                frames += struct.pack("<h", integer)
            elif bits == 24:
                frames += integer.to_bytes(3, "little", signed=True)
            else:  # pragma: no cover - 测试辅助只需要两种位深
                raise ValueError(bits)
    payload = bytes(frames)
    header = b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
    block_align = channels * width
    header += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, rate, rate * block_align, block_align, bits
    )
    header += b"data" + struct.pack("<I", len(payload))
    path.write_bytes(header + payload)


def wait_for(predicate, timeout_ms: int = 20000) -> bool:
    """跑事件循环直到 predicate 为真或超时。"""
    if predicate():
        return True
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    timer.start(40)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    timer.stop()
    return bool(predicate())


class FormatSpecTests(unittest.TestCase):
    def test_known_formats(self) -> None:
        fmt = QAudioFormat.SampleFormat
        self.assertEqual(_format_spec(fmt.Int16), ("h", 32768.0, 0.0))
        self.assertEqual(_format_spec(fmt.Int32), ("i", 2147483648.0, 0.0))
        self.assertEqual(_format_spec(fmt.UInt8), ("B", 128.0, 128.0))
        self.assertEqual(_format_spec(fmt.Float), ("f", 1.0, 0.0))

    def test_unknown_falls_back_to_int16(self) -> None:
        self.assertEqual(_format_spec(QAudioFormat.SampleFormat.Unknown), ("h", 32768.0, 0.0))

    def test_format_time(self) -> None:
        self.assertEqual(_format_time(0), "00:00")
        self.assertEqual(_format_time(1000), "00:01")
        self.assertEqual(_format_time(65000), "01:05")
        self.assertEqual(_format_time(3600000), "60:00")


class AudioFileInfoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-audio-info-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_reads_source_wav_channels_rate_and_bit_depth(self) -> None:
        path = self.tmp / "录音.wav"
        write_wav(path, rate=48000, channels=2, bits=24)
        info = inspect_audio_file(path)
        self.assertEqual(info.format_name, "WAV")
        self.assertEqual(info.channels, 2)
        self.assertEqual(info.sample_rate, 48000)
        self.assertEqual(info.bit_depth, 24)

    def test_non_wav_keeps_format_without_inventing_bit_depth(self) -> None:
        path = self.tmp / "音乐.flac"
        path.write_bytes(b"not needed")
        info = inspect_audio_file(path)
        self.assertEqual(info.format_name, "FLAC")
        self.assertEqual(info.channels, 0)
        self.assertEqual(info.bit_depth, 0)


class PeakAccumulatorTests(unittest.TestCase):
    def test_bucketing(self) -> None:
        acc = PeakAccumulator(1, 44100, 32768.0, bucket=2)
        acc.feed(array("h", [1, 2, 3, 4, 5]))
        peaks = acc.finish()
        self.assertEqual(list(peaks.levels[0][0]), [1.0, 3.0, 5.0])
        self.assertEqual(list(peaks.levels[0][1]), [2.0, 4.0, 5.0])
        self.assertEqual(peaks.frames, 5)

    def test_bucket_spans_buffers(self) -> None:
        acc = PeakAccumulator(1, 44100, 32768.0, bucket=4)
        acc.feed(array("h", [1, 2, 3]))
        acc.feed(array("h", [4, 5, 6]))
        peaks = acc.finish()
        self.assertEqual(list(peaks.levels[0][0]), [1.0, 5.0])
        self.assertEqual(list(peaks.levels[0][1]), [4.0, 6.0])
        self.assertEqual(peaks.frames, 6)

    def test_channels_reduce_to_frames(self) -> None:
        acc = PeakAccumulator(2, 44100, 32768.0)
        acc.feed(array("h", [0, 0, 0, 0, 0, 0]))
        self.assertEqual(acc.finish().frames, 3)

    def test_stereo_channels_keep_independent_peaks(self) -> None:
        acc = PeakAccumulator(2, 48000, 1.0, bucket=2)
        acc.feed(array("h", [1, 10, 2, 20, 3, 30]))
        peaks = acc.finish()
        self.assertEqual(peaks.channels, 2)
        self.assertEqual(list(peaks.channel_levels[0][0][0]), [1.0, 3.0])
        self.assertEqual(list(peaks.channel_levels[0][0][1]), [2.0, 3.0])
        self.assertEqual(list(peaks.channel_levels[1][0][0]), [10.0, 30.0])
        self.assertEqual(list(peaks.channel_levels[1][0][1]), [20.0, 30.0])
        self.assertEqual(peaks.columns(2, channel=1), [(10.0, 20.0), (30.0, 30.0)])

    def test_stereo_partial_frame_spans_buffers(self) -> None:
        acc = PeakAccumulator(2, 48000, 1.0, bucket=2)
        acc.feed(array("h", [1]))
        acc.feed(array("h", [10, 2, 20]))
        peaks = acc.finish()
        self.assertEqual(peaks.frames, 2)
        self.assertEqual(peaks.columns(1, channel=0), [(1.0, 2.0)])
        self.assertEqual(peaks.columns(1, channel=1), [(10.0, 20.0)])

    def test_empty_feed(self) -> None:
        acc = PeakAccumulator(1, 44100, 32768.0)
        acc.feed(array("h"))
        peaks = acc.finish()
        self.assertEqual(acc.total, 0)
        self.assertEqual(peaks.bucket_count, 0)
        self.assertEqual(peaks.columns(10), [])


class BuildLevelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = waveform.MAX_LEVEL_BUCKETS

    def tearDown(self) -> None:
        waveform.MAX_LEVEL_BUCKETS = self._original

    def test_single_level_below_threshold(self) -> None:
        mins = array("f", [0.0] * 100)
        maxs = array("f", [1.0] * 100)
        self.assertEqual([len(level[0]) for level in build_levels(mins, maxs)], [100])

    def test_pyramid_halves_until_threshold(self) -> None:
        mins = array("f", [float(index) for index in range(4096)])
        maxs = array("f", [float(index) for index in range(4096)])
        levels = build_levels(mins, maxs)
        self.assertEqual([len(level[0]) for level in levels], [4096, 2048])

    def test_pairwise_merge_values(self) -> None:
        waveform.MAX_LEVEL_BUCKETS = 2
        mins = array("f", [10.0, 3.0, 9.0, 1.0])
        maxs = array("f", [20.0, 30.0, 40.0, 50.0])
        levels = build_levels(mins, maxs)
        self.assertEqual([len(level[0]) for level in levels], [4, 2])
        self.assertEqual(list(levels[1][0]), [3.0, 1.0])
        self.assertEqual(list(levels[1][1]), [30.0, 50.0])

    def test_single_bucket_stops(self) -> None:
        waveform.MAX_LEVEL_BUCKETS = 0
        levels = build_levels(array("f", [1.0]), array("f", [2.0]))
        self.assertEqual([len(level[0]) for level in levels], [1])


def _make_peaks(levels, frames: int = 44100, rate: int = 44100, scale: float = 1.0, offset: float = 0.0) -> PeakData:
    return PeakData(levels=levels, frames=frames, sample_rate=rate, channels=1, scale=scale, offset=offset)


class PeakDataTests(unittest.TestCase):
    def _levels(self):
        return [
            (array("f", [0.0, 10.0, 20.0, 30.0]), array("f", [5.0, 15.0, 25.0, 35.0])),
            (array("f", [0.0, 20.0]), array("f", [15.0, 35.0])),
            (array("f", [0.0]), array("f", [35.0])),
        ]

    def test_level_selection(self) -> None:
        peaks = _make_peaks(self._levels())
        self.assertEqual(peaks.level_for(3), 0)
        self.assertEqual(peaks.level_for(2), 1)
        self.assertEqual(peaks.level_for(1), 2)
        self.assertEqual(peaks.level_for(99), 0)

    def test_duration_and_buckets(self) -> None:
        peaks = _make_peaks(self._levels())
        self.assertEqual(peaks.bucket_count, 4)
        self.assertEqual(peaks.duration, 1.0)
        self.assertEqual(_make_peaks(self._levels(), rate=0).duration, 0.0)

    def test_columns_normalize_scale_offset(self) -> None:
        peaks = _make_peaks([(array("f", [3.0]), array("f", [5.0]))], scale=2.0, offset=1.0)
        self.assertEqual(peaks.columns(1), [(1.0, 2.0)])

    def test_columns_downsample_by_min_max(self) -> None:
        peaks = _make_peaks(self._levels())
        self.assertEqual(peaks.columns(2), [(0.0, 15.0), (20.0, 35.0)])

    def test_columns_wider_than_buckets(self) -> None:
        peaks = _make_peaks(self._levels())
        columns = peaks.columns(10)
        self.assertEqual(len(columns), 4)
        self.assertEqual(columns[0], (0.0, 5.0))


class FingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-wave-fp-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_stable(self) -> None:
        path = self.tmp / "a.bin"
        path.write_bytes(bytes(range(256)) * 8)
        self.assertEqual(fingerprint(path), fingerprint(path))
        self.assertTrue(fingerprint(path))

    def test_missing_returns_empty(self) -> None:
        self.assertEqual(fingerprint(self.tmp / "missing.bin"), "")

    def test_empty_file_is_truthy(self) -> None:
        path = self.tmp / "empty.bin"
        path.write_bytes(b"")
        self.assertTrue(fingerprint(path))

    def test_head_change_detected(self) -> None:
        first = self.tmp / "one.bin"
        second = self.tmp / "two.bin"
        first.write_bytes(b"A" * 1000)
        second.write_bytes(b"B" * 1000)
        self.assertNotEqual(fingerprint(first), fingerprint(second))

    def test_tail_change_detected(self) -> None:
        payload = bytes(range(256)) * 400
        first = self.tmp / "one.bin"
        second = self.tmp / "two.bin"
        first.write_bytes(payload + b"TAIL-A")
        second.write_bytes(payload + b"TAIL-B")
        self.assertNotEqual(fingerprint(first), fingerprint(second))

    def test_middle_change_ignored(self) -> None:
        # 指纹只看大小 + 头尾各 64 KiB，是刻意的性能取舍
        head = b"H" * (64 * 1024)
        tail = b"T" * (64 * 1024)
        first = self.tmp / "one.bin"
        second = self.tmp / "two.bin"
        first.write_bytes(head + b"m" * (128 * 1024) + tail)
        second.write_bytes(head + b"n" * (128 * 1024) + tail)
        self.assertEqual(fingerprint(first), fingerprint(second))


class PeakCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-wave-cache-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _file(self, name: str, marker: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(marker * 100)
        return path

    def test_put_get(self) -> None:
        cache = PeakCache(limit=2)
        path = self._file("a.bin", b"a")
        peaks = _make_peaks([(array("f", [0.0]), array("f", [1.0]))])
        self.assertIsNone(cache.get(path))
        cache.put(path, peaks)
        self.assertIs(cache.get(path), peaks)

    def test_lru_eviction(self) -> None:
        cache = PeakCache(limit=2)
        paths = [self._file(f"{index}.bin", bytes([index])) for index in range(3)]
        peaks = _make_peaks([(array("f", [0.0]), array("f", [1.0]))])
        for path in paths[:2]:
            cache.put(path, peaks)
        cache.get(paths[0])  # 触碰 0，使 1 成为最久未用
        cache.put(paths[2], peaks)
        self.assertIsNotNone(cache.get(paths[0]))
        self.assertIsNone(cache.get(paths[1]))
        self.assertIsNotNone(cache.get(paths[2]))

    def test_clear(self) -> None:
        cache = PeakCache()
        path = self._file("a.bin", b"a")
        cache.put(path, _make_peaks([(array("f", [0.0]), array("f", [1.0]))]))
        cache.clear()
        self.assertIsNone(cache.get(path))


class WaveformViewTests(unittest.TestCase):
    def test_position_clamped(self) -> None:
        view = WaveformView()
        view.set_position(-1.0)
        self.assertEqual(view._position, 0.0)
        view.set_position(5.0)
        self.assertEqual(view._position, 1.0)

    def test_click_emits_seek_ratio(self) -> None:
        view = WaveformView()
        view.resize(200, 100)
        seen: list[float] = []
        view.seek_requested.connect(seen.append)
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(150, 50))
        self.assertEqual(len(seen), 1)
        self.assertAlmostEqual(seen[0], 0.75, places=3)

    def test_columns_requires_peaks(self) -> None:
        view = WaveformView()
        view.resize(120, 80)
        self.assertEqual(view._visible_columns(), [])

    def test_stereo_uses_two_independent_lanes(self) -> None:
        left = [(array("f", [-1.0, -0.5]), array("f", [1.0, 0.5]))]
        right = [(array("f", [-0.25, -0.1]), array("f", [0.25, 0.1]))]
        peaks = PeakData(
            levels=left,
            channel_levels=[left, right],
            frames=2,
            sample_rate=48000,
            channels=2,
            scale=1.0,
        )
        view = WaveformView()
        view.resize(120, 88)
        view.set_peaks(peaks)
        self.assertEqual(view._lane_count(), 2)
        self.assertNotEqual(view._visible_columns(0), view._visible_columns(1))


class AudioPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-wave-preview-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.wav = self.tmp / "音调.wav"
        write_wav(self.wav, seconds=0.3)
        self.stereo_wav = self.tmp / "双声道.wav"
        write_wav(self.stereo_wav, seconds=0.3, rate=48000, channels=2, bits=24)

    def test_backend_flag_is_bool(self) -> None:
        self.assertIsInstance(waveform.backend_available(), bool)
        self.assertIsInstance(waveform.PRELOADED_LIBS, int)

    @unittest.skipUnless(waveform.backend_available(), "无 QtMultimedia 解码后端")
    def test_show_file_decodes_peaks(self) -> None:
        preview = AudioPreview()
        self.addCleanup(preview.deleteLater)
        self.addCleanup(preview.clear)
        preview.show_file(self.wav, "音调.wav")
        self.assertTrue(wait_for(lambda: preview.peaks is not None), "未在超时内解出波形")
        peaks = preview.peaks
        self.assertGreater(peaks.bucket_count, 0)
        self.assertAlmostEqual(peaks.duration, 0.3, places=2)
        self.assertEqual(len(peaks.columns(32)), 32)
        # 桶数少于宽度时不补零放大，直接返回现有列
        self.assertEqual(len(peaks.columns(999)), peaks.bucket_count)

    @unittest.skipUnless(waveform.backend_available(), "无 QtMultimedia 解码后端")
    def test_stereo_file_decodes_two_waveform_lanes(self) -> None:
        preview = AudioPreview()
        self.addCleanup(preview.deleteLater)
        self.addCleanup(preview.clear)
        preview.show_file(self.stereo_wav, "双声道.wav")
        self.assertTrue(wait_for(lambda: preview.peaks is not None), "未在超时内解出双声道波形")
        self.assertEqual(preview.peaks.channels, 2)
        self.assertEqual(len(preview.peaks.channel_levels), 2)
        self.assertEqual(preview._wave._lane_count(), 2)

    @unittest.skipUnless(waveform.backend_available(), "无 QtMultimedia 解码后端")
    def test_second_show_uses_cache(self) -> None:
        preview = AudioPreview()
        self.addCleanup(preview.deleteLater)
        self.addCleanup(preview.clear)
        preview.show_file(self.wav)
        self.assertTrue(wait_for(lambda: preview.peaks is not None))
        preview.show_file(self.wav)
        self.assertIsNotNone(preview.peaks)  # 缓存命中，无需再跑事件循环

    @unittest.skipUnless(waveform.backend_available(), "无 QtMultimedia 解码后端")
    def test_clear_resets(self) -> None:
        preview = AudioPreview()
        self.addCleanup(preview.deleteLater)
        preview.show_file(self.wav)
        self.assertTrue(wait_for(lambda: preview.peaks is not None))
        preview.clear()
        self.assertIsNone(preview.peaks)
        self.assertIsNone(preview.source)

    def test_toggle_without_source_is_noop(self) -> None:
        preview = AudioPreview()
        self.addCleanup(preview.deleteLater)
        preview.toggle_playback()
        self.assertIsNone(preview.source)

    def test_missing_backend_message(self) -> None:
        original = waveform._backend_available
        waveform._backend_available = False
        try:
            preview = AudioPreview()
            self.addCleanup(preview.deleteLater)
            preview.show_file(self.wav)
            self.assertIn("后端", preview._message.text())
            self.assertFalse(preview._message.isHidden())
        finally:
            waveform._backend_available = original


class PreviewPanelAudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-wave-panel-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="波形面板", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)
        wav = self.tmp / "音频.wav"
        write_wav(wav, seconds=0.2)
        stereo = self.tmp / "录音_48k_24bit.wav"
        write_wav(stereo, seconds=0.2, rate=48000, channels=2, bits=24)
        self.session.import_disk_paths([wav, stereo])
        self.panel = PreviewPanel(self.session)
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.clear)

    def _node(self, name: str):
        return next(node for node in self.session.root.walk() if node.name == name)

    @unittest.skipUnless(waveform.backend_available(), "无 QtMultimedia 解码后端")
    def test_audio_node_uses_waveform_page(self) -> None:
        self.panel.show_node(self._node("音频.wav"))
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_AUDIO)
        self.assertIn("WAV · mono · 44.1kHz · 16bit", self.panel._subtitle.text())
        self.assertTrue(wait_for(lambda: self.panel._audio.peaks is not None))

    @unittest.skipUnless(waveform.backend_available(), "无 QtMultimedia 解码后端")
    def test_audio_subtitle_shows_source_format_channels_rate_and_bits(self) -> None:
        self.panel.show_node(self._node("录音_48k_24bit.wav"))
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_AUDIO)
        self.assertEqual(
            self.panel._subtitle.text(),
            "WAV · stereo · 48kHz · 24bit · 56.3 KB",
        )
        self.assertTrue(wait_for(lambda: self.panel._audio.peaks is not None))
        self.assertEqual(self.panel._audio._wave._lane_count(), 2)

    def test_audio_node_without_backend_shows_info(self) -> None:
        original = preview_module.backend_available
        preview_module.backend_available = lambda: False
        try:
            self.panel.show_node(self._node("音频.wav"))
        finally:
            preview_module.backend_available = original
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_INFO)
        self.assertIn("后端", self.panel._info_note.text())

    def test_clear_returns_to_empty(self) -> None:
        self.panel.show_node(self._node("音频.wav"))
        self.panel.clear()
        self.assertEqual(self.panel._stack.currentIndex(), PreviewPanel._PAGE_EMPTY)
        self.assertIsNone(self.panel._audio.peaks)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
