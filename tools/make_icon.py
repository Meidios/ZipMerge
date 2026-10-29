"""从高分辨率母版生成 ZipMerge 的应用图标与压缩包文件图标。

用法：
    python tools/make_icon.py            # 生成 assets/icons/ 下的 PNG / ICO
    python tools/make_icon.py --sheet    # 额外输出 build/icon_sheet.png 供人工核对

应用图标用「多个压缩文件汇入一个整理箱」表达合并动作；文件图标用
「带折角的文档 + 拉链」表达压缩包类型。两者共用色彩语言，但轮廓不同，
避免任务栏应用图标与资源管理器文件图标混淆。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "icons"

APP_MASTER = OUT_DIR / "app-master.png"
ARCHIVE_MASTER = OUT_DIR / "archive-master.png"
APP_PNG = OUT_DIR / "app.png"
ARCHIVE_PNG = OUT_DIR / "archive.png"
APP_ICO = OUT_DIR / "ZipMerge.ico"
ARCHIVE_ICO = OUT_DIR / "ZipMergeArchive.ico"

ICO_SIZES: tuple[int, ...] = (256, 128, 64, 48, 32, 24, 20, 16)
PREVIEW_SIZE = 512


def _master(path: Path, *, fill_ratio: float) -> Image.Image:
    """裁掉母版空白并重新居中，保留一致且可控的 Windows 图标安全边距。"""
    image = Image.open(path).convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError(f"图标母版没有可见像素：{path}")
    content = image.crop(bbox)
    target = round(1024 * fill_ratio)
    content.thumbnail((target, target), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    canvas.alpha_composite(content, ((1024 - content.width) // 2, (1024 - content.height) // 2))
    return canvas


def _frame(master: Image.Image, size: int) -> Image.Image:
    """为一个 ICO 档位独立缩放；小尺寸轻锐化，避免拉链与折角糊在一起。"""
    frame = master.resize((size, size), Image.Resampling.LANCZOS)
    if size <= 32:
        frame = frame.filter(ImageFilter.UnsharpMask(radius=0.55, percent=115, threshold=2))
    return frame


def _write_ico(master: Image.Image, path: Path) -> None:
    frames = [_frame(master, size) for size in ICO_SIZES]
    frames[0].save(
        path,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
        append_images=frames[1:],
    )


def build_assets() -> tuple[Image.Image, Image.Image]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    app = _master(APP_MASTER, fill_ratio=0.90)
    archive = _master(ARCHIVE_MASTER, fill_ratio=0.94)
    app.resize((PREVIEW_SIZE, PREVIEW_SIZE), Image.Resampling.LANCZOS).save(APP_PNG)
    archive.resize((PREVIEW_SIZE, PREVIEW_SIZE), Image.Resampling.LANCZOS).save(ARCHIVE_PNG)
    _write_ico(app, APP_ICO)
    _write_ico(archive, ARCHIVE_ICO)
    return app, archive


def _checker(size: tuple[int, int], cell: int = 8) -> Image.Image:
    image = Image.new("RGBA", size, "#F4F4F4")
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill="#DADADA")
    return image


def build_sheet(app: Image.Image, archive: Image.Image) -> Path:
    """输出明暗底与实际小尺寸并排的视觉核对表。"""
    sheet_path = ROOT / "build" / "icon_sheet.png"
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    samples = (64, 48, 32, 24, 16)
    cell = 320
    sheet = Image.new("RGBA", (cell * 2, cell * 2), "#FFFFFF")
    backgrounds = ("#F3F6FA", "#12161C")
    masters = (app, archive)
    for row, background in enumerate(backgrounds):
        for col, master in enumerate(masters):
            panel = Image.new("RGBA", (cell, cell), background)
            preview = _frame(master, 208)
            panel.alpha_composite(preview, ((cell - 208) // 2, 18))
            x = 18
            for size in samples:
                icon = _frame(master, size)
                tile = _checker((48, 48), cell=6)
                tile.alpha_composite(icon, ((48 - size) // 2, (48 - size) // 2))
                panel.alpha_composite(tile, (x, 248))
                x += 58
            sheet.alpha_composite(panel, (col * cell, row * cell))
    sheet.save(sheet_path)
    return sheet_path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 ZipMerge 应用与压缩包文件图标")
    parser.add_argument("--sheet", action="store_true", help="额外输出 build/icon_sheet.png")
    args = parser.parse_args()

    app, archive = build_assets()
    for path in (APP_ICO, ARCHIVE_ICO, APP_PNG, ARCHIVE_PNG):
        print(f"已生成 {path.relative_to(ROOT)} ({path.stat().st_size} 字节)")
    if args.sheet:
        print(f"已生成 {build_sheet(app, archive).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
