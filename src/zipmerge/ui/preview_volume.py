"""One user-selected playback level for all audio and video previews."""

from PySide6.QtCore import QObject, Signal, Slot

from ..util.settings import DEFAULT_PREVIEW_VOLUME, KEY_PREVIEW_VOLUME, Settings


class PreviewVolume(QObject):
    changed = Signal(int)

    def __init__(self, settings: Settings | None, parent=None):
        super().__init__(parent)
        self.settings = settings
        raw = settings.value(KEY_PREVIEW_VOLUME, DEFAULT_PREVIEW_VOLUME) if settings else DEFAULT_PREVIEW_VOLUME
        try:
            self.value = min(100, max(0, int(raw)))
        except (ValueError, TypeError):
            self.value = DEFAULT_PREVIEW_VOLUME

    def bind(self, slider):
        slider.setValue(self.value)
        self.changed.connect(slider.setValue)
        slider.valueChanged.connect(self.set_value)

    @Slot(int)
    def set_value(self, value):
        if value != self.value:
            self.value = value
            if self.settings is not None:
                self.settings.set_value(KEY_PREVIEW_VOLUME, value)
            self.changed.emit(value)
