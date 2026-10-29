"""校验 dist/ZipMerge 打包结果的完整性（PLAN 11 / 12）。

检查的是「漏收资产就一定会踩到」的那几项，而不是走一遍全量验收：

* 入口 exe 与内置 7-Zip 资产（含 Rar/Rar5 解码器，PLAN 7.1）；
* 界面字体与图标（缺失会静默回退到系统字体 / 默认图标）；
* ffmpeg 共享库与 Qt 多媒体插件（缺失会让音频 / 视频预览退化成「无后端」，MEMORY 3.7）；
* 明确被排除的未使用 Qt 模块不应出现在包里（PLAN 11 的体积要求）。

最后用 `ZipMerge.exe --version` 确认打包产物真的能跑起来。
"""

from __future__ import annotations

import subprocess
import sys
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "dist" / "ZipMerge"
INTERNAL = BUNDLE / "_internal"
EXE = BUNDLE / "ZipMerge.exe"

REQUIRED_FILES: tuple[str, ...] = (
    "assets/fonts/FluentSystemIcons-Regular.ttf",
    "assets/fonts/FluentSystemIcons-Regular.json",
    "assets/fonts/NotoSansSC-Regular.otf",
    "assets/fonts/NotoSansSC-Medium.otf",
    "assets/icons/ZipMerge.ico",
    "assets/icons/ZipMergeArchive.ico",
    "vendor/7zip/7z.exe",
    "vendor/7zip/7z.dll",
    "vendor/7zip/License.txt",
    "PySide6/plugins/multimedia/ffmpegmediaplugin.dll",
    "PySide6/QtMultimediaWidgets.pyd",
    "PySide6/Qt6MultimediaWidgets.dll",
    "legal/THIRD_PARTY_NOTICES.txt",
    "legal/SOURCE_MANIFEST.json",
    "legal/FFMPEG_BUILD.json",
    "legal/LICENSE",
)

#: ffmpeg 共享库必须同时出现在包根与 PySide6 包目录（见 installer/zipmerge.spec 的说明）
FFMPEG_PATTERNS: tuple[str, ...] = (
    "avcodec-*.dll",
    "avformat-*.dll",
    "avutil-*.dll",
    "swresample-*.dll",
    "swscale-*.dll",
)

#: PLAN 11 要求排除的未使用模块
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "opengl32sw.dll",
    "Qt6WebEngine*.dll",
    "Qt6Quick*.dll",
    "Qt6Qml*.dll",
    "Qt63D*.dll",
    "Qt6Pdf*.dll",
    "Qt6OpenGL*.dll",
    "numpy",
    "numpy.libs",
    "PySide6/QtWebEngine*",
    "PySide6/QtQuick*",
    "PySide6/QtQml*",
    "PySide6/Qt3D*",
    "PySide6/Qt6VirtualKeyboard*.dll",
    "PySide6/QtVirtualKeyboard*",
    "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
    # 系统 / 外来运行库：Qt 用 Windows 自带的那份。第三方同名副本（poppler 的 ICU、
    # libheif 的 ucrtbase）ABI 不兼容，进包就会让 QtWidgets 加载失败（MEMORY 3.8）
    "icuuc.dll",
    "icuin.dll",
    "icudt*.dll",
    "ucrtbase.dll",
    "api-ms-win-*.dll",
)

REQUIRED_CODECS: tuple[str, ...] = ("Rar", "Rar5")


