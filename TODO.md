# ZipMerge 正式开源 — 2026-10-29

本轮范围已由用户授权：将当前项目提交到 `Meidios/ZipMerge`，补齐独立中英文介绍和 GitHub 仓库信息。用户已确认项目代码采用 MIT。

1. **检查发布内容与许可。** 已完成：确认当前版本 0.1.7、远程仅含初始化 README；106 个候选文件未检出常见凭据特征。当前快照发布在远程 main 的历史之后，本地旧开发历史保留在 master；探针和旧版视觉预览不公开。项目代码采用用户确认的 MIT，第三方保留原许可。
2. **补齐中英文开源资料。** 已完成：独立中英文 README 与贡献指南、MIT、第三方来源与许可说明；保留 Fluent / Noto / Pillow / 安装器翻译原始许可并记录 URL 与哈希。规格首页同步为 0.1.7 已实现。40 个本地文档链接和锚点通过检查。
3. **验证当前项目与文档。** 已完成：116 个文件的独立源码副本使用下载缓存逐项核验资源哈希，首次资源准备成功；389/389 单测通过（21.856 秒），7-Zip 25.01 与 Rar/Rar5 校验、compileall、实际 `--version` 0.1.7 均通过。40 个文档本地链接与锚点通过。本轮无 UI 行为修改，未重建或上传安装包。
4. **提交并发布到 GitHub。** 源码发布完成：`4998f0f` 已推送到公开仓库 main，保留远程初始化提交与本地 master 历史；远程 API 确认默认分支 main、MIT 识别成功且提交匹配。最终复核修正了 Pillow 上游许可首次暂存时的换行转换，以保留原始字节。**剩余：About 与主题待写入。** 中英文简介和 12 个主题已存入 `.github/REPOSITORY_METADATA.md`；连接器缺少该修改接口，Windows 浏览器自动化因无法可靠确认 URL 被安全检查停止。远程 About 仍为空，主题仍为空，不将这部分标为完成。

---

# Confirmed fixes — 2026-10-22

## Folder Add to Session regression — P1

- Symptom/risk: folder context menus show Compress to ZIP but omit Add to Current Merge Session; the old verb exists only under the file `*` class.
- Exact locations: `shell_integration.py` registration/status/unregistration, installer fallback cleanup, registry and directory-dispatch tests.
- Direction: register the same existing command under `Directory` as well as `*`, controlled by the same existing preference; preserve all other verbs. Rebuild 0.1.7 and deliver to the previously requested `D:\Synching\Codex\` directory.
- Status: source complete; 33 targeted and 389/389 full tests passed, along with compileall/diff checks. Actual user preference/file/folder key inspection confirmed the diagnosis. Installer 0.1.7 was built, bundle-verified, and delivered to `D:\Synching\Codex\ZipMerge-Setup-0.1.7.exe`; frozen code contains the Directory verb. No installation was executed.

## Current approved scope — shell ZIP creation and isolated preview

1. **P1 — Create ZIP from Explorer.** Symptom/risk: no direct action to pack a file/folder. Locations: `shell_integration.py`, `main.py`, the 7-Zip engine, a core packing operation, related tests, and installer cleanup. Direction: HKCU file/folder verb creates a sibling ZIP without confirmation or session forwarding; retain the directory level, UTF-8 names, and automatically suffix existing output instead of overwriting. Status: implemented; 50 related engine/core/entry/registry tests passed, including real Chinese directory/file packing, empty directories, sibling exclusion, collision preservation, failed staging cleanup, and IPC/GUI bypass.
2. **P1 — Isolated temporary archive inspection.** Symptom/risk: default-handler opening reimports output into the current session. Locations: `temporary_archive.py`, a dedicated Qt dialog, and preview tests. Direction: bundled 7-Zip lists the actual archive in a background worker; a separate read-only tree offers no extraction, execution, external opening, or session import. Status: implemented; 43 related tests passed, followed by native Windows checks of real ZIP and encrypted 7z dialogs, both themes, unchanged session state, and no external handler. Loading cancellation, corrupted archives, read-only flags, and double-click non-execution have regression coverage. Final full suite: 386/386 passed; compileall and diff checks passed. Complete; installer 0.1.6 delivered to the requested parent directory. Frozen code includes the isolated inspector and excludes the old external-handler route.

Both items passed. Version 0.1.6 was rebuilt and delivered to `D:\Synching\Codex\ZipMerge-Setup-0.1.6.exe` (54,461,136 bytes). Bundle verification, frozen CLI ZIP/collision/content checks, frozen inspector inclusion, and copy SHA-256 verification passed. Installer execution and physical Explorer-menu mouse acceptance were not performed. Authorization is the current explicit user request.

Scope approved by the user's repair request and the two attached annotations.

## 1. Source archive list — P1

- Symptom and risk: the source panel duplicates target browsing and exposes expandable archive entries.
- Exact locations: `src/zipmerge/ui/source_panel.py`, `src/zipmerge/ui/source_model.py`, related panel tests, and `PLAN.md`.
- Fix direction: show archive icons and titles only; preserve whole-archive operations and select the owning archive when locating a target entry.
- Status: complete; 88 related tests passed first. Final suite: 371 tests passed. Native Windows startup with a Chinese ZIP confirmed archive-only presentation, retained internal contents, source location, and target preview.

## 2. Target selection indentation — P2

- Symptom and risk: selecting a nested target row shows unwanted vertical lines in its left indentation area.
- Exact location: `src/zipmerge/ui/design/qss.py`, the opaque selection token in `src/zipmerge/ui/design/tokens.py`, and rendered panel regression coverage.
- Fix direction: keep the accent border on flat source/list selections, and use an opaque token-derived branch selection background to suppress native ancestor markers while retaining expand/collapse icons and selection feedback.
- Status: complete; rendered pixel regression passed for nested files/folders in dark/light themes, and native Windows screenshots passed the same pixel checks. Final suite: 371 tests passed; compileall and git diff --check passed. No installer rebuild or Explorer mouse-gesture acceptance was performed.

## 3. Temporary archive preview — P1

- Symptom and risk: the generated temporary archive card offers drag delivery but no direct way to inspect the actual output.
- Exact locations: `src/zipmerge/ui/temporary_archive.py`, `tests/test_temporary_archive.py`, and the temporary delivery specification in `PLAN.md`.
- Fix direction: add a right-aligned Preview button using the existing system-default file-opening mechanism; allow only a ready, existing archive, disable stale/missing/cleared output, and report launch failure without changing drag or session cleanup behavior.
- Status: complete; 5 new button/state/failure regressions passed, 71 related tests passed, and the final full suite passed 376/376. Native Windows verification generated and extracted Chinese ZIP/7z outputs, checked button-click path dispatch, dark/light layout, stale disabling, and close cleanup. compileall and diff checks passed. OS launch was intercepted; external archive-manager rendering and installer rebuild were not performed.
