"""Interpret stable releases from the ZipMerge GitHub repository."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from zipmerge.i18n import tr

HOMEPAGE = "https://github.com/Meidios/ZipMerge"
RELEASES = HOMEPAGE + "/releases"
LATEST_API = "https://api.github.com/repos/Meidios/ZipMerge/releases/latest"
MAX_METADATA = 1024 * 1024
MAX_INSTALLER = 512 * 1024 * 1024
VERSION = r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})"


@dataclass(frozen=True)
class Installer:
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Release:
    version: str
    installer: Installer | None
    notes: str = ""

    @property
    def page(self) -> str:
        return f"{RELEASES}/tag/v{self.version}"


def available_release(data: bytes, current: str) -> Release | None:
    """Return a newer stable release; missing integrity data permits page access only."""
    if len(data) > MAX_METADATA:
        raise ValueError(tr("GitHub 返回的数据过大"))
    info = json.loads(data)
    if not isinstance(info, dict) or not all(type(info.get(k)) is bool for k in ("draft", "prerelease")):
        raise ValueError(tr("GitHub 返回的版本信息不完整"))
    if info["draft"] or info["prerelease"]:
        return None
    tag = info.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch("v" + VERSION, tag):
        raise ValueError(tr("无法识别 GitHub 上的版本号"))
    version = tag[1:]
    if tuple(map(int, version.split("."))) <= tuple(map(int, current.split("."))):
        return None
    name = f"ZipMerge-Setup-{version}.exe"
    url = f"{RELEASES}/download/{tag}/{name}"
    assets = info.get("assets")
    body = info.get("body")
    notes = body.strip() if isinstance(body, str) else ""
    for asset in assets if isinstance(assets, list) else ():
        if not isinstance(asset, dict) or asset.get("name") != name or asset.get("state") != "uploaded":
            continue
        digest, size = asset.get("digest"), asset.get("size")
        if (asset.get("browser_download_url") == url and type(size) is int and 0 < size <= MAX_INSTALLER
                and isinstance(digest, str) and re.fullmatch("sha256:[a-f0-9]{64}", digest)):
            return Release(version, Installer(url, size, digest[7:]), notes)
    return Release(version, None, notes)


def is_upgrade(previous: object, current: str) -> bool:
    return (isinstance(previous, str) and re.fullmatch(VERSION, previous) is not None
            and tuple(map(int, previous.split("."))) < tuple(map(int, current.split("."))))


def localized_notes(body: str, language: str) -> str:
    """Select a release's language section, leaving unsectioned author notes intact."""
    names = {"zh_CN": "简体中文", "zh_TW": "繁體中文", "en": "English"}
    headings = list(re.finditer(r"^##[ \t]+(简体中文|繁體中文|English)[ \t]*\r?$", body, re.M))
    sections = {m[1]: body[m.end():(headings[i+1].start() if i+1 < len(headings) else len(body))].strip()
                for i, m in enumerate(headings)}
    for name in (names.get(language), "简体中文" if language == "zh_TW" else "English", *sections):
        if sections.get(name):
            return sections[name]
    return body.strip()
