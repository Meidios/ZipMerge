"""主窗口：多标签工作区、菜单、工具栏、状态栏与整体布局。

一个标签页 = 一个 :class:`~zipmerge.core.MergeSession` + 源包 / 目标 / 预览三个面板 +
底部导出栏（见 PLAN 6.6、7.5）。本模块负责：

* 标签页增删与切换，关闭标签时回收会话临时目录；
* 两套工作区（三栏并排 / 两栏 + 底部预览）切换与分割条尺寸记忆；
* 撤销重做、编辑、视图、主题菜单与快捷键；
* 主题变化时下发调色板并刷新原生窗口的暗色标题栏与 Mica。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QSizePolicy,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core import MergeSession
from ..util.settings import (
    DEFAULT_CLEAN_ON_CLOSE,
    DEFAULT_FONT_SCALE,
    KEY_ACCENT,
    KEY_DENSITY,
    KEY_FONT_SCALE,
    KEY_LAYOUT,
    KEY_LAYOUT_SIZES,
    KEY_PREVIEW_PANEL_VISIBLE,
    KEY_SOURCE_PANEL_VISIBLE,
    KEY_TEMP_CLEAN_ON_CLOSE,
    KEY_TEMP_ROOT,
    KEY_THEME_MODE,
    KEY_WINDOW_GEOMETRY,
    Settings,
)
from ..util.paths import asset_path
from ..util.tempdir import default_root
from .design import (
    DARK,
    DENSITIES,
    DWMSBT_MAINWINDOW,
    THEME_MODES,
    ThemeService,
    apply_window_effects,
    icon as design_icon,
    register_fonts,
)
from .export_bar import ExportBar
from .export_runner import run_export_dialog
from .drag_export import is_internal_drag
from .preview_panel import PreviewPanel
from .source_panel import SourcePanel
from .target_panel import TargetPanel

#: 工作区布局：三栏并排 / 两栏 + 底部预览（PLAN 6.6）
LAYOUT_COLUMNS = "columns"
LAYOUT_BOTTOM = "bottom"
LAYOUTS: tuple[str, ...] = (LAYOUT_COLUMNS, LAYOUT_BOTTOM)
LAYOUT_LABELS = {LAYOUT_COLUMNS: "三栏并排", LAYOUT_BOTTOM: "两栏 + 底部预览"}

#: 各布局的默认分割尺寸（无记忆时使用）
DEFAULT_SIZES: dict[str, list[int]] = {
    LAYOUT_COLUMNS: [300, 520, 380],
    LAYOUT_BOTTOM: [440, 240],
    "bottom_inner": [300, 520],
}

#: 主题模式的显示名
MODE_LABELS = {"system": "跟随系统", "light": "浅色", "dark": "深色"}

# 工作区卡片统一使用 spacing token 的 8 px 档位；外沿、卡片间与分割条保持同宽。
WORKSPACE_GAP = 8


def _parse_sizes(raw: object) -> dict[str, list[int]]:
    """解析 `columns:300,520,380;bottom:440,240` 形式的尺寸记忆。"""
    result: dict[str, list[int]] = {}
    for chunk in str(raw or "").split(";"):
        mode, sep, numbers = chunk.partition(":")
        if not sep:
            continue
        values = [int(piece) for piece in numbers.split(",") if piece.strip().isdigit()]
        if values:
            result[mode.strip()] = values
    return result


def _format_sizes(sizes: dict[str, list[int]]) -> str:
    return ";".join(f"{mode}:{','.join(str(v) for v in values)}" for mode, values in sizes.items())


class WorkspaceTab(QWidget):
    """一个会话的工作区：三个面板 + 导出栏，可在两种布局间切换。"""

    #: 会话内容变化（导入 / 编辑 / 撤销）时发出，参数为自身
    changed = Signal(object)

    def __init__(self, session: MergeSession, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self._settings = settings

        self.source_panel = SourcePanel(session, settings)
        self.target_panel = TargetPanel(session)
        self.preview_panel = PreviewPanel(session, settings)
        self.export_bar = ExportBar(session, settings)

        self._sizes = _parse_sizes(settings.value(KEY_LAYOUT_SIZES, ""))
        self._layout_mode = str(settings.value(KEY_LAYOUT, LAYOUT_COLUMNS) or LAYOUT_COLUMNS)
        if self._layout_mode not in LAYOUTS:
            self._layout_mode = LAYOUT_COLUMNS

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(
            WORKSPACE_GAP, WORKSPACE_GAP, WORKSPACE_GAP, WORKSPACE_GAP
        )
        self._layout.setSpacing(WORKSPACE_GAP)
        self._splitter: QSplitter | None = None
        self._inner_splitter: QSplitter | None = None
        self._rebuild_splitter()
        self._layout.addWidget(self.export_bar, 0)

        self.target_panel.locate_requested.connect(self._locate_in_source)
        self.target_panel.current_node_changed.connect(self.preview_panel.show_node)
        self.preview_panel.open_external_requested.connect(self.target_panel.open_node)
        # 目标树里按空格，播放 / 暂停预览面板正在展示的音频
        self.target_panel.playback_toggle_requested.connect(self.preview_panel.toggle_playback)
        self.export_bar.export_requested.connect(self._run_export)
        self.export_bar.temporary_export_requested.connect(self._run_temporary_export)

        session.on_change = self._on_session_changed
        session.on_undo_change = self._on_session_changed

    # ---- 对外 -------------------------------------------------------
    def set_palette(self, palette) -> None:
        self.source_panel.set_palette(palette)
        self.target_panel.set_palette(palette)
        self.preview_panel.set_palette(palette)
        self.export_bar.set_palette(palette)

    @property
    def layout_mode(self) -> str:
        return self._layout_mode

    def set_layout(self, mode: str) -> None:
        if mode not in LAYOUTS or mode == self._layout_mode:
            return
        self._layout_mode = mode
        self._settings.set_value(KEY_LAYOUT, mode)
        self._rebuild_splitter()

    def set_panel_visible(self, which: str, visible: bool) -> None:
        widget = {"source": self.source_panel, "preview": self.preview_panel}.get(which)
        if widget is None:
            return
        widget.setVisible(visible)
        if visible and self._splitter is not None:
            self._splitter.setSizes(self._sizes.get(self._layout_mode) or DEFAULT_SIZES[self._layout_mode])

    def refresh_views(self, *, invalidate_temporary: bool = False) -> None:
        self.target_panel.refresh()
        self.export_bar.refresh_from_session(invalidate_temporary=invalidate_temporary)
        node = self.target_panel.current_node()
        # 根节点不是可预览对象；撤销 / 重建后索引可能回落到根，此时清空预览
        self.preview_panel.show_node(node if node is not None and node.parent is not None else None)

    # ---- 内部：布局 -------------------------------------------------
    def _rebuild_splitter(self) -> None:
        if self._splitter is not None:
            self._layout.removeWidget(self._splitter)
            self._splitter.setParent(None)
            self._splitter.deleteLater()
        self._inner_splitter = None

        if self._layout_mode == LAYOUT_BOTTOM:
            outer = QSplitter(Qt.Orientation.Vertical)
            inner = QSplitter(Qt.Orientation.Horizontal)
            inner.addWidget(self.source_panel)
            inner.addWidget(self.target_panel)
            inner.setStretchFactor(0, 1)
            inner.setStretchFactor(1, 1)
            inner.setChildrenCollapsible(False)
            inner.setSizes(self._sizes.get("bottom_inner") or DEFAULT_SIZES["bottom_inner"])
            inner.splitterMoved.connect(self._on_inner_splitter_moved)
            self._inner_splitter = inner
            outer.addWidget(inner)
            outer.addWidget(self.preview_panel)
            outer.setStretchFactor(0, 3)
            outer.setStretchFactor(1, 1)
        else:
            outer = QSplitter(Qt.Orientation.Horizontal)
            outer.addWidget(self.source_panel)
            outer.addWidget(self.target_panel)
            outer.addWidget(self.preview_panel)
            for index in range(3):
                outer.setStretchFactor(index, 1)

        outer.setChildrenCollapsible(False)
        self._splitter = outer
        # 三栏吃掉全部多余高度纵向扩展；导出栏固定高贴底（PLAN 7.7 界面 2）
        self._layout.insertWidget(0, outer, 1)
        saved = self._sizes.get(self._layout_mode) or DEFAULT_SIZES[self._layout_mode]
        if len(saved) == outer.count():
            outer.setSizes(saved)
        outer.splitterMoved.connect(self._on_splitter_moved)

    def _on_splitter_moved(self, *_args) -> None:
        if self.preview_panel.isHidden() or (
            self._layout_mode == LAYOUT_COLUMNS and self.source_panel.isHidden()
        ):
            return
        self._sizes[self._layout_mode] = self._splitter.sizes()
        self._settings.set_value(KEY_LAYOUT_SIZES, _format_sizes(self._sizes))

    def _on_inner_splitter_moved(self, *_args) -> None:
        if self._inner_splitter is None or self.source_panel.isHidden():
            return
        self._sizes["bottom_inner"] = self._inner_splitter.sizes()
        self._settings.set_value(KEY_LAYOUT_SIZES, _format_sizes(self._sizes))

    def persist_layout(self) -> None:
        """保存当前实际分割比例；窗口缩放也可能改变它，不能只依赖拖动信号。"""
        # 隐藏面板会让 QSplitter 报告 0；保留隐藏前的比例，重新显示时才能复原。
        if self._splitter is not None and not self.preview_panel.isHidden() and not (
            self._layout_mode == LAYOUT_COLUMNS and self.source_panel.isHidden()
        ):
            self._sizes[self._layout_mode] = self._splitter.sizes()
        if self._inner_splitter is not None and not self.source_panel.isHidden():
            self._sizes["bottom_inner"] = self._inner_splitter.sizes()
        self._settings.set_value(KEY_LAYOUT_SIZES, _format_sizes(self._sizes))

    # ---- 内部：联动 -------------------------------------------------
    def _on_session_changed(self) -> None:
        self.refresh_views(invalidate_temporary=True)
        self.changed.emit(self)

    def _locate_in_source(self, node) -> None:
        ref = node.source
        if ref is None or not ref.source_id:
            return
        self.source_panel.focus_entry(ref.source_id, ref.entry_path or "")

    def _run_export(self, options) -> None:
        run_export_dialog(self.window(), self.session, options)

    def _run_temporary_export(self) -> None:
        candidate: Path | None = None
        try:
            candidate = self.session.create_temporary_output(self.export_bar.output_filename())
            result = run_export_dialog(
                self.window(),
                self.session,
                self.export_bar.build_options(candidate),
                show_success=False,
            )
            if result is None:
                self.session.discard_temporary_output(candidate)
                return
            self.session.commit_temporary_archive(result.output_path)
        except (OSError, ValueError) as exc:
            if candidate is not None:
                self.session.discard_temporary_output(candidate)
            QMessageBox.warning(self.window(), "临时包生成失败", f"无法保存临时压缩包：\n{exc}")
            return
        self.export_bar.set_temporary_archive(result.output_path)


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, settings: Settings | None = None, theme: ThemeService | None = None, parent=None) -> None:
        super().__init__(parent)
        register_fonts()
        self.setWindowTitle("ZipMerge")
        icon = QIcon(str(asset_path("icons", "ZipMerge.ico")))
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(1180, 720)

        self.settings = settings or Settings()
        self.theme = theme or ThemeService(self)
        self._restore_appearance()
        self._temp_root = self.settings.value(KEY_TEMP_ROOT, "") or default_root()
        self._panel_visible = {
            "source": self.settings.value(KEY_SOURCE_PANEL_VISIBLE, True, type=bool),
            "preview": self.settings.value(KEY_PREVIEW_PANEL_VISIBLE, True, type=bool),
        }

        self._tabs = QTabWidget(self)
        # 不用 documentMode：它会让标签栏与工具栏贴在同一行高度里，标签被动作栏压住（PLAN 7.7 界面 3）
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self.setCentralWidget(self._tabs)
        # 从资源管理器把压缩包拖到窗口任意位置都能加进来（PLAN 7.7 问题 1）
        self.setAcceptDrops(True)

        self._status_label = QLabel("")
        self._status_label.setProperty("variant", "secondary")
        self.statusBar().addPermanentWidget(self._status_label)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()

        self._tabs.tabCloseRequested.connect(self._close_tab)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self.theme.changed.connect(self._apply_theme)
        self.theme.metrics_changed.connect(self._apply_theme)
        self.theme.mode_changed.connect(lambda mode: self.settings.set_value(KEY_THEME_MODE, mode))
        self.theme.accent_changed.connect(self._on_accent_changed)
        self.theme.metrics_changed.connect(
            lambda _metrics: self.settings.set_value(KEY_DENSITY, self.theme.density)
        )

        self._apply_theme()
        self.new_tab()
        # 中央工作区创建后再恢复；过早恢复会被首个标签的 sizeHint 覆盖。
        self._restore_window_geometry()

    def _restore_appearance(self) -> None:
        """启动时恢复上次的主题模式 / 强调色 / 密度。"""
        mode = str(self.settings.value(KEY_THEME_MODE, "") or "")
        if mode in THEME_MODES:
            self.theme.set_mode(mode)
        accent = str(self.settings.value(KEY_ACCENT, "") or "")
        if accent:
            self.theme.set_accent(accent)
        density = str(self.settings.value(KEY_DENSITY, "") or "")
        if density in DENSITIES:
            self.theme.set_density(density)
        self.theme.set_font_scale(self.settings.value(KEY_FONT_SCALE, DEFAULT_FONT_SCALE, int))

    def _on_accent_changed(self, _accent: str) -> None:
        """只把「用户显式选择的强调色」写进设置；跟随系统时留空串。"""
        self.settings.set_value(
            KEY_ACCENT, "" if self.theme.accent_is_system else self.theme.accent
        )

    # ---- 标签页 -----------------------------------------------------
    def current_tab(self) -> WorkspaceTab | None:
        widget = self._tabs.currentWidget()
        return widget if isinstance(widget, WorkspaceTab) else None

    # ---- 拖入压缩包 -------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if is_internal_drag(event.mimeData()):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if is_internal_drag(event.mimeData()):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if is_internal_drag(event.mimeData()):
            event.ignore()
            return
        tab = self.current_tab()
        if tab is None or not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            super().dropEvent(event)
            return
        tab.source_panel.add_archives(paths)
        event.acceptProposedAction()

    def new_tab(self) -> WorkspaceTab:
        name = f"会话 {self._tabs.count() + 1}"
        session = MergeSession(name=name, temp_root=self._temp_root)
        tab = WorkspaceTab(session, self.settings, self)
        # 标签页在 __init__ 里晚于首次 _apply_theme() 创建，必须显式下发当前调色板，
        # 否则面板会保留构造函数里的默认调色板（默认强调色）而不是系统强调色。
        tab.set_palette(self.theme.palette())
        tab.source_panel.bind_extract_action(self.act_extract)
        tab.source_panel.selection_changed.connect(self._sync_actions)
        tab.changed.connect(self._on_tab_changed)
        self._tabs.addTab(tab, name)
        self._tabs.setCurrentWidget(tab)
        self._apply_panel_visibility(tab)
        self._sync_actions()
        return tab

    def _close_tab(self, index: int) -> None:
        tab = self._tabs.widget(index)
        if tab is None:
            return
        self._tabs.removeTab(index)
        if isinstance(tab, WorkspaceTab):
            tab.session.on_change = None
            tab.session.on_undo_change = None
            tab.preview_panel.shutdown()
            tab.session.clear_temporary_archives()
            if self._clean_on_close():
                tab.session.close()
        tab.deleteLater()
        if self._tabs.count() == 0:
            self.new_tab()
        self._sync_actions()

    def _clean_on_close(self) -> bool:
        """是否在关闭标签页时清理该会话的临时文件（PLAN 8.6，设置页可关）。"""
        return self.settings.value(
            KEY_TEMP_CLEAN_ON_CLOSE,
            DEFAULT_CLEAN_ON_CLOSE,
            type=bool,
        )

    def _on_tab_changed(self, *_args) -> None:
        self._sync_actions()

    def _apply_panel_visibility(self, tab: WorkspaceTab) -> None:
        for which, visible in self._panel_visible.items():
            tab.set_panel_visible(which, visible)

    # ---- 动作 -------------------------------------------------------
    def _build_actions(self) -> None:
        self.act_add = QAction("添加压缩包…", self)
        self.act_add.setShortcut(QKeySequence(QKeySequence.StandardKey.Open))
        self.act_add.triggered.connect(self._action_add)

        self.act_extract = QAction("解压所选到…", self)
        self.act_extract.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.act_extract.triggered.connect(self._action_extract)

        self.act_export = QAction("导出…", self)
        self.act_export.setShortcut(QKeySequence("Ctrl+E"))
        self.act_export.triggered.connect(self._action_export)

        self.act_new_tab = QAction("新建标签页", self)
        self.act_new_tab.setShortcut(QKeySequence(QKeySequence.StandardKey.AddTab))
        self.act_new_tab.triggered.connect(lambda: self.new_tab())

        self.act_close_tab = QAction("关闭标签页", self)
        self.act_close_tab.setShortcut(QKeySequence(QKeySequence.StandardKey.Close))
        self.act_close_tab.triggered.connect(self._action_close_tab)

        self.act_quit = QAction("退出", self)
        self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.act_quit.triggered.connect(self.close)

        self.act_settings = QAction("设置…", self)
        self.act_settings.setShortcut(QKeySequence("Ctrl+,"))
        self.act_settings.triggered.connect(self._open_settings)

        self.act_undo = QAction("撤销", self)
        self.act_undo.setShortcut(QKeySequence(QKeySequence.StandardKey.Undo))
        self.act_undo.triggered.connect(self._action_undo)

        self.act_redo = QAction("重做", self)
        self.act_redo.setShortcuts(
            [QKeySequence(QKeySequence.StandardKey.Redo), QKeySequence("Ctrl+Y")]
        )
        self.act_redo.triggered.connect(self._action_redo)

        self.act_cut = QAction("剪切", self)
        self.act_cut.setShortcut(QKeySequence(QKeySequence.StandardKey.Cut))
        self.act_cut.triggered.connect(lambda: self._with_tab(lambda t: t.target_panel.cut_selected()))

        self.act_copy = QAction("复制", self)
        self.act_copy.setShortcut(QKeySequence(QKeySequence.StandardKey.Copy))
        self.act_copy.triggered.connect(lambda: self._with_tab(lambda t: t.target_panel.copy_selected()))

        self.act_paste = QAction("粘贴", self)
        self.act_paste.setShortcut(QKeySequence(QKeySequence.StandardKey.Paste))
        self.act_paste.triggered.connect(lambda: self._with_tab(lambda t: t.target_panel.paste()))

        # 删除 / 重命名 的快捷键由目标树面板自身处理，菜单项不重复绑定，避免快捷键冲突
        self.act_delete = QAction("删除", self)
        self.act_delete.triggered.connect(lambda: self._with_tab(lambda t: t.target_panel.delete_selected()))

        self.act_rename = QAction("重命名", self)
        self.act_rename.triggered.connect(lambda: self._with_tab(lambda t: t.target_panel.rename_current()))

        self.act_new_folder = QAction("新建文件夹", self)
        self.act_new_folder.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self.act_new_folder.triggered.connect(lambda: self._with_tab(lambda t: t.target_panel.new_folder()))

        self.act_flatten = QAction("摊平到当前目录", self)
        self.act_flatten.triggered.connect(
            lambda: self._with_tab(lambda t: t.target_panel.flatten_current(0))
        )

        self.act_flatten_depth = QAction("摊平到指定层数…", self)
        self.act_flatten_depth.triggered.connect(
            lambda: self._with_tab(lambda t: t.target_panel.prompt_flatten_depth())
        )

        self.act_locate = QAction("在源包中定位", self)
        self.act_locate.triggered.connect(lambda: self._with_tab(lambda t: t.target_panel.locate_current()))

        # 视图：布局（全局偏好）与面板显隐
        self._layout_group = QActionGroup(self)
        self._layout_group.setExclusive(True)
        self._layout_actions: dict[str, QAction] = {}
        current_layout = str(self.settings.value(KEY_LAYOUT, LAYOUT_COLUMNS) or LAYOUT_COLUMNS)
        for mode in LAYOUTS:
            action = QAction(LAYOUT_LABELS[mode], self)
            action.setCheckable(True)
            action.setChecked(mode == current_layout)
            action.triggered.connect(lambda _checked=False, m=mode: self._set_layout(m))
            self._layout_group.addAction(action)
            self._layout_actions[mode] = action

        # 工具栏只提供一个布局切换入口；两套预设的明确选择仍保留在「视图」菜单。
        self.act_toggle_layout = QAction("切换布局", self)
        self.act_toggle_layout.triggered.connect(self._toggle_layout)
        self._sync_layout_toggle_action(current_layout)

        self.act_show_source = QAction("源包面板", self)
        self.act_show_source.setCheckable(True)
        self.act_show_source.setChecked(self._panel_visible["source"])
        self.act_show_source.triggered.connect(lambda checked: self._toggle_panel("source", checked))

        self.act_show_preview = QAction("预览面板", self)
        self.act_show_preview.setCheckable(True)
        self.act_show_preview.setChecked(self._panel_visible["preview"])
        self.act_show_preview.triggered.connect(lambda checked: self._toggle_panel("preview", checked))

        # 主题
        self._mode_group = QActionGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_actions: dict[str, QAction] = {}
        for mode in THEME_MODES:
            action = QAction(MODE_LABELS.get(mode, mode), self)
            action.setCheckable(True)
            action.setChecked(mode == self.theme.mode_preference)
            action.triggered.connect(lambda _checked=False, m=mode: self.theme.set_mode(m))
            self._mode_group.addAction(action)
            self._mode_actions[mode] = action

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件(&F)")
        file_menu.addAction(self.act_add)
        file_menu.addAction(self.act_extract)
        file_menu.addAction(self.act_export)
        file_menu.addSeparator()
        file_menu.addAction(self.act_new_tab)
        file_menu.addAction(self.act_close_tab)
        file_menu.addSeparator()
        file_menu.addAction(self.act_settings)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        edit_menu = self.menuBar().addMenu("编辑(&E)")
        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_cut)
        edit_menu.addAction(self.act_copy)
        edit_menu.addAction(self.act_paste)
        edit_menu.addAction(self.act_delete)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_new_folder)
        edit_menu.addAction(self.act_rename)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_flatten)
        edit_menu.addAction(self.act_flatten_depth)
        edit_menu.addAction(self.act_locate)

        view_menu = self.menuBar().addMenu("视图(&V)")
        for mode in LAYOUTS:
            view_menu.addAction(self._layout_actions[mode])
        view_menu.addSeparator()
        view_menu.addAction(self.act_show_source)
        view_menu.addAction(self.act_show_preview)
        view_menu.addSeparator()
        theme_menu = view_menu.addMenu("主题")
        for mode in THEME_MODES:
            theme_menu.addAction(self._mode_actions[mode])

        help_menu = self.menuBar().addMenu("帮助(&H)")
        about = QAction("关于 ZipMerge", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("主工具栏", self)
        toolbar.setObjectName("mainToolBar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addAction(self.act_add)
        toolbar.addAction(self.act_extract)
        toolbar.addAction(self.act_export)
        toolbar.addSeparator()
        toolbar.addAction(self.act_undo)
        toolbar.addAction(self.act_redo)
        # 布局切换放在动作栏最右侧：弹性空白把它顶到尾部（PLAN 6.6）
        spacer = QWidget(toolbar)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addAction(self.act_toggle_layout)
        self.addToolBar(toolbar)
        self._toolbar = toolbar

    # ---- 动作实现 ---------------------------------------------------
    def _with_tab(self, callback) -> None:
        tab = self.current_tab()
        if tab is not None:
            callback(tab)

    def _action_add(self) -> None:
        self._with_tab(lambda tab: tab.source_panel.prompt_add())

    def _action_extract(self) -> None:
        self._with_tab(lambda tab: tab.source_panel.prompt_extract())

    def _action_export(self) -> None:
        self._with_tab(lambda tab: tab.export_bar.request_export())

    def _action_close_tab(self) -> None:
        index = self._tabs.currentIndex()
        if index >= 0:
            self._close_tab(index)

    def _action_undo(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.session.undo.undo()

    def _action_redo(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.session.undo.redo()

    def _set_layout(self, mode: str) -> None:
        if mode not in LAYOUTS:
            return
        self.settings.set_value(KEY_LAYOUT, mode)
        for index in range(self._tabs.count()):
            tab = self._tabs.widget(index)
            if isinstance(tab, WorkspaceTab):
                tab.set_layout(mode)
        if mode in self._layout_actions:
            self._layout_actions[mode].setChecked(True)
        self._sync_layout_toggle_action(mode)

    def _toggle_layout(self) -> None:
        tab = self.current_tab()
        current = tab.layout_mode if tab is not None else str(
            self.settings.value(KEY_LAYOUT, LAYOUT_COLUMNS) or LAYOUT_COLUMNS
        )
        target = LAYOUT_BOTTOM if current == LAYOUT_COLUMNS else LAYOUT_COLUMNS
        self._set_layout(target)

    def _sync_layout_toggle_action(self, current: str) -> None:
        if current not in LAYOUTS:
            current = LAYOUT_COLUMNS
        target = LAYOUT_BOTTOM if current == LAYOUT_COLUMNS else LAYOUT_COLUMNS
        hint = f"当前：{LAYOUT_LABELS[current]}；点击切换到：{LAYOUT_LABELS[target]}"
        self.act_toggle_layout.setToolTip(hint)
        self.act_toggle_layout.setStatusTip(hint)

    def _toggle_panel(self, which: str, visible: bool) -> None:
        if not visible:
            tab = self.current_tab()
            if tab is not None:
                tab.persist_layout()
        self._panel_visible[which] = visible
        key = {
            "source": KEY_SOURCE_PANEL_VISIBLE,
            "preview": KEY_PREVIEW_PANEL_VISIBLE,
        }.get(which)
        if key is not None:
            self.settings.set_value(key, visible)
        # 菜单项与工具栏按钮是同一动作的不同呈现，显隐后把勾选状态对齐
        action = {"source": self.act_show_source, "preview": self.act_show_preview}.get(which)
        if action is not None and action.isChecked() != visible:
            action.setChecked(visible)
        for index in range(self._tabs.count()):
            tab = self._tabs.widget(index)
            if isinstance(tab, WorkspaceTab):
                tab.set_panel_visible(which, visible)

    def _sync_actions(self) -> None:
        tab = self.current_tab()
        has_tab = tab is not None
        for action in (
            self.act_add, self.act_export, self.act_close_tab, self.act_cut, self.act_copy,
            self.act_paste, self.act_delete, self.act_rename, self.act_new_folder,
            self.act_flatten, self.act_flatten_depth, self.act_locate,
        ):
            action.setEnabled(has_tab)
        if has_tab:
            undo = tab.session.undo
            self.act_extract.setEnabled(bool(tab.source_panel.selected_extraction_selections()))
            self.act_undo.setEnabled(undo.can_undo)
            self.act_redo.setEnabled(undo.can_redo)
            self.act_undo.setText(f"撤销 {undo.undo_label}" if undo.undo_label else "撤销")
            self.act_redo.setText(f"重做 {undo.redo_label}" if undo.redo_label else "重做")
            self._update_status(tab)
        else:
            self.act_extract.setEnabled(False)
            self.act_undo.setEnabled(False)
            self.act_redo.setEnabled(False)
            self._status_label.setText("")

    def _update_status(self, tab: WorkspaceTab) -> None:
        session = tab.session
        files = session.root.total_file_count()
        dirs = sum(1 for node in session.root.walk(include_self=False) if node.is_dir)
        self._status_label.setText(
            f"{len(session.sources)} 个源包 · {dirs} 个文件夹 · {files} 个文件"
        )

    # ---- 主题与窗口 -------------------------------------------------
    def _restore_window_geometry(self) -> None:
        """恢复位置、大小及最大化状态；无效或旧配置沿用默认尺寸。"""
        geometry = self.settings.value(KEY_WINDOW_GEOMETRY)
        if not geometry:
            return
        try:
            self.restoreGeometry(geometry)
        except (TypeError, ValueError):
            # 用户配置可能被手工修改或来自不兼容版本，启动不能因此失败。
            return

    def _save_window_layout(self) -> None:
        """以用户关闭时看到的活动工作区为准保存窗口和布局。"""
        tab = self.current_tab()
        if tab is not None:
            tab.persist_layout()
        self.settings.set_value(KEY_WINDOW_GEOMETRY, self.saveGeometry())
        self.settings.set_value(KEY_SOURCE_PANEL_VISIBLE, self._panel_visible["source"])
        self.settings.set_value(KEY_PREVIEW_PANEL_VISIBLE, self._panel_visible["preview"])
        self.settings.sync()

    def _apply_theme(self, *_args) -> None:
        self.setStyleSheet(self.theme.stylesheet())
        palette = self.theme.palette()
        for index in range(self._tabs.count()):
            tab = self._tabs.widget(index)
            if isinstance(tab, WorkspaceTab):
                tab.set_palette(palette)
        for action, name, emphasized in (
            (self.act_add, "folder_open", True),
            (self.act_extract, "arrow_download", True),
            (self.act_export, "export", True),
            (self.act_undo, "undo", False),
            (self.act_redo, "redo", False),
            (self.act_new_tab, "add", False),
            (self.act_close_tab, "dismiss", False),
            (self.act_delete, "delete", False),
            (self.act_rename, "rename", False),
            (self.act_new_folder, "folder_add", False),
            (self.act_cut, "cut", False),
            (self.act_copy, "copy", False),
            (self.act_paste, "clipboard_paste", False),
            (self.act_locate, "search", False),
            (self.act_settings, "settings", False),
            (self.act_toggle_layout, "layout_dynamic", False),
        ):
            color = palette.accent if emphasized else palette.text_secondary
            action.setIcon(design_icon(name, 16, color))
        mode = self.theme.mode_preference
        if mode in self._mode_actions:
            self._mode_actions[mode].setChecked(True)
        if self.isVisible():
            self._apply_window_effects()

    def _apply_window_effects(self) -> None:
        apply_window_effects(
            int(self.winId()),
            dark=self.theme.effective_mode == DARK,
            backdrop=DWMSBT_MAINWINDOW,
            rounded=True,
        )

    def _show_about(self) -> None:
        from .. import __version__

        self.statusBar().showMessage(f"ZipMerge {__version__}", 4000)

    def _open_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.settings, self.theme, temp_root=self._temp_root, parent=self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        dialog.apply()
        self._temp_root = Path(str(self.settings.value(KEY_TEMP_ROOT, "") or "") or default_root())
        layout = str(self.settings.value(KEY_LAYOUT, LAYOUT_COLUMNS) or LAYOUT_COLUMNS)
        for index in range(self._tabs.count()):
            tab = self._tabs.widget(index)
            if isinstance(tab, WorkspaceTab):
                tab.set_layout(layout)
                # 设置页可能改了输出目录 / 格式 / 级别 / 命名模板，已打开的导出栏必须重读
                tab.export_bar.reload_settings()
                tab.preview_panel.reload_settings(self.settings)
                tab.refresh_views()
        if layout in self._layout_actions:
            self._layout_actions[layout].setChecked(True)
        self._sync_layout_toggle_action(layout)
        self._sync_actions()

    # ---- Qt 事件 ----------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        self._apply_window_effects()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._save_window_layout()
        for index in range(self._tabs.count()):
            tab = self._tabs.widget(index)
            if isinstance(tab, WorkspaceTab):
                tab.session.on_change = None
                tab.session.on_undo_change = None
                tab.preview_panel.shutdown()
                tab.session.clear_temporary_archives()
                if self._clean_on_close():
                    tab.session.close()
        super().closeEvent(event)
