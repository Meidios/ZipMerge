"""Read-only Office content for the preview pane. OPC parts are read with bundled 7-Zip."""

from __future__ import annotations

import posixpath
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from ..i18n import tr
from ..util.paths import long_path, sevenzip_exe

WORD_EXT = {".docx", ".docm"}
EXCEL_EXT = {".xlsx", ".xlsm"}
LEGACY_EXT = {".doc", ".xls"}
DOCUMENT_EXT = WORD_EXT | EXCEL_EXT | LEGACY_EXT | {".pdf"}

MAX_PART_BYTES = 12 * 1024 * 1024
MAX_TEXT = 250_000
MAX_ROWS = 500
MAX_COLS = 64


class _ContentOnly(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError(tr("文档包含不支持的 XML 声明"))


def _part(path: Path, name: str) -> ET.Element:
    # Stream exactly one literal ZIP part. Nothing from the inner document is
    # extracted onto disk, and the byte ceiling applies to the actual output.
    args = [str(sevenzip_exe()), "e", "-so", "-tzip", "-spd", "--", long_path(path), name]
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW) as proc:
        try:
            data = proc.stdout.read(MAX_PART_BYTES + 1)
            if len(data) > MAX_PART_BYTES:
                proc.kill()
                raise ValueError(tr("文档内容超过预览上限"))
        finally:
            proc.stdout.close()
        if proc.wait() != 0:
            raise ValueError(tr("无法读取文档，文件可能已加密或损坏"))
    try:
        return ET.fromstring(data, parser=ET.XMLParser(target=_ContentOnly()))
    except ET.ParseError as exc:
        raise ValueError(tr("无法读取文档，文件可能已加密或损坏")) from exc


def word_text(path: Path) -> str:
    document = _part(path, "word/document.xml")
    body = document.find("{*}body")
    if body is None:
        raise ValueError(tr("无法读取文档，文件可能已加密或损坏"))
    paragraphs = []
    length = 0
    def paragraph_text(paragraph):
        pieces = []
        for item in paragraph.iter():
            tag = item.tag.rsplit("}", 1)[-1]
            if tag == "t":
                pieces.append(item.text or "")
            elif tag == "tab":
                pieces.append("\t")
            elif tag in {"br", "cr"}:
                pieces.append("\n")
        return "".join(pieces)

    def lines(element):
        for child in element:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                yield paragraph_text(child)
            elif tag == "tr":
                yield "\t".join(" ".join(lines(cell)) for cell in child.findall("{*}tc"))
            elif tag in {"tbl", "sdt", "sdtContent", "customXml", "ins"}:
                yield from lines(child)

    for line in lines(body):
        paragraphs.append(line)
        length += len(line) + 1
        if length > MAX_TEXT:
            return "\n".join(paragraphs)[:MAX_TEXT] + "\n" + tr("已达到预览上限")
    return "\n".join(paragraphs)


@dataclass(frozen=True)
class Workbook:
    sheets: list[tuple[str, str]]
    strings: list[str]


def _cell_text(element: ET.Element) -> str:
    # Rich strings may also contain phonetic guides; only display the actual runs.
    return (element.findtext("{*}t", "") +
            "".join(t.text or "" for t in element.findall("{*}r/{*}t")))


def read_workbook(path: Path) -> Workbook:
    book = _part(path, "xl/workbook.xml")
    rels = _part(path, "xl/_rels/workbook.xml.rels")
    targets = {}
    shared_part = None
    for rel in rels:
        if rel.get("TargetMode") == "External":
            continue
        target = posixpath.normpath(posixpath.join("xl", rel.get("Target", ""))).lstrip("/")
        if not target.startswith("xl/") or "\\" in target or ":" in target:
            continue
        kind = rel.get("Type", "").rsplit("/", 1)[-1]
        if kind == "worksheet":
            targets[rel.get("Id")] = target
        elif kind == "sharedStrings":
            shared_part = target
    sheets = []
    for sheet in book.findall("{*}sheets/{*}sheet"):
        rid = next((val for key, val in sheet.attrib.items() if key.endswith("}id")), None)
        if rid in targets and sheet.get("state", "visible") == "visible":
            sheets.append((sheet.get("name", ""), targets[rid]))
    if not sheets:
        raise ValueError(tr("工作簿没有可预览的工作表"))
    strings = []
    if shared_part:
        shared = _part(path, shared_part)
        strings = [_cell_text(item) for item in shared.findall("{*}si")]
    return Workbook(sheets, strings)


@dataclass(frozen=True)
class Sheet:
    cells: dict[tuple[int, int], str]
    rows: int
    columns: int
    truncated: bool


def column_label(index: int) -> str:
    label = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        label = chr(65 + remainder) + label
    return label


def read_sheet(path: Path, book: Workbook, index: int) -> Sheet:
    root = _part(path, book.sheets[index][1])
    cells = {}
    rows = columns = previous_row = 0
    truncated = False
    for row in root.findall("{*}sheetData/{*}row"):
        row_no = int(row.get("r", previous_row + 1))
        previous_row = row_no
        if not 1 <= row_no <= MAX_ROWS:
            truncated = True
            continue
        column = 0
        for cell in row.findall("{*}c"):
            ref = cell.get("r")
            if ref is None:
                column += 1
            else:
                match = re.fullmatch(r"([A-Z]{1,3})[1-9][0-9]{0,6}", ref)
                if not match:
                    continue
                column = 0
                for char in match[1]:
                    column = column * 26 + ord(char) - 64
            if column > MAX_COLS:
                truncated = True
                continue
            kind = cell.get("t")
            value = cell.findtext("{*}v", "")
            if kind == "s" and value.isdecimal():
                n = int(value)
                value = book.strings[n] if n < len(book.strings) else ""
            elif kind == "inlineStr":
                inline = cell.find("{*}is")
                value = _cell_text(inline) if inline is not None else ""
            elif kind == "b":
                value = "TRUE" if value == "1" else "FALSE"
            elif not value and (formula := cell.findtext("{*}f")):
                value = "=" + formula
            if value:
                cells[row_no - 1, column - 1] = value
                rows = max(rows, row_no)
                columns = max(columns, column)
    return Sheet(cells, rows, columns, truncated)
