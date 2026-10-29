"""导出栏：命名模板字段、输出位置与格式，实时预览最终文件名。

对应 PLAN 6.6 的底部导出栏与 7.7 的命名模板。栏本身只负责收集选项并发出
:attr:`ExportBar.export_requested`，真正的导出执行由 :mod:`export_runner` 完成。
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    DEFAULT_TEMPLATE,
    TEMPLATE_FIELDS,
    ExportOptions,
    MergeSession,
    NamingFields,
    estimate_export_summary,
    render_filename,
    suggest_content_tag,
)
from ..util.settings import (
    DEFAULT_OUTPUT_DIR_AUTO,
    KEY_ACTIVE_TEMPLATE,
    KEY_NAMING_CONTENT,
    KEY_NAMING_DATE,
    KEY_NAMING_PROJECT,
    KEY_NAMING_REQUIREMENT,
    KEY_NAMING_TEMPLATE,
    KEY_NAMING_VERSION,
    KEY_OUTPUT_DIR,
    KEY_OUTPUT_DIR_AUTO,
    KEY_OUTPUT_FORMAT,
    KEY_OUTPUT_LEVEL,
    KEY_TEMPLATES,
    Settings,
)
from . import fmt
from .design import icon as design_icon
from .design.tokens import Palette, palette_for
from .target_model import ARCHIVE_EXT
from .temporary_archive import TemporaryArchiveCard

#: 输出格式：显示名 → 扩展名
FORMATS: tuple[tuple[str, str], ...] = (("zip", ".zip"), ("7z", ".7z"))

#: 压缩级别：级别 → 显示名（PLAN 7.6 默认标准 5）
LEVELS: tuple[tuple[int, str], ...] = ((1, "快速"), (5, "标准"), (9, "最大"))

DEFAULT_LEVEL = 5

#: 模板条目字段（JSON 存储结构，PLAN 6.6 新增 3）
TEMPLATE_KEYS: tuple[str, ...] = (
    "name", "template", "project", "requirement", "content", "date", "version"
)

#: 未套用任何模板时的下拉选项
CUSTOM_TEMPLATE_LABEL = "（自定义）"

#: 模板库为空时由旧 naming/* 键生成的兜底条目名
DEFAULT_TEMPLATE_NAME = "默认"


class ExportBar(QWidget):
    """底部导出栏。"""

    #: 参数为 :class:`~zipmerge.core.ExportOptions`
    export_requested = Signal(object)
    temporary_export_requested = Signal()

    def __init__(self, session: MergeSession, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self._settings = settings
        self.setObjectName("exportBar")
        self.setProperty("role", "panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 竖向固定：多余高度全部让给上方三栏，导出栏永远贴在底部且高度不变（PLAN 7.7 界面 2）
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._palette: Palette = palette_for("dark")
        self._temporary_card = TemporaryArchiveCard()
        self._estimated_size = 0
        self._estimated_file_count = 0
        self._output_password = ""

        self._template = str(settings.value(KEY_NAMING_TEMPLATE, DEFAULT_TEMPLATE) or DEFAULT_TEMPLATE)
        self._content_edited = False
        self._templates = self._load_templates()
        self._applying_template = False

        # ---- 第一行：输出位置与格式 ----
        self._dir_edit = QLineEdit(str(settings.value(KEY_OUTPUT_DIR, str(Path.home())) or Path.home()))
        self._dir_edit.setClearButtonEnabled(True)
        self._dir_edit.textEdited.connect(self._on_dir_text_edited)
        self._dir_edit.editingFinished.connect(self._on_dir_changed)
        self._browse_button = QToolButton()
        self._browse_button.setProperty("variant", "ghost")
        self._browse_button.setToolTip("选择输出文件夹")
        self._browse_button.clicked.connect(self._prompt_directory)

        self._format_combo = QComboBox()
        for value, suffix in FORMATS:
            self._format_combo.addItem(f"{value.upper()}（{suffix}）", value)
        self._select_data(self._format_combo, settings.value(KEY_OUTPUT_FORMAT, "zip"), "zip")
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)

        self._level_combo = QComboBox()
        for level, label in LEVELS:
            self._level_combo.addItem(f"{label}（{level}）", level)
        self._select_data(self._level_combo, settings.value(KEY_OUTPUT_LEVEL, DEFAULT_LEVEL, int), DEFAULT_LEVEL)
        self._level_combo.currentIndexChanged.connect(self._on_level_changed)

        self._password_button = QToolButton()
        self._password_button.setProperty("variant", "ghost")
        self._password_button.setText("密码…")
        self._password_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._password_button.setToolTip("设置仅在当前会话内保存的输出密码")
        self._password_button.clicked.connect(self._prompt_output_password)

        self._template_button = QToolButton()
        self._template_button.setProperty("variant", "ghost")
        self._template_button.setToolTip("编辑命名模板")
        self._template_button.clicked.connect(self.edit_template)

        self._template_combo = QComboBox()
        self._template_combo.setMinimumContentsLength(len(CUSTOM_TEMPLATE_LABEL) + 1)
        self._template_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._template_combo.setToolTip("切换命名模板")
        self._template_combo.currentIndexChanged.connect(self._on_template_selected)

        self._template_menu = QMenu(self)
        self._act_save_as_template = self._template_menu.addAction("另存为模板…")
        self._act_save_as_template.triggered.connect(self.prompt_save_template)
        self._act_save_to_template = self._template_menu.addAction("保存当前设置到模板")
        self._act_save_to_template.triggered.connect(self.save_current_to_template)
        self._act_rename_template = self._template_menu.addAction("重命名模板…")
        self._act_rename_template.triggered.connect(self.prompt_rename_template)
        self._act_delete_template = self._template_menu.addAction("删除模板")
        self._act_delete_template.triggered.connect(self.prompt_delete_template)

        self._template_menu_button = QToolButton()
        self._template_menu_button.setObjectName("templateMenuButton")
        self._template_menu_button.setProperty("variant", "ghost")
        self._template_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._template_menu_button.setMenu(self._template_menu)
        self._template_menu_button.setToolTip("模板管理")

        dir_label = QLabel("输出到")
        dir_label.setProperty("variant", "secondary")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(dir_label)
        top.addWidget(self._dir_edit, 1)
        top.addWidget(self._browse_button)
        top.addSpacing(8)
        top.addWidget(QLabel("格式"))
        top.addWidget(self._format_combo)
        top.addWidget(QLabel("级别"))
        top.addWidget(self._level_combo)
        top.addWidget(self._password_button)
        top.addWidget(self._template_button)

        # ---- 第二行：命名字段与预览 ----
        self._project_edit = self._field(KEY_NAMING_PROJECT, "项目名")
        self._requirement_edit = self._field(KEY_NAMING_REQUIREMENT, "需求名")
        self._content_edit = self._field(KEY_NAMING_CONTENT, "内容标识")
        self._date_edit = self._field(KEY_NAMING_DATE, "日期", NamingFields.today().date)
        self._version_edit = self._field(KEY_NAMING_VERSION, "版本", "V1")
        self._content_edit.textEdited.connect(self._on_content_edited)

        for edit in self._fields().values():
            edit.textChanged.connect(self._update_preview)
            edit.textChanged.connect(self._on_field_changed)

        self._preview_edit = QLineEdit()
        self._preview_edit.setReadOnly(True)
        self._preview_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        self._export_button = QPushButton("导出")
        self._export_button.setProperty("variant", "primary")
        self._export_button.setToolTip("按当前结构导出为单个压缩包")
        self._export_button.clicked.connect(self.request_export)

        self._temporary_button = QPushButton("生成临时包")
        self._temporary_button.setToolTip("生成到会话临时目录，完成后可直接拖拽发送")
        self._temporary_button.clicked.connect(self.request_temporary_export)

        preview_label = QLabel("文件名")
        preview_label.setProperty("variant", "secondary")

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)
        template_label = QLabel("模板")
        template_label.setProperty("variant", "secondary")
        bottom.addWidget(template_label)
        bottom.addWidget(self._template_combo)
        bottom.addWidget(self._template_menu_button)
        bottom.addSpacing(8)
        for edit in (self._project_edit, self._requirement_edit, self._content_edit,
                     self._date_edit, self._version_edit):
            bottom.addWidget(edit, 1)
        bottom.addWidget(preview_label)
        bottom.addWidget(self._preview_edit, 3)
        bottom.addWidget(self._temporary_button)
        bottom.addWidget(self._export_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        layout.addWidget(self._temporary_card)
        layout.addLayout(top)
        layout.addLayout(bottom)

        self.set_palette(self._palette)
        self._refresh_template_combo()
        self._update_template_combo_width()
        self.refresh_from_session()

    # ---- 对外 -------------------------------------------------------
    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._temporary_card.set_palette(palette)
        self._browse_button.setIcon(design_icon("folder_open", 16, palette.text_secondary))
        self._template_button.setIcon(design_icon("settings", 16, palette.text_secondary))
        self._password_button.setIcon(design_icon("key", 16, palette.text_secondary))
        self._template_menu_button.setIcon(design_icon("chevron_down", 16, palette.text_secondary))

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().changeEvent(event)
        if event.type() in {QEvent.Type.FontChange, QEvent.Type.StyleChange}:
            self._update_template_combo_width()

    def _update_template_combo_width(self) -> None:
        """为文字、左右内边距和下拉箭头预留空间，字体缩放后也不截断。"""
        if not hasattr(self, "_template_combo"):
            return
        text_width = self._template_combo.fontMetrics().horizontalAdvance(CUSTOM_TEMPLATE_LABEL)
        self._template_combo.setMinimumWidth(text_width + 48)

    def fields(self) -> NamingFields:
        return NamingFields(
            project=self._project_edit.text().strip(),
            requirement=self._requirement_edit.text().strip(),
            content=self._content_edit.text().strip(),
            date=self._date_edit.text().strip(),
            version=self._version_edit.text().strip(),
        )

    def output_directory(self) -> Path:
        text = self._dir_edit.text().strip()
        return Path(text) if text else Path.home()

    def base_name(self) -> str:
        """渲染后的文件名主体（不含扩展名）。"""
        return render_filename(self._template, self.fields())

    def target_path(self) -> Path:
        """当前期望的输出路径（不做重名递增）。"""
        return self.output_directory() / self.output_filename()

    def output_filename(self) -> str:
        """当前模板与格式渲染出的完整客户可见文件名。"""
        return f"{self.base_name()}{self._suffix()}"

    def build_options(self, output_path: str | Path | None = None) -> ExportOptions:
        return ExportOptions(
            output_path=Path(output_path) if output_path is not None else self.target_path(),
            fmt=str(self._format_combo.currentData()),
            level=int(self._level_combo.currentData()),
            password=self._output_password or None,
            stage_dir=self.session.stage_dir,
        )

    def set_temporary_archive(self, path: str | Path) -> None:
        self._temporary_card.set_archive(path, password=self._output_password or None)
        self._temporary_button.setText("重新生成临时包")

    def mark_temporary_archive_stale(self) -> None:
        self._temporary_card.mark_stale()

    def clear_temporary_archive(self) -> None:
        self._temporary_card.clear_archive()
        self._temporary_button.setText("生成临时包")

    def refresh_from_session(self, *, invalidate_temporary: bool = False) -> None:
        """源包或目标结构变化后刷新内容标识建议与体积估算。"""
        if invalidate_temporary:
            self.mark_temporary_archive_stale()
        if not self._content_edited and not self._content_edit.text().strip():
            suggestion = suggest_content_tag(self.session.source_names)
            if suggestion:
                self._content_edit.setText(suggestion)
        self._estimated_file_count, _dirs, self._estimated_size = estimate_export_summary(
            self.session.root, self.session.sources
        )
        self._apply_auto_output_dir()
        self._update_preview()

    def reload_settings(self) -> None:
        """设置页改动后重读输出目录 / 格式 / 级别 / 命名模板与五个命名字段。

        已打开的标签页在设置对话框确认后必须走这里，否则导出栏会继续沿用旧值
        （PLAN 6.6 问题 3）。
        """
        template = str(self._settings.value(KEY_NAMING_TEMPLATE, DEFAULT_TEMPLATE) or DEFAULT_TEMPLATE)
        if template != self._template:
            self.mark_temporary_archive_stale()
        self._template = template
        dir_value = str(self._settings.value(KEY_OUTPUT_DIR, "") or "").strip()
        if dir_value:
            self._dir_edit.setText(dir_value)
        self._select_data(self._format_combo, self._settings.value(KEY_OUTPUT_FORMAT, "zip"), "zip")
        self._select_data(
            self._level_combo,
            self._settings.value(KEY_OUTPUT_LEVEL, DEFAULT_LEVEL, int),
            DEFAULT_LEVEL,
        )
        values = self._settings_fields()
        for name, edit in self._fields().items():
            if edit.text() != values[name]:
                edit.setText(values[name])
        self._templates = self._load_templates()
        self._refresh_template_combo()
        self._apply_auto_output_dir()
        self._update_preview()

    def edit_template(self) -> None:
        hint = "可用字段：" + " ".join(f"{{{name}}}" for name in TEMPLATE_FIELDS)
        text, ok = QInputDialog.getText(
            self, "命名模板", f"模板（{hint}）：", QLineEdit.EchoMode.Normal, self._template
        )
        if not ok:
            return
        template = text.strip() or DEFAULT_TEMPLATE
        changed = template != self._template
        self._template = template
        self._settings.set_value(KEY_NAMING_TEMPLATE, self._template)
        self._mark_custom()
        if changed:
            self.mark_temporary_archive_stale()
        self._update_preview()

    # ---- 模板库（PLAN 6.6 新增 3）-------------------------------------
    def apply_template(self, name: str) -> bool:
        """套用名为 ``name`` 的模板：命名模板串 + 五个命名字段一起替换。"""
        entry = next((item for item in self._templates if item["name"] == name), None)
        if entry is None:
            return False
        changed = self._template != str(entry.get("template") or DEFAULT_TEMPLATE)
        changed = changed or any(
            edit.text() != str(entry.get(key) or "") for key, edit in self._fields().items()
        )
        if changed:
            self.mark_temporary_archive_stale()
        self._applying_template = True
        try:
            self._template = str(entry.get("template") or DEFAULT_TEMPLATE)
            self._settings.set_value(KEY_NAMING_TEMPLATE, self._template)
            values = {key: str(entry.get(key) or "") for key in TEMPLATE_KEYS if key != "name"}
            for key, edit in self._fields().items():
                edit.setText(values.get(key, ""))
            self._settings.set_value(KEY_ACTIVE_TEMPLATE, name)
            index = self._template_combo.findData(name)
            if index >= 0:
                self._template_combo.setCurrentIndex(index)
        finally:
            self._applying_template = False
        self._update_preview()
        return True

    def save_template(self, name: str) -> bool:
        """把当前命名设置保存为模板 ``name``（同名覆盖）。"""
        cleaned = name.strip()
        if not cleaned:
            return False
        entry = self.current_template_entry(cleaned)
        for index, item in enumerate(self._templates):
            if item["name"] == cleaned:
                self._templates[index] = entry
                break
        else:
            self._templates.append(entry)
        self._settings.set_value(KEY_ACTIVE_TEMPLATE, cleaned)
        self._persist_templates()
        self._refresh_template_combo()
        return True

    def current_template_entry(self, name: str) -> dict:
        """当前命名设置快照，结构即 JSON 存储结构。"""
        fields = self.fields()
        return {
            "name": name.strip(),
            "template": self._template,
            "project": fields.project,
            "requirement": fields.requirement,
            "content": fields.content,
            "date": fields.date,
            "version": fields.version,
        }

    def rename_template(self, old: str, new: str) -> bool:
        cleaned = new.strip()
        if not cleaned or cleaned == old:
            return False
        if any(item["name"] == cleaned for item in self._templates):
            return False
        entry = next((item for item in self._templates if item["name"] == old), None)
        if entry is None:
            return False
        entry["name"] = cleaned
        if str(self._settings.value(KEY_ACTIVE_TEMPLATE, "") or "") == old:
            self._settings.set_value(KEY_ACTIVE_TEMPLATE, cleaned)
        self._persist_templates()
        self._refresh_template_combo()
        return True

    def delete_template(self, name: str) -> bool:
        remaining = [item for item in self._templates if item["name"] != name]
        if len(remaining) == len(self._templates):
            return False
        self._templates = remaining or [self._default_template_entry()]
        if str(self._settings.value(KEY_ACTIVE_TEMPLATE, "") or "") == name:
            self._settings.set_value(KEY_ACTIVE_TEMPLATE, "")
        self._persist_templates()
        self._refresh_template_combo()
        return True

    def save_current_to_template(self) -> None:
        name = self._template_combo.currentData()
        if name is None:
            self.prompt_save_template()
            return
        self.save_template(str(name))

    def prompt_save_template(self) -> None:
        current = str(self._template_combo.currentData() or "")
        name, ok = QInputDialog.getText(
            self, "另存为模板", "模板名称：", QLineEdit.EchoMode.Normal, current
        )
        if ok:
            self.save_template(name)

    def prompt_rename_template(self) -> None:
        current = str(self._template_combo.currentData() or "")
        if not current:
            return
        name, ok = QInputDialog.getText(
            self, "重命名模板", "新名称：", QLineEdit.EchoMode.Normal, current
        )
        if ok and not self.rename_template(current, name):
            QMessageBox.warning(self, "重命名模板", "模板名为空或已被其他模板占用。")

    def prompt_delete_template(self) -> None:
        current = str(self._template_combo.currentData() or "")
        if not current:
            return
        answer = QMessageBox.question(self, "删除模板", f"确定删除模板「{current}」？")
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_template(current)

    # ---- 内部：模板 --------------------------------------------------
    def _on_template_selected(self, index: int) -> None:
        if self._applying_template:
            return
        name = self._template_combo.itemData(index)
        if name is None:
            self._settings.set_value(KEY_ACTIVE_TEMPLATE, "")
            return
        self.apply_template(str(name))

    def _on_field_changed(self) -> None:
        """命名字段被改动 ⇒ 当前内容已不再等于所选模板，退回「（自定义）」。"""
        self.mark_temporary_archive_stale()
        if not self._applying_template:
            self._mark_custom()

    def _mark_custom(self) -> None:
        if self._template_combo.currentData() is None:
            return
        self._applying_template = True
        try:
            self._template_combo.setCurrentIndex(0)
            self._settings.set_value(KEY_ACTIVE_TEMPLATE, "")
        finally:
            self._applying_template = False

    def _settings_fields(self) -> dict[str, str]:
        return {
            "project": str(self._settings.value(KEY_NAMING_PROJECT, "") or ""),
            "requirement": str(self._settings.value(KEY_NAMING_REQUIREMENT, "") or ""),
            "content": str(self._settings.value(KEY_NAMING_CONTENT, "") or ""),
            "date": str(self._settings.value(KEY_NAMING_DATE, "") or ""),
            "version": str(self._settings.value(KEY_NAMING_VERSION, "") or ""),
        }

    def _default_template_entry(self) -> dict:
        """模板库为空时用旧的 naming/* 键造一条「默认」条目，保证往来兼容。"""
        entry = self._settings_fields()
        entry["name"] = DEFAULT_TEMPLATE_NAME
        entry["template"] = str(
            self._settings.value(KEY_NAMING_TEMPLATE, DEFAULT_TEMPLATE) or DEFAULT_TEMPLATE
        )
        return {key: str(entry.get(key, "") or "") for key in TEMPLATE_KEYS}

    def _load_templates(self) -> list[dict]:
        raw = self._settings.value(KEY_TEMPLATES, "")
        entries: list[dict] = []
        if raw:
            try:
                parsed = json.loads(str(raw))
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and str(item.get("name") or "").strip():
                        entries.append(
                            {key: str(item.get(key, "") or "") for key in TEMPLATE_KEYS}
                        )
        return entries or [self._default_template_entry()]

    def _persist_templates(self) -> None:
        self._settings.set_value(
            KEY_TEMPLATES, json.dumps(self._templates, ensure_ascii=False)
        )

    def _refresh_template_combo(self) -> None:
        active = str(self._settings.value(KEY_ACTIVE_TEMPLATE, "") or "")
        self._applying_template = True
        try:
            self._template_combo.clear()
            self._template_combo.addItem(CUSTOM_TEMPLATE_LABEL, None)
            for entry in self._templates:
                self._template_combo.addItem(entry["name"], entry["name"])
            index = self._template_combo.findData(active) if active else 0
            self._template_combo.setCurrentIndex(max(0, index))
        finally:
            self._applying_template = False

    # ---- 内部 -------------------------------------------------------
    def _field(self, key: str, placeholder: str, default: str = "") -> QLineEdit:
        edit = QLineEdit(str(self._settings.value(key, default) or default))
        edit.setPlaceholderText(placeholder)
        edit.setClearButtonEnabled(True)
        edit.textChanged.connect(lambda _text, k=key, e=edit: self._settings.set_value(k, e.text()))
        return edit

    def _fields(self) -> dict[str, QLineEdit]:
        return {
            "project": self._project_edit,
            "requirement": self._requirement_edit,
            "content": self._content_edit,
            "date": self._date_edit,
            "version": self._version_edit,
        }

    def _suffix(self) -> str:
        fmt_value = str(self._format_combo.currentData())
        for value, suffix in FORMATS:
            if value == fmt_value:
                return suffix
        return ".zip"

    @staticmethod
    def _select_data(combo: QComboBox, want, fallback) -> None:
        index = combo.findData(want)
        if index < 0:
            index = combo.findData(fallback)
        combo.setCurrentIndex(max(0, index))

    def _on_dir_text_edited(self, _text: str) -> None:
        """用户手动键入输出目录 ⇒ 关闭「跟随源包目录」的自动行为。"""
        self._settings.set_value(KEY_OUTPUT_DIR_AUTO, False)

    def _on_dir_changed(self) -> None:
        self._settings.set_value(KEY_OUTPUT_DIR, self._dir_edit.text().strip())
        self._update_preview()

    def _output_dir_auto(self) -> bool:
        value = self._settings.value(KEY_OUTPUT_DIR_AUTO, DEFAULT_OUTPUT_DIR_AUTO)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _apply_auto_output_dir(self) -> None:
        """输出目录默认跟随首个源包所在目录；用户指定过之后不再覆盖。"""
        if not self._output_dir_auto() or not self.session.sources:
            return
        first = next(iter(self.session.sources.values()))
        self._dir_edit.setText(str(Path(first.archive_path).parent))

    def _on_format_changed(self) -> None:
        self._settings.set_value(KEY_OUTPUT_FORMAT, str(self._format_combo.currentData()))
        self.mark_temporary_archive_stale()
        self._update_preview()

    def _on_level_changed(self) -> None:
        self._settings.set_value(KEY_OUTPUT_LEVEL, int(self._level_combo.currentData()))
        self.mark_temporary_archive_stale()

    def _on_content_edited(self, _text: str) -> None:
        self._content_edited = True

    def _prompt_directory(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择输出文件夹", str(self.output_directory()))
        if chosen:
            self._dir_edit.setText(chosen)
            self._settings.set_value(KEY_OUTPUT_DIR_AUTO, False)
            self._on_dir_changed()

    def _prompt_output_password(self) -> None:
        password, ok = QInputDialog.getText(
            self,
            "设置输出密码",
            "输入密码；留空可关闭输出加密：",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return
        if password:
            confirm, confirmed = QInputDialog.getText(
                self,
                "确认输出密码",
                "再次输入密码：",
                QLineEdit.EchoMode.Password,
            )
            if not confirmed:
                return
            if confirm != password:
                QMessageBox.warning(self, "密码不一致", "两次输入的输出密码不一致。")
                return
        self._output_password = password
        self._password_button.setText("已加密" if password else "密码…")
        self._password_button.setToolTip(
            "输出密码已启用；点击可更改或清除"
            if password else "设置仅在当前会话内保存的输出密码"
        )
        self.mark_temporary_archive_stale()

    def _update_preview(self) -> None:
        name = self.output_filename()
        self._preview_edit.setText(name)
        target = self.target_path()
        pieces = [
            f"{fmt.count_label(self._estimated_file_count, '个文件')}"
            f" · 约 {fmt.human_size(self._estimated_size)}"
        ]
        if name and target.exists():
            pieces.append("已存在同名文件，导出时会要求确认")
            self._preview_edit.setProperty("variant", "warning")
        else:
            self._preview_edit.setProperty("variant", "")
        self._repolish(self._preview_edit)
        self._preview_edit.setToolTip(str(target) + ("\n" + " · ".join(pieces) if pieces else ""))

    def request_export(self) -> None:
        """校验当前输入并发起导出；菜单 / 快捷键也走这里。"""
        if not self.session.has_content:
            QMessageBox.information(self, "还不能导出", "目标结构还是空的，先从源包拖入要交付的内容。")
            return
        directory = self.output_directory()
        if not str(directory).strip():
            QMessageBox.warning(self, "输出目录无效", "请先选择输出文件夹。")
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "输出目录不可用", f"无法创建输出文件夹：\n{exc}")
            return
        self.export_requested.emit(self.build_options())

    def request_temporary_export(self) -> None:
        """临时导出只校验内容，不读取或创建普通输出目录。"""
        if not self.session.has_content:
            QMessageBox.information(self, "还不能生成", "目标结构还是空的，先从源包拖入要交付的内容。")
            return
        self.temporary_export_requested.emit()

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
