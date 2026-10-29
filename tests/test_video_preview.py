"""视频预览：播放器、后台提取、缓存与面板分发。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.core import MergeSession  # noqa: E402
from zipmerge.engine import create_archive  # noqa: E402
from zipmerge.ui import preview_panel as preview_module  # noqa: E402
from zipmerge.ui.preview_panel import PreviewCache, PreviewPanel  # noqa: E402
from zipmerge.ui.video_preview import (  # noqa: E402
    VideoPreview,
    _format_time,
    video_backend_available,
)
from zipmerge.util.settings import (  # noqa: E402
    KEY_PREVIEW_VIDEO_AUTO_LIMIT,
    Settings,
)

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return bool(predicate())


class VideoPreviewWidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preview = VideoPreview()
        self.addCleanup(self.preview.deleteLater)
        self.addCleanup(self.preview.clear)

    def test_time_format_supports_long_video(self) -> None:
        self.assertEqual(_format_time(0), "00:00")
        self.assertEqual(_format_time(65_000), "01:05")
        self.assertEqual(_format_time(3_665_000), "1:01:05")

    def test_backend_flag_is_bool(self) -> None:
        self.assertIsInstance(video_backend_available(), bool)

    def test_gate_loading_and_clear_states(self) -> None:
        self.preview.show_gate("600 MB")
        self.assertFalse(self.preview._load_button.isHidden())
        self.assertFalse(self.preview._open_button.isHidden())
        self.assertIn("600 MB", self.preview._status_label.text())

        self.preview.show_loading(25)
        self.assertFalse(self.preview._progress.isHidden())
        self.assertEqual(self.preview._progress.value(), 25)
        self.assertFalse(self.preview._cancel_button.isHidden())
        self.preview.update_loading(60, "正在提取视频… 60%")
        self.assertEqual(self.preview._progress.value(), 60)

        self.preview.clear()
        self.assertIsNone(self.preview.source)
        self.assertTrue(self.preview._play_button.isEnabled() is False)

    def test_mute_toggle_roundtrip(self) -> None:
        initial = self.preview._output.isMuted()
        self.preview.toggle_muted()
        self.assertNotEqual(self.preview._output.isMuted(), initial)
        self.preview.toggle_muted()
        self.assertEqual(self.preview._output.isMuted(), initial)

    @unittest.skipUnless(shutil.which("ffmpeg"), "需要测试环境提供 ffmpeg")
    @unittest.skipUnless(video_backend_available(), "无 QtMultimedia 视频后端")
    def test_h264_aac_chinese_file_decodes_real_frames(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="zipmerge-video-widget-"))
        self.addCleanup(shutil.rmtree, root, True)
        sample = root / "中文镜头_H264.mp4"
        subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x180:rate=12",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100",
                "-t",
                "1.2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(sample),
            ],
            check=True,
        )
        player = QMediaPlayer()
        output = QAudioOutput()
        sink = QVideoSink()
        player.setAudioOutput(output)
        player.setVideoSink(sink)
        frames: list[tuple[int, int]] = []
        errors: list[str] = []
        sink.videoFrameChanged.connect(
            lambda frame: frames.append((frame.width(), frame.height()))
            if frame.isValid()
            else None
        )
        player.errorOccurred.connect(lambda _error, message: errors.append(message))
        player.setSource(QUrl.fromLocalFile(str(sample)))
        player.play()
        self.assertTrue(wait_for(lambda: bool(frames)), "未解码出视频帧")
        player.pause()
        self.assertEqual(frames[0], (320, 180))
        self.assertGreater(player.duration(), 0)
        self.assertEqual(errors, [])
        player.stop()
        player.setSource(QUrl())

        # 选中只设置 source，不自动播放；空格对应的 toggle 才开始解码。
        self.preview.resize(640, 420)
        self.preview.show()
        self.preview.show_file(sample)
        self.assertEqual(
            self.preview._player.playbackState(),
            QMediaPlayer.PlaybackState.StoppedState,
        )
        self.assertEqual(self.preview.source, sample.resolve())
        self.preview.toggle_playback()
        self.assertTrue(
            wait_for(
                lambda: self.preview._frame_size == (320, 180)
            ),
            "VideoPreview 按空格播放后首帧解码超时",
        )
        self.assertGreater(self.preview._player.duration(), 0)

    @unittest.skipUnless(shutil.which("ffmpeg"), "需要测试环境提供 ffmpeg")
    @unittest.skipUnless(video_backend_available(), "无 QtMultimedia 视频后端")
    def test_mov_avi_and_mkv_container_smoke(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="zipmerge-video-containers-"))
        self.addCleanup(shutil.rmtree, root, True)
        cases = (
            ("镜头.mov", ("-c:v", "libx264", "-pix_fmt", "yuv420p")),
            ("镜头.avi", ("-c:v", "mjpeg", "-pix_fmt", "yuvj420p")),
            ("镜头.mkv", ("-c:v", "libx264", "-pix_fmt", "yuv420p")),
        )
        for filename, codec_args in cases:
            with self.subTest(filename=filename):
                sample = root / filename
                subprocess.run(
                    [
                        shutil.which("ffmpeg"),
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        "testsrc=size=160x90:rate=8",
                        "-t",
                        "0.6",
                        *codec_args,
                        str(sample),
                    ],
                    check=True,
                )
                player = QMediaPlayer()
                sink = QVideoSink()
                player.setVideoSink(sink)
                frames: list[tuple[int, int]] = []
                sink.videoFrameChanged.connect(
                    lambda frame, target=frames: target.append((frame.width(), frame.height()))
                    if frame.isValid()
                    else None
                )
                player.setSource(QUrl.fromLocalFile(str(sample)))
                player.play()
                self.assertTrue(wait_for(lambda: bool(frames)), f"{filename} 未解码出画面")
                self.assertEqual(frames[0], (160, 90))
                player.stop()
                player.setSource(QUrl())

    def test_show_file_prepares_controls_without_autoplay(self) -> None:
        self.preview.show_file(Path("fake.mp4"))
        self.assertEqual(
            self.preview._player.playbackState(),
            QMediaPlayer.PlaybackState.StoppedState,
        )
        self.assertTrue(self.preview._play_button.isEnabled())


class PreviewCacheVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="zipmerge-video-cache-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.session = MergeSession(name="视频缓存", temp_root=self.root / "sessions")
        self.addCleanup(self.session.close)
        src = self.root / "src"
        src.mkdir()
        (src / "中文镜头.mp4").write_bytes(b"video payload")
        archive = self.root / "视频包.zip"
        create_archive(archive, "zip", src)
        self.session.import_archive(archive)
        self.node = next(node for node in self.session.root.walk() if node.name.endswith(".mp4"))
        self.cache = PreviewCache(self.root / "preview")

    def test_prepare_commit_and_discard_are_isolated(self) -> None:
        first = self.cache.prepare_video_extraction(self.node, 1)
        first.destination.parent.mkdir(parents=True)
        first.destination.write_bytes(b"one")
        self.assertEqual(self.cache.commit(first), first.destination)
        self.assertEqual(self.cache.cached_path(self.node), first.destination)

        second = self.cache.prepare_video_extraction(self.node, 2)
        second.destination.parent.mkdir(parents=True)
        second.destination.write_bytes(b"two")
        self.cache.discard(second)
        self.assertFalse(second.cleanup_root.exists())
        self.assertEqual(self.cache.cached_path(self.node), first.destination)

    def test_panel_auto_extracts_archive_video_without_autoplay(self) -> None:
        panel = PreviewPanel(self.session)
        self.addCleanup(panel.shutdown)
        self.addCleanup(panel.deleteLater)
        shown: list[Path] = []
        panel._video.show_file = lambda path: shown.append(Path(path))
        with mock.patch.object(preview_module, "video_backend_available", return_value=True):
            panel.show_node(self.node)
            self.assertEqual(panel._stack.currentIndex(), PreviewPanel._PAGE_VIDEO)
            self.assertTrue(panel._video_jobs)
            self.assertTrue(wait_for(lambda: bool(shown) and not panel._video_jobs))
        self.assertEqual(shown[0].read_bytes(), b"video payload")

    def test_panel_auto_extracts_video_from_7z_without_autoplay(self) -> None:
        src = self.root / "src-7z"
        src.mkdir()
        (src / "七号镜头.mp4").write_bytes(b"seven zip video")
        archive = self.root / "视频包.7z"
        create_archive(archive, "7z", src)
        session = MergeSession(name="7z视频", temp_root=self.root / "sessions-7z")
        self.addCleanup(session.close)
        session.import_archive(archive)
        node = next(item for item in session.root.walk() if item.name.endswith(".mp4"))
        panel = PreviewPanel(session)
        self.addCleanup(panel.shutdown)
        self.addCleanup(panel.deleteLater)
        shown: list[Path] = []
        panel._video.show_file = lambda path: shown.append(Path(path))
        with mock.patch.object(preview_module, "video_backend_available", return_value=True):
            panel.show_node(node)
            self.assertTrue(wait_for(lambda: bool(shown) and not panel._video_jobs))
        self.assertEqual(shown[0].read_bytes(), b"seven zip video")

    def test_archive_video_over_limit_waits_for_explicit_load(self) -> None:
        settings = Settings("ZipMergeVideoTest", "Threshold")
        settings.clear()
        self.addCleanup(settings.clear)
        settings.set_value(KEY_PREVIEW_VIDEO_AUTO_LIMIT, 1)
        panel = PreviewPanel(self.session, settings)
        self.addCleanup(panel.shutdown)
        self.addCleanup(panel.deleteLater)
        with mock.patch.object(preview_module, "video_backend_available", return_value=True):
            panel.show_node(self.node)
        self.assertFalse(panel._video_jobs)
        self.assertFalse(panel._video._load_button.isHidden())
        self.assertIn("超过自动提取上限", panel._video._status_label.text())
        shown: list[Path] = []
        panel._video.show_file = lambda path: shown.append(Path(path))
        panel._video.load_requested.emit()
        self.assertTrue(wait_for(lambda: bool(shown) and not panel._video_jobs))
        self.assertEqual(shown[0].read_bytes(), b"video payload")

    def test_disk_video_prepares_player_immediately(self) -> None:
        disk = self.root / "磁盘镜头.mp4"
        disk.write_bytes(b"disk")
        self.session.import_disk_paths([disk])
        node = next(node for node in self.session.root.walk() if node.name == disk.name)
        settings = Settings("ZipMergeVideoTest", "Disk")
        settings.clear()
        self.addCleanup(settings.clear)
        settings.set_value(KEY_PREVIEW_VIDEO_AUTO_LIMIT, 1)
        panel = PreviewPanel(self.session, settings)
        self.addCleanup(panel.shutdown)
        self.addCleanup(panel.deleteLater)
        shown: list[Path] = []
        panel._video.show_file = lambda path: shown.append(Path(path))
        with mock.patch.object(preview_module, "video_backend_available", return_value=True):
            panel.show_node(node)
        self.assertEqual(shown, [disk])
        self.assertFalse(panel._video_jobs)

    def test_switching_type_stops_audio_and_video(self) -> None:
        panel = PreviewPanel(self.session)
        self.addCleanup(panel.shutdown)
        self.addCleanup(panel.deleteLater)
        calls: list[str] = []
        panel._audio.clear = lambda: calls.append("audio")
        panel._video.clear = lambda: calls.append("video")
        panel.show_node(None)
        self.assertEqual(calls[-2:], ["audio", "video"])

    def test_late_video_result_is_discarded(self) -> None:
        panel = PreviewPanel(self.session)
        self.addCleanup(panel.shutdown)
        self.addCleanup(panel.deleteLater)
        extraction = panel._cache.prepare_video_extraction(self.node, 40)
        extraction.destination.parent.mkdir(parents=True)
        extraction.destination.write_bytes(b"late")
        panel._video_jobs[40] = (mock.Mock(), extraction)
        panel._video_token = 41
        shown: list[Path] = []
        panel._video.show_file = lambda path: shown.append(Path(path))
        panel._on_video_extracted(40, extraction.destination)
        self.assertEqual(shown, [])
        self.assertFalse(extraction.cleanup_root.exists())
        self.assertIsNone(panel._cache.cached_path(self.node))

    def test_reload_settings_updates_video_threshold(self) -> None:
        settings = Settings("ZipMergeVideoTest", "Reload")
        settings.clear()
        self.addCleanup(settings.clear)
        panel = PreviewPanel(self.session, settings)
        self.addCleanup(panel.shutdown)
        self.addCleanup(panel.deleteLater)
        settings.set_value(KEY_PREVIEW_VIDEO_AUTO_LIMIT, 123 * 1024 * 1024)
        panel.reload_settings(settings)
        self.assertEqual(panel._video_auto_limit, 123 * 1024 * 1024)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
