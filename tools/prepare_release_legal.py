"""Prepare verified source archives and complete notices for Windows releases.

All extraction uses the bundled 7-Zip. Upstream archives remain unmodified and
are shipped alongside the installer, rather than relying on future URL uptime.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.metadata
import json
import shutil
import ssl
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "build/release-source-cache"
LEGAL = ROOT / "build/release-legal"
SEVENZIP = ROOT / "vendor/7zip/7z.exe"
SOURCE_CONFIG = ROOT / "config/sources.json"


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def check_ffmpeg(license_name: str, configuration: str) -> None:
    if "LGPL version 2.1 or later" != license_name:
        raise ValueError(f"Unapproved FFmpeg license: {license_name}")
    if any(flag in configuration.split() for flag in ("--enable-gpl", "--enable-nonfree", "--enable-version3")):
        raise ValueError("FFmpeg build enables a license-changing option")
    if "--enable-shared" not in configuration.split():
        raise ValueError("FFmpeg must remain replaceable shared libraries")


def runtime_manifest() -> dict:
    import PySide6
    from PySide6.QtCore import QLibraryInfo, qVersion

    versions = {name: importlib.metadata.version(name) for name in
                ("PySide6", "shiboken6", "Pillow", "PyInstaller")}
    expected = {"PySide6": "6.10.2", "shiboken6": "6.10.2", "Pillow": "12.1.0", "PyInstaller": "6.18.0"}
    if versions != expected or sys.version_info[:3] != (3, 13, 14) or qVersion() != "6.10.2":
        raise ValueError(f"Release runtime differs from the locked source versions: {versions}, {sys.version}")
    if ssl.OPENSSL_VERSION.split()[1] != "3.0.21":
        raise ValueError(f"Unexpected OpenSSL build: {ssl.OPENSSL_VERSION}")
    pyside = Path(PySide6.__file__).parent
    avutil = ctypes.CDLL(str(pyside / "avutil-59.dll"))
    avutil.av_version_info.restype = ctypes.c_char_p
    avutil.avutil_license.restype = ctypes.c_char_p
    avutil.avutil_configuration.restype = ctypes.c_char_p
    info = {"version": avutil.av_version_info().decode(),
            "license": avutil.avutil_license().decode(),
            "configuration": avutil.avutil_configuration().decode()}
    if not info["version"].startswith("7.1.2"):
        raise ValueError(f"Unexpected FFmpeg source version: {info['version']}")
    check_ffmpeg(info["license"], info["configuration"])
    info["libraries"] = {p.name: sha256(p) for pattern in ("av*-*.dll", "sw*-*.dll") for p in pyside.glob(pattern)}
    return {"python": ".".join(map(str, sys.version_info[:3])), "python_build": sys.version,
            "packages": versions, "qt": qVersion(), "qt_build": QLibraryInfo.build(),
            "openssl": ssl.OPENSSL_VERSION, "ffmpeg": info,
            "project_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "pyinstaller_hooks": importlib.metadata.version("pyinstaller-hooks-contrib")}


def obtain_source(item: dict, cache: Path = CACHE) -> Path:
    filename = item["filename"]
    if Path(filename).name != filename or filename in (".", ".."):
        raise ValueError("Source filename must be a single safe path component")
    target = cache / filename
    cache.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        request = urllib.request.Request(item["url"], headers={"User-Agent": "ZipMerge-release-preparation"})
        part = target.with_suffix(target.suffix + ".part")
        try:
            with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as out:
                shutil.copyfileobj(response, out, 1 << 20)
            if sha256(part) != item["sha256"]:
                raise ValueError(f"Source hash mismatch: {filename}")
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
    if sha256(target) != item["sha256"]:
        raise ValueError(f"Cached source hash mismatch: {filename}")
    return target


def sevenzip(*args: str, cwd: Path = ROOT) -> None:
    subprocess.run([str(SEVENZIP), *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def extract_notices(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    # 7-Zip performs both outer decompression and archive extraction.
    if archive.name.endswith(".tar.xz"):
        stage = CACHE / "expanded" / archive.stem
        stage.mkdir(parents=True, exist_ok=True)
        sevenzip("x", "-y", f"-o{stage}", "--", str(archive))
        archive = next(stage.glob("*.tar"))
    patterns = ("*LICENSE*", "*LICENCE*", "*COPYING*", "*COPYRIGHT*", "*NOTICE*", "*AUTHORS*",
                "*copyright*", "*license*", "*licence*", "*README*", "*qt_attribution.json")
    if archive.name.startswith("pyinstaller-"):
        # Its news/README.txt is an upstream relative symlink. Keep 7-Zip's
        # safety rejection intact; COPYING and license files are regular files.
        patterns = tuple(p for p in patterns if p != "*README*")
    sevenzip("x", "-y", f"-o{destination}", *(f"-ir!{p}" for p in patterns), "--", str(archive))
    if not any(destination.rglob("*")):
        raise ValueError(f"No attribution files extracted from {archive.name}")


def prepare() -> None:
    runtime = runtime_manifest()
    config = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    sources = config["release_sources"]
    if LEGAL.exists():
        resolved = LEGAL.resolve()
        if resolved != (ROOT / "build/release-legal").resolve() or not resolved.is_relative_to((ROOT / "build").resolve()):
            raise ValueError("Refuse to clean outside the release legal build directory")
        shutil.rmtree(resolved)
    LEGAL.mkdir(parents=True)
    for item in sources:
        archive = obtain_source(item)
        print(f"Verified source: {archive.name}", flush=True)
        extract_notices(archive, LEGAL / "upstream" / item["name"])
    for item in config["release_notices"]:
        original = obtain_source(item)
        shutil.copy2(original, LEGAL / original.name)
        if original.suffix == ".docx":
            xml = subprocess.check_output([str(SEVENZIP), "e", "-so", str(original), "word/document.xml"])
            document = ET.fromstring(xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs = ["".join(p.itertext()) for p in document.findall(".//w:p", ns)]
            (LEGAL / "Microsoft-VC-Runtime-License.txt").write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
    shutil.copytree(ROOT / "licenses", LEGAL / "project-assets")
    shutil.copy2(ROOT / "LICENSE", LEGAL / "LICENSE")
    shutil.copy2(ROOT / "vendor/7zip/License.txt", LEGAL / "7-Zip-License.txt")
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", LEGAL / "Python-distribution-LICENSE.txt")
    dist = importlib.metadata.distribution("PyInstaller")
    entry = next(p for p in dist.files if str(p).endswith("licenses/COPYING.txt"))
    shutil.copy2(dist.locate_file(entry), LEGAL / "PyInstaller-COPYING.txt")
    # Qt's FFmpeg provisioning/build scripts are included in the source sidecar.
    shutil.copy2(ROOT / "docs/BINARY_DISTRIBUTION.md", LEGAL / "THIRD_PARTY_NOTICES.txt")
    (LEGAL / "SOURCE_MANIFEST.json").write_text(json.dumps({"runtime": runtime, "sources": sources, "notice_sources": config["release_notices"]}, indent=2) + "\n", encoding="utf-8")
    (LEGAL / "FFMPEG_BUILD.json").write_text(json.dumps(runtime["ffmpeg"], indent=2) + "\n", encoding="utf-8")
    files = {str(p.relative_to(LEGAL)).replace("\\", "/"): sha256(p) for p in sorted(LEGAL.rglob("*")) if p.is_file()}
    (LEGAL / "NOTICE_HASHES.json").write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(files)} legal files in build/release-legal")


if __name__ == "__main__":
    prepare()