def check_sevenzip(problems: list[str]) -> str:
    sevenzip = INTERNAL / "vendor" / "7zip" / "7z.exe"
    if not sevenzip.is_file():
        return "(缺少 7z.exe)"
    result = subprocess.run(
        [str(sevenzip), "i"], capture_output=True, text=True, errors="replace"
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        problems.append("包内 7z.exe i 执行失败")
        return "(执行失败)"
    lower = output.lower()
    for codec in REQUIRED_CODECS:
        if codec.lower() not in lower:
            problems.append(f"包内 7z.exe 缺少解码器 {codec}")
    return next((line.strip() for line in output.splitlines() if line.strip()), "(未知版本)")


def check_exe(problems: list[str]) -> str:
    try:
        result = subprocess.run(
            [str(EXE), "--version"], capture_output=True, text=True, errors="replace", timeout=60
        )
    except subprocess.TimeoutExpired:
        problems.append("ZipMerge.exe --version 超时未退出（窗口模式下崩溃会弹阻塞对话框）")
        return "(超时)"
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        problems.append(f"ZipMerge.exe --version 退出码 {result.returncode}：{output}")
    return output or "(无输出)"


def check_legal(problems: list[str]) -> None:
    """Reject missing/tampered notices and binary/manifest FFmpeg mismatches."""
    legal = INTERNAL / "legal"
    try:
        hashes = json.loads((legal / "NOTICE_HASHES.json").read_text(encoding="utf-8"))
        if not hashes:
            raise ValueError("empty legal file inventory")
        for relative, expected in hashes.items():
            path = (legal / relative).resolve()
            if not path.is_relative_to(legal.resolve()):
                raise ValueError("legal inventory path escapes the legal directory")
            with path.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
                    raise ValueError(f"notice hash mismatch: {relative}")
        info = json.loads((legal / "FFMPEG_BUILD.json").read_text(encoding="utf-8"))
        if info["license"] != "LGPL version 2.1 or later":
            raise ValueError("unexpected FFmpeg license")
        if any(flag in info["configuration"].split() for flag in ("--enable-gpl", "--enable-nonfree", "--enable-version3")):
            raise ValueError("FFmpeg license-changing option is enabled")
        for name, expected in info["libraries"].items():
            if Path(name).name != name:
                raise ValueError("invalid FFmpeg library name")
            for directory in (INTERNAL, INTERNAL / "PySide6"):
                with (directory / name).open("rb") as handle:
                    if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
                        raise ValueError(f"FFmpeg binary does not match legal inventory: {name}")
    except (OSError, ValueError, KeyError) as exc:
        problems.append(f"许可资料验证失败：{exc}")


def main() -> int:
    problems: list[str] = []
    if not EXE.is_file():
        print(f"缺少 {EXE.relative_to(ROOT)}，先运行 tools/build_release.py", file=sys.stderr)
        return 1

    for relative in REQUIRED_FILES:
        if not (INTERNAL / relative).is_file():
            problems.append(f"缺少资源 {relative}")

    for pattern in FFMPEG_PATTERNS:
        root_hits = sorted(INTERNAL.glob(pattern))
        package_hits = sorted((INTERNAL / "PySide6").glob(pattern))
        if not root_hits:
            problems.append(f"_internal 根缺少 {pattern}")
        if not package_hits:
            problems.append(f"_internal/PySide6 缺少 {pattern}")

    for pattern in FORBIDDEN_PATTERNS:
        hits = sorted(INTERNAL.glob(pattern))
        if hits:
            problems.append(f"未使用模块被打进包：{', '.join(p.name for p in hits[:3])}")

    sevenzip_line = check_sevenzip(problems)
    exe_line = check_exe(problems)
    check_legal(problems)
    total = sum(item.stat().st_size for item in BUNDLE.rglob("*") if item.is_file())

    if problems:
        print("打包校验失败：", file=sys.stderr)
        for item in problems:
            print("  " + item, file=sys.stderr)
        return 1

    print(f"打包校验通过：{BUNDLE.relative_to(ROOT)}（{total / 1024 / 1024:.1f} MB）")
    print(f"  入口：ZipMerge.exe --version -> {exe_line}")
    print(f"  引擎：{sevenzip_line}")
    print(f"  资源：{len(REQUIRED_FILES)} 项齐全，ffmpeg 共享库在包根与 PySide6 目录各一份")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
