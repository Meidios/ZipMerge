"""Buffer decoded PCM before opening the Windows output device for a preview."""

import os

from PySide6.QtCore import QByteArray, Qt, QTimer, Slot
from PySide6.QtMultimedia import (
    QAudio, QAudioBuffer, QAudioBufferOutput, QAudioOutput, QAudioSink, QMediaDevices, QMediaPlayer,
)

PREROLL_US = 64_000


class PreviewAudioOutput(QAudioOutput):
    """Keep the native media clock and prime a short PCM queue at its output."""

    def __init__(self, player: QMediaPlayer):
        super().__init__(player)
        self._player = player
        self._tap = self._sink = self._writer = None
        self._pending = QByteArray()
        self._retry = QTimer(self)
        self._retry.setInterval(10)
        self._retry.timeout.connect(self._write)
        self._ending = False
        self._device_id = None
        self._devices = QMediaDevices(self)
        self._devices.audioOutputsChanged.connect(self._device_changed)
        self.volumeChanged.connect(self._volume_changed)
        self.mutedChanged.connect(self._volume_changed)
        player.playbackStateChanged.connect(self._state_changed)

    def prepare(self):
        self.reset()
        device = self._devices.defaultAudioOutput()
        self._device_id = device.id()
        # QAudioBufferOutput is supported by the FFmpeg backend only.
        if device.isNull() or os.environ.get("QT_MEDIA_BACKEND", "ffmpeg") != "ffmpeg":
            self._player.setAudioOutput(self)
            return
        self._player.setAudioOutput(None)
        self._format = device.preferredFormat()
        self._tap = QAudioBufferOutput(self._format, self)
        self._tap.audioBufferReceived.connect(self._receive, Qt.ConnectionType.QueuedConnection)
        self._player.setAudioBufferOutput(self._tap)
        self._sink = QAudioSink(device, self._format, self)
        self._sink.stateChanged.connect(self._sink_state, Qt.ConnectionType.QueuedConnection)
        self._sink.setBufferSize(self._format.bytesForDuration(160_000))
        self._volume_changed()

    @property
    def preroll_ms(self):
        return PREROLL_US // 1000 if self._tap is not None else 0

    def reset(self):
        self._retry.stop()
        self._pending.clear()
        self._ending = False
        if self._tap is not None:
            self._player.setAudioBufferOutput(None)
            self._tap.deleteLater()
            self._tap = None
        if self._sink is not None:
            self._sink.reset()
            self._sink.deleteLater()
            self._sink = self._writer = None

    def seek(self, position):
        if self._tap is not None:
            self.prepare()
        self._player.setPosition(position)

    def _device_changed(self):
        if (not self._player.source().isEmpty() and
                self._devices.defaultAudioOutput().id() != self._device_id):
            self.prepare()

    def _volume_changed(self):
        if self._sink is not None:
            self._sink.setVolume(0.0 if self.isMuted() else self.volume())

    @Slot(QAudioBuffer)
    def _receive(self, buffer):
        if self._tap is None or self.sender() is not self._tap:
            return
        if buffer.isValid():
            self._ending = False
            self._pending.append(bytes(buffer.constData()))
            if self._pending.size() > self._format.bytesForDuration(500_000):
                # A stalled device cannot follow the transport; let Qt renegotiate it.
                self._fallback()
                return
        else:
            self._ending = True
        self._write()
        self._release_if_drained()

    def _fallback(self):
        self.reset()
        self._player.setAudioOutput(self)

    @Slot(QAudio.State)
    def _sink_state(self, _state):
        if self.sender() is self._sink:
            self._release_if_drained()

    def _release_if_drained(self):
        if (self._ending and self._writer is not None and self._pending.isEmpty()
                and self._sink.state() == QAudio.State.IdleState):
            self._sink.reset()
            self._writer = None
            self._ending = False

    def _write(self):
        if self._sink is None or self._pending.isEmpty():
            self._retry.stop()
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self._retry.stop()
            return
        if self._writer is None:
            if not self._ending and self._pending.size() < self._format.bytesForDuration(PREROLL_US):
                return
            self._writer = self._sink.start()
            if self._writer is None:
                self._fallback()
                return
        written = self._writer.write(self._pending.left(self._sink.bytesFree()))
        if written < 0:
            self._fallback()
            return
        if written > 0:
            self._pending.remove(0, written)
        if self._pending.isEmpty():
            self._retry.stop()
        else:
            self._retry.start()

    def _state_changed(self, state):
        if self._writer is None:
            return
        if state == QMediaPlayer.PlaybackState.PausedState:
            self._sink.suspend()
        elif state == QMediaPlayer.PlaybackState.PlayingState:
            self._sink.resume()
            self._write()
