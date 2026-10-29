<p align="center">
  <img src="assets/icons/app-readme.png" width="112" alt="ZipMerge application icon">
</p>

# ZipMerge

**Multiple archives. One delivery workspace.**

[简体中文](README.md) · English

ZipMerge is a Windows desktop workspace for merging and organizing archives. Bring multiple archives, local files, and folders into one workspace, arrange a virtual output tree, preview content, and export a consistently named ZIP or 7z deliverable.

It grew out of game audio production: combining separate SFX, VO, and music submissions into one organized client delivery. It also works for asset collection and file handoffs from multiple sources.

[Quick start](#quick-start) · [Run from source](#run-from-source) · [Contributing](CONTRIBUTING.md) · [Report an issue](https://github.com/Meidios/ZipMerge/issues)

## Workflow

1. Add multiple source archives, or drag local files and folders into the output tree.
2. Organize folders, rename, flatten, and move entries. Copy, paste, undo, and redo are available.
3. Preview content, resolve name conflicts, and choose a naming template and output format.
4. Export a persistent archive, or generate a temporary archive to drag into Explorer or a client that accepts file drops.

The virtual tree records source references and output paths, so organizing does not require extracting every source archive first. Preview, external dragging, and export extract the content they need into temporary storage. **Source archives are never rewritten.**

## Features

| Feature | Details |
| --- | --- |
| Multi-archive workspace | Multiple sources share one output tree; tabs keep delivery tasks separate |
| File organization | Drag and drop, folder flattening, batch moves, renaming, clipboard operations, undo and redo |
| Content previews | Text and code, images and GIFs, per-channel audio waveforms and playback, video playback and fullscreen |
| Delivery naming | Save templates built from project, request, content label, date, and version |
| ZIP / 7z export | Password protection, conflict checks, background progress, cancellation, and UTF-8 ZIP filenames |
| Temporary delivery | Drag a generated archive out of the app and inspect its actual contents in a separate read-only dialog; cleaned on session close |
| Windows integration | Add to the current merge session, open archives, extract here, and compress a file or folder to ZIP |
| Desktop appearance | Light and dark themes, system accent colors, two workspace layouts, and saved window/layout preferences |

## Formats and platform

| Item | Scope |
| --- | --- |
| Input | ZIP, 7z, RAR4 / RAR5, TAR, GZIP, BZIP2, XZ, CAB, ISO; recognizes `.tgz`, `.tar.gz`, `.tar.bz2`, and `.tar.xz` |
| Output | ZIP and 7z |
| Platform | Windows 10 / 11, x64; primarily tested on Windows 11 |
| Interface language | Currently Simplified Chinese; bilingual documentation does not imply an English UI |
| Stack | Python 3.13, PySide6 / Qt Widgets, Pillow, and a standalone 7-Zip engine |
| Current version | 0.1.7, an early release |

RAR is read-only input; creating RAR archives is unsupported. Encrypted RAR, multipart RAR, and recovery records have not received dedicated validation. Audio and video playback depends on the Qt Multimedia FFmpeg backend and the source codec; not every codec is guaranteed to preview.

## Quick start

Download the Windows x64 installer from [GitHub Releases](https://github.com/Meidios/ZipMerge/releases). The same page provides checksums, complete third-party notices, and corresponding source archives. The project's own code is MIT licensed; bundled third-party components retain their respective terms.

The installer targets the current user and does not require administrator privileges. Manage shell integration in the app settings. On Windows 11, classic context-menu entries appear under **Show more options**. Setting ZipMerge as the default archive handler requires confirmation in Windows settings; it does not silently replace existing defaults.

Temporary delivery archives are removed when their session closes. Use the regular export action for files you want to keep. Saving and restoring editing sessions is currently unsupported.

## Run from source

You need Windows x64, Python 3.13, and Git. For the initial resource download, install **7-Zip or Bandizip** locally to unpack the official 7-Zip installer. If `7z.exe` is not on PATH, add its installation directory to your current PowerShell session.

```powershell
git clone https://github.com/Meidios/ZipMerge.git
cd ZipMerge
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Adjust this path to your local 7-Zip installation if needed:
$env:Path += ";C:\Program Files\7-Zip"

.\.venv\Scripts\python.exe tools/fetch_vendor.py
.\.venv\Scripts\python.exe tools/verify_7zip.py
.\.venv\Scripts\python.exe main.py
```

`tools/fetch_vendor.py` downloads the 7-Zip engine, icon font, Noto fonts, and test fixtures using the URLs and fixed SHA-256 hashes in [config/sources.json](config/sources.json). Engine binaries and fonts are excluded from Git; source ZIP downloads also need this step. If upstream content changes and a hash fails, stop using that asset and verify its source rather than replacing the expected hash blindly.

The only third-party Python runtime dependencies are `PySide6==6.10.2` and `Pillow==12.1.0`. All archive operations invoke `vendor/7zip/7z.exe` from the project resources.

## Test and build

After preparing resources:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
.\.venv\Scripts\python.exe -m compileall -q main.py src tests tools
```

Tests use the standard-library `unittest` framework. Qt tests use an offscreen platform, and registry tests use temporary test keys. Final drag-and-drop and shell integration behavior still requires real Windows verification.

Building an installer additionally requires **PyInstaller 6.18.0** and **Inno Setup 6** (6.7.3 has been tested):

```powershell
.\.venv\Scripts\python.exe -m pip install PyInstaller==6.18.0
.\.venv\Scripts\python.exe tools/build_release.py
.\.venv\Scripts\python.exe tools/verify_bundle.py
```

Public release builds automatically run `tools/prepare_release_legal.py` to obtain hash-verified corresponding sources from `config/sources.json` and include complete notices in the installer. The release environment pins Python 3.13.14 and OpenSSL 3.0.21; changing runtime versions requires reviewing the corresponding source inventory. `tools/package_release_assets.py` produces the installer, notice ZIP, source 7z, and checksums. The Windows release workflow publishes only after verification passes.

The application directory is written to `dist/ZipMerge/`, and the installer to `installer/Output/`. Use `--skip-installer` for the application directory only. Without Inno Setup, the build script produces the application directory and reports the missing installer compiler.

## Project structure and limits

| Path | Purpose |
| --- | --- |
| `src/zipmerge/engine/` | Qt-independent 7-Zip wrapper |
| `src/zipmerge/core/` | Sessions, virtual tree, conflicts, export, and extraction |
| `src/zipmerge/ui/` | Qt Widgets UI; shared design tokens live in `design/` |
| `src/zipmerge/platform/` | Per-user Windows shell integration |
| `tests/` | Unit tests and regression fixtures |
| `tools/`, `installer/` | Resource fetching, verification, packaging, and installer |

[PLAN.md](PLAN.md) is the product and implementation specification. [MEMORY.md](MEMORY.md) records architecture decisions and historical verification. These internal project references are primarily in Chinese. Cross-platform use, saved editing sessions, split archive output, permission/symlink preservation, command-line batch merging, and automatic online updates are outside the current scope. Multi-GB assets, every video codec, and all high-DPI/multiple-monitor configurations have not been comprehensively tested.

## License and acknowledgments

ZipMerge's own code and original assets are available under the [MIT License](LICENSE), Copyright © 2026 MEI (Meidios). Third-party components and assets retain their respective licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md). The project MIT license does not replace the terms for Qt / PySide6, 7-Zip, FFmpeg, fonts, or test fixtures.

Thanks to the maintainers of 7-Zip, Qt / PySide6, Pillow, Microsoft Fluent UI System Icons, Noto, FFmpeg, libarchive, and the Inno Setup Chinese translation.
