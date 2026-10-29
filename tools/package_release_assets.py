"""Package the installer, complete notices, corresponding sources and hashes."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEVENZIP = ROOT / "vendor/7zip/7z.exe"


def main() -> None:
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from zipmerge import __version__

    version = __version__
    out = ROOT / "build/release-assets"
    out.mkdir(parents=True, exist_ok=True)
    installer = ROOT / "installer/Output" / f"ZipMerge-Setup-{version}.exe"
    shutil.copy2(installer, out / installer.name)
    source_cache = ROOT / "build/release-source-cache"
    manifest = ROOT / "build/release-legal/SOURCE_MANIFEST.json"
    sources = json.loads(manifest.read_text(encoding="utf-8"))["sources"]
    shutil.copy2(manifest, source_cache / "SOURCE_MANIFEST.json")
    source_out = out / f"ZipMerge-{version}-ThirdParty-Sources.7z"
    notices_out = out / f"ZipMerge-{version}-ThirdParty-Notices.zip"
    # Refuse archive updates: a fresh archive cannot retain stale source entries.
    source_out.unlink(missing_ok=True)
    notices_out.unlink(missing_ok=True)
    for item in sources:
        with (source_cache / item["filename"]).open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != item["sha256"]:
                raise ValueError(f"Source archive changed: {item['filename']}")
    subprocess.run([str(SEVENZIP), "a", "-t7z", "-mx=0", "--", str(source_out),
                    "SOURCE_MANIFEST.json", *(item["filename"] for item in sources)], cwd=source_cache, check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run([str(SEVENZIP), "a", "-tzip", "-mx=5", "--", str(notices_out), ".\\*"],
                   cwd=ROOT / "build/release-legal", check=True, stdout=subprocess.DEVNULL)
    sums = []
    for path in (out / installer.name, notices_out, source_out):
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        sums.append(f"{digest}  {path.name}")
        print(f"{path.name}: {path.stat().st_size:,} bytes; SHA256 {digest}")
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
