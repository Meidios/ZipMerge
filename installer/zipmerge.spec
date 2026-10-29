# -*- mode: python ; coding: utf-8 -*-
"""ZipMerge 的 PyInstaller 打包描述（PLAN 11：onedir，非 onefile）。

用 tools/build_release.py 调用，不要直接跑 pyinstaller：版本资源文件由该脚本从
src/zipmerge/__init__.py 的 __version__ 生成，保证版本只有一处事实来源。

两条硬约束在这里落地：

1. **PySide6 包根目录的 av*.dll 必须随包收集**（MEMORY 3.7）。Qt 的多媒体插件加载时
   不带 LOAD_WITH_ALTERED_SEARCH_PATH，插件的传递依赖解析不到就会静默退化成「无后端」。
   这里同时放到 _internal 根（Windows 默认会对进程主目录与 PyInstaller 注入的搜索目录生效）
   与 PySide6 包目录（waveform.preload_multimedia_libs 用 add_dll_directory 找的就是这里），
   两条路径都覆盖，避免依赖某一个特定布局。
2. 未使用的 Qt 模块显式排除（PLAN 11），把体积压在 120-180 MB。
"""

import fnmatch
from pathlib import Path

import PySide6

PROJECT = Path(SPECPATH).resolve().parent

#: 随程序分发的资源目录：源码运行与打包运行共用 app_root() 同一套相对布局
DATA_DIRS: tuple[str, ...] = ("assets/fonts", "assets/icons", "vendor/7zip")

#: PySide6 中本项目用不到的模块（PLAN 11）。名称是 PySide6 子模块名，
#: 会被拼成 "PySide6.<name>" 交给 Analysis(excludes=...)。
UNUSED_QT_MODULES: tuple[str, ...] = (
    "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic", "Qt3DRender",
    "QtBluetooth", "QtCharts", "QtDataVisualization", "QtDesigner", "QtGraphs", "QtHelp",
    "QtLocation", "QtNfc", "QtNetworkAuth", "QtPdf", "QtPdfWidgets",
    "QtPositioning", "QtQml", "QtQuick", "QtQuick3D", "QtQuickControls2", "QtQuickTest",
    "QtQuickWidgets", "QtRemoteObjects", "QtScxml", "QtSensors", "QtSerialBus",
    "QtSerialPort", "QtSpatialAudio", "QtSql", "QtTest", "QtTextToSpeech", "QtUiTools",
    "QtWebChannel", "QtWebEngineCore", "QtWebEngineQuick", "QtWebEngineWidgets",
    "QtWebSockets", "QtAsyncio",
)

#: 运行期用不到的标准库 / 打包工具，排除可省体积且无副作用
UNUSED_STDLIB: tuple[str, ...] = (
    "tkinter", "unittest", "pydoc", "doctest", "test", "lib2to3", "pdb",
    "distutils", "setuptools", "pip", "wheel", "sqlite3",
    # numpy 只是被 Pillow 的函数内可选导入（Image.fromarray）牵连进来的，本项目从不用它
    "numpy",
)

#: PySide6 的 hook 会把包目录下的 Qt DLL 整批收进来，与 UNUSED_QT_MODULES 无关。
#: 这里按文件名把不用的 Qt 运行库剔掉（都是叶子模块，Qt6Core/Gui/Widgets/Network/
#: Multimedia 都不依赖它们）；opengl32sw.dll 是「强制软件 OpenGL」的兜底库，本产品用不到。
DROP_BINARY_PATTERNS: tuple[str, ...] = (
    "opengl32sw.dll",
    "Qt6Quick*.dll",
    "Qt6Qml*.dll",
    "Qt6Pdf*.dll",
    "Qt6OpenGL*.dll",
    "Qt63D*.dll",
    "Qt6WebEngine*.dll",
    "Qt6Designer*.dll",
    "Qt6Help*.dll",
    "Qt6Charts*.dll",
    "Qt6DataVisualization*.dll",
    "Qt6Graphs*.dll",
    "Qt6Bluetooth*.dll",
    "Qt6Nfc*.dll",
    "Qt6Positioning*.dll",
    "Qt6Location*.dll",
    "Qt6RemoteObjects*.dll",
    "Qt6Scxml*.dll",
    "Qt6Sensors*.dll",
    "Qt6SerialPort*.dll",
    "Qt6SerialBus*.dll",
    "Qt6Sql*.dll",
    "Qt6Test*.dll",
    "Qt6TextToSpeech*.dll",
    "Qt6WebChannel*.dll",
    "Qt6WebSockets*.dll",
    "Qt6SpatialAudio*.dll",
    "Qt6UiTools*.dll",
    "Qt6ShaderTools*.dll",
    # 系统 / 外来运行库：Qt 与本程序都用 Windows 自带的那份。PyInstaller 会按文件名在
    # PATH 上解析二级依赖，命中第三方同名副本（poppler 的 icuuc.dll、libheif 的
    # ucrtbase.dll）就会把 ABI 不兼容的坏副本打进包，导致 QtWidgets 直接加载失败
    # （MEMORY 3.8）。tools/build_release.py 也会在打包前摘掉这些外来 PATH 目录。
    "icuuc.dll",
    "icuin.dll",
    "icudt*.dll",
    "ucrtbase.dll",
    "api-ms-win-*.dll",
)

