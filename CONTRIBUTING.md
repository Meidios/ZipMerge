# Contributing to ZipMerge

English · [简体中文](CONTRIBUTING.zh-CN.md)

Bug reports, focused fixes, and documentation improvements are welcome. Open an [issue](https://github.com/Meidios/ZipMerge/issues) before starting a substantial feature so its scope can be agreed upon.

## Report a bug

Include the ZipMerge version, Windows version, archive format, reproduction steps, expected behavior, and actual behavior. For preview issues, include the codec/container if known. Attach a small synthetic sample when possible; remove client material, private paths, passwords, and other sensitive information first.

## Development

Follow the [source setup instructions](README.en.md#run-from-source). Read [AGENTS.md](AGENTS.md), [PLAN.md](PLAN.md), and the relevant decisions in [MEMORY.md](MEMORY.md) before changing behavior. For work spanning multiple items, record the agreed scope in `TODO.md`, work on one item at a time, and update its verification result. The user-facing task list is in Chinese; code, commit messages, and technical explanations should be in English.

- Keep the UI in Qt Widgets, and keep `engine/` independent of Qt.
- Runtime Python dependencies are limited to PySide6 and Pillow. Use standard-library `unittest` for tests.
- Archive operations must use the bundled 7-Zip engine. Do not rewrite source archives.
- Keep colors, spacing, and corner radii in `ui/design/`.
- Windows integration belongs in `ShellIntegration`, writes only to HKCU, and must preserve existing default handlers.
- Add engine/core regression tests for behavior changes. Validate UI changes by launching a real Windows window. Test Chinese filenames when changing paths, encoding, or drag mappings.
- Preserve upstream attribution and license files. Record external resources and hashes in `config/sources.json`.

## Verify a change

```powershell
.\.venv\Scripts\python.exe tools/verify_7zip.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
.\.venv\Scripts\python.exe -m compileall -q main.py src tests tools
git diff --check
```

Keep pull requests focused. Describe the problem, resulting behavior, tests run, and any remaining verification limits. Include real screenshots for visible UI changes. New findings should update the specification or decision record where appropriate.

Contributions are submitted under the project's [MIT License](LICENSE). Third-party material must remain clearly identified and compatible with the repository's licensing constraints.
