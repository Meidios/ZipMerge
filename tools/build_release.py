"""产出 ZipMerge 分发包（PLAN 11）。

用法::

    python tools/build_release.py                  # PyInstaller onedir + Inno Setup 安装包
    python tools/build_release.py --skip-installer # 只打包 onedir
    python tools/build_release.py --reuse-bundle   # 复用已有 dist，只重新生成 Setup.exe

版本号唯一来源是 `src/zipmerge/__init__.py` 的 `__version__`：

* 交给 PyInstaller 的 EXE 版本资源由本脚本生成 `build/version_info.txt`；
* 交给 Inno Setup 的版本号通过 `ISCC /DMyAppVersion=x.y.z` 注入，`.iss` 里不写死。

Inno Setup 未安装时只产出 onedir，并明确提示缺失的编译器和获取方式（不静默跳过）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SPEC = ROOT / "installer" / "zipmerge.spec"
ISS = ROOT / "installer" / "zipmerge.iss"
DIST = ROOT / "dist"
BUNDLE = DIST / "ZipMerge"
WORK = ROOT / "build" / "pyinstaller"
VERSION_INFO = ROOT / "build" / "version_info.txt"

#: 打包前要从 PATH 摘掉的目录标记。Codex 等工具链会把自带的 native 目录注入 PATH，
#: 里面有与 Qt 依赖同名的**不兼容**副本（poppler 的 icuuc.dll 符号带版本后缀、
#: libheif 的 ucrtbase.dll / api-ms-win-*.dll）。PyInstaller 按文件名解析二级依赖时
#: 会命中它们并打进包，结果是 QtWidgets 加载失败（MEMORY 3.8）。
FOREIGN_PATH_MARKERS: tuple[str, ...] = ("codex-runtimes",)

#: ISCC 的常见安装位置（含免管理员的当前用户安装）
_ISCC_CANDIDATES: tuple[Path, ...] = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
)


def app_version() -> str:
    sys.path.insert(0, str(SRC))
    from zipmerge import __version__  # noqa: PLC0415 - 需要在 sys.path 就绪后导入

    return __version__


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = [int(piece) for piece in version.split(".") if piece.isdigit()][:4]
    return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]


def write_version_info(version: str) -> Path:
    """生成 PyInstaller 的 EXE 版本资源文件（Python 表达式文本，由 PyInstaller eval）。"""
    major, minor, patch, build = _version_tuple(version)
    VERSION_INFO.parent.mkdir(parents=True, exist_ok=True)
    VERSION_INFO.write_text(
        f"""# 由 tools/build_release.py 生成，请勿手工编辑
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {build}),
    prodvers=({major}, {minor}, {patch}, {build}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404B0',
        [StringStruct('CompanyName', 'MEI'),
         StringStruct('FileDescription', '多压缩包合并工作台'),
         StringStruct('FileVersion', '{version}'),
         StringStruct('InternalName', 'ZipMerge'),
         StringStruct('LegalCopyright', 'Copyright (C) 2026 MEI'),
         StringStruct('OriginalFilename', 'ZipMerge.exe'),
         StringStruct('ProductName', 'ZipMerge'),
         StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return VERSION_INFO


def sanitized_env() -> dict[str, str]:
    """去掉 PATH 上的外来 native 目录，避免收集到不兼容的同名 DLL。"""
    env = dict(os.environ)
    entries = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    kept = [part for part in entries if not any(m in part.lower() for m in FOREIGN_PATH_MARKERS)]
    if len(kept) != len(entries):
        print(f"已从 PATH 摘除 {len(entries) - len(kept)} 个外来 native 目录")
    env["PATH"] = os.pathsep.join(kept)
    return env


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> int:
    print("$ " + " ".join(f'"{part}"' if " " in part else part for part in command))
    return subprocess.run(command, cwd=cwd, env=env).returncode


def clean_previous() -> None:
    """只清理上次的打包产物，保留 build/cache 里的外部资产缓存。"""
    for target in (WORK, BUNDLE):
        if target.exists():
            shutil.rmtree(target)
            print(f"已清理 {target.relative_to(ROOT)}")


def build_bundle() -> None:
    code = run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--log-level=WARN",
            "--distpath",
            str(DIST),
            "--workpath",
            str(WORK),
            str(SPEC),
        ],
        env=sanitized_env(),
    )
    if code != 0:
        raise SystemExit(f"PyInstaller 失败（退出码 {code}）")
    exe = BUNDLE / "ZipMerge.exe"
    if not exe.is_file():
        raise SystemExit(f"打包结果缺少 {exe.relative_to(ROOT)}")
    print(f"\n已产出 {BUNDLE.relative_to(ROOT)}（{tree_size(BUNDLE) / 1024 / 1024:.1f} MB）")


def tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def find_iscc() -> Path | None:
    for candidate in _ISCC_CANDIDATES:
        if candidate.is_file():
            return candidate
    local = Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "ISCC.exe"
    if local.is_file():
        return local
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    return Path(found) if found else None


def build_installer(version: str) -> None:
    iscc = find_iscc()
    if iscc is None:
        print(
            "\n[跳过] 未找到 Inno Setup 编译器 ISCC.exe，未能产出 Setup.exe。\n"
            "        获取方式：winget install --id JRSoftware.InnoSetup -e\n"
            "        安装后重跑：python tools/build_release.py --reuse-bundle"
        )
        return
    code = run([str(iscc), f"/DMyAppVersion={version}", str(ISS)], cwd=ISS.parent)
    if code != 0:
        raise SystemExit(f"ISCC 失败（退出码 {code}）")
    setup = ISS.parent / "Output" / f"ZipMerge-Setup-{version}.exe"
    if not setup.is_file():
        raise SystemExit(f"安装包未生成：{setup}")
    print(f"已产出 {setup.relative_to(ROOT)}（{setup.stat().st_size / 1024 / 1024:.1f} MB）")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="产出 ZipMerge 分发包")
    parser.add_argument("--skip-installer", action="store_true", help="只打包 onedir，不生成安装包")
    parser.add_argument("--reuse-bundle", action="store_true", help="复用已有 dist，只重新生成安装包")
    parser.add_argument("--clean", action="store_true", help="先清理上次的 dist 与 PyInstaller 工作目录")
    args = parser.parse_args(argv)

    version = app_version()
    print(f"ZipMerge {version}")

    if args.clean:
        clean_previous()

    if not args.reuse_bundle:
        write_version_info(version)
        build_bundle()
    elif not (BUNDLE / "ZipMerge.exe").is_file():
        raise SystemExit("没有可复用的 dist/ZipMerge，先去掉 --reuse-bundle 重新打包")

    if not args.skip_installer:
        write_version_info(version)
        build_installer(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
