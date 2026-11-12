"""Help, project information and readable release notes for the desktop app."""

from __future__ import annotations

import re

from PySide6.QtCore import QByteArray, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
    QTextBrowser, QVBoxLayout,
)

from .. import __version__
from ..core.updates import HOMEPAGE, VERSION, localized_notes
from ..i18n import tr
from ..util.paths import app_root
from .design.tokens import COMFORTABLE as M
from .language import current_language


def open_project_page(parent, url: str) -> None:
    if not QDesktopServices.openUrl(QUrl(url)):
        QMessageBox.information(parent, tr("无法打开浏览器"), url)


def _label(text, variant=None):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if variant:
        label.setProperty("variant", variant)
    return label


class InfoDialog(QDialog):
    def __init__(self, parent, title, width=580):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(min(width, 420))
        self.resize(width, self.height())
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(M.space_xl, M.space_xl, M.space_xl, M.space_lg)
        self.body.setSpacing(M.space_md)
        self.buttons = QDialogButtonBox(self)

    def finish(self):
        close = self.buttons.addButton(tr("关闭"), QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)
        close.setDefault(True)
        self.body.addWidget(self.buttons)


class AboutDialog(InfoDialog):
    def __init__(self, parent):
        super().__init__(parent, tr("关于 ZipMerge"), 500)
        heading = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(48, 48))
        heading.addWidget(logo)
        titles = QVBoxLayout()
        titles.addWidget(_label("ZipMerge", "title"))
        titles.addWidget(_label(tr("版本 {0} · 作者 MEI", __version__), "secondary"))
        heading.addLayout(titles, 1)
        self.body.addLayout(heading)
        self.body.addWidget(_label(tr("在一个窗口里整理多个压缩包，预览素材，再导出一个交付包。")))
        self.body.addWidget(_label(tr("如果 ZipMerge 帮你省下了时间，欢迎到 GitHub 给项目点个 Star。"), "secondary"))
        project = self.buttons.addButton(tr("GitHub 主页"), QDialogButtonBox.ButtonRole.ActionRole)
        project.clicked.connect(lambda: open_project_page(self, HOMEPAGE))
        self.finish()
        self.resize(500, self.sizeHint().height())


class HelpDialog(InfoDialog):
    def __init__(self, parent):
        super().__init__(parent, tr("使用帮助"))
        self.body.addWidget(_label(tr("从源包到交付包"), "title"))
        self.body.addWidget(_label(tr("以音效和语音为例：把两份素材包放进同一个会话，整理成 SFX 和 VO 两个目录，最后导出一个 ZIP。"), "secondary"))
        for title, description in (
            (tr("1. 添加素材"), tr("把 ZIP、7z 或 RAR 拖到窗口，或点击「添加压缩包」。左栏保留源包；散文件和文件夹可以直接拖入目标结构。")),
            (tr("2. 整理和预览"), tr("在「目标结构」里移动、重命名或删除条目，只会改变导出结构，不会改写源包。选中文件即可预览，音频和视频可按空格播放或暂停。")),
            (tr("3. 命名和导出"), tr("在底部选择输出目录、格式和命名字段，核对「文件名」后点击「导出」。想直接拖给同事，可先「生成临时包」，再从文件卡片拖出。")),
        ):
            group = QVBoxLayout()
            group.setSpacing(M.space_xs)
            group.addWidget(_label(title, "subtitle"))
            group.addWidget(_label(description))
            self.body.addLayout(group)
        self.finish()
        self.resize(580, self.sizeHint().height())


class NotesView(QTextBrowser):
    """Format release Markdown without following links or loading embedded resources."""
    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    def loadResource(self, resource_type, url):
        return QByteArray()

    def show_notes(self, notes: str):
        text = localized_notes(notes, current_language())
        if text:
            flags = QTextDocument.MarkdownFeature
            self.document().setMarkdown(text, flags.MarkdownDialectGitHub | flags.MarkdownNoHTML)
        else:
            self.setPlainText(tr("此版本没有提供更新说明。"))


class HistoryDialog(InfoDialog):
    def __init__(self, parent, previous=None):
        super().__init__(parent, tr("更新记录"), 620)
        self.resize(620, 440)
        self.body.addWidget(_label(tr("已更新至 ZipMerge {0}", __version__) if previous else
                                   tr("ZipMerge {0}", __version__), "title"))
        if previous:
            self.body.addWidget(_label(tr("上次版本：{0}", previous), "secondary"))
        row = QHBoxLayout()
        row.addWidget(_label(tr("查看版本")))
        self.versions = QComboBox()
        row.addWidget(self.versions)
        row.addStretch()
        self.body.addLayout(row)
        self.notes = NotesView()
        self.body.addWidget(self.notes, 1)
        folder = app_root() / "docs" / "releases"
        versions = {p.stem[1:] for p in folder.glob("v*.md") if re.fullmatch("v" + VERSION, p.stem)}
        self.versions.addItem(tr("当前版本 {0}", __version__), __version__)
        for version in sorted(versions - {__version__}, key=lambda v: tuple(map(int, v.split("."))), reverse=True):
            self.versions.addItem(version, version)

        def show_version():
            try:
                text = (folder / f"v{self.versions.currentData()}.md").read_text(encoding="utf-8")
            except OSError:
                text = ""
            self.notes.show_notes(text)
        self.versions.currentIndexChanged.connect(show_version)
        show_version()
        self.finish()


class UpdateOffer(InfoDialog):
    def __init__(self, parent, release, current):
        super().__init__(parent, tr("发现新版本"), 620)
        self.resize(620, 430)
        self.body.addWidget(_label(tr("ZipMerge {0} 已发布，当前版本为 {1}。", release.version, current), "subtitle"))
        self.body.addWidget(_label(tr("本次更新")))
        self.notes = NotesView()
        self.notes.show_notes(release.notes)
        self.body.addWidget(self.notes, 1)
        self.body.addWidget(_label(tr("更新将下载安装包，校验后打开安装向导。安装前请先导出需要保留的会话内容。")
                                   if release.installer else tr("此版本没有可校验的安装包，可以前往发布页面查看。"), "secondary"))
        self.choice = "later"
        for key, label in (("update", tr("下载并更新")), ("skip", tr("跳过此版本")),
                           ("page", tr("打开 GitHub Release")), ("later", tr("稍后"))):
            button = self.buttons.addButton(label, QDialogButtonBox.ButtonRole.ActionRole)
            button.clicked.connect(lambda checked=False, k=key: self._choose(k))
            if key == "update":
                button.setProperty("variant", "primary")
                button.setEnabled(release.installer is not None)
            button.setAutoDefault(False)
            if key == "later":
                button.setDefault(True)
        self.body.addWidget(self.buttons)

    def _choose(self, key):
        self.choice = key
        self.accept()