#: 与上面被剔模块配套的插件目录
DROP_DATA_PATTERNS: tuple[str, ...] = (
    "qmltooling/*", "designer/*", "sqldrivers/*", "position/*", "sensors/*",
    "nfc/*", "scxml/*", "webview/*", "assetimporters/*", "geoservices/*",
)

#: 需要显式收集的 ffmpeg 共享库（名字模式，跨 PySide6 版本可用）
FFMPEG_PATTERNS: tuple[str, ...] = (
    "avcodec-*.dll", "avformat-*.dll", "avutil-*.dll", "swresample-*.dll", "swscale-*.dll",
)


def _data_tree(relative: str) -> list[tuple[str, str]]:
    """把资源目录整棵收进包，目标位置保持与仓库一致的相对路径。"""
    root = PROJECT / relative
    if not root.is_dir():
        raise SystemExit(f"缺少资源目录：{relative}（先跑 tools/fetch_vendor.py）")
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".pdb", ".pyc"}:
            continue
        entries.append((str(path), str(path.parent.relative_to(PROJECT))))
    if not entries:
        raise SystemExit(f"资源目录为空：{relative}")
    return entries


def _ffmpeg_binaries() -> list[tuple[str, str]]:
    """收集 PySide6 包根目录下的 ffmpeg 共享库，返回 (源文件, 目标目录) 列表。"""
    pyside_root = Path(PySide6.__file__).resolve().parent
    found: list[tuple[str, str]] = []
    matched: dict[str, list[str]] = {}
    for pattern in FFMPEG_PATTERNS:
        hits = sorted(pyside_root.glob(pattern))
        if not hits:
            raise SystemExit(
                f"PySide6 包根目录缺少 {pattern}：{pyside_root}\n"
                "音频预览会退化成「无后端」，检查 PySide6 安装是否完整。"
            )
        matched[pattern] = [str(p) for p in hits]
        for library in hits:
            found.append((str(library), "."))
            found.append((str(library), "PySide6"))
    print("[spec] 收集 ffmpeg 共享库：")
    for pattern, names in matched.items():
        for name in names:
            print(f"        {pattern:18s} -> {Path(name).name}")
    return found


datas: list[tuple[str, str]] = []
for _dir in DATA_DIRS:
    datas.extend(_data_tree(_dir))

binaries = _ffmpeg_binaries()

excludes = [f"PySide6.{name}" for name in UNUSED_QT_MODULES] + list(UNUSED_STDLIB)

# 版本资源由 tools/build_release.py 生成；缺失时不阻塞分析（例如手工试打包）
_version_file = PROJECT / "build" / "version_info.txt"
version = str(_version_file) if _version_file.is_file() else None

analysis = Analysis(
    [str(PROJECT / "main.py")],
    pathex=[str(PROJECT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["zipmerge"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)


def _drop_unused(entries, patterns: tuple[str, ...]) -> list:
    """按文件名模式剔掉不用的二进制 / 数据条目，并报告省下的体积。"""
    kept: list = []
    dropped = 0
    for entry in entries:
        name = str(entry[0]).replace("\\", "/")
        base = name.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(base.lower(), pattern.lower()) for pattern in patterns):
            try:
                dropped += Path(entry[1]).stat().st_size
            except OSError:
                pass
            continue
        kept.append(entry)
    print(f"[spec] 剔除 {len(entries) - len(kept)} 项，约 {dropped / 1024 / 1024:.1f} MB")
    return kept


analysis.binaries = _drop_unused(analysis.binaries, DROP_BINARY_PATTERNS)
analysis.datas = _drop_unused(analysis.datas, DROP_DATA_PATTERNS)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ZipMerge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT / "assets" / "icons" / "ZipMerge.ico"),
    version=version,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="ZipMerge",
)
