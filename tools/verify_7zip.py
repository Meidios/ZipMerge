"""校验 vendor/7zip/7z.exe 可用且包含所需解码器。

验收依据（PLAN 7.1）：`7z.exe i` 输出中必须存在 Rar / Rar5 解码器。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEVENZIP = ROOT / "vendor" / "7zip" / "7z.exe"

REQUIRED_FORMATS = ("zip", "7z", "tar", "gzip", "bzip2", "xz")
REQUIRED_CODECS = ("Rar", "Rar5")


def main() -> int:
    if not SEVENZIP.exists():
        print(f"缺少 {SEVENZIP.relative_to(ROOT)}，先运行 tools/fetch_vendor.py", file=sys.stderr)
        return 1

    result = subprocess.run(
        [str(SEVENZIP), "i"], capture_output=True, text=True, errors="replace"
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        print("7z.exe i 执行失败：\n" + output, file=sys.stderr)
        return 1

    lower = output.lower()
    problems: list[str] = []
    for fmt in REQUIRED_FORMATS:
        if fmt not in lower:
            problems.append(f"缺少格式 {fmt}")
    for codec in REQUIRED_CODECS:
        if codec.lower() not in lower:
            problems.append(f"缺少解码器 {codec}")

    if problems:
        print("校验失败：", file=sys.stderr)
        for item in problems:
            print("  " + item, file=sys.stderr)
        return 1

    version_line = next(
        (line.strip() for line in output.splitlines() if line.strip()), "(未知版本)"
    )
    print(f"7z.exe 校验通过：{version_line}")
    print(f"格式 {', '.join(REQUIRED_FORMATS)}；解码器 {', '.join(REQUIRED_CODECS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

