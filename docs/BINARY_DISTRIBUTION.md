# ZipMerge 0.1.7 — Binary distribution notices

## English

Copyright (c) 2026 MEI (Meidios). ZipMerge's own code and original assets are
provided under the MIT license, reproduced in LICENSE. Third-party software
retains its original copyright, license, and disclaimer.

The installed application includes a complete attribution tree in
`_internal/legal/`. Read SOURCE_MANIFEST.json for actual versions, original
source URLs and SHA-256 hashes, and FFMPEG_BUILD.json for the FFmpeg license,
build configuration, and shared-library hashes. NOTICE_HASHES.json verifies
the complete notice tree. Some upstream source notice collections also cover
optional features not compiled into this application.

The same release provides `ZipMerge-0.1.7-ThirdParty-Notices.zip` and
`ZipMerge-0.1.7-ThirdParty-Sources.7z`. These contain the notices and the
unmodified corresponding source archives listed in the manifest, respectively.
They are available alongside the installer, without a purchase or account:
https://github.com/Meidios/ZipMerge/releases/tag/v0.1.7

| Component | Version | Applicable terms and location |
| --- | --- | --- |
| Qt Core, GUI, Widgets, Network; SVG, image formats; Multimedia and Multimedia Widgets | 6.10.2 | LGPL-3.0; third-party notices in upstream/qtbase, qtsvg, qtimageformats, qtmultimedia. The upstream LICENSES collections include full LGPL-3.0 and GPL-3.0 texts required by LGPL-3.0. No Qt Virtual Keyboard, Qt PDF, Qt Charts, or Qt WebEngine module is distributed. |
| PySide6 and Shiboken6 | 6.10.2 | LGPL-3.0 option; upstream/pyside-setup contains license texts, attribution and binding source notices. |
| FFmpeg shared libraries | 7.1.2 | LGPL-2.1-or-later plus permissive third-party portions; upstream/ffmpeg and upstream/qtmultimedia. The actual binary configuration does not enable GPL, nonfree, or version3 options. |
| FFmpeg zlib dependency | 1.3.1 | zlib license, upstream/zlib. |
| 7-Zip | 25.01 | LGPL-2.1-or-later, BSD portions and unRAR restrictions; 7-Zip-License.txt and upstream/7zip. The RAR decoding code must not be used to develop a RAR-compatible compressor. |
| CPython | 3.13.14 | PSF and incorporated notices; Python-distribution-LICENSE.txt and upstream/cpython. |
| OpenSSL | 3.0.21 | Apache-2.0; upstream/openssl. |
| libffi | 3.4.4 | MIT; upstream/libffi. |
| Pillow and its bundled codec libraries | 12.1.0 | HPND and the individual library licenses, reproduced in project-assets/Pillow.txt. |
| Fluent UI System Icons | Font locked by config/sources.json | MIT, Microsoft Corporation; project-assets/Fluent-Icons.txt. |
| Noto Sans SC | Fonts locked by config/sources.json | SIL Open Font License 1.1; project-assets/Noto-OFL.txt. |
| Inno Setup Simplified Chinese translation | Included translation | MIT, kirakira; project-assets/Inno-Setup-Chinese.txt. |
| Inno Setup installer engine | 6.7.3 | Inno Setup license; Inno-Setup-6.7.3-LICENSE.txt. |
| PyInstaller bootloader | 6.18.0 | GPL-2.0-or-later with the explicit bootloader distribution exception; PyInstaller-COPYING.txt and upstream/pyinstaller. The exception permits distribution of this MIT application without applying the GPL to the application. |
| Microsoft Visual C++ runtime DLLs | Versions vary by upstream Python / PySide6 wheel | Microsoft redistributable runtime terms; original Microsoft-VC-Runtime-2015-2022-License.docx and extracted Microsoft-VC-Runtime-License.txt, plus Python-distribution-LICENSE.txt. These are proprietary runtime components, not MIT software. https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/ |

### LGPL rights, replacement, and rebuilding

Qt, PySide6, Shiboken6 and FFmpeg are dynamically loaded, unmodified upstream
libraries. You may replace them with compatible modified versions and debug
those modifications, including reverse engineering to the extent necessary
for that purpose. ZipMerge imposes no contractual restriction on those rights.

Close the application and back up the installation directory first. Qt and
binding libraries live in `_internal/PySide6/` and `_internal/shiboken6/`.
FFmpeg DLLs are intentionally present both in `_internal/` and
`_internal/PySide6/`; replace both copies consistently. Match the Windows x64
architecture, Python ABI, exported names and compatible library ABI. Replacing
DLLs with incompatible builds can prevent the application from starting.

The source sidecar includes the Qt modules, Qt/PySide build and FFmpeg
provisioning scripts, FFmpeg, CPython, 7-Zip, OpenSSL, libffi, zlib and PyInstaller
sources with their upstream build instructions. ZipMerge has not patched these
upstream libraries. Build compatible shared libraries using those upstream
instructions. FFMPEG_BUILD.json preserves the actual upstream compiler options;
its original build-machine paths are informational, not required local paths.

To rebuild the application against replacement bindings, use the project source
at tag v0.1.7, install the modified PySide6/Shiboken6 wheels, preserve the
compatible APIs, and build the PyInstaller onedir bundle described in the README.
The public-release preparation script pins official versions and hashes for
verification; for a modified local build, regenerate its source and notice
manifest rather than presenting the modified binaries as the official release.
There are no activation keys, signature checks, or installation secrets needed
to run a modified build. The Inno Setup source is in installer/zipmerge.iss.

## 简体中文

ZipMerge 自有代码和原创资产采用 MIT，第三方组件分别保留原有版权、许可和免责声明。
完整许可位于安装目录 `_internal/legal/`，版本、源码 URL 与哈希位于
SOURCE_MANIFEST.json，实际 FFmpeg 编译选项与 DLL 哈希位于 FFMPEG_BUILD.json。
NOTICE_HASHES.json 用于核对许可资料完整性。上游许可集合可能包含本程序未编译的可选部分。

同一 Release 页面提供第三方许可 ZIP 和对应源码 7z，可免费直接下载。
Qt / PySide6 / Shiboken6 采用 LGPL-3.0 选项；FFmpeg 7.1.2 采用
LGPL-2.1-or-later，实际构建没有启用 GPL、nonfree 或 version3 选项。
7-Zip 另有 BSD 和 unRAR 条款，不能利用 RAR 解码代码开发 RAR 兼容压缩器。
Qt Virtual Keyboard、Qt PDF、Qt Charts 和 Qt WebEngine 不进入本安装包。
PyInstaller 引导器的 GPL 许可带有明确的分发例外，不改变 ZipMerge 的 MIT 许可。
Microsoft Visual C++ 运行库仍适用微软的专有再分发条款。

LGPL 组件以共享库形式加载。你可以用 ABI 兼容的修改版替换这些库，并为调试修改而
进行必要的逆向工程；本项目不限制这些权利。替换前关闭程序并备份安装目录。
Qt 和绑定库在 `_internal/PySide6/`、`_internal/shiboken6/`；FFmpeg 在
`_internal/` 与 `_internal/PySide6/` 各有一份，替换时需同步。
修改版需匹配 Windows x64、Python ABI、导出符号和共享库 ABI。

源码资料包保留相关上游源码、许可证和构建脚本。本项目未修改这些上游库。
可按上游说明构建共享库，或使用 v0.1.7 项目源码和修改版绑定重新打包；具体流程见 README。
正式发布校验锁定官方版本与哈希，修改构建时应重新生成对应的源码和许可清单。
不需要激活密钥、签名绕过或安装秘密即可运行修改版。
