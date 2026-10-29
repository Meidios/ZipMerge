<p align="center">
  <img src="assets/icons/app-readme.png" width="112" alt="ZipMerge 应用图标">
</p>

# ZipMerge

**多压缩包，一个交付工作台。**

简体中文 · [English](README.en.md)

ZipMerge 是面向 Windows 的多压缩包合并与整理工具。把多个压缩包、磁盘文件和文件夹放进同一个工作区，在目标结构中拖拽归类、改名、预览，再导出为一份命名统一的 ZIP 或 7z。

它起源于游戏音频交付：把同事分别提交的 SFX、VO、音乐素材包，整理成一份可直接交给客户的交付包。项目也适用于多来源素材归档与文件交付。

[快速开始](#快速开始) · [从源码运行](#从源码运行) · [贡献指南](CONTRIBUTING.zh-CN.md) · [问题反馈](https://github.com/Meidios/ZipMerge/issues)

## 工作方式

1. 添加多个源压缩包，也可以把磁盘文件或文件夹拖入目标结构。
2. 在虚拟目标树中整理目录、改名、摊平或移动文件；支持复制、粘贴、撤销与重做。
3. 预览内容，处理同名冲突，选择命名模板和输出格式。
4. 导出持久交付包，或生成可拖到资源管理器及支持文件拖放的客户端的临时包。

目标树记录文件来源与最终路径，整理时不要求把所有源包先完整解压。预览、拖出或导出时，所需内容会提取到临时目录。**源压缩包不会被改写。**

## 主要功能

| 功能 | 说明 |
| --- | --- |
| 多包工作区 | 多个源包共享一个目标结构，多标签处理独立交付任务 |
| 文件整理 | 拖拽、目录摊平、批量移动、改名、复制粘贴、撤销与重做 |
| 内容预览 | 文本与代码、图片与 GIF、分声道音频波形及播放、视频播放与全屏 |
| 交付命名 | 项目名、需求名、内容标识、日期与版本组成命名模板，可保存模板 |
| ZIP / 7z 导出 | 密码保护、冲突检查、后台进度和取消；ZIP 中文文件名按 UTF-8 写入 |
| 临时交付包 | 生成后直接拖出，支持独立只读查看实际包内容；关闭会话后清理 |
| Windows 集成 | 添加到当前合并会话、打开压缩包、解压到此处、文件或文件夹压缩为 ZIP |
| 桌面外观 | 深色 / 浅色主题、系统强调色、两套工作区布局、窗口与布局记忆 |

## 格式与平台

| 项目 | 支持范围 |
| --- | --- |
| 输入 | ZIP、7z、RAR4 / RAR5、TAR、GZIP、BZIP2、XZ、CAB、ISO；识别 `.tgz`、`.tar.gz`、`.tar.bz2`、`.tar.xz` |
| 输出 | ZIP、7z |
| 平台 | Windows 10 / 11，x64；主要在 Windows 11 验证 |
| 界面语言 | 当前为简体中文；中英文文档不代表软件已提供英文界面 |
| 实现 | Python 3.13、PySide6 / Qt Widgets、Pillow、独立 7-Zip 引擎 |
| 当前版本 | 0.1.7，早期版本 |

RAR 仅作只读输入，不支持生成 RAR；加密 RAR、多卷 RAR及恢复记录尚未专项验证。音视频格式能否播放取决于 Qt Multimedia 随附的 FFmpeg 后端及素材编码，不能保证所有编码均可预览。

## 快速开始

从 [GitHub Releases](https://github.com/Meidios/ZipMerge/releases) 下载 Windows x64 安装包；同页提供校验和、完整第三方许可与对应源码资料包。软件自有源码采用 MIT，安装包中第三方组件仍保留各自许可。

安装版面向当前用户，不要求管理员权限。系统集成通过程序设置管理；Windows 11 的经典右键项位于“显示更多选项”。设为默认打开程序需要你在 Windows 系统设置中确认，ZipMerge 不会静默抢占已有默认程序。

会话临时包随会话关闭清理。需要长期保留的交付物请使用普通“导出”。当前不支持保存和恢复编辑会话。

## 从源码运行

需要 Windows x64、Python 3.13 和 Git。首次获取资源时，还需在本机安装可用的 **7-Zip 或 Bandizip**，用于解开官方 7-Zip 安装器；安装 7-Zip 后若命令不在 PATH，可在当前 PowerShell 中追加其目录。

```powershell
git clone https://github.com/Meidios/ZipMerge.git
cd ZipMerge
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 如 7z.exe 不在 PATH，按实际安装位置设置：
$env:Path += ";C:\Program Files\7-Zip"

.\.venv\Scripts\python.exe tools/fetch_vendor.py
.\.venv\Scripts\python.exe tools/verify_7zip.py
.\.venv\Scripts\python.exe main.py
```

`tools/fetch_vendor.py` 按 [config/sources.json](config/sources.json) 下载并校验固定 SHA-256 的 7-Zip、图标字体、Noto 字体与测试样本。二进制引擎和字体不存入 Git，源码 ZIP 下载后也需要执行此步骤。上游文件变化导致哈希不符时，应停止使用该资源并核对来源，不要直接重写锁定哈希。

运行时 Python 第三方依赖仅有 `PySide6==6.10.2` 和 `Pillow==12.1.0`。程序执行压缩包操作时统一调用项目资源目录下的 `vendor/7zip/7z.exe`。

## 测试与构建

在完成资源准备后运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
.\.venv\Scripts\python.exe -m compileall -q main.py src tests tools
```

测试使用标准库 `unittest`。Qt 测试使用离屏平台，注册表测试使用临时测试键。涉及拖放和系统集成的最终体验仍需真实 Windows 验证。

构建安装包需要额外安装 **PyInstaller 6.18.0** 和 **Inno Setup 6**（已验证 6.7.3）：

```powershell
.\.venv\Scripts\python.exe -m pip install PyInstaller==6.18.0
.\.venv\Scripts\python.exe tools/build_release.py
.\.venv\Scripts\python.exe tools/verify_bundle.py
```

正式发布构建会自动调用 `tools/prepare_release_legal.py`，按 `config/sources.json` 下载并验证对应源码，再将完整许可放进安装包。该发布环境固定 Python 3.13.14 与 OpenSSL 3.0.21；更换运行库版本时需要同步审核源码清单。`tools/package_release_assets.py` 生成安装器、许可 ZIP、对应源码 7z 和校验和；仓库的 Windows release workflow 在校验通过后发布。

应用目录位于 `dist/ZipMerge/`，安装包位于 `installer/Output/`。仅需应用目录时使用 `--skip-installer`；未安装 Inno Setup 时脚本只生成应用目录并提示安装器缺失。

## 项目结构与边界

| 路径 | 用途 |
| --- | --- |
| `src/zipmerge/engine/` | 不依赖 Qt 的 7-Zip 封装 |
| `src/zipmerge/core/` | 会话、虚拟结构、冲突、导出与解压 |
| `src/zipmerge/ui/` | Qt Widgets 界面；`design/` 集中管理设计令牌 |
| `src/zipmerge/platform/` | 当前用户范围的 Windows 系统集成 |
| `tests/` | 单元测试与回归样本 |
| `tools/`、`installer/` | 资源获取、校验、打包与安装器 |

[PLAN.md](PLAN.md) 是产品与实现规格，[MEMORY.md](MEMORY.md) 记录架构决策与历史验证。当前不支持跨平台、会话工程保存、分卷输出、权限与软链接保留、命令行批量合并和联网自动更新。多 GB 素材、所有视频编码、全部高 DPI / 多屏组合尚未全面验证。

## 开源许可与致谢

ZipMerge 自有代码与原创资产采用 [MIT License](LICENSE)，Copyright © 2026 MEI (Meidios)。第三方组件与素材仍遵循各自许可证，详见 [第三方许可说明](THIRD_PARTY_NOTICES.md)；项目的 MIT 许可不会替代 Qt / PySide6、7-Zip、FFmpeg、字体及测试样本的原有条款。

感谢 7-Zip、Qt / PySide6、Pillow、Microsoft Fluent UI System Icons、Noto、FFmpeg、libarchive 及 Inno Setup 中文翻译的维护者。
