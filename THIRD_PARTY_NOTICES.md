# Third-party notices / 第三方许可说明

ZipMerge's MIT license applies to its own code and original assets. It does not relicense the components listed below. Downloaded engines and fonts are excluded from Git and are prepared using [config/sources.json](config/sources.json).

ZipMerge 的 MIT 许可只覆盖项目自有代码与原创资产，不替代以下组件的原许可。引擎与字体通过资源清单准备，不存入 Git。测试样本仅用于回归，不进入安装包。

| Component | Usage and source | License / notice |
| --- | --- | --- |
| 7-Zip 25.01, Igor Pavlov and other credited authors | Unmodified `7z.exe` / `7z.dll`, invoked as a separate process; [upstream](https://www.7-zip.org/), [25.01 source archive](https://www.7-zip.org/a/7z2501-src.tar.xz) | LGPL-2.1-or-later, BSD-2-Clause / BSD-3-Clause portions, and unRAR restrictions; [included upstream notice](vendor/7zip/License.txt) |
| Qt / PySide6 / Shiboken6 6.10.2, The Qt Company and contributors | Dynamically loaded Qt Widgets and Multimedia libraries; [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html), [source releases](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.2-src/) | LGPL-3.0 option for the components used; licenses vary across the wider Qt distribution. [Upstream LGPL-3.0 text](https://code.qt.io/cgit/pyside/pyside-setup.git/tree/LICENSES/LGPL-3.0-only.txt?h=v6.10.2) |
| FFmpeg, FFmpeg developers | Shared libraries supplied with PySide6 and loaded by Qt Multimedia for audio/video decoding; [legal and build conditions](https://ffmpeg.org/legal.html), [source releases](https://ffmpeg.org/releases/) | LGPL-2.1-or-later by default; optional build features may change the applicable license. Determine the exact build configuration before redistributing binaries |
| Pillow 12.1.0, Secret Labs AB, Fredrik Lundh, and contributors | Image inspection and decoding; [source](https://github.com/python-pillow/Pillow/tree/12.1.0) | HPND (historically described as the PIL software license); [full license](licenses/Pillow.txt) |
| Microsoft Fluent UI System Icons, Microsoft Corporation | Icon font and codepoint mapping; [source](https://github.com/microsoft/fluentui-system-icons) | MIT; [full license](licenses/Fluent-Icons.txt) |
| Noto Sans SC, Noto font contributors | Bundled Simplified Chinese UI fonts; [source](https://github.com/notofonts/noto-cjk/tree/main/Sans) | SIL Open Font License 1.1; [full license](licenses/Noto-OFL.txt) |
| libarchive contributors | Two uuencoded RAR regression fixtures, pinned to commit `a4ba070a01e967f7b98998c654dcc082e16fe58c`; [fixture provenance and full notice](tests/fixtures/libarchive/README.md) | BSD-2-Clause; test data only |
| Inno Setup Chinese Simplified Translation, Copyright (c) 2019–2020 kirakira | Installer translation in `installer/languages/ChineseSimplified.isl`; [source](https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation) | MIT; [full license](licenses/Inno-Setup-Chinese.txt) |
| CPython, Python Software Foundation and contributors | Python runtime included when packaging with PyInstaller; [license](https://docs.python.org/3.13/license.html) | PSF License Agreement and notices for incorporated software |

## Redistribution / 再分发

Keep copyright notices and the full applicable license texts with redistributed components. For LGPL components, preserve the ability to replace the shared libraries and provide the corresponding source, modification notices if any, and the information required by the applicable license. A project-level MIT notice alone does not fulfill these separate obligations.

再分发第三方组件时应保留署名与完整许可文本。LGPL 组件另有共享库替换、对应源码及相关信息提供等要求；项目本身采用 MIT 不会免除这些义务。7-Zip 的 RAR 解码代码还包含禁止用于重建 RAR 压缩算法的限制。

This source publication does not publish a newly built installer. Before distributing a binary release, verify the actual Qt/FFmpeg build, collect its complete dependency notices and corresponding source information, and ship them with that release. The historical [installer notice](installer/THIRD_PARTY_NOTICES.txt) is a component summary, not a complete set of binary redistribution materials.

本次开源发布为源码发布，未随之上传新构建的安装包。发布二进制安装包前，需按实际 Qt / FFmpeg 构建补齐完整许可、对应源码信息及依赖声明。历史安装器许可摘要不能代替完整的二进制再分发材料。
