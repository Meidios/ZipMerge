# ZipMerge 项目规则

本文件只写与本仓库强相关、且容易做错的约束。通用协作规则以上层 AGENTS.md 为准。

## 事实来源

- 产品与实现规格：`PLAN.md`（唯一规格）。实现与规格不一致时，先按代码事实判断，再同步修订 `PLAN.md`。
- 架构决策、平台约束、踩坑结论、验证状态：`MEMORY.md`。**动手前先读，得到新结论后追加。**
- 外部资产清单：`config/sources.json`。

## 技术约束

- UI 用 **Qt Widgets**，不使用 QML / QtQuick。
- 运行期 Python 依赖只有 **PySide6 + Pillow**，不新增第三方依赖（测试用标准库 `unittest`）。
- 不引入 GPL 组件（历史原因见 `PLAN.md` 5.1）。
- 压缩包操作一律走 `vendor/7zip/7z.exe`，不要在 Windows 上回退到 `zipfile`/`tarfile` 做解压。
- 不改写源压缩包；`delete_entries` 只能作用于本程序新建的包。

## 目录约定

- `src/zipmerge/engine/` 不导入 Qt，任何模块都要能被纯 Python 测试直接调用。
- UI 层放 `src/zipmerge/ui/`，设计令牌与 QSS 生成放 `ui/design/`，颜色/圆角/间距不得在业务代码里硬编码。
- 测试放 `tests/`，通过 `python -m unittest discover -s tests -t .` 运行。

## 验证要求

- 引擎与核心模型改动必须配单测；UI 改动至少做一次真实启动验证。
- 涉及压缩包路径、编码、拖拽映射的改动，要用中文名样本包实测。

## 打包与注册

- 安装器不写注册表，关联与右键菜单全部由程序内 `ShellIntegration` 统一管理。
- 只写 `HKCU`，不要求管理员权限，不抢占已有默认程序。

