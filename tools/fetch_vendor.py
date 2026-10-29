"""下载并校验 ZipMerge 的开发期外部资产。

用法::

    python tools/fetch_vendor.py            # 下载缺失资产并校验哈希
    python tools/fetch_vendor.py --freeze   # 额外把实测哈希回填到 config/sources.json
    python tools/fetch_vendor.py --force    # 忽略缓存重新下载

资产清单与哈希以 config/sources.json 为唯一事实来源。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "config" / "sources.json"
CACHE = ROOT / "build" / "cache"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path, force: bool = False) -> Path:
    if dest.exists() and not force:
        print(f"  缓存命中 {dest.relative_to(ROOT)}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  下载 {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)
    tmp.replace(dest)
    print(f"  完成 {dest.name} ({dest.stat().st_size:,} 字节)")
    return dest


def find_unpacker() -> list[str]:
    """返回可用于解开 7-Zip 自解压 exe 的命令前缀。"""
    bundled = ROOT / "vendor" / "7zip" / "7z.exe"
    if bundled.exists():
        return [str(bundled)]
    for candidate in (
        Path(r"D:\Program Files\Bandizip\bz.exe"),
        Path(r"C:\Program Files\Bandizip\bz.exe"),
    ):
        if candidate.exists():
            return [str(candidate)]
    found = shutil.which("7z") or shutil.which("7za") or shutil.which("bandizip")
    if found:
        return [found]
    raise SystemExit("找不到可用的解包工具（需要 7z.exe 或 Bandizip bz.exe）")


def unpack_installer(asset: dict, archive: Path) -> None:
    out_dir = ROOT / asset["unpack"]
    stage = CACHE / "unpacked" / Path(asset["unpack"]).name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    unpacker = find_unpacker()
    is_bandizip = Path(unpacker[0]).name.lower() == "bz.exe"
    if is_bandizip:
        cmd = unpacker + ["x", "-y", f"-o:{stage}", str(archive)]
    else:
        cmd = unpacker + ["x", "-y", f"-o{stage}", str(archive)]
    print("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        raise SystemExit(f"解包失败：\n{result.stdout}\n{result.stderr}")

    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {name.lower(): name for name in asset.get("include", [])}
    seen = set()
    for path in stage.rglob("*"):
        if not path.is_file():
            continue
        key = path.name.lower()
        if key in wanted and key not in seen:
            target = out_dir / wanted[key]
            shutil.copy2(path, target)
            seen.add(key)
            print(f"  提取 {target.relative_to(ROOT)} ({target.stat().st_size:,} 字节)")
    missing = set(wanted) - seen
    if missing:
        raise SystemExit(f"解包结果缺少文件：{sorted(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="获取 ZipMerge 外部资产")
    parser.add_argument("--force", action="store_true", help="忽略缓存重新下载")
    parser.add_argument("--freeze", action="store_true", help="把实测哈希回填 sources.json")
    args = parser.parse_args()

    data = json.loads(SOURCES.read_text(encoding="utf-8"))
    failures: list[str] = []
    changed = False

    for asset in data["assets"]:
        print(f"[{asset['name']}]")
        filename = asset["url"].rsplit("/", 1)[-1]
        cached = download(asset["url"], CACHE / filename, force=args.force)
        digest = sha256_of(cached)

        expected = asset.get("sha256") or ""
        if expected and expected != digest:
            failures.append(f"{asset['name']}: 期望 {expected} 实际 {digest}")
            print(f"  哈希不匹配！期望 {expected}")
        elif not expected:
            print(f"  sha256 {digest}")
            if args.freeze:
                asset["sha256"] = digest
                changed = True
        else:
            print("  哈希校验通过")

        if asset["kind"] == "installer":
            unpack_installer(asset, cached)
        else:
            dest = ROOT / asset["dest"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cached, dest)
            print(f"  安装到 {asset['dest']} ({dest.stat().st_size:,} 字节)")

    if args.freeze and changed:
        SOURCES.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"已回填哈希到 {SOURCES.relative_to(ROOT)}")

    if failures:
        print("\n校验失败：", file=sys.stderr)
        for item in failures:
            print("  " + item, file=sys.stderr)
        return 1
    print("\n全部资产就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

