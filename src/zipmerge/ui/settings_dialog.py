"""设置对话框：五组设置的统一编辑入口（PLAN 7.8）。

分组：系统集成 / 临时目录 / 常规 / 预览 / 密码。对话框只收集输入，点「确定」后由
SettingsDialog.apply 一次性落盘并同步注册表；取消不留半套修改。

视觉全部走设计令牌（QSS 已覆盖 QDialog / QGroupBox / QTabWidget / QCheckBox /
QSpinBox / QComboBox），本文件不硬编码颜色与圆角，间距取自当前度量。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..platform.shell_integration import ShellStatus, ShellIntegration
from ..util.paths import ARCHIVE_SUFFIXES, normalize_suffix
from ..util.settings import (
    DEFAULT_CACHE_LIMIT,
    DEFAULT_CLEAN_ON_CLOSE,
    DEFAULT_IMAGE_PIXELS,
    DEFAULT_TEXT_LIMIT,
    DEFAULT_VIDEO_AUTO_LIMIT,
    KEY_ACCENT,
    KEY_DENSITY,
    KEY_FONT_SCALE,
    KEY_INTEGRATION_ADD_TO_SESSION,
    KEY_INTEGRATION_OPEN_WITH,
    KEY_LAYOUT,
    KEY_OUTPUT_DIR,
    KEY_OUTPUT_DIR_AUTO,
    KEY_OUTPUT_FORMAT,
    KEY_OUTPUT_LEVEL,
    KEY_PREVIEW_CACHE_LIMIT,
    KEY_PREVIEW_IMAGE_PIXELS,
    KEY_PREVIEW_TEXT_LIMIT,
    KEY_PREVIEW_VIDEO_AUTO_LIMIT,
    KEY_REMEMBER_PASSWORD,
    KEY_TEMP_CLEAN_ON_CLOSE,
    KEY_TEMP_ROOT,
    KEY_THEME_MODE,
    Settings,
)
from ..util.tempdir import clear_dir, default_root, dir_size
from . import fmt
from .design import (
    DENSITIES,
    FONT_SCALE_MAX,
    FONT_SCALE_MIN,
    FONT_SCALE_STEP,
    THEME_MODES,
    ThemeService,
    icon as design_icon,
    ui_family,
)
from .export_bar import DEFAULT_LEVEL, FORMATS, LEVELS
from .main_window import LAYOUTS, LAYOUT_LABELS, MODE_LABELS

#: 界面密度的显示名
_DENSITY_LABELS = {"comfortable": "舒适", "compact": "紧凑"}

#: 关联格式勾选清单的列数
_FORMAT_COLUMNS = 4

#: 示例文件的扩展名兜底（没有勾选任何格式时）
_FALLBACK_SUFFIX = ".zip"


class SettingsDialog(QDialog):
    """五组设置对话框；只有 apply 方法会写入设置与注册表。"""

    def __init__(
        self,
        settings: Settings,
        theme: ThemeService,
        *,
        integration: ShellIntegration | None = None,
        temp_root: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.theme = theme
        self.integration = integration or ShellIntegration(settings)
        self._m = theme.metrics()
        self._temp_root = Path(temp_root) if temp_root else self._read_temp_root()

        self.setWindowTitle("设置")
        self.setModal(True)
        self.setStyleSheet(theme.stylesheet())

        status = self.integration.status()
        self._registered: set[str] = set(status.extensions)
        self._custom_exts: list[str] = list(self.integration.custom_suffixes())
        self._initial_customs: list[str] = list(self._custom_exts)
        self._checks: dict[str, QCheckBox] = {}
        self._accent_value: str = "" if theme.accent_is_system else theme.accent

        self._tabs = QTabWidget(self)
        self._tabs.addTab(self._build_integration_page(status), "系统集成")
        self._tabs.addTab(self._build_temp_page(), "临时目录")
        self._tabs.addTab(self._build_general_page(), "常规")
        self._tabs.addTab(self._build_preview_page(), "预览")
        self._tabs.addTab(self._build_password_page(), "密码")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setProperty("variant", "primary")
        ok_button.setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._m.space_md, self._m.space_md, self._m.space_md, self._m.space_md)
        layout.setSpacing(self._m.space_sm)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(buttons)

    # ---- 系统集成 ---------------------------------------------------
    def _build_integration_page(self, status: ShellStatus) -> QWidget:
        page, layout = self._page()

        self._status_label = QLabel(self._status_text())
        self._status_label.setProperty("variant", "secondary")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        formats = QGroupBox("关联格式")
        self._format_grid = QGridLayout(formats)
        self._format_grid.setSpacing(self._m.space_sm)
        layout.addWidget(formats)
        self._render_format_checks(set(status.extensions))

        custom = QGroupBox("自定义后缀")
        custom_layout = QVBoxLayout(custom)
        custom_layout.setSpacing(self._m.space_sm)
        row = QHBoxLayout()
        row.setSpacing(self._m.space_sm)
        self._custom_edit = QLineEdit()
        self._custom_edit.setPlaceholderText("例如 .psarc")
        self._custom_edit.returnPressed.connect(self._add_custom)
        add_button = QPushButton("添加")
        add_button.clicked.connect(self._add_custom)
        row.addWidget(self._custom_edit, 1)
        row.addWidget(add_button)
        custom_layout.addLayout(row)
        self._custom_list = QListWidget()
        self._custom_list.setMaximumHeight(self._m.control_height * 4)
        for ext in self._custom_exts:
            self._custom_list.addItem(ext)
        custom_layout.addWidget(self._custom_list)
        remove_button = QPushButton("移除选中")
        remove_button.clicked.connect(self._remove_custom)
        custom_layout.addWidget(remove_button)
        layout.addWidget(custom)

        menu = QGroupBox("右键菜单")
        menu_layout = QVBoxLayout(menu)
        menu_layout.setSpacing(self._m.space_sm)
        self._add_session_check = QCheckBox("添加到当前合并会话（所有文件）")
        self._add_session_check.setChecked(bool(status.add_to_session))
        self._open_with_check = QCheckBox("用 ZipMerge 打开（受支持格式）")
        self._open_with_check.setChecked(bool(status.open_with))
        self._extract_here_check = QCheckBox("解压到此处（受支持格式）")
        self._extract_here_check.setChecked(bool(status.extract_here))
        menu_layout.addWidget(self._add_session_check)
        menu_layout.addWidget(self._open_with_check)
        menu_layout.addWidget(self._extract_here_check)
        layout.addWidget(menu)

        default_app = QGroupBox("默认打开方式")
        default_layout = QVBoxLayout(default_app)
        default_layout.setSpacing(self._m.space_sm)
        hint = QLabel("系统不允许程序静默抢占默认程序，需要在这里手动确认一次。")
        hint.setProperty("variant", "secondary")
        hint.setWordWrap(True)
        default_layout.addWidget(hint)
        open_as_button = QPushButton("打开「打开方式」对话框…")
        open_as_button.clicked.connect(self._open_as_dialog)
        settings_button = QPushButton("打开系统「默认应用」设置…")
        settings_button.clicked.connect(self._open_default_apps)
        default_layout.addWidget(open_as_button)
        default_layout.addWidget(settings_button)
        layout.addWidget(default_app)

        undo_button = QPushButton("一键撤销全部系统集成")
        undo_button.setProperty("variant", "danger")
        undo_button.clicked.connect(self._unregister_all)
        layout.addWidget(undo_button)
        layout.addStretch(1)
        return page

    def _candidate_extensions(self) -> list[str]:
        return sorted(set(ARCHIVE_SUFFIXES) | set(self._custom_exts))

    def _checked_exts(self) -> set[str]:
        return {ext for ext, box in self._checks.items() if box.isChecked()}

    def _render_format_checks(self, checked: set[str]) -> None:
        while self._format_grid.count():
            item = self._format_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._checks = {}
        for index, ext in enumerate(self._candidate_extensions()):
            box = QCheckBox(ext)
            box.setChecked(ext in checked)
            self._checks[ext] = box
            self._format_grid.addWidget(box, index // _FORMAT_COLUMNS, index % _FORMAT_COLUMNS)

    def _rebuild_format_checks(self) -> None:
        checked = self._checked_exts() if self._checks else set(self._registered)
        self._render_format_checks(checked)

    def _add_custom(self) -> None:
        suffix = normalize_suffix(self._custom_edit.text())
        if not suffix:
            QMessageBox.warning(self, "后缀无效", "请输入形如 .psarc 的扩展名。")
            return
        self._custom_edit.clear()
        if suffix in ARCHIVE_SUFFIXES:
            self._status_label.setText(f"{suffix} 属于内置格式，无需自定义。")
            return
        if suffix not in self._custom_exts:
            self._custom_exts.append(suffix)
            self._custom_list.addItem(QListWidgetItem(suffix))
            self._rebuild_format_checks()

    def _remove_custom(self) -> None:
        item = self._custom_list.currentItem()
        if item is None:
            return
        suffix = item.text()
        self._custom_list.takeItem(self._custom_list.row(item))
        if suffix in self._custom_exts:
            self._custom_exts.remove(suffix)
        self._rebuild_format_checks()

    def _open_as_dialog(self) -> None:
        sample = self._make_sample_file()
        if sample is None:
            QMessageBox.warning(self, "无法创建示例文件", "临时目录不可写，请检查后重试。")
            return
        QProcess.startDetached("rundll32.exe", ["shell32.dll,OpenAs_RunDLL", str(sample)])

    def _open_default_apps(self) -> None:
        QDesktopServices.openUrl(QUrl("ms-settings:defaultapps"))

    def _unregister_all(self) -> None:
        answer = QMessageBox.question(
            self,
            "撤销系统集成",
            "将移除全部文件关联、右键菜单与「默认应用」登记，确定继续吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.integration.unregister()
        self._registered = set()
        self._render_format_checks(set())
        self._add_session_check.setChecked(False)
        self._open_with_check.setChecked(False)
        self._extract_here_check.setChecked(False)
        self._status_label.setText(self._status_text())

    def _make_sample_file(self) -> Path | None:
        checked = sorted(self._checked_exts())
        suffix = checked[0] if checked else _FALLBACK_SUFFIX
        folder = Path(tempfile.gettempdir()) / "ZipMerge"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            sample = folder / f"ZipMerge 示例{suffix}"
            if not sample.exists():
                sample.write_bytes(b"")
        except OSError:
            return None
        return sample

    def _status_text(self) -> str:
        status = self.integration.status()
        if not status.registered:
            return "当前尚未注册系统集成。"
        pieces = ["已关联 " + "、".join(status.extensions) if status.extensions else "未关联任何格式"]
        pieces.append("右键「添加到当前合并会话」已开启" if status.add_to_session
                      else "右键「添加到当前合并会话」已关闭")
        pieces.append("右键「用 ZipMerge 打开」已开启" if status.open_with
                      else "右键「用 ZipMerge 打开」已关闭")
        pieces.append("右键「解压到此处」已开启" if status.extract_here
                      else "右键「解压到此处」已关闭")
        pieces.append("已登记到系统「默认应用」" if status.capabilities
                      else "未登记到系统「默认应用」")
        return "；".join(pieces) + "。"

    # ---- 临时目录 ---------------------------------------------------
    def _build_temp_page(self) -> QWidget:
        page, layout = self._page()

        location = QGroupBox("位置")
        form = QFormLayout(location)
        form.setSpacing(self._m.space_sm)
        self._temp_edit = QLineEdit(str(self._temp_root))
        browse = QToolButton()
        browse.setProperty("variant", "ghost")
        browse.setToolTip("选择临时目录")
        browse.setIcon(design_icon("folder_open", self._m.icon_sm, self.theme.palette().text_secondary))
        browse.clicked.connect(self._prompt_temp_root)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(self._m.space_sm)
        row_layout.addWidget(self._temp_edit, 1)
        row_layout.addWidget(browse)
        form.addRow("临时目录", row)
        layout.addWidget(location)

        usage = QGroupBox("占用")
        usage_layout = QHBoxLayout(usage)
        usage_layout.setSpacing(self._m.space_sm)
        self._usage_label = QLabel("")
        self._usage_label.setProperty("variant", "secondary")
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self._refresh_usage)
        clear = QPushButton("清空临时文件")
        clear.clicked.connect(self._clear_temp)
        usage_layout.addWidget(self._usage_label, 1)
        usage_layout.addWidget(refresh)
        usage_layout.addWidget(clear)
        layout.addWidget(usage)

        self._clean_check = QCheckBox("关闭标签页时清理该会话的临时文件")
        self._clean_check.setChecked(self._read_bool(KEY_TEMP_CLEAN_ON_CLOSE, DEFAULT_CLEAN_ON_CLOSE))
        layout.addWidget(self._clean_check)

        layout.addStretch(1)
        self._refresh_usage()
        return page

    def refresh_usage(self) -> None:
        """刷新当前临时目录占用（供设置页与外部调用）。"""
        self._refresh_usage()

    def _refresh_usage(self) -> None:
        self._usage_label.setText(f"当前占用 {fmt.human_size(dir_size(self._temp_root))}")

    def _clear_temp(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空临时文件",
            "将删除临时目录下的全部内容，正在进行的会话可能需要重新导入源包。确定继续吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        clear_dir(self._temp_root)
        self._refresh_usage()

    def _prompt_temp_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择临时目录", str(self._temp_root))
        if chosen:
            self._temp_edit.setText(chosen)
            self._temp_root = Path(chosen)
            self._refresh_usage()

    # ---- 常规 -------------------------------------------------------
    def _build_general_page(self) -> QWidget:
        page, layout = self._page()

        export_group = QGroupBox("导出默认值")
        export_form = QFormLayout(export_group)
        export_form.setSpacing(self._m.space_sm)
        self._output_edit = QLineEdit(str(self.settings.value(KEY_OUTPUT_DIR, "") or ""))
        self._output_edit.setPlaceholderText("例如 D:\\交付")
        self._output_edit.setClearButtonEnabled(True)
        browse = QToolButton()
        browse.setProperty("variant", "ghost")
        browse.setToolTip("选择输出文件夹")
        browse.setIcon(design_icon("folder_open", self._m.icon_sm, self.theme.palette().text_secondary))
        browse.clicked.connect(self._prompt_output_dir)
        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(self._m.space_sm)
        output_layout.addWidget(self._output_edit, 1)
        output_layout.addWidget(browse)
        export_form.addRow("默认输出目录", output_row)

        self._format_combo = QComboBox()
        for value, suffix in FORMATS:
            self._format_combo.addItem(f"{value.upper()}（{suffix}）", value)
        self._select(self._format_combo, self.settings.value(KEY_OUTPUT_FORMAT, "zip"), "zip")
        export_form.addRow("默认输出格式", self._format_combo)

        self._level_combo = QComboBox()
        for level, label in LEVELS:
            self._level_combo.addItem(f"{label}（{level}）", level)
        self._select(self._level_combo, self.settings.value(KEY_OUTPUT_LEVEL, DEFAULT_LEVEL, int), DEFAULT_LEVEL)
        export_form.addRow("默认压缩级别", self._level_combo)
        layout.addWidget(export_group)

        appearance = QGroupBox("外观")
        appearance_form = QFormLayout(appearance)
        appearance_form.setSpacing(self._m.space_sm)

        self._mode_combo = QComboBox()
        for mode in THEME_MODES:
            self._mode_combo.addItem(MODE_LABELS.get(mode, mode), mode)
        self._select(self._mode_combo, self.theme.mode_preference, THEME_MODES[0])
        appearance_form.addRow("主题模式", self._mode_combo)

        accent_row = QWidget()
        accent_layout = QHBoxLayout(accent_row)
        accent_layout.setContentsMargins(0, 0, 0, 0)
        accent_layout.setSpacing(self._m.space_sm)
        self._follow_accent = QCheckBox("跟随系统")
        self._follow_accent.setChecked(self.theme.accent_is_system)
        self._follow_accent.toggled.connect(self._on_follow_accent)
        self._accent_button = QPushButton("")
        self._accent_button.clicked.connect(self._pick_accent)
        accent_layout.addWidget(self._follow_accent)
        accent_layout.addWidget(self._accent_button, 1)
        appearance_form.addRow("强调色", accent_row)
        self._update_accent_button()

        self._density_combo = QComboBox()
        for key in DENSITIES:
            self._density_combo.addItem(_DENSITY_LABELS.get(key, key), key)
        self._select(self._density_combo, self.theme.density, "comfortable")
        appearance_form.addRow("界面密度", self._density_combo)

        self._font_spin = QSpinBox()
        self._font_spin.setRange(FONT_SCALE_MIN, FONT_SCALE_MAX)
        self._font_spin.setSingleStep(FONT_SCALE_STEP)
        self._font_spin.setSuffix(" %")
        self._font_spin.setValue(self.theme.font_scale)
        appearance_form.addRow("界面字号", self._font_spin)

        font_label = QLabel(ui_family())
        font_label.setProperty("variant", "secondary")
        appearance_form.addRow("界面字体", font_label)
        layout.addWidget(appearance)

        workspace = QGroupBox("工作区")
        workspace_form = QFormLayout(workspace)
        workspace_form.setSpacing(self._m.space_sm)
        self._layout_combo = QComboBox()
        for mode in LAYOUTS:
            self._layout_combo.addItem(LAYOUT_LABELS[mode], mode)
        self._select(self._layout_combo, self.settings.value(KEY_LAYOUT, LAYOUTS[0]), LAYOUTS[0])
        workspace_form.addRow("布局模式", self._layout_combo)
        layout.addWidget(workspace)

        layout.addStretch(1)
        return page

    def _on_follow_accent(self, _checked: bool) -> None:
        self._update_accent_button()

    def _update_accent_button(self) -> None:
        color = self._accent_value or self.theme.accent
        self._accent_button.setText(f"选择颜色…（当前 {color}）")
        self._accent_button.setEnabled(not self._follow_accent.isChecked())

    def _pick_accent(self) -> None:
        initial = QColor(self._accent_value or self.theme.accent)
        chosen = QColorDialog.getColor(initial, self, "选择强调色")
        if not chosen.isValid():
            return
        self._accent_value = chosen.name().upper()
        self._update_accent_button()

    def _prompt_output_dir(self) -> None:
        start = self._output_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择输出文件夹", start)
        if chosen:
            self._output_edit.setText(chosen)

    # ---- 预览 -------------------------------------------------------
    def _build_preview_page(self) -> QWidget:
        page, layout = self._page()
        form = QFormLayout()
        form.setSpacing(self._m.space_sm)

        self._text_spin = QSpinBox()
        self._text_spin.setRange(1, 512)
        self._text_spin.setSuffix(" MB")
        self._text_spin.setValue(max(1, self._read_int(KEY_PREVIEW_TEXT_LIMIT, DEFAULT_TEXT_LIMIT) // (1024 * 1024)))
        form.addRow("文本预览上限", self._text_spin)

        self._pixels_spin = QSpinBox()
        self._pixels_spin.setRange(1, 512)
        self._pixels_spin.setSuffix(" 百万像素")
        self._pixels_spin.setValue(max(1, self._read_int(KEY_PREVIEW_IMAGE_PIXELS, DEFAULT_IMAGE_PIXELS) // 1_000_000))
        form.addRow("图片解码上限", self._pixels_spin)

        self._cache_spin = QSpinBox()
        self._cache_spin.setRange(1, 64)
        self._cache_spin.setSuffix(" 个文件")
        self._cache_spin.setValue(max(1, self._read_int(KEY_PREVIEW_CACHE_LIMIT, DEFAULT_CACHE_LIMIT)))
        form.addRow("波形缓存上限", self._cache_spin)

        self._video_limit_spin = QSpinBox()
        self._video_limit_spin.setRange(64, 8192)
        self._video_limit_spin.setSingleStep(64)
        self._video_limit_spin.setSuffix(" MB")
        self._video_limit_spin.setValue(
            max(
                64,
                self._read_int(KEY_PREVIEW_VIDEO_AUTO_LIMIT, DEFAULT_VIDEO_AUTO_LIMIT)
                // (1024 * 1024),
            )
        )
        self._video_limit_spin.setToolTip(
            "压缩包内不超过此大小的视频可自动提取；实际解码在按空格或播放后开始"
        )
        form.addRow("视频自动提取上限", self._video_limit_spin)

        layout.addLayout(form)
        layout.addStretch(1)
        return page

    # ---- 密码 -------------------------------------------------------
    def _build_password_page(self) -> QWidget:
        page, layout = self._page()
        self._remember_check = QCheckBox("在本会话内记住为加密包输入的密码")
        self._remember_check.setChecked(self._read_bool(KEY_REMEMBER_PASSWORD, False))
        hint = QLabel("密码只保留在内存里，关闭程序即失效，不写入磁盘。")
        hint.setProperty("variant", "secondary")
        hint.setWordWrap(True)
        layout.addWidget(self._remember_check)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    # ---- 落盘 -------------------------------------------------------
    def apply(self) -> None:
        """写入全部设置与系统集成项；仅应在用户确认后调用。"""
        self._apply_integration()
        self._apply_settings()
        self._apply_theme()
        self.settings.sync()

    def _apply_integration(self) -> None:
        removed = [ext for ext in self._initial_customs if ext not in self._custom_exts]
        self.integration.set_custom_suffixes(self._custom_exts)
        if removed:
            self.integration.unregister_extensions(removed)
        self.integration.apply(
            sorted(self._checked_exts()),
            add_to_session=self._add_session_check.isChecked(),
            open_with=self._open_with_check.isChecked(),
            extract_here=self._extract_here_check.isChecked(),
        )

    def _apply_settings(self) -> None:
        self.settings.set_value(KEY_TEMP_ROOT, self._temp_edit.text().strip())
        self.settings.set_value(KEY_TEMP_CLEAN_ON_CLOSE, self._clean_check.isChecked())
        output_dir = self._output_edit.text().strip()
        # 用户在设置页指定了新的输出目录 ⇒ 停止「跟随首个源包目录」的自动行为
        if output_dir != str(self.settings.value(KEY_OUTPUT_DIR, "") or ""):
            self.settings.set_value(KEY_OUTPUT_DIR_AUTO, False)
        self.settings.set_value(KEY_OUTPUT_DIR, output_dir)
        self.settings.set_value(KEY_OUTPUT_FORMAT, str(self._format_combo.currentData()))
        self.settings.set_value(KEY_OUTPUT_LEVEL, int(self._level_combo.currentData()))
        self.settings.set_value(KEY_LAYOUT, str(self._layout_combo.currentData()))
        self.settings.set_value(KEY_PREVIEW_TEXT_LIMIT, int(self._text_spin.value()) * 1024 * 1024)
        self.settings.set_value(KEY_PREVIEW_IMAGE_PIXELS, int(self._pixels_spin.value()) * 1_000_000)
        self.settings.set_value(KEY_PREVIEW_CACHE_LIMIT, int(self._cache_spin.value()))
        self.settings.set_value(
            KEY_PREVIEW_VIDEO_AUTO_LIMIT,
            int(self._video_limit_spin.value()) * 1024 * 1024,
        )
        self.settings.set_value(KEY_REMEMBER_PASSWORD, self._remember_check.isChecked())
        self.settings.set_value(KEY_FONT_SCALE, int(self._font_spin.value()))
        self.settings.set_value(KEY_DENSITY, str(self._density_combo.currentData()))

    def _apply_theme(self) -> None:
        self.theme.set_mode(str(self._mode_combo.currentData()))
        self.theme.set_density(str(self._density_combo.currentData()))
        self.theme.set_font_scale(int(self._font_spin.value()))
        if self._follow_accent.isChecked():
            self.theme.set_accent(None)
            self.settings.set_value(KEY_ACCENT, "")
        else:
            self.theme.set_accent(self._accent_value or self.theme.accent)
            self.settings.set_value(KEY_ACCENT, self.theme.accent)
        self.settings.set_value(KEY_THEME_MODE, self.theme.mode_preference)

    # ---- 内部工具 ---------------------------------------------------
    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(self._m.space_md, self._m.space_md, self._m.space_md, self._m.space_md)
        layout.setSpacing(self._m.space_md)
        return page, layout

    def _read_temp_root(self) -> Path:
        raw = str(self.settings.value(KEY_TEMP_ROOT, "") or "").strip()
        return Path(raw) if raw else default_root()

    def _read_bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value) if value is not None else default

    def _read_int(self, key: str, default: int) -> int:
        try:
            return int(self.settings.value(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _select(combo: QComboBox, want, fallback) -> None:
        index = combo.findData(want)
        if index < 0:
            index = combo.findData(fallback)
        combo.setCurrentIndex(max(0, index))
