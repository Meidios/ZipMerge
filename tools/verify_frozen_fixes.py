"""Run repair regressions against code embedded in the actual PyInstaller EXE.

This is a development-only verifier. Application modules are loaded exclusively
from the EXE, and Qt bindings/plugins/assets from its _internal directory.
It neither installs the app nor invokes its registry/IPC startup entry point.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.abc
import importlib.util
import json
import marshal
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class BundleLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, bundle):
        from PyInstaller.archive.readers import CArchiveReader
        self.bundle = bundle
        self.internal = bundle / "_internal"
        self.archive = CArchiveReader(str(bundle / "ZipMerge.exe"))
        self.pyz = self.archive.open_embedded_archive("PYZ.pyz")
        self.loaded = set()

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "main":
            return importlib.util.spec_from_file_location(fullname, self.internal / "main.py", loader=self)
        if fullname.split(".")[0] not in {"zipmerge", "PySide6", "shiboken6", "PIL"}:
            return None
        if fullname not in self.pyz.toc:
            if fullname.split(".")[0] == "zipmerge":
                raise ModuleNotFoundError(f"Application module missing from frozen EXE: {fullname}")
            return None  # Extension modules are found under the bundled package path.
        package = self.pyz.toc[fullname][0] in (1, 3)
        file = self.internal.joinpath(*fullname.split("."))
        file = file / "__init__.py" if package else file.with_suffix(".py")
        spec = importlib.util.spec_from_file_location(fullname, file, loader=self,
                                                    submodule_search_locations=[str(file.parent)] if package else None)
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__name__
        code = marshal.loads(self.archive.extract("main")) if name == "main" else self.pyz.extract(name)
        self.loaded.add(name)
        if code is not None:
            exec(code, module.__dict__)


def verify_ui(output, scale):
    from PySide6.QtCore import QSettings, QSize, QTimer
    from PySide6.QtWidgets import QApplication
    from zipmerge.engine import create_archive
    from zipmerge.ui import archive_loader
    from zipmerge.ui.design import ThemeService, icons
    from zipmerge.ui.main_window import MainWindow
    from zipmerge.ui.target_model import icon_name_for
    from zipmerge.util.settings import KEY_TEMP_ROOT, Settings

    app = QApplication([])
    if app.platformName() != "windows":
        raise AssertionError("Native Windows Qt backend required for --ui")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="frozen-ui-", dir=output) as temporary:
        root = Path(temporary)
        settings = Settings.__new__(Settings)
        settings._store = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        settings.set_value(KEY_TEMP_ROOT, str(root / "sessions"))
        settings.set_value("appearance/theme_mode", "dark")
        window = MainWindow(settings=settings, theme=ThemeService())
        window.resize(1180, 720)
        window.show()
        source = root / "中文素材"
        (source / "目录" / "空目录").mkdir(parents=True)
        (source / "音效清单.txt").write_text("frozen asset verification", encoding="utf-8")
        archives = []
        for fmt in ("zip", "7z"):
            archive = root / f"中文加密.{fmt}"
            create_archive(archive, fmt, source, password="fixture-pass")
            archives.append(str(archive))
        tab = window.current_tab()
        with mock.patch.object(archive_loader.QInputDialog, "getText", return_value=("fixture-pass", True)):
            assert tab.source_panel.add_archives(archives) == 2
        assert all(source.password == "fixture-pass" for source in tab.session.sources.values())
        assert all(icon_name_for(node) == "folder_zip" for node in tab.session.root.children)
        tab.target_panel.view.expandAll()
        ratio = window.devicePixelRatioF()
        pixmap = icons().icon("folder_zip", 20, "#FFFFFF").pixmap(QSize(20, 20), ratio)
        assert abs(pixmap.deviceIndependentSize().width() - 20) <= 0.5
        result = {"platform": app.platformName(), "visible": window.isVisible(),
                  "native_id": int(window.winId()), "dpr": ratio, "logical_icon_size": 20}
        assert result["visible"] and result["native_id"]
        completed = []

        def finish():
            assert window.grab().save(str(output / f"light-{scale}.png"))
            completed.append(True)
            window.close()

        def light():
            assert window.grab().save(str(output / f"dark-{scale}.png"))
            window.theme.set_mode("light")
            QTimer.singleShot(100, finish)

        QTimer.singleShot(200, light)
        QTimer.singleShot(15000, app.quit)
        app.exec()
        assert completed, "Frozen UI probe did not complete"
        print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "dist/ZipMerge")
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--scale", default="1")
    parser.add_argument("--output", type=Path, default=ROOT / "build/frozen-verification")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    development_qt = Path(importlib.util.find_spec("PySide6").submodule_search_locations[0])
    loader = BundleLoader(bundle)
    for name in ("zipmerge.ui.archive_loader", "zipmerge.platform.native_drag"):
        assert name in loader.pyz.toc, f"Repair module missing: {name}"
    os.environ["QT_QPA_PLATFORM"] = "windows" if args.ui else "offscreen"
    os.environ["QT_SCALE_FACTOR"] = args.scale
    os.environ["QT_PLUGIN_PATH"] = str(loader.internal / "PySide6/plugins")
    handles = [os.add_dll_directory(str(directory)) for directory in
               (loader.internal, loader.internal / "PySide6", loader.internal / "shiboken6") if directory.is_dir()]
    sys.meta_path.insert(0, loader)
    sys.frozen = True
    sys._MEIPASS = str(loader.internal)
    sys.path.insert(0, str(ROOT))  # Test modules only; zipmerge cannot fall back to source.
    import zipmerge
    from PySide6 import QtCore
    expected = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/zipmerge/__init__.py").read_text(encoding="utf-8"))[1]
    assert zipmerge.__version__ == expected
    assert Path(QtCore.__file__).resolve().is_relative_to(loader.internal)
    if args.ui:
        verify_ui(args.output, args.scale)
    else:
        # QtTest is a development-only helper and is correctly excluded from
        # the shipping bundle. Load just that extension from the pinned build
        # environment; the application and its Qt bindings stay in the bundle.
        handles.append(ctypes.WinDLL(str(development_qt / "Qt6Test.dll")))
        test_spec = importlib.util.spec_from_file_location("PySide6.QtTest", development_qt / "QtTest.pyd")
        test_module = importlib.util.module_from_spec(test_spec)
        sys.modules["PySide6.QtTest"] = test_module
        test_spec.loader.exec_module(test_module)
        suite = unittest.defaultTestLoader.loadTestsFromNames([
            "tests.test_archive_password", "tests.test_native_drag", "tests.test_design",
            "tests.test_target_model", "tests.test_drag_export", "tests.test_archive_inspector",
            "tests.test_delivery_log", "tests.test_temporary_archive",
            "tests.test_batch_rename", "tests.test_batch_rename_ui",
            "tests.test_macos_cleanup",
            "tests.test_preview_volume",
            "tests.test_updates",
            "tests.test_language",
            "tests.test_ui.ExportBarTests",
        ])
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        if not result.wasSuccessful():
            return 1
    assert "zipmerge.ui.design.icons" in loader.loaded
    print(f"Verified frozen ZipMerge {zipmerge.__version__}; {len(loader.loaded)} embedded modules; Qt from {QtCore.__file__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
