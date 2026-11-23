from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPainter, QPdfWriter
from PySide6.QtWidgets import QApplication

from zipmerge.core import MergeSession
from zipmerge.core import document_preview as docs
from zipmerge.engine import create_archive
from zipmerge.ui.preview_panel import PreviewPanel
from .test_video_preview import wait_for


def office_package(path, parts):
    with tempfile.TemporaryDirectory() as stage:
        for part, xml in parts.items():
            file = Path(stage, part)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(xml, encoding="utf-8")
        create_archive(path, "zip", stage)


class DocumentPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def workbook(self):
        path = self.root / "对白清单.xlsx"
        office_package(path, {
            "xl/workbook.xml": '<workbook xmlns:r="urn:rels"><sheets>'
                '<sheet name="对白" r:id="b"/><sheet name="音效" r:id="a"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": '<Relationships>'
                '<Relationship Id="a" Type="test/worksheet" Target="/xl/worksheets/first.xml"/>'
                '<Relationship Id="b" Type="test/worksheet" Target="worksheets/second.xml"/>'
                '<Relationship Id="s" Type="test/sharedStrings" Target="sharedStrings.xml"/>'
                '</Relationships>',
            "xl/sharedStrings.xml": '<sst><si><r><t>脚</t></r><r><t>步</t></r></si></sst>',
            "xl/worksheets/first.xml": '<worksheet><sheetData><row r="3">'
                '<c r="C3" t="s"><v>0</v></c></row></sheetData></worksheet>',
            "xl/worksheets/second.xml": '<worksheet><sheetData><row><c t="inlineStr">'
                '<is><t>场次</t></is></c><c><f>2+2</f><v>4</v></c></row>'
                '<row r="10000"><c><v>2</v></c></row></sheetData></worksheet>',
        })
        return path

    def test_workbook_coordinates_relationships_and_cached_values(self):
        path = self.workbook()
        book = docs.read_workbook(path)
        self.assertEqual([s[0] for s in book.sheets], ["对白", "音效"])
        first, second = (docs.read_sheet(path, book, i) for i in range(2))
        self.assertEqual(first.cells, {(0, 0): "场次", (0, 1): "4"})
        self.assertTrue(first.truncated)
        self.assertEqual(second.cells, {(2, 2): "脚步"})
        self.assertEqual((second.rows, second.columns), (3, 3))

    def test_word_paragraphs_and_expansion_limit(self):
        path = self.root / "录音方案.docx"
        xml = '<document><body><p><r><t>第一段</t><tab/><t>说明</t></r></p>' \
              '<tbl><tr><tc><p><r><t>表格</t></r></p></tc>' \
              '<tc><p><r><t>第二列</t></r></p></tc></tr></tbl></body></document>'
        office_package(path, {"word/document.xml": xml})
        self.assertEqual(docs.word_text(path), "第一段\t说明\n表格\t第二列")
        with patch.object(docs, "MAX_PART_BYTES", 50), self.assertRaises(ValueError):
            docs.word_text(path)

    def test_archive_workbook_switch_and_late_results(self):
        path = self.workbook()
        archive = self.root.parent / (self.root.name + "中文包.7z")
        self.addCleanup(archive.unlink, missing_ok=True)
        create_archive(archive, "7z", self.root)
        session = MergeSession(name="文档", temp_root=self.root / "session")
        self.addCleanup(session.close)
        session.import_archive(archive)
        panel = PreviewPanel(session)
        self.addCleanup(panel.shutdown)
        node = next(n for n in session.root.walk() if n.name == path.name)
        panel.show_node(node)
        view = panel._document
        self.assertTrue(wait_for(lambda: view._table.model() is not None))
        view._sheet_names.setCurrentIndex(1)
        self.assertTrue(wait_for(lambda: view._table.model().rowCount() == 3))
        model = view._table.model()
        self.assertEqual(model.index(2, 2).data(), "脚步")
        view._sheet_names.setCurrentIndex(0)
        old_generation = view._generation
        panel.clear()
        view._on_result_ready(old_generation, None, "late error")
        self.assertEqual(panel._stack.currentIndex(), panel._PAGE_EMPTY)

    def test_pdf_renders_two_different_pages_and_releases_file(self):
        path = self.root / "两页.pdf"
        writer = QPdfWriter(str(path))
        painter = QPainter(writer)
        painter.fillRect(painter.viewport(), QColor("red"))
        writer.newPage()
        painter.fillRect(painter.viewport(), QColor("blue"))
        painter.end()
        del writer
        session = MergeSession(name="PDF", temp_root=self.root / "session")
        self.addCleanup(session.close)
        node = session.import_disk_paths([path])[0]
        panel = PreviewPanel(session)
        self.addCleanup(panel.shutdown)
        panel.show_node(node)
        view = panel._document
        self.assertTrue(wait_for(lambda: not view._page.original.isNull(), 15))
        self.assertEqual(view._number.maximum(), 2)
        view._zoom.setCurrentIndex(view._zoom.findData(100))
        self.assertEqual(view._page.label.width(), view._page.page_width)
        first = view._page.original.toImage()
        view._number.setValue(2)
        self.assertTrue(wait_for(lambda: first != view._page.original.toImage(), 10))
        self.assertNotEqual(first, view._page.original.toImage())
        panel.clear()
        path.unlink()

    def test_invalid_pdf_reports_error_and_switch_to_word_recovers(self):
        path = self.root / "损坏.pdf"
        path.write_bytes(b"invalid")
        word = self.root / "可读.docx"
        office_package(word, {"word/document.xml": "<document><body><p><t>正文</t></p></body></document>"})
        session = MergeSession(name="文档", temp_root=self.root / "session")
        self.addCleanup(session.close)
        bad, good = session.import_disk_paths([path, word])
        panel = PreviewPanel(session)
        self.addCleanup(panel.shutdown)
        panel.show_node(bad)
        self.assertTrue(wait_for(lambda: panel._stack.currentIndex() == panel._PAGE_MESSAGE, 15))
        panel.show_node(good)
        self.assertTrue(wait_for(lambda: panel._document._text.toPlainText() == "正文"))
