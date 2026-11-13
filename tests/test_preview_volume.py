import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from zipmerge.ui.main_window import MainWindow
from zipmerge.util.settings import KEY_PREVIEW_VOLUME, KEY_TEMP_ROOT, Settings


class PreviewVolumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_shared_level_survives_new_tabs_and_restarts_including_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            def reopen():
                prefs = Settings("ZipMergeTest", "Volume")
                prefs._store = QSettings(str(Path(tmp)/"volume.ini"), QSettings.Format.IniFormat)
                prefs.set_value(KEY_TEMP_ROOT, str(Path(tmp)/"work"))
                return MainWindow(settings=prefs)

            def levels(panel, expected):
                for widget in (panel._audio, panel._video):
                    self.assertEqual(widget._volume_slider.value(), expected)
                    self.assertAlmostEqual(widget._output.volume(), expected / 100, places=5)

            for initial, changed in ((100, 37), (37, 0), (0, 81)):
                window = reopen()
                try:
                    panel = window.current_tab().preview_panel
                    levels(panel, initial)
                    second = window.new_tab().preview_panel
                    panel._audio._volume_slider.setValue(changed)
                    levels(second, changed)
                    second._video._volume_slider.setValue(19)
                    levels(panel, 19)
                    second._video._volume_slider.setValue(changed)
                    second._video.toggle_muted()
                    levels(window.new_tab().preview_panel, changed)
                    self.assertEqual(window.settings.value(KEY_PREVIEW_VOLUME, type=int), changed)
                    window.settings.sync()
                finally:
                    window.close()
                    window.deleteLater()
