"""安装后一次性系统集成引导。"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from ..platform.shell_integration import ShellIntegration
from ..util.settings import (
    DEFAULT_INTEGRATION_ADD_TO_SESSION,
    DEFAULT_INTEGRATION_EXTRACT_HERE,
    DEFAULT_INTEGRATION_OPEN_WITH,
    KEY_INTEGRATION_ADD_TO_SESSION,
    KEY_INTEGRATION_EXTRACT_HERE,
    KEY_INTEGRATION_OPEN_WITH,
    KEY_POST_INSTALL_PROMPT_PENDING,
    Settings,
)


def run_post_install_integration_prompt(
    parent,
    settings: Settings,
    integration: ShellIntegration | None = None,
) -> bool:
    """完成安装后的两步引导；没有待处理标记时保持静默。"""
    if not settings.value(KEY_POST_INSTALL_PROMPT_PENDING, False, type=bool):
        return False

    integration = integration or ShellIntegration(settings)
    answer = QMessageBox.question(
        parent,
        "启用压缩格式关联",
        "是否让 ZipMerge 关联全部受支持的压缩格式？\n\n"
        "启用后，ZipMerge 会出现在 ZIP、7Z、RAR 等格式的“打开方式”中。"
        "这一步不会替换当前默认压缩软件。",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    candidates = integration.candidate_extensions()
    extensions = candidates if answer == QMessageBox.StandardButton.Yes else ()
    menus = dict(
        add_to_session=settings.value(
            KEY_INTEGRATION_ADD_TO_SESSION,
            DEFAULT_INTEGRATION_ADD_TO_SESSION,
            type=bool,
        ),
        open_with=settings.value(
            KEY_INTEGRATION_OPEN_WITH,
            DEFAULT_INTEGRATION_OPEN_WITH,
            type=bool,
        ),
        extract_here=settings.value(
            KEY_INTEGRATION_EXTRACT_HERE,
            DEFAULT_INTEGRATION_EXTRACT_HERE,
            type=bool,
        ),
    )
    integration.apply(extensions, **menus)

    default_answer = QMessageBox.question(
        parent,
        "设为默认压缩软件",
        "是否将 ZipMerge 设为默认压缩软件？\n\n"
        "Windows 不允许应用静默修改默认程序。选择“是”后将打开系统“默认应用”页面，"
        "请在 ZipMerge 中确认需要设为默认的格式。若尚未关联，将先完成候选登记。",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if default_answer == QMessageBox.StandardButton.Yes:
        if not extensions:
            integration.apply(candidates, **menus)
        QDesktopServices.openUrl(QUrl("ms-settings:defaultapps"))

    settings.set_value(KEY_POST_INSTALL_PROMPT_PENDING, False)
    settings.sync()
    return True
