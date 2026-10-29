"""输出命名模板：字段拼接、非法字符替换、版本递增、内容标识建议。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..util.paths import sanitize_filename

# 占位符顺序即设置页的字段顺序
TEMPLATE_FIELDS: tuple[str, ...] = ("项目名", "需求名", "内容标识", "日期", "版本")
DEFAULT_TEMPLATE = "{项目名}_{需求名}_{内容标识}_{日期}_{版本}"

_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")

# 源包名中的内容标识关键词（小写匹配），命中顺序决定拼接顺序
_CONTENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SFX", ("sfx", "sound_effect", "soundeffect", "音效", "效果音", "特效")),
    ("VO", ("vo", "voice", "vocals", "vocal", "配音", "人声", "语音", "对白")),
    ("MUSIC", ("music", "bgm", "配乐", "音乐")),
    ("AMB", ("amb", "ambience", "ambiance", "环境音", "氛围")),
)


@dataclass
class NamingFields:
    project: str = ""
    requirement: str = ""
    content: str = ""
    date: str = ""
    version: str = "V1"

    @classmethod
    def today(cls) -> NamingFields:
        return cls(date=date.today().strftime("%Y%m%d"))

    def as_template_values(self) -> dict[str, str]:
        return {
            "项目名": self.project,
            "需求名": self.requirement,
            "内容标识": self.content,
            "日期": self.date,
            "版本": self.version,
        }


def render_filename(template: str, fields: NamingFields) -> str:
    """渲染模板为文件名主体（不含扩展名），已做非法字符替换与空白整理。"""
    values = fields.as_template_values()

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return values.get(key, match.group(0))

    rendered = _PLACEHOLDER_RE.sub(substitute, template or DEFAULT_TEMPLATE)
    # 拼接产生连续分隔符时收敛为一个，避免出现 __ 或首尾多余分隔符
    rendered = re.sub(r"[\s]+", " ", rendered)
    rendered = re.sub(r"_{2,}", "_", rendered)
    rendered = rendered.strip(" _-&")
    return sanitize_filename(rendered) or "未命名"


def suggest_content_tag(source_names: list[str]) -> str:
    """按源包名中的关键词给出内容标识建议，未命中返回空串。"""
    haystack = " ".join(source_names).lower()
    tags: list[str] = []
    for tag, keywords in _CONTENT_KEYWORDS:
        for keyword in keywords:
            if _keyword_present(haystack, keyword):
                tags.append(tag)
                break
    return "&".join(tags)


def _keyword_present(haystack: str, keyword: str) -> bool:
    """关键词一律按词边界匹配，避免 ``voice`` 命中 ``invoice`` 之类的误判。

    边界只排除英文字母与数字，因此中文关键词（如「音效」）仍可在中文串中命中。
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", haystack) is not None


# stem / 分隔符 / 版本标记 / 序号分别捕获，便于原样保留大小写与分隔符
_VERSION_RE = re.compile(r"^(?P<stem>.*?)(?P<sep>[ _-]?)(?P<mark>[vV])(?P<num>\d+)$")


def bump_version(version: str) -> str:
    """V1 → V2；v9 → v10（保留原前缀大小写）；无法识别时追加 _1。"""
    match = _VERSION_RE.match(version.strip()) if version else None
    if match:
        return (
            f"{match.group('stem')}{match.group('sep')}"
            f"{match.group('mark')}{int(match.group('num')) + 1}"
        )
    return f"{version}_1" if version else "V2"


def unique_output_path(directory: Path, base_name: str, extension: str = ".zip") -> tuple[Path, bool]:
    """返回可用的输出路径；已存在同名文件时递增版本号。

    返回 ``(路径, 是否发生了版本递增)``。
    """
    candidate = directory / f"{base_name}{extension}"
    if not candidate.exists():
        return candidate, False

    version_match = _VERSION_RE.match(base_name)
    if version_match:
        stem = f"{version_match.group('stem')}{version_match.group('sep')}"
        mark = version_match.group("mark")
        number = int(version_match.group("num"))
    else:
        stem, mark, number = base_name, "V", 0

    while True:
        number += 1
        candidate = directory / f"{stem}{mark}{number}{extension}"
        if not candidate.exists():
            return candidate, True
