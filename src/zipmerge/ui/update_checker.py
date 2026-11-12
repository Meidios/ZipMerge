"""Asynchronous GitHub update checks and user-initiated installer downloads."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from PySide6.QtCore import QIODevice, QObject, QSaveFile, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from zipmerge.i18n import tr
from .. import __version__
from ..core.updates import LATEST_API, MAX_METADATA, RELEASES, Release, available_release
from ..util.settings import KEY_UPDATE_AUTO, KEY_UPDATE_LAST, KEY_UPDATE_SKIPPED


class UpdateChecker(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.settings = window.settings
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self.closed = False
        self.check_action = None

    def add_menu(self, menu):
        self.check_action = menu.addAction(tr("检查更新…"))
        self.check_action.triggered.connect(lambda: self.check(manual=True))
        auto = menu.addAction(tr("启动时检查更新"))
        auto.setCheckable(True)
        auto.setChecked(self.settings.value(KEY_UPDATE_AUTO, True, bool))
        auto.toggled.connect(lambda enabled: self.settings.set_value(KEY_UPDATE_AUTO, enabled))

    def _get(self, url, limit, consume, done, *, asset=False):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", f"ZipMerge/{__version__}".encode("ascii"))
        request.setTransferTimeout(30000 if asset else 12000)
        policy = QNetworkRequest.RedirectPolicy
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             policy.UserVerifiedRedirectPolicy if asset else policy.SameOriginRedirectPolicy)
        reply = self.network.get(request)
        self.reply = reply
        self.check_action.setEnabled(False)
        reply.setReadBufferSize(256 * 1024)
        received = 0
        failure = ""

        def redirect(target):
            nonlocal failure
            if (target.scheme() == "https" and target.port(-1) in (-1, 443)
                    and not target.userInfo() and target.host() in
                    ("release-assets.githubusercontent.com", "objects.githubusercontent.com")):
                reply.redirectAllowed.emit()
            else:
                failure = tr("安装包重定向到了非预期的地址")
                reply.abort()

        def drain():
            nonlocal received, failure
            if failure or not reply.isOpen():
                return
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            while reply.bytesAvailable():
                block = bytes(reply.read(128 * 1024))
                if not block:
                    break
                if status != 200:
                    continue
                received += len(block)
                try:
                    if received > limit:
                        raise ValueError(tr("下载大小超出预期"))
                    consume(block)
                except (ValueError, OSError) as exc:
                    failure = str(exc)
                    if not reply.isFinished():
                        reply.abort()
                    return

        def finish():
            drain()
            cancelled = reply.error() == QNetworkReply.NetworkError.OperationCanceledError and not failure
            error = failure
            if not error and (reply.error() != QNetworkReply.NetworkError.NoError or
                              reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200):
                error = reply.errorString()
            self.reply = None
            reply.deleteLater()
            self.check_action.setEnabled(True)
            done(error, cancelled or self.closed)

        if asset:
            reply.redirected.connect(redirect)
        reply.readyRead.connect(drain)
        reply.finished.connect(finish)

    def check(self, *, manual=False):
        if self.closed or self.reply is not None:
            return
        now = int(time.time())
        if not manual:
            last = self.settings.value(KEY_UPDATE_LAST, 0, int)
            if not self.settings.value(KEY_UPDATE_AUTO, True, bool) or 0 <= now - last < 86400:
                return
        self.settings.set_value(KEY_UPDATE_LAST, now)
        if manual:
            self.window.statusBar().showMessage(tr("正在检查 GitHub 上的最新版本…"))
        data = bytearray()

        def checked(error, cancelled):
            if cancelled:
                return
            if manual:
                self.window.statusBar().clearMessage()
            try:
                if error:
                    raise ValueError(error)
                release = available_release(data, __version__)
            except (ValueError, RecursionError) as exc:
                if manual:
                    self._error(tr("无法检查更新：{0}", exc), RELEASES)
                return
            if release is None:
                if manual:
                    QMessageBox.information(self.window, tr("检查更新"), tr("当前版本 {0}，没有可用的新版本。", __version__))
                return
            if manual or (self.settings.value(KEY_UPDATE_AUTO, True, bool) and
                          self.settings.value(KEY_UPDATE_SKIPPED, "") != release.version):
                self._offer(release)

        self._get(LATEST_API, MAX_METADATA, data.extend, checked)

    def _offer(self, release: Release):
        box = QMessageBox(QMessageBox.Icon.Information, tr("发现新版本"),
                          tr("ZipMerge {0} 已发布，当前版本为 {1}。", release.version, __version__), parent=self.window)
        box.setInformativeText(tr("更新将下载安装包，校验后打开安装向导。安装前请先导出需要保留的会话内容。")
                               if release.installer else tr("此版本没有可校验的安装包，可以前往发布页面查看。"))
        update = box.addButton(tr("下载并更新"), QMessageBox.ButtonRole.AcceptRole)
        update.setEnabled(release.installer is not None)
        skip = box.addButton(tr("跳过此版本"), QMessageBox.ButtonRole.ActionRole)
        page = box.addButton(tr("打开 GitHub Release"), QMessageBox.ButtonRole.ActionRole)
        later = box.addButton(tr("稍后"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(later)
        box.setEscapeButton(later)
        box.exec()
        if self.closed:
            return
        choice = box.clickedButton()
        if choice is skip:
            self.settings.set_value(KEY_UPDATE_SKIPPED, release.version)
        elif choice is page:
            self._open_page(release.page)
        elif choice is update and release.installer:
            self._download(release)

    def _open_page(self, url):
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.information(self.window, tr("无法打开浏览器"), url)

    def _error(self, message, page):
        box = QMessageBox(QMessageBox.Icon.Warning, tr("ZipMerge 更新"), message, parent=self.window)
        box.setTextFormat(Qt.TextFormat.PlainText)
        browse = box.addButton(tr("打开 GitHub Release"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(tr("关闭"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is browse:
            self._open_page(page)

    def _download(self, release):
        asset = release.installer
        cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "updates"
        # Keep one verified installer outside session folders. A failed or
        # cancelled transfer never replaces it and is never offered for launch.
        path = cache / "ZipMerge-Setup.exe"
        out = QSaveFile(str(path))
        try:
            cache.mkdir(parents=True, exist_ok=True)
            if not out.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(out.errorString())
        except OSError as exc:
            self._error(tr("无法保存安装包：{0}", exc), release.page)
            return
        progress = QProgressDialog(tr("正在下载 ZipMerge {0}…", release.version), tr("取消下载"), 0, 100, self.window)
        progress.setWindowTitle(tr("下载更新"))
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        digest = hashlib.sha256()
        written = 0

        def write(block):
            nonlocal written
            if out.write(block) != len(block):
                raise OSError(out.errorString())
            digest.update(block)
            written += len(block)
            progress.setValue(written * 100 // asset.size)

        def finish(error, cancelled):
            progress.hide()
            progress.deleteLater()
            if not error and (written != asset.size or digest.hexdigest() != asset.sha256):
                error = tr("安装包不完整或 SHA-256 校验不一致，未启动安装。")
            if cancelled or error:
                out.cancelWriting()
                out.commit()
                if not cancelled:
                    self._error(error, release.page)
                return
            if not out.commit():
                self._error(out.errorString(), release.page)
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self._error(tr("已验证安装包，但无法启动安装向导：{0}", path), release.page)

        self._get(asset.url, asset.size, write, finish, asset=True)
        progress.canceled.connect(self.reply.abort)

    def shutdown(self):
        self.closed = True
        if self.reply is not None:
            self.reply.abort()
