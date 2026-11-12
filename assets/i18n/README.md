# ZipMerge translations

Simplified Chinese is the source language. Edit the English and Traditional Chinese TS files with Qt Linguist. Keep format arguments such as `{0}` and `{1}` intact. Translate message templates, never a filename, password or user-authored template.

Run these commands from the repository root with the project's PySide6 environment active, and commit the TS files and the resulting QM files together:

```text
pyside6-lrelease assets/i18n/zipmerge_en.ts
pyside6-lrelease assets/i18n/zipmerge_zh_TW.ts
python -m unittest tests.test_language
```

All messages use the `ZipMerge` translation context. When adding a user-facing string, add the source and translation to both catalogues, including labels selected from tables at runtime.
