"""Name transformations and final-state validation for virtual tree edits."""

from __future__ import annotations

import ntpath
from collections import Counter
from dataclasses import dataclass

from zipmerge.i18n import tr
from ..util.paths import ARCHIVE_COMPOUND_SUFFIXES
from .nodes import TargetNode, validate_name


def name_parts(node: TargetNode) -> tuple[str, str]:
    if node.is_dir:
        return node.name, ""
    for suffix in ARCHIVE_COMPOUND_SUFFIXES:
        if node.name.lower().endswith(suffix) and len(node.name) > len(suffix):
            return node.name[:-len(suffix)], node.name[-len(suffix):]
    return ntpath.splitext(node.name)


@dataclass(frozen=True)
class ReplaceRule:
    mode: str = "literal"
    find: str = ""
    replacement: str = ""
    start: int = 1
    count: int = 1

    def apply(self, text: str) -> str:
        if self.mode == "literal":
            return text.replace(self.find, self.replacement) if self.find else text
        if self.mode == "first":
            left, right = 0, min(self.count, len(text))
        elif self.mode == "last":
            left, right = max(0, len(text) - self.count), len(text)
        elif self.mode == "range":
            left = self.start - 1
            right = left + self.count
        else:
            pivot = text.find(self.find) if self.find else -1
            if pivot < 0:
                return text
            if self.mode in ("before", "before_n"):
                right = pivot
                left = max(0, right - self.count) if self.mode == "before_n" else 0
            elif self.mode in ("after", "after_n"):
                left = pivot + len(self.find)
                right = left + self.count if self.mode == "after_n" else len(text)
            else:
                raise ValueError(self.mode)
        if left >= len(text) or left == right:
            return text
        return text[:left] + self.replacement + text[right:]


def replace_name(node: TargetNode, rules: list[ReplaceRule], include_extension: bool) -> str:
    text, suffix = (node.name, "") if include_extension else name_parts(node)
    for rule in rules:
        text = rule.apply(text)
    return text + suffix


def insert_name(node: TargetNode, content: str, position: str, start: int = 1) -> str:
    stem, suffix = name_parts(node)
    index = {"start": 0, "end": len(stem), "position": max(0, start - 1)}[position]
    return stem[:index] + content + stem[index:] + suffix


def sequence_number(value: int, style: str, width: int = 1) -> str:
    if value < 1:
        raise ValueError(tr("序号必须从 1 或更大的数字开始"))
    if style == "decimal":
        return str(value).zfill(width)
    if style in ("lower", "upper"):
        letters = ""
        while value:
            value, digit = divmod(value - 1, 26)
            letters = chr(65 + digit) + letters
        return letters.lower() if style == "lower" else letters
    if style not in ("chinese", "financial"):
        raise ValueError(style)
    financial = style == "financial"
    digits = "零壹贰叁肆伍陆柒捌玖" if financial else "零一二三四五六七八九"
    units = ("", "拾", "佰", "仟") if financial else ("", "十", "百", "千")
    groups = []
    while value:
        value, group = divmod(value, 10000)
        groups.append(group)
    result = ""
    skipped = False
    for power in reversed(range(len(groups))):
        group = groups[power]
        if not group:
            skipped = True
            continue
        if result and (skipped or group < 1000):
            result += digits[0]
        skipped = False
        part = ""
        zero = False
        for place in (3, 2, 1, 0):
            digit = group // (10 ** place) % 10
            if digit:
                part += (digits[0] if zero else "") + digits[digit] + units[place]
                zero = False
            elif part:
                zero = True
        result += part + ("", "万", "亿", "兆")[power]
    return result[1:] if not financial and result.startswith("一十") else result


def rename_errors(root: TargetNode, names: dict[TargetNode, str]) -> dict[TargetNode, str]:
    """Check final sibling names together, allowing swaps and case-only edits."""
    errors = {}
    parents = set()
    for node, name in names.items():
        if not root.is_ancestor_of(node) or node.parent is None:
            errors[node] = tr("条目已不在当前目标结构中")
            continue
        if name == node.name:
            continue
        parents.add(node.parent)
        reason = validate_name(name)
        if not reason and ntpath.isreserved(name):
            reason = tr("名称包含 Windows 保留名称、字符或末尾空格")
        if not reason and len(name.encode("utf-16-le", "surrogatepass")) > 510:
            reason = tr("名称过长（超过 255 个 UTF-16 单元）")
        if reason:
            errors[node] = reason
    for parent in parents:
        counts = Counter(names.get(n, n.name).casefold() for n in parent.children)
        for node in parent.children:
            if node in names and names[node] != node.name and counts[names[node].casefold()] > 1:
                errors.setdefault(node, tr("同一目录中已有相同的目标名称"))
    return errors
