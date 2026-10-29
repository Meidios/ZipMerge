"""QSettings 封装：统一键名与读写入口（Windows 下落到 ``HKCU\\Software\\ZipMerge``）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSettings

ORG = "ZipMerge"
APP = "ZipMerge"

# 外观
KEY_THEME_MODE = "appearance/theme_mode"
KEY_ACCENT = "appearance/accent"
KEY_DENSITY = "appearance/density"
KEY_LAYOUT = "appearance/layout"
KEY_LAYOUT_SIZES = "appearance/layout_sizes"
# 主窗口几何与工作区面板显隐
KEY_WINDOW_GEOMETRY = "window/geometry"
KEY_SOURCE_PANEL_VISIBLE = "window/source_panel_visible"
KEY_PREVIEW_PANEL_VISIBLE = "window/preview_panel_visible"
# 命名模板
KEY_NAMING_TEMPLATE = "naming/template"
KEY_NAMING_PROJECT = "naming/project"
KEY_NAMING_REQUIREMENT = "naming/requirement"
KEY_NAMING_CONTENT = "naming/content"
KEY_NAMING_DATE = "naming/date"
KEY_NAMING_VERSION = "naming/version"
# 导出
KEY_OUTPUT_DIR = "export/output_dir"
#: 输出目录是否随首个源包自动定位（用户手改后置 0 并停止自动覆盖）
KEY_OUTPUT_DIR_AUTO = "export/output_dir_auto"
KEY_OUTPUT_FORMAT = "export/format"
KEY_OUTPUT_LEVEL = "export/level"
#: 命名模板库（JSON 数组）与当前使用的模板名
KEY_TEMPLATES = "export/templates"
KEY_ACTIVE_TEMPLATE = "export/active_template"
# 导入
#: 「添加压缩包」上次使用的目录
KEY_IMPORT_LAST_DIR = "import/last_dir"
# 解压
#: 「解压所选到」上次成功选择的目标目录
KEY_EXTRACT_LAST_DIR = "extract/last_dir"
# 临时目录
KEY_TEMP_ROOT = "temp/root"
KEY_TEMP_CLEAN_ON_CLOSE = "temp/clean_on_close"
# 系统集成（只写 HKCU，见 PLAN 8）
KEY_INTEGRATION_EXTENSIONS = "integration/extensions"
KEY_INTEGRATION_ADD_TO_SESSION = "integration/add_to_session"
KEY_INTEGRATION_OPEN_WITH = "integration/open_with"
KEY_INTEGRATION_EXTRACT_HERE = "integration/extract_here"
# 首次安装默认值是否已初始化；升级/修复安装不得重新打开用户已关闭的菜单
KEY_INSTALL_DEFAULTS_INITIALIZED = "onboarding/install_defaults_initialized"
# 安装器注入默认右键菜单后置位；首次正常启动完成两步系统集成引导后清除
KEY_POST_INSTALL_PROMPT_PENDING = "onboarding/post_install_prompt_pending"
# 用户自定义的识别后缀（PLAN 8.2）；只存用户新增的那些，内置后缀见 util/paths.py
KEY_INTEGRATION_CUSTOM_SUFFIXES = "integration/custom_suffixes"
# 预览上限（PLAN 7.8「预览」组）
KEY_PREVIEW_TEXT_LIMIT = "preview/text_limit"
KEY_PREVIEW_IMAGE_PIXELS = "preview/image_pixels"
KEY_PREVIEW_CACHE_LIMIT = "preview/waveform_cache"
KEY_PREVIEW_VIDEO_AUTO_LIMIT = "preview/video_auto_load_limit"
# 密码
KEY_REMEMBER_PASSWORD = "password/remember_in_session"
# 外观：界面字号缩放百分比
KEY_FONT_SCALE = "appearance/font_scale"

# 默认值集中在这里，设置页与各消费方共用同一个事实来源
DEFAULT_TEXT_LIMIT = 5 * 1024 * 1024
DEFAULT_IMAGE_PIXELS = 40_000_000
DEFAULT_CACHE_LIMIT = 6
DEFAULT_VIDEO_AUTO_LIMIT = 512 * 1024 * 1024
DEFAULT_FONT_SCALE = 100
DEFAULT_CLEAN_ON_CLOSE = True
DEFAULT_OUTPUT_DIR_AUTO = True
DEFAULT_INTEGRATION_ADD_TO_SESSION = True
DEFAULT_INTEGRATION_OPEN_WITH = True
DEFAULT_INTEGRATION_EXTRACT_HERE = True


class Settings:
    """薄封装；键名集中在模块常量里，避免各处散落字符串。"""

    def __init__(self, org: str = ORG, app: str = APP) -> None:
        self._store = QSettings(org, app)

    def value(self, key: str, default: Any = None, type: type | None = None) -> Any:
        if type is not None:
            return self._store.value(key, default, type=type)
        return self._store.value(key, default)

    def set_value(self, key: str, value: Any) -> None:
        self._store.setValue(key, value)

    def contains(self, key: str) -> bool:
        return self._store.contains(key)

    def sync(self) -> None:
        self._store.sync()

    def clear(self) -> None:
        self._store.clear()
