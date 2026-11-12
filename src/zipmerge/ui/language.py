"""Select the UI language once per run, before creating application widgets."""

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

from ..i18n import set_lookup
from ..util.paths import asset_path
from ..util.settings import KEY_LANGUAGE

LANGUAGES = {"zh_CN": "简体中文", "zh_TW": "繁體中文", "en": "English"}
_language = "zh_CN"


def resolve_language(preference, system_languages=None):
    if isinstance(preference, str) and preference in LANGUAGES:
        return preference
    languages = QLocale.system().uiLanguages() if system_languages is None else system_languages
    for tag in languages:
        locale = QLocale(tag)
        if locale.language() == QLocale.Language.Chinese:
            return "zh_TW" if locale.script() == QLocale.Script.TraditionalHanScript else "zh_CN"
        if locale.language() == QLocale.Language.English:
            return "en"
    return "en"


def load_language(settings):
    global _language
    _language = resolve_language(settings.value(KEY_LANGUAGE, "system"))
    if _language == "zh_CN":
        set_lookup(None)
        return
    catalog = QTranslator()
    if not catalog.load(str(asset_path("i18n", f"zipmerge_{_language}.qm"))):
        raise RuntimeError(f"Missing ZipMerge translation catalog: {_language}")
    set_lookup(lambda source: catalog.translate("ZipMerge", source))


def install_qt_language(app):
    if _language == "en":
        return  # Qt's source messages are English.
    native = QTranslator(app)
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if native.load(f"qtbase_{_language}", directory):
        app.installTranslator(native)
