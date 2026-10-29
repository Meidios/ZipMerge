"""Inspect an Inno installer without executing installation or registry hooks."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from tools.prepare_release_legal import obtain_source, sevenzip
from zipmerge import __version__


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main() -> None:
    config = json.loads((ROOT / "config/sources.json").read_text(encoding="utf-8"))
    tool = obtain_source(next(x for x in config["verification_tools"] if x["name"] == "innounp"))
    inspector = ROOT / "build/installer-inspector"
    sevenzip("x", "-y", f"-o{inspector}", "--", str(tool))
    setup = ROOT / "installer/Output" / f"ZipMerge-Setup-{__version__}.exe"
    with tempfile.TemporaryDirectory(prefix="installer-check-", dir=ROOT / "build") as temp:
        subprocess.run([str(inspector / "innounp.exe"), "-x", "-q", f"-d{temp}", str(setup)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        app = Path(temp) / "{app}"
        bundle = ROOT / "dist/ZipMerge"
        expected = {str(p.relative_to(bundle)): p for p in bundle.rglob("*") if p.is_file()}
        expected["LICENSE"] = ROOT / "LICENSE"
        expected["THIRD_PARTY_NOTICES.txt"] = ROOT / "build/release-legal/THIRD_PARTY_NOTICES.txt"
        actual = {str(p.relative_to(app)): p for p in app.rglob("*") if p.is_file()}
        if expected.keys() != actual.keys():
            raise ValueError(f"Installer payload differs: missing {expected.keys()-actual.keys()}, extra {actual.keys()-expected.keys()}")
        for name, source in expected.items():
            if digest(source) != digest(actual[name]):
                raise ValueError(f"Installer payload hash differs: {name}")
        print(f"Installer verified: {len(actual)} payload files match the validated bundle and notices byte-for-byte")
        print("Installer was inspected only; no installation or shell registration was executed")


if __name__ == "__main__":
    main()
