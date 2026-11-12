import hashlib
import tempfile
import unittest
from pathlib import Path

from zipmerge.core.batch_rename import ReplaceRule, insert_name, replace_name, sequence_number
from zipmerge.core.exporter import ExportOptions, run_export
from zipmerge.core.nodes import TargetNode
from zipmerge.core.session import MergeSession
from zipmerge.engine import create_archive, extract_entries


class BatchRenameTests(unittest.TestCase):
    def test_ranges_and_delimiters(self):
        for mode, expected in {
            "literal": "abcXdefXghi", "first": "Xc-def-ghi", "last": "abc-def-gX",
            "range": "aX-def-ghi", "before": "X-def-ghi", "after": "abc-X",
            "before_n": "aX-def-ghi", "after_n": "abc-Xf-ghi",
        }.items():
            with self.subTest(mode=mode):
                self.assertEqual(ReplaceRule(mode, "-", "X", 2, 2).apply("abc-def-ghi"), expected)
        self.assertEqual(ReplaceRule("after", "absent", "X").apply("abc"), "abc")
        self.assertEqual(ReplaceRule("range", "", "X", 5, 2).apply("abc"), "abc")

    def test_chained_rules_extensions_insert_and_numbering(self):
        file = TargetNode("Armour A.wav", False)
        rules = [ReplaceRule(find="A", replacement="S"), ReplaceRule(find="Sr", replacement="Ar")]
        self.assertEqual(replace_name(file, rules, False), "Armour S.wav")
        ext = [ReplaceRule(find="wav", replacement="aif")]
        self.assertEqual(replace_name(file, ext, False), file.name)
        self.assertEqual(replace_name(file, ext, True), "Armour A.aif")
        for name, directory, pos, expect in [
            ("鼓.wav", False, "end", "鼓01.wav"), ("鼓.A", True, "end", "鼓.A01"),
            ("a.tar.GZ", False, "position", "a01.tar.GZ"), (".env", False, "start", "01.env"),
        ]:
            self.assertEqual(insert_name(TargetNode(name, directory), "01", pos, 7), expect)
        for value, style, expected in [(2, "decimal", "002"), (27, "upper", "AA"),
                                       (26, "lower", "z"), (10, "chinese", "十"),
                                       (10101, "chinese", "一万零一百零一"),
                                       (100000001, "financial", "壹亿零壹")]:
            self.assertEqual(sequence_number(value, style, 3), expected)

    def test_atomic_validation_swap_and_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = MergeSession(temp_root=Path(tmp))
            a, b, c = [session.new_folder(session.root, n) for n in ("A", "B", "C")]
            for invalid in ("C", "b", "nul.txt", "bad.", "x ", "", "../x", "音" * 256, "🎵" * 128):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    session.batch_rename({a: "first", b: invalid} if invalid != "b" else {a: invalid})
                self.assertEqual([a.name, b.name, c.name], ["A", "B", "C"])
            self.assertTrue(session.batch_rename({a: "B", b: "a"}))
            session.undo.undo()
            self.assertEqual([a.name, b.name], ["A", "B"])
            session.undo.redo()
            self.assertEqual([a.name, b.name], ["B", "a"])
            self.assertFalse(session.batch_rename({a: "B"}))
            session.close()

    def test_chinese_archive_export_keeps_source_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "输入"
            src.mkdir()
            for i in (1, 2):
                (src / f"脚步 {i}.wav").write_bytes(bytes([i]))
            archive = root / "素材.zip"
            create_archive(archive, "zip", src)
            before = hashlib.sha256(archive.read_bytes()).digest()
            session = MergeSession(temp_root=root / "work")
            wrapper = session.import_archive(archive)
            names = {n: insert_name(n, "_循环", "end") for n in wrapper.children}
            names[wrapper] = "拟音"
            session.batch_rename(names)
            output = root / "交付.zip"
            run_export(session.root, session.sources, ExportOptions(output_path=output))
            extract_entries(output, None, root / "验证")
            for i in (1, 2):
                self.assertEqual((root / "验证" / "拟音" / f"脚步 {i}_循环.wav").read_bytes(), bytes([i]))
            self.assertEqual(hashlib.sha256(archive.read_bytes()).digest(), before)
            session.close()
