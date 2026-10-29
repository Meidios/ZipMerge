# 参与 ZipMerge

简体中文 · [English](CONTRIBUTING.md)

欢迎提交问题反馈、小范围修复和文档改进。较大的功能请先创建 [Issue](https://github.com/Meidios/ZipMerge/issues)，讨论范围后再实施。

## 反馈问题

请提供 ZipMerge 版本、Windows 版本、压缩格式、复现步骤、预期和实际结果。音视频预览问题请尽可能说明容器与编码。优先附上最小人工样本，移除客户素材、私人路径、密码和其他敏感信息。

## 开发约定

先按 [源码运行说明](README.md#从源码运行) 准备环境。修改前阅读 [AGENTS.md](AGENTS.md)、[PLAN.md](PLAN.md) 和 [MEMORY.md](MEMORY.md) 中相关决策。多项工作先在 `TODO.md` 记录批准范围，一次完成并验证一条，随即记录结果。面向用户的待办用中文，代码、提交信息与技术说明用英文。

- UI 使用 Qt Widgets，`engine/` 不依赖 Qt。
- 运行时 Python 依赖仅限 PySide6 和 Pillow；测试使用标准库 `unittest`。
- 压缩包操作统一调用随项目准备的 7-Zip，引擎不得改写源压缩包。
- 颜色、间距与圆角统一放在 `ui/design/`。
- Windows 集成统一由 `ShellIntegration` 管理，只写 HKCU，保留已有默认程序。
- 引擎与核心行为变更必须配回归测试；UI 修改需要真实 Windows 启动验证；路径、编码和拖拽映射使用中文文件名实测。
- 保留上游署名和许可文本，外部资产 URL 与哈希记录在 `config/sources.json`。

## 提交前验证

```powershell
.\.venv\Scripts\python.exe tools/verify_7zip.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
.\.venv\Scripts\python.exe -m compileall -q main.py src tests tools
git diff --check
```

Pull Request 请保持聚焦，说明解决的问题、最终行为、已完成的验证和验证边界。涉及可见 UI 变化时附真实截图；得到新结论后同步规格或决策记录。

提交贡献即按项目 [MIT License](LICENSE) 提供。第三方材料需要单独标识来源，并符合仓库许可约束。
