"""Exercise release selection, prompt choices and the real Qt download pipeline."""
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QSettings, QStandardPaths, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QApplication, QMainWindow, QDialogButtonBox, QMessageBox, QProgressDialog

from zipmerge.core.updates import RELEASES, available_release, localized_notes
from zipmerge.ui import update_checker
from zipmerge.ui.update_checker import UpdateChecker
from zipmerge.util.settings import KEY_UPDATE_AUTO, KEY_UPDATE_SKIPPED, Settings

BODY = b"update fixture" * 20000


def metadata(version="0.1.11"):
    name = f"ZipMerge-Setup-{version}.exe"
    return {"tag_name": "v" + version, "draft": False, "prerelease": False,
            "assets": [{"name": name, "state": "uploaded",
                        "browser_download_url": f"{RELEASES}/download/v{version}/{name}",
                        "digest": "sha256:" + hashlib.sha256(BODY).hexdigest(), "size": len(BODY)}]}


def parse(info, current="0.1.10"):
    return available_release(json.dumps(info).encode(), current)


class ReleaseTests(unittest.TestCase):
    def test_release_notes_with_and_without_assets_or_translations(self):
        body = "## 简体中文\r\n修复预览。\r\n## English\r\nPreview fix."
        for assets in (metadata()["assets"], []):
            release = parse(dict(metadata(), body=body, assets=assets))
            self.assertEqual(localized_notes(release.notes, "zh_TW"), "修复预览。")
            self.assertEqual(localized_notes(release.notes, "en"), "Preview fix.")
        self.assertEqual(parse(dict(metadata(), body=None)).notes, "")
        self.assertEqual(localized_notes("Notes without headings", "zh_CN"), "Notes without headings")

    def test_only_newer_stable_numeric_versions(self):
        self.assertEqual(parse(metadata("0.1.10"), "0.1.9").version, "0.1.10")
        for version in ("0.1.9", "0.1.10"):
            self.assertIsNone(parse(metadata(version)))
        for flag in ("draft", "prerelease"):
            self.assertIsNone(parse(dict(metadata(), **{flag: True})))
        with self.assertRaises(ValueError):
            parse(metadata("../next"))
        with self.assertRaises(ValueError):
            available_release(b"<html>", "0.1.10")

    def test_incomplete_or_untrusted_asset_is_page_only(self):
        for key, bad in (("name", "Other.exe"), ("digest", None), ("size", -1),
                         ("state", "new"), ("browser_download_url", "https://example.com/a.exe")):
            info = metadata()
            info["html_url"] = "https://example.com/"
            info["assets"][0][key] = bad
            release = parse(info)
            self.assertIsNone(release.installer)
            self.assertEqual(release.page, RELEASES + "/tag/v0.1.11")


class UpdateUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.window = QMainWindow()
        self.window.settings = Settings("ZipMergeTest", "Updater")
        self.window.settings._store = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.u = UpdateChecker(self.window)
        self.u.add_menu(self.window.menuBar().addMenu("帮助"))
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.addCleanup(self.u.shutdown)
        self.window.show()
        self.app.processEvents()

    def click(self, label):
        def choose():
            box = self.app.activeModalWidget()
            button = next(b for b in box.findChild(QDialogButtonBox).buttons() if b.text() == label)
            button.click()
        QTimer.singleShot(0, choose)

    def test_skip_survives_restart_manual_check_and_next_version(self):
        info = metadata()
        def fetch(url, limit, consume, done):
            consume(json.dumps(info).encode())
            done("", False)
        with patch.object(self.u, "_get", side_effect=fetch) as get, patch.object(update_checker, "__version__", "0.1.10"):
            self.click("跳过此版本")
            self.u.check()
            self.assertEqual(self.window.settings.value(KEY_UPDATE_SKIPPED), "0.1.11")
            with patch.object(self.u, "_offer") as offer:
                self.u.startup()
                self.assertEqual(get.call_count, 2)
                offer.assert_not_called()
                self.u.check(manual=True)
                self.assertEqual(offer.call_args.args[0].version, "0.1.11")
                info = metadata("0.1.12")
                self.u.startup()
                self.assertEqual(offer.call_args.args[0].version, "0.1.12")
            self.window.settings.set_value(KEY_UPDATE_AUTO, False)
            prior = get.call_count
            self.u.check()
            self.assertEqual(get.call_count, prior)
            with patch.object(self.u, "_offer") as offer:
                self.u.check(manual=True)
                self.assertEqual(offer.call_args.args[0].version, "0.1.12")

    def test_new_release_between_launches_after_recent_or_failed_check(self):
        self.window.settings.set_value("updates/last_check", int(time.time()))
        responses = iter([("0.1.14", ""), ("0.1.14", "offline"), ("0.1.15", "")])

        def fetch(url, limit, consume, done):
            version, error = next(responses)
            consume(json.dumps(metadata(version)).encode())
            done(error, False)

        with (patch.object(update_checker, "__version__", "0.1.14"),
              patch.object(self.u, "_get", side_effect=fetch) as get,
              patch.object(self.u, "_offer") as offer):
            self.u.startup()
            self.u.startup()
            offer.assert_not_called()
            self.u.startup()
            self.assertEqual(get.call_count, 3)
            offer.assert_called_once()
            self.assertEqual(offer.call_args.args[0].version, "0.1.15")

    def test_page_update_and_dismiss_are_distinct(self):
        release = parse(metadata())
        with patch.object(self.u, "_download") as download, patch.object(self.u, "_open_page") as page:
            self.click("打开 GitHub Release")
            self.u._offer(release)
            page.assert_called_once_with(release.page)
            self.click("稍后")
            self.u._offer(release)
            download.assert_not_called()
            self.assertFalse(self.window.settings.contains(KEY_UPDATE_SKIPPED))
            self.click("下载并更新")
            self.u._offer(release)
            download.assert_called_once_with(release)

    def test_check_errors_and_no_update(self):
        def stub(error, body):
            def fetch(url, limit, consume, done):
                consume(body)
                done(error, False)
            return fetch
        with patch.object(self.u, "_error") as error, patch.object(QMessageBox, "information") as notice:
            with patch.object(self.u, "_get", side_effect=stub("offline", b"")):
                self.u.check()
                error.assert_not_called()
                self.u.check(manual=True)
                error.assert_called_once()
            with patch.object(self.u, "_get", side_effect=stub("", json.dumps(metadata("0.1.0")).encode())):
                self.u.check(manual=True)
                notice.assert_called_once()

    def test_history_only_after_successful_upgrade_even_when_checks_are_off(self):
        self.window.settings.set_value(KEY_UPDATE_AUTO, False)
        with patch.object(update_checker, "HistoryDialog") as history:
            with patch.object(update_checker, "__version__", "0.1.14"):
                self.u.startup()
                self.u.startup()
                history.assert_not_called()
            with patch.object(update_checker, "__version__", "0.1.15"):
                self.u.startup()
                self.u.startup()
                history.assert_called_once_with(self.window, "0.1.14")
            with patch.object(update_checker, "__version__", "0.1.13"):
                self.u.startup()
                self.assertEqual(history.call_count, 1)

    def test_transfer_integrity_and_cancel(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                try:
                    self.wfile.write(BODY)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        class LoopbackNetwork(QNetworkAccessManager):
            def get(self, request):
                local = QNetworkRequest(request)
                local.setUrl(QUrl(f"http://127.0.0.1:{server.server_port}/setup"))
                return super().get(local)

        self.u.network = LoopbackNetwork(self.u)
        path = self.root / "updates/ZipMerge-Setup.exe"
        with (patch.object(QStandardPaths, "writableLocation", return_value=str(self.root)),
              patch.object(update_checker.QDesktopServices, "openUrl", return_value=True) as launch,
              patch.object(self.u, "_error") as error):
            for case in ("ok", "wrong_digest", "truncated", "oversize", "cancel"):
                with self.subTest(case=case):
                    info = metadata()
                    if case == "wrong_digest":
                        info["assets"][0]["digest"] = "sha256:" + "0"*64
                    if case in ("truncated", "oversize"):
                        info["assets"][0]["size"] += 1 if case == "truncated" else -1
                    if case != "ok":
                        path.write_bytes(b"keep")
                    self.u._download(parse(info))
                    if case == "cancel":
                        self.window.findChildren(QProgressDialog)[-1].canceled.emit()
                    if self.u.reply is not None:
                        loop = QEventLoop()
                        self.u.reply.finished.connect(loop.quit)
                        timer = QTimer(loop)
                        timer.timeout.connect(loop.quit)
                        timer.start(3000)
                        loop.exec()
                    self.assertIsNone(self.u.reply)
                    self.assertEqual(path.read_bytes(), BODY if case == "ok" else b"keep")
                    self.assertEqual(launch.call_count, int(case == "ok"))
                    self.assertEqual(error.call_count, int(case not in ("ok", "cancel")))
                    self.assertEqual(list(path.parent.iterdir()), [path])
                    launch.reset_mock()
                    error.reset_mock()
