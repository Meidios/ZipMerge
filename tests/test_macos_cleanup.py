import hashlib
import tempfile
import unittest
from pathlib import Path

from zipmerge.core.exporter import ExportOptions, run_export
from zipmerge.core.session import MergeSession
from zipmerge.engine import create_archive, extract_entries


class MacCleanupTests(unittest.TestCase):
    def test_real_zip_cleanup_export_undo_and_redo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "输入"
            keep = ["拟音/脚步.wav", "拟音/.gitignore", ".config/.env", "拟音/说明.DS_Store",
                    "._资料/正文.txt", "同名文件/__MACOSX", ".DS_Store/正常内容"]
            remove = ["拟音/.DS_Store", "拟音/._脚步.wav", "._孤立文件",
                      "__MACOSX/拟音/._脚步.wav", ".config/__MACOSX/._settings"]
            for name in keep + remove:
                path = src / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode("utf-8"))
            archive = root / "__MACOSX.zip"
            create_archive(archive, "zip", src)
            digest = hashlib.sha256(archive.read_bytes()).digest()
            session = MergeSession(temp_root=root / "work")
            try:
                wrapper = session.import_archive(archive)
                original = [n.uid for n in wrapper.walk()]
                self.assertEqual(session.clean_macos_metadata(), 5)
                self.assertIs(wrapper.parent, session.root)
                self.assertEqual(session.clean_macos_metadata(), 0)
                session.undo.undo()
                self.assertEqual([n.uid for n in wrapper.walk()], original)
                session.undo.redo()
                out = root / "干净交付.zip"
                run_export(session.root, session.sources, ExportOptions(output_path=out))
                extracted = root / "检查"
                extract_entries(out, None, extracted)
                delivered = extracted / wrapper.name
                self.assertEqual({p.relative_to(delivered).as_posix(): p.read_bytes()
                                  for p in delivered.rglob("*") if p.is_file()},
                                 {n: n.encode("utf-8") for n in keep})
                self.assertEqual(hashlib.sha256(archive.read_bytes()).digest(), digest)
            finally:
                session.close()
