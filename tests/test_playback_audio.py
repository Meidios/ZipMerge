import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from zipmerge.ui.waveform import QAudioFormat, QMediaPlayer
from zipmerge.ui.playback_audio import PREROLL_US, PreviewAudioOutput


class PreviewAudioOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.player = QMediaPlayer()
        self.output = PreviewAudioOutput(self.player)
        fmt = QAudioFormat()
        fmt.setSampleRate(48_000)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.output._format = fmt
        self.sink = self.output._sink = Mock()
        self.sink.bytesFree.return_value = fmt.bytesForDuration(160_000)
        self.writer = self.sink.start.return_value
        self.writer.write.side_effect = len
        self.addCleanup(self.output.reset)

    def test_cold_output_waits_and_writes_first_samples_in_order(self):
        first = b"A" * self.output._format.bytesForDuration(PREROLL_US // 2)
        self.output._pending.append(first)
        self.output._write()
        self.sink.start.assert_not_called()
        second = b"B" * len(first)
        self.output._pending.append(second)
        self.output._write()
        self.sink.start.assert_called_once()
        self.assertEqual(bytes(self.writer.write.call_args.args[0]), first + second)

    def test_short_clip_drains_and_partial_device_writes_keep_the_tail(self):
        self.output._pending.append(b"12345678")
        self.output._ending = True
        self.sink.bytesFree.return_value = 4
        self.output._write()
        self.assertEqual(bytes(self.output._pending), b"5678")
        self.output._write()
        self.assertTrue(self.output._pending.isEmpty())
        self.assertFalse(self.output._retry.isActive())

    def test_reset_discards_queued_samples_and_releases_old_sink(self):
        self.output._pending.append(b"old file")
        self.output._ending = True
        self.output.reset()
        self.assertTrue(self.output._pending.isEmpty())
        self.assertIsNone(self.output._writer)
        self.assertFalse(self.output._ending)
        self.sink.reset.assert_called_once()
