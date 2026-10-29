# ZipMerge 开发方案

> 多压缩包合并工作台 · Windows 桌面工具
> 状态：0.1.7 已实现；以 MIT 许可证公开源码，中英文使用说明分别见 README.md / README.en.md
> 最后更新：2026-10-29

---

## 1. 背景与目标

### 1.1 真实场景

甲方给一批需求（含 SFX 与 VO）。项目管理者把工作分给两位同事，同事 A 交付 `项目xxx_SFX_20260910_Demo_V1.zip`，同事 B 交付 `0910项目xxx_vo_demo.7z`。两个包内容都可提交，但不能原样转发给客户：

- 一批需求返回两个压缩包，客户侧容易混乱；
- 两个包命名风格与格式不一致，不专业；
- 交付物形态不统一，增加自身管理与归档难度。

现状做法是：手动全部解压到本地 → 重新归类文件夹与子文件夹 → 重新打包成一个 `项目xxx_需求名_SFX&VO_20260910_V1.zip`。

### 1.2 核心目标

把上述手动流程压缩成一次工作台操作：

1. 打开、浏览、解压、预览常见压缩包格式；
2. 在同一个窗口中同时挂载多个压缩包；
3. 通过拖拽把多个包的内部文件整理成一个统一结构；
4. 按命名规范一键导出为单一最终压缩包。

### 1.3 与市面工具的区别

Bandizip / 7-Zip / WinRAR 均以「单个压缩包」为中心组织交互，多包之间没有共享工作区，合并必须落地到磁盘文件系统手动搬运。ZipMerge 以「一次交付任务」为中心：多个源包共享一个虚拟目标结构，整理过程不产生磁盘中间态，导出时才统一落地。

---

## 2. 已确认决策速查

| 项目 | 决策 |
| --- | --- |
| UI 框架 | Qt Widgets（不用 QML） |
| 组件层 | 原生控件 + 自研设计令牌 + 统一 QSS |
| 外观主题 | 跟随系统，可强制深色 / 浅色 |
| 强调色 | 跟随系统强调色，可手动覆盖 |
| 图标 | Microsoft Fluent UI System Icons（MIT），字形渲染 |
| 字体 | 内置 Noto Sans SC（OFL，可商用可分发） |
| 窗口边框 | 原生标题栏 + DWM 圆角与 Mica |
| 主布局 | 三栏并排 / 两栏 + 底部预览，可切换并记忆 |
| 音频预览 | 格式 / 声道 / 采样率 / 位深信息 + 分声道波形 + 播放头 |
| 视频预览 | 选中即就绪、空格播放 + 基础播放控制 + 全屏 |
| 解压引擎 | 内置 7-Zip 25.01 独立版 |
| RAR | RAR4 / RAR5 只读：导入、浏览、解压、拖出与合并导出；不生成 RAR |
| 分发形式 | PyInstaller onedir + Inno Setup 安装包，当前用户安装 |
| 会话模型 | 单窗口多标签，每个标签一个独立合并会话 |
| 导入语义 | 压缩包以「包名文件夹」占一层，可摊平 |
| 整理方式 | 虚拟目标树 + 拖拽归位（不落磁盘中间态） |
| 预览深度 | 文本 / 代码、图片、音频、视频 |
| 重名策略 | 导出前统一询问，整体策略 + 单项指定 |
| 输出命名 | 带命名模板（项目名 / 需求名 / 内容标识 / 日期 / 版本） |
| 工程保存 | 不做，每次全新会话 |
| 临时目录 | 默认 `%TEMP%\ZipMerge`，随标签页关闭清理，可配置 |
| 默认打开方式 | 注册关联 + 引导确认、打开方式对话框，两种入口都提供 |
| 自定义后缀 | 完全系统级注册 |
| 右键菜单 | 精简三项（见 8.3） |
| Win11 新式右键菜单 | 不做，只做经典菜单 |
| 升级方式 | 覆盖安装，不做联网更新 |
| 注册逻辑归属 | 程序内统一管理，安装器不写注册表 |
| 设置范围 | 系统集成 / 临时目录 / 常规 / 预览 / 密码 五组 |

---

## 3. 范围界定

### 3.1 首版包含

- 压缩包基础能力：打开、目录浏览、解压到指定目录、完整性测试、新建压缩包、删除包内条目；输入支持 zip / 7z / RAR4 / RAR5 / tar / gzip / bzip2 / xz / cab / iso，输出仅支持 zip / 7z；
- 多压缩包同窗口挂载（多标签会话）；
- 虚拟目标结构树的全部整理操作（新建、改名、删除、剪切 / 复制 / 粘贴、跨目录拖拽、多选批量移动、重排、撤销 / 重做）；
- 冲突检测与三种解决策略；
- 命名模板、持久导出与可直接拖拽发送的会话临时包；
- 文本 / 图片 / 波形音频 / 视频预览；
- Windows 系统集成：文件关联、自定义后缀、右键菜单、单实例、临时目录管理；
- PyInstaller 打包与 Inno Setup 安装包。

### 3.2 首版不做

- 不做工程保存（不记录会话中间状态）；
- 不做命令行合并入口；
- 不做联网自动更新；
- 不做 Win11 新式右键菜单（需代码签名与打包应用）；
- 不做分卷压缩；
- 不保留文件权限与软链接；
- 不修改源压缩包内容（仅新建包内删除，不就地改写源包）；
- 不做「解压到暂存区再整理」的降级模式，目标树始终为虚拟结构；
- 不做文件加密与密码管理以外的安全功能。

---

## 4. 平台约束（必须知晓）

以下为 Windows 平台硬约束，影响用户预期，不属于实现选择：

1. **默认打开方式无法静默设置。** Win10/11 的 `UserChoice` 带哈希保护，程序写入会被判定异常并重置。因此「设为默认」只能做实为「注册关联 + 引导用户在系统设置中确认一次」，两个入口：打开系统「打开方式」对话框（勾选「始终使用」）、跳转系统「默认应用」设置页并高亮目标格式。
2. **Win11 新式右键菜单只认已签名打包的应用。** 本工具右键项位于「显示更多选项」（Shift+F10 或右键第二层）的经典菜单内。
3. **本机当前环境参考：** Windows 11 23H2、非管理员账户、3440×1440 @100% 缩放、已安装 Bandizip 7.40 并占用 .zip / .7z / .rar 关联。ZipMerge 只注册为「打开方式」候选，不抢占现有默认。

---

## 5. 技术选型

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 运行环境 | Python 3.13 | 本机已装 3.13.9 / 3.13.7 |
| UI | PySide6 6.10.2 | 已装，QtWidgets / QtMultimedia / QAudioDecoder 已验证可用 |
| 图片处理 | Pillow 12.1.0 | 已装，仅用于预览解码与缩放 |
| 解压引擎 | 7-Zip 25.01 独立版（`7z.exe` + `7z.dll`） | 随程序分发，不依赖用户环境 |
| 打包 | PyInstaller 6.18.0 | 已装，采用 onedir |
| 安装包 | Inno Setup | 未安装，实施阶段 `winget install JRSoftware.InnoSetup` |
| 测试 | unittest（标准库） | 不引入新的测试依赖 |

**运行期第三方 Python 依赖为零新增**（仅 PySide6 + Pillow，均已安装）。

### 5.1 为什么不用 PySide6-Fluent-Widgets

该库为 **GPLv3**，作者官方声明：非商业使用 GPLv3，商业用途需在官网购买授权（599 USD，限 1 名开发者、最多 3 台设备）。

本项目属商业项目交付，且需要打包分发给同事。GPLv3 一经分发即要求整个 ZipMerge 以 GPLv3 开源并提供源码，在公司项目场景不可接受。因此改为自研设计令牌 + 统一 QSS，Win11 原生观感（Mica、圆角、暗色标题栏）通过 ctypes 直接调用 DWM 实现。

补充：PySide6 本身是 LGPL，可商用，但这与第三方组件库的授权互相独立，不能相互推导。

---

## 6. 视觉与交互设计

### 6.1 设计令牌（语义命名，深浅两套值）

**颜色**

| 令牌 | 深色基准 | 浅色基准 |
| --- | --- | --- |
| `bg.base` | #141416 | #E5E9EE |
| `bg.chrome` | #1B1B1D | #EDF0F4 |
| `bg.layer` | #202022 | #F4F6F9 |
| `bg.card` | #252628 | #F8FAFC |
| `bg.canvas` | #18181A | #EEF2F6 |
| `bg.hover` | #2A2B2E | #EBEFF4 |
| `bg.pressed` | #18181A | #E2E7ED |
| `border.subtle` | #34373C | #AEB7C2 |
| `border.strong` | #44484E | #929EAB |
| `text.primary` | #ECEEF1 | #1C232A |
| `text.secondary` | #A5AAB2 | #495766 |
| `text.disabled` | #6E747C | #7B8794 |
| `accent` | 系统强调色 | 系统强调色 |
| `danger` / `warning` / `success` | 语义色 | 语义色 |
| `source.badge` | 来源包角标色 | 来源包角标色 |

强调色继续跟随系统或用户手动覆盖；主题渲染时保留原始设置值，并派生出与最亮常用表面至少达到 3:1 的显示色，深色主题中过暗则向白色混合、浅色主题中过亮则向黑色混合。叠加在显示强调色上的文字按 WCAG sRGB 相对亮度计算，白字达到 4.5:1 时优先白字，否则自动切换为黑字。

深色主题采用专业音频工作台式的中性灰层级：窗口使用 `bg.base`，菜单栏、工具栏与状态栏使用 `bg.chrome`，工作面板与设置分组使用 `bg.layer`，输入与抬升卡片使用 `bg.card`。树视图、空状态与普通预览舞台透明地落在面板表面；图片、视频和波形等内容进入独立的 `bg.canvas` 深层画布。常态保持低对比，模块标题、悬停、当前选中和实时内容才使用强调色；绿 / 黄 / 红只表达成功、临界与风险语义。

**其他令牌**

- 圆角：控件 5px，面板与卡片 6px，窗口圆角由 DWM 提供；
- 间距阶梯：4 / 8 / 12 / 16 / 24；
- 字号阶梯：12 / 13（正文）/ 15（小标题）/ 20（标题）；
- 信息密度：舒适行高 32px，紧凑行高 28px（同步调整图标尺寸与内边距）。

主题由令牌驱动，一套代码两套值，QSS 由令牌表生成而非手写硬编码。

### 6.2 图标

内置 Microsoft Fluent UI System Icons（MIT，9708 个图标）的 `FluentSystemIcons-Regular.ttf` + JSON 码点表，走字形渲染。

- 优点：字形图标可被 QSS `color` 直接染色，主题与选中态零成本适配；
- 映射示例：压缩包 `folder_zip` / `archive_multiple`、音频 `music_note`、图片 `image`、文本 `document`、功能位 `add` / `delete` / `save` / `export` / `import` / `filter` / `search` / `arrow_sync` / `checkmark` / `warning`；
- 压缩包内部格式差异由文件扩展名体现，不额外自绘徽标。
- 产品身份图标另用两张同色系位图资产：应用图标 `ZipMerge.ico` 用「多个压缩文件汇入整理箱」，压缩包文件图标 `ZipMergeArchive.ico` 用「折角文档 + 拉链」；两者视觉相关但轮廓不同。

### 6.3 字体

- 界面字体：安装包内置 Noto Sans SC 变体（OFL，可商用可分发，约 10 MB），经 `QFontDatabase.addApplicationFont` 注册，保证任何机器字形一致；
- 文本 / 代码预览：Cascadia Mono → Consolas 回退。

### 6.4 主题

- 默认跟随系统：读 `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\AppsUseLightTheme` 决定初始明暗；
- 实时切换：用注册表变更通知（`RegNotifyChangeKeyValue`）监听，不依赖 `darkdetect`（它只读一次、不监听）；
- 同时提供手动强制深色 / 浅色；
- 强调色默认读系统 `HKCU\...\DWM\AccentColor`，设置内可覆盖为指定颜色。

### 6.5 窗口

- 保留系统标题栏，用 ctypes 调 `DwmSetWindowAttribute` 开启圆角（`DWMWA_WINDOW_CORNER_PREFERENCE`）与 Mica 背景（`DWMWA_SYSTEMBACKDROP_TYPE`）；
- 调用失败或系统不支持时静默降级为纯色背景；
- 贴靠布局、最大化、多屏、DPI 缩放全部交给系统处理。
- 正常关闭时通过 QSettings 保存窗口位置、大小及最大化状态，下次在首个工作区创建后恢复；显示器或 DPI 变化时由 Qt 将窗口约束回当前可见区域，损坏的几何配置回退默认 `1180 × 720`。
- 标题栏与任务栏图标由 `QApplication.setWindowIcon` 与 `MainWindow` 统一从 `assets/icons/ZipMerge.ico` 设置：源码运行时 exe 是 python.exe，不设就只会显示 Python 的图标。

### 6.6 布局

两套可切换工作区，设置与快捷键均可切换，选择持久化：

1. **三栏并排**（宽屏默认）：源包列表 | 目标结构树 | 预览面板，底部一条导出栏；
2. **两栏 + 底部预览**：源包 | 目标树，预览折叠于底部。

分割条可拖拽、面板可单独折叠；两套布局的外层比例、「两栏 + 底部预览」中的源包/目标内层比例及源包/预览面板显隐状态均持久化。隐藏面板时保留隐藏前的非零比例，重新显示后恢复原布局。

主工具栏右端只放一个「切换布局」按钮，在上述两套完整预设之间循环切换；源包 / 预览面板的单独折叠入口只放在「视图」菜单，不占用主工具栏。

**导出栏**：固定高度并永远贴底（`SizePolicy.Fixed`，高度上限随字体 / 密度重算）；三栏并排时由三个面板吸收全部多余高度，随窗口纵向自动扩展。普通「导出」旁提供「生成临时包」；成功后在导出栏最上方显示文件名、大小与拖拽提示组成的文件卡片。

**会话标签栏**：不使用 `QTabWidget.documentMode`——它把标签栏与内容压进同一行高度，pane 的顶边线会压住标签；标签保持 1px 下分隔线，选中标签用 2px 强调色下边框，标签与工具栏之间留出上边距。

**视觉层次**：工作区外沿、面板之间及面板与导出栏之间统一保留 8px 沟槽；面板使用 1px 边框，列表 / 树视图透明落在面板表面并用行分隔线组织内容，媒体预览使用独立深层画布；列表与面板不加阴影。

Selected target rows use a solid token-derived background in ancestor indentation cells, without repeated vertical accent markers. Expand/collapse icons and selection feedback remain visible.

### 6.7 动效

全部使用 `QPropertyAnimation` + `QEasingCurve`，不引入 QML：

- 面板展开 / 折叠 200ms OutCubic；
- 标签切换 150ms 淡入；
- 拖拽插入指示线 120ms；
- 提示条滑入 200ms。

启动时用 `SystemParametersInfo(SPI_GETCLIENTAREAANIMATION)` 读取系统「在 Windows 中显示动画」设置，若关闭则跳过全部动效。

### 6.8 预览面板

| 类型 | 行为 |
| --- | --- |
| 文本 / 代码 | 上限 5 MB，自动识别 UTF-8 / GBK，等宽只读显示 |
| 图片 | Pillow 校验与静态解码；GIF 由 QMovie 按原帧时长和循环信息自动播放，自适应缩放并限制解码尺寸 |
| 音频 | 格式 / mono/stereo / 采样率 / 源位深信息；QtMultimedia 播放 + mono 单行、stereo 双行波形与播放头 |
| 视频 | QtMultimedia + QVideoWidget，选中后播放器立即就绪但不自动播放，空格直接开始播放；进度、时间、音量 / 静音、全屏 |
| 其他 | 显示元信息 + 「用系统默认程序打开」 |

**波形实现**：用 `QAudioDecoder` 解出交错 PCM 后按每个声道分别计算峰值桶，mono 绘制单行，stereo 按 L/R 绘制双行；峰值数据按文件哈希缓存，长音频的每个声道分别做多级降采样。预览按需从源包提取单个文件到临时目录并做 LRU 缓存。信息栏固定显示容器格式，声道数与采样率由源文件头或解码格式补齐；WAV 位深直接读取 RIFF/WAVE 头，不能用解码后可能被扩展为 32-bit / float 的 PCM 格式冒充源位深。

**视频实现**：识别 mp4 / mov / m4v / mkv / avi / wmv / mpg / mpeg / m2ts / mts / webm，实际编码能力以随 PySide6 分发的 Qt FFmpeg 后端为准；无法解码时显示原因并允许交给系统打开。选中磁盘视频后只异步设置媒体 source，不自动 `play`，播放器立即可用且可直接按空格开始播放；包内视频默认不超过 512 MB 时在后台提取，完成后同样进入待播放状态，超限才要求用户明确点击后提取。视频帧回调显式排队回 UI 线程，避免解码线程跨线程读取播放器状态。切换节点以请求 token 隔离迟到结果，避免旧任务覆盖当前预览。

**打开与播放**：双击文件项、或右键菜单首项「打开」，用系统默认程序打开；`ARCHIVE_ENTRY` 先解压到会话暂存区再打开，失败弹警告。目标树与预览面板内按空格播放 / 暂停音频或视频预览（`WidgetWithChildrenShortcut`，文本输入控件自行消费空格，不被抢键）；视频全屏时按 Esc 退出。

---

## 7. 核心功能设计

### 7.1 引擎层

- 获取 7-Zip：从 `https://www.7-zip.org/a/7z2501-x64.exe` 下载（已验证可达，1.64 MB），用本机 Bandizip 的 `bz.exe` 直接解包取出 `7z.exe` + `7z.dll` + `license.txt`，放入 `vendor/7zip/`；
- 构建时校验：`7z.exe i` 输出中必须存在 `Rar` / `Rar5` 解码器；
- RAR 能力边界：RAR4 / RAR5 作为只读源包参与导入、预览提取、用户解压、树拖出物化和 zip / 7z 合并导出；7-Zip 不提供 RAR 编码器，因此界面不提供 RAR 输出，源 RAR 永不改写；
- 列表解析：`7z l -slt -sccUTF-8`，解析为结构化条目；
- 路径安全：路径经清单文件或 `--` 传递，规避中文、空格、引号转义问题；超长路径加 `\\?\` 前缀；
- 执行模型：耗时操作在 QThread worker 中执行，可取消，进度实时回报；
- 错误模型：统一 `ArchiveError`，携带原始 7-Zip 输出，由 UI 转成可读中文提示。

**用户解压流程**：源包列表仅显示压缩包图标和标题，不展开内部条目，不显示类型与大小列；包内浏览与预览统一由目标结构树承担。源包列表支持完整压缩包多选解压，文件菜单、主工具栏、源包面板按钮和右键菜单共用「解压所选到…」（`Ctrl+Shift+E`）。单个完整包写入 `<目标>/<包名>/`；单包部分条目直接保留包内相对结构；跨源包选择始终按包名分目录，同名包稳定追加 `_2` / `_3`。同时选择父目录与子项时只执行父目录。

解压前按 Windows 大小写不敏感语义预检目标：普通同名文件统一选择「跳过已有 / 覆盖已有 / 取消」，默认跳过；文件/目录类型冲突与包内大小写折叠冲突直接中止。任务在独立 QThread 中逐包执行并聚合进度，可取消；失败或取消后保留已经写入的部分结果并明确提示。目标目录记忆在 `extract/last_dir`，不递归展开包中包，不改变目标树或源压缩包。

### 7.2 目标树数据模型（核心设计）

目标树是**纯虚拟结构**，文件节点只记录来源引用，不在磁盘复制数据。拖拽、改名、删除均为零成本操作，支持无限次调整。

节点来源四态：

| 类型 | 含义 |
| --- | --- |
| `VIRTUAL_DIR` | 用户新建的虚拟文件夹 |
| `ARCHIVE_ENTRY` | 来自某个源压缩包内部条目（源包 ID + 包内路径） |
| `DISK_FILE` | 来自磁盘散文件（右键「添加到当前合并会话」或资源管理器拖入） |
| `DISK_DIR` | 来自磁盘文件夹；会在导出预检时递归展开为文件映射，实际内容仍从原目录实时复制 |

### 7.3 导入语义

- 拖入压缩包后在目标树中以「包名文件夹」占一层，包内结构原样保留；
- 右键可「摊平到当前目录」或「摊平到指定深度」；
- 同一路径的压缩包重复导入时直接选中已有项，不产生重复树；
- 支持多选批量拖入；
- 支持从源包列表把完整压缩包拖入目标树；包内文件 / 文件夹在目标结构树中整理；
- 支持资源管理器拖入散文件与整个文件夹。
- 拖放落点覆盖整个窗口与源包面板，一次可拖入多个压缩包；
- 「添加压缩包」对话框从上次使用的目录开始（`import/last_dir`）。

**直接拖出**：源包列表可把完整压缩包内容拖到资源管理器，目标结构树可拖出所选文件 / 文件夹。`QDrag` 必须在用户第一次手势仍按住鼠标时立即启动；拖拽 MIME 同时携带应用内载荷与 `text/uri-list`。Windows Explorer 桌面会在拖放会话开始阶段读取并缓存 `CF_HDROP`，因此起拖前先在会话 `drag-exports\generation-*` 创建与所选顶层同名的空文件 / 空目录占位，第一次 URL 请求直接返回这些已经存在的稳定路径，不能返回空列表后再更换路径。拖放会话期间以短周期检查光标下的 Qt 控件：仍在 ZipMerge 内只走自有 MIME，不调用 7-Zip；真正离开应用后才显示可取消进度，并把真实内容物化到 Explorer 已缓存的同一组路径。纯应用内拖动结束后立即删除占位代次，不弹进度、不保留缓存；外拖成功结果继续缓存到会话关闭。源包整包以包名文件夹拖出，单包部分选择直接保留所选顶层结构，跨包按包名分组并以 `_2` / `_3` 解决同名；目标树按当前可见名称和结构物化。失败/取消不暴露半成品，外部始终使用复制语义。

### 7.4 目标树操作

新建文件夹、重命名、删除、剪切 / 复制 / 粘贴、跨目录拖拽、多选批量移动、重排顺序；约 50 步撤销 / 重做栈；每行显示来源包角标，右键可「在源包中定位」，选中所属压缩包，不展开包内条目。

- 多选后批量删除 / 剪切 / 复制：右键点已选中项不塌缩选区，操作作用于整个选中集；空白处右键先清空选区回到根，新建文件夹落在根；
- 拖入、粘贴、移动、摊平或重命名使同一目录出现同名文件时，按 Windows 大小写不敏感语义统计本次全部冲突并只提示一次；可选「覆盖旧文件」或「重命名新文件」（`name_1.ext`），取消则整次操作不生效，覆盖与重命名都必须可撤销；
- 双击或右键「打开」用系统默认程序打开（打包内条目先解压到暂存区）；重命名保留 F2 与右键菜单；
- 空格播放 / 暂停音频预览；
- 目标模型刷新（导入 / 拖放）后按节点 uid 还原展开状态与当前项；源包列表按源包 ID 还原选择、当前项与滚动位置。

### 7.5 会话与多标签

`QTabWidget` 多标签，每个标签一个独立 `MergeSession`（源包集合 + 目标树 + 临时目录 + 撤销栈）。可同时推进多个项目，标签间互不影响。

### 7.6 导出流程

1. 遍历目标树生成映射（来源 → 目标相对路径）；磁盘文件夹在此时递归展开，使文件数、预计大小和路径冲突覆盖其真实内容；
2. 在暂存区预建全部空文件夹（保证空目录被保留）；
3. 冲突扫描（多个来源映射到同一目标路径）→ 弹窗列出冲突清单，可选整体策略（自动改名 `name_1` / 覆盖 / 跳过）并对个别项单独指定；
4. 逐源整包提取所需条目到 `stage\raw\<sourceId>\`（保留包内路径，一次调用一个源）；带密码的包弹密码框，可选本会话内存中记住；
5. 按映射把 raw 中文件移动 / 改名到 `stage\final\<目标相对路径>`；磁盘文件按展开后的映射直接从原路径复制；
6. 在目标目录生成同格式的隐藏唯一候选包，以 `stage\final` 为工作目录执行 `7z a -tzip -mx5 <候选包> .\*`，避免绝对路径被写入包内；
7. 打包完整且未取消时用原子替换把候选包提交为最终文件，再清理候选文件与暂存区；取消或失败不改动已有目标包。

**参数默认**：格式 `zip`（可选 `7z`），压缩级别标准（5）。素材类内容多已压缩，级别对体积影响有限。可选输出密码（默认关闭，仅保留在当前会话内存；7z 输出同时加密文件名）。目标文件已存在时先确认，确认后仍先生成全新候选包再原子替换，绝不向旧包增量追加。默认输出目录跟随首个源包所在目录（`export/output_dir_auto`）；用户手改输入框、浏览选择或设置页指定目录后该键置 0，此后不再自动覆盖。

**磁盘占用说明**：第 4–5 步的 raw 中转会让峰值占用约为素材体积的两倍，换取实现简单与结果可校验。

**临时交付模式**：沿用当前命名模板、格式、压缩级别、冲突处理与进度流程，但忽略普通输出目录，写入当前会话 `deliveries\generation-*\<客户可见文件名>`。卡片用本地文件 URL 与 `CopyAction` 拖到聊天、邮件、网盘或资源管理器；拖放后不自动删除。目标树、命名、格式或级别变化后旧卡片标为过期并禁止拖出，重新生成成功前保留旧文件，避免打断外部应用读取。

Generation cleanup compares canonical resolved paths on both sides, so Windows 8.3 aliases in `%TEMP%` cannot cause the newly committed archive to be mistaken for an older generation and deleted.

**不保留**：权限、软链接、包中包的递归展开（嵌套压缩包按普通文件处理）。

### 7.7 命名模板

- 字段：项目名 / 需求名 / 内容标识 / 日期 / 版本；
- 默认模板：`{项目名}_{需求名}_{内容标识}_{日期}_{版本}`；
- 日期默认今天（YYYYMMDD），版本默认 V1；
- 实时预览生成的完整文件名；
- 非法字符 `\ / : * ? " < > |` 自动替换为 `_`；
- 字段与模板经 QSettings 记忆上次输入；
- 内容标识留空时，按源包名中的 SFX / VO 等关键词给出建议值，用户可覆盖；
- 输出目录已存在同名文件时提示递增版本号。

示例产出：`项目xxx_需求名_SFX&VO_20260910_V1.zip`

**模板库**：命名模板 + 五个命名字段可另存为模板、切换、重命名、删除，或把当前设置写回所选模板；模板只含命名相关字段，不含格式 / 级别 / 输出目录。

模板选择框按字体度量保留可完整显示 `（自定义）` 的最小宽度；模板管理按钮隐藏 Qt 原生菜单指示器，只显示一枚 Fluent 下箭头。

存储：`export/templates`（JSON 数组，元素 `{name, template, project, requirement, content, date, version}`）与 `export/active_template`；模板库为空时用旧 `naming/*` 键构造「默认」条目，旧键继续作为写入来源，保证兼容。

### 7.8 设置页（五组，全部由程序内统一管理）

**系统集成**

- 设为默认打开方式：两个入口（打开系统「打开方式」对话框 / 跳转系统「默认应用」设置页）+ 一键撤销；
- 关联格式勾选清单；
- 自定义后缀增删（完全系统级注册）；
- 右键菜单三个条目的独立开关。

**临时目录**

- 位置（默认 `%TEMP%\ZipMerge`）；
- 当前占用查看；
- 手动清空；
- 随标签关闭自动清理开关。

**常规**

- 默认输出格式、默认压缩级别、默认输出目录；
- 界面字体与字号、界面密度；
- 主题模式与强调色；
- 布局模式。

**预览**

- 文本大小上限、图片解码尺寸上限、波形缓存上限、包内视频自动加载上限（默认 512 MB）。

**密码**

- 是否在本会话内记住输入的密码（默认关）。

---

## 8. Windows 系统集成

全部使用 **HKCU**，无需管理员权限。

### 8.1 文件关联

- 安装器完成文件复制后调用 `ZipMerge.exe --install-context-menus`：首次安装由 `ShellIntegration` 注入三个右键菜单并写入一次性引导标记；覆盖安装按原有关联范围与菜单开关重写图标路径和命令，不改变用户选择；不在 Inno Setup 中复制注册表实现；
- 安装后首次正常启动分两步询问：是否登记全部受支持格式、是否前往系统页面将 ZipMerge 设为默认压缩软件；拒绝格式关联不影响默认右键菜单；
- `HKCU\Software\Classes\ZipMerge.Archive` 作为 ProgID，含 `DefaultIcon`（指向 `<安装目录>\_internal\assets\icons\ZipMergeArchive.ico`）与 `shell\open\command`（指向 `<安装目录>\ZipMerge.exe`）；
- 对每个启用格式，向 `HKCU\Software\Classes\.<ext>\OpenWithProgids` 写入 `ZipMerge.Archive`；
- **不修改扩展名默认值**，因此不会抢占 Bandizip 等现有默认程序；
- 注册 `HKCU\Software\RegisteredApplications` 中的 `ZipMerge` 指向 `HKCU\Software\ZipMerge\Capabilities`（含 `FileAssociations` 列表），使 ZipMerge 出现在系统「默认应用」页。**缺少这一步，系统设置页里根本找不到本工具。**

### 8.2 自定义后缀

新增 `.xxx` 时同时写入：

- `OpenWithProgids`；
- `SystemFileAssociations\.xxx\shell` 下的右键项；
- `Capabilities\FileAssociations`；
- 程序内的格式识别表。

移除时反向清理。全程只进候选，不抢默认。

### 8.3 右键菜单（精简三项）

Explorer Add to Current Merge Session: register `ZipMerge.AddToSession` under both `HKCU\Software\Classes\*\shell` and `HKCU\Software\Classes\Directory\shell`, using the same `--add-to-session` command and existing preference. Upgrade initialization repairs a missing directory verb when enabled; disabling the preference and uninstalling remove both keys. Folder inputs are imported as disk directories through the existing session path.

Explorer quick ZIP creation: add “用 ZipMerge 打包为 ZIP” to file and directory context menus through `ShellIntegration` under HKCU only. `--compress-here` runs independently before single-instance forwarding, with no confirmation or success dialog. For a file `voice.wav` create sibling `voice.zip`; for directory `Sounds` create sibling `Sounds.zip`, retaining the selected directory inside the archive. Use the bundled 7-Zip, UTF-8 ZIP names, a temporary sibling candidate, and no-overwrite publication; occupied names become `_2`, `_3`, etc. Failure cleans staging and reports an error. The menu is installed/upgraded with existing context menus and removed by program uninstall plus installer fallback. This action handles one selected file/directory per shell invocation.


| 菜单项 | 注册位置 | 出现范围 |
| --- | --- | --- |
| 添加到当前合并会话 | `HKCU\Software\Classes\*\shell\ZipMerge.AddToSession` | 所有文件类型 |
| 用 ZipMerge 打开 | 各受支持扩展名的 `SystemFileAssociations\.<ext>\shell\ZipMerge.Open` | 仅压缩包文件 |
| 解压到此处 | 各受支持扩展名的 `SystemFileAssociations\.<ext>\shell\ZipMerge.ExtractHere` | 仅压缩包文件 |

每项含 `MUIVerb`（中文名）、`Icon`、`command`，首次安装默认全部开启并立即注入 HKCU，可在设置里单独开关。格式专用右键菜单覆盖全部受支持后缀，与“是否把 ZipMerge 登记为打开方式候选”的选择解耦。

「解压到此处」调用独立的 `--extract-here "%1"` 分支，不转发到主窗口单实例；内容直接写入压缩包所在目录，不额外套包名文件夹。执行前复用用户解压的 Windows 大小写、文件/目录类型和已有文件冲突检查，默认跳过已有文件，可在本次操作中选择覆盖；加密包现场询问密码，执行过程可取消。

右键菜单的 `Icon` 指向随包分发的应用图标 `assets/icons/ZipMerge.ico`（绝对路径），仅在 ico 缺失时回退到 exe；ProgID `DefaultIcon` 独立指向 `assets/icons/ZipMergeArchive.ico`。源码运行时 exe 是 python.exe，直接指向它只会显示 Python 的图标。

### 8.4 单实例机制

右键「添加到当前合并会话」的前提：ZipMerge 全局只有一个进程。

- 具名管道：QLocalServer 使用 `ZipMerge.SingleInstance`；
- 第二次启动时连接已运行实例，发送 JSON 参数（文件路径列表 + 动作 `open` / `addToSession`）后退出；
- 已运行实例按动作处理：`open` 在活动标签打开这些包；`addToSession` 写入活动标签的目标树，无活动标签时新建一个。

### 8.5 注册表布局汇总

| 用途 | 键路径 |
| --- | --- |
| ProgID | `HKCU\Software\Classes\ZipMerge.Archive` |
| 打开方式候选 | `HKCU\Software\Classes\.<ext>\OpenWithProgids` |
| 默认应用页可见性 | `HKCU\Software\RegisteredApplications` + `HKCU\Software\ZipMerge\Capabilities` |
| 右键（所有文件） | `HKCU\Software\Classes\*\shell\ZipMerge.AddToSession` |
| 右键（仅压缩包） | `HKCU\Software\Classes\SystemFileAssociations\.<ext>\shell\ZipMerge.Open` |
| 右键解压（仅压缩包） | `HKCU\Software\Classes\SystemFileAssociations\.<ext>\shell\ZipMerge.ExtractHere` |
| 程序设置 | `HKCU\Software\ZipMerge`（QSettings） |

### 8.6 临时目录

- 默认 `%TEMP%\ZipMerge`，按会话分子目录；
- 每张标签页关闭时清理该会话的临时文件；
- 程序启动时扫除上次残留；
- 设置内可改位置、查看占用、手动清空。
- 临时交付包位于会话 `deliveries` 子目录；关闭标签或退出程序时始终清理，不受「随标签关闭自动清理」开关影响，异常退出残留由下次启动兜底。

### 8.7 卸载清理

- 卸载时优先调用 `ZipMerge.exe --unregister-all` 清除全部注册项；
- 若程序文件已被删除，则直接删除已知的 HKCU 注册表键路径，保证无残留。

---

## 9. 项目结构

```
ZipMerge/
  main.py                  # 入口 + --extract-here / --unregister-all + 启动参数解析
  run.bat                  # 开发期双击启动
  requirements.txt         # PySide6, Pillow（均已安装）
  PLAN.md                  # 本文件
  MEMORY.md                # 架构决策、平台约束、验证状态、已知死路
  AGENTS.md                # 项目规则
  config/
    sources.json           # 外部资产下载地址与校验哈希
  tools/
    fetch_vendor.py        # 下载 7-Zip / 图标字体 / Noto Sans SC 并校验
    verify_7zip.py         # 校验 7z.exe 的编解码器清单
  vendor/7zip/             # 7z.exe, 7z.dll, license.txt
  assets/fonts/            # FluentSystemIcons-Regular.ttf, NotoSansSC, 码点表
  assets/icons/            # 应用图标、压缩包文件图标与高分辨率母版
  src/zipmerge/
    engine/sevenzip.py     # subprocess 封装 + -slt 解析
    core/                  # 会话、目标树、导入、导出、冲突、模板
    platform/              # winreg 关联 / 后缀 / 右键菜单、系统主题与强调色
    ipc/                   # 单实例服务与客户端
    ui/design/             # 令牌、QSS、图标字形、DWM 效果、动效
    ui/                    # 主窗口、标签、源包面板、目标树、预览（含波形）、导出栏、设置页
    util/                  # 路径、临时目录、设置
  installer/zipmerge.iss   # Inno Setup 脚本
  tests/                   # unittest（标准库）
```

---

## 10. 接口与数据结构

```python
list_entries(archive, password=None) -> list[ArchiveEntry]
extract_entries(archive, paths, out_dir, password=None) -> None
create_archive(out_path, fmt, src_dir, level=5, password=None) -> None
test_archive(archive, password=None) -> bool

ArchiveEntry(path, size, packed_size, is_dir, mtime, crc, encrypted)

TargetNode(name, kind, source, children)
SourceRef(kind, archive_path | disk_path, entry_path, source_id)

ExportPlan(root, mappings, conflicts)
Conflict(target_path, existing, incoming)
```

**服务类**

- `Settings`：QSettings 封装；
- `ShellIntegration`：注册、撤销、状态查询走同一入口，保证设置页与实际注册不会不一致；
- `ThemeService`：系统主题与强调色读取、变更监听。

**错误模型**：统一 `ArchiveError`（含原始 7-Zip 输出），UI 层转成可读中文提示。

---

## 11. 打包与分发

- **打包**：PyInstaller onedir（非 onefile，启动更快、无临时解包），排除 `QtWebEngine*`、`Qt3D*`、`QtQuick/QML`、`QtPdf`、`QtNetworkAuth` 和 GPL/商业许可的 `QtVirtualKeyboard` 等未用模块；关联的孤立 PDF、虚拟键盘插件也排除。安装后体积预期 120–180 MB。
- **发布许可**：`tools/prepare_release_legal.py` 按 `config/sources.json` 固定版本和哈希准备完整许可、对应源码与实际 FFmpeg 构建记录；`_internal/legal/` 必须随包分发，安装根目录另附 MIT 与许可入口。`verify_bundle.py` 校验声明和 FFmpeg DLL 哈希，禁止误收排除模块；同一 GitHub Release 提供许可 ZIP、对应源码 7z 和 SHA-256 校验和。
- **正式发布**：Windows GitHub Actions workflow 从指定发布分支构建，执行全量回归、打包校验后，先上传 draft 的全部资产，再公开 `v<version>` Release；中英文发布说明分节，已公开版本不自动覆盖。
- **安装器**：Inno Setup 产出 `ZipMerge-Setup-x.y.z.exe`；
- **安装范围**：`%LOCALAPPDATA%\Programs\ZipMerge`，写 HKCU，全程免 UAC；
- **安装器职责**：放置文件与快捷方式，隐藏调用 `ZipMerge.exe --install-context-menus`，再以 `SHChangeNotify` + Windows 自带 `ie4uinit.exe` 通知 Shell 并重建当前用户图标缓存；实际 HKCU 写入仍全部由程序内 `ShellIntegration` 统一管理；
- **首次启动**：仅当安装器写入待引导标记时显示两步提示；便携/源码运行与之后普通启动保持静默。格式关联只登记候选；若用户要求设为默认但尚未登记，先登记全部受支持格式，再打开 Windows「默认应用」页由用户确认；
- **升级**：覆盖安装。安装器先关闭运行中的 ZipMerge 实例，QSettings、关联范围和右键菜单开关保留；新图标与命令路径会重写并立即刷新 Explorer 缓存；
- **卸载**：调用 `--unregister-all` 清理注册项，删除文件与快捷方式。

---

## 12. 测试与验收

### 12.1 单元测试

- `7z l -slt` 输出解析（中文名、目录项、加密标志）；
- 目标树移动 / 改名 / 删除 / 摊平 / 复制粘贴；
- 冲突检测与三种策略；
- 命名模板拼接、非法字符替换、版本递增、关键词建议；
- 设计令牌与 QSS 生成的键完整性；
- 注册表键生成与撤销（在临时 HKCU 子树中构造与清理）；
- 单实例参数 JSON 序列化。
- 临时交付包 generation 生命周期、过期状态、单文件 URL MIME 与仅复制拖放。
- 解压选择归一化、跨包输出分目录、同名包后缀、目标冲突预检、覆盖 / 跳过与后台取消。
- 源包 / 目标树拖出结构、父子去重、目录物化、中文路径、空目录、大小写冲突、取消清理与多条目 MIME。
- GIF 动图启动、切换停止与尺寸限制；源包列表模型重置后的选择恢复与整包操作；模板下拉完整宽度与单菜单图标。

### 12.2 集成测试

脚本构造样本包（zip / 7z / tar.gz、中文文件名与中文目录名、深层嵌套、空文件夹、重名文件、带密码包）→ 导入 → 整理 → 导出 → 用 7z 重新列表并逐项比对文件集合与 SHA256；使用 libarchive 固定哈希样本验证 RAR4 / RAR5 的列表、完整性测试、目录/空目录识别、解压、拖出物化与合并导出；加密包密码流程；散文件与散文件夹节点导出；中文名临时 ZIP / 7z 的生成、解包与标签关闭清理；中文 ZIP 与带密码 7z 的整包 / 子集 / 多包解压、长目标路径及覆盖策略实测；源包列表整包和目标结构树所选条目分别拖到 Windows 资源管理器并核对落地结构与内容。

### 12.3 外观验收

- 深色 / 浅色 / 跟随系统三态切换正确且无样式残留；
- 系统强调色变化后界面同步；
- 窗口位置/大小、最大化状态、两套布局、全部分割比例及面板显隐在正常关闭后恢复；损坏几何配置不影响启动；
- 布局切换与密度切换；
- 系统关闭动画时无动效；
- 窗口圆角与 Mica 生效，不支持时降级正常；
- 170% / 200% DPI 下无错位与图标模糊。

### 12.4 音频预览验收

WAV 信息栏能准确显示 `WAV · mono/stereo · 44.1/48kHz · 16/24bit · 文件大小`；其他格式显示能可靠取得的格式、声道与采样率，不臆造源位深。mono 绘制单行波形，stereo 的 L/R 峰值独立并绘制为上下双行，播放头贯穿全部声道且同步；wav / mp3 / flac / ogg 可解码，长音频（> 30 分钟）不卡顿，异常 / 损坏文件给出提示而非崩溃。

### 12.4.1 视频预览验收

选中磁盘或已提取的包内视频只设置媒体 source，不自动播放且不得阻塞主界面；无需点击额外按钮，直接按空格即可开始播放。中文文件名 H.264/AAC MP4 从磁盘与中文名 zip / 7z 中均能显示有效画面；播放、暂停、拖动、音量、静音、全屏和 Esc 正常。超过自动提取阈值的包内视频不自行提取；切换文件会停止旧媒体并取消旧提取任务。MOV / AVI / MKV 做容器烟雾测试，损坏文件或不支持编码显示错误并可用系统默认程序打开。

### 12.5 系统集成验收

实机确认首次安装自动注入三个右键菜单，安装后首次启动只提示一次；格式关联“是/否”和默认应用“是/否”四种组合符合预期，其中“设为默认”必须进入 Windows 系统确认页面。确认关联候选出现、ZipMerge 出现在系统「默认应用」页、三个右键项在「显示更多选项」中可见且能执行；逐项撤销后相关键清除；卸载后无残留；覆盖安装不重新开启用户已关闭的菜单，并在安装结束后刷新应用图标与压缩包文件图标。

### 12.6 中文编码验收

导出包分别用 Windows 资源管理器、7-Zip、Bandizip 打开确认无乱码。zip 文件名默认按 UTF-8 写入；若实测资源管理器出现乱码，则提供「兼容旧系统」开关并记录结论。

### 12.7 长路径与大体量

超 260 字符路径与多 GB 素材下验证导出与预览不崩。

Temporary archive preview: the delivery card opens a dedicated read-only Qt window. A background worker uses bundled 7-Zip to list the actual generated ZIP/7z and displays names, directories, file sizes, and counts. Output passwords remain in session memory and are supplied to listing encrypted 7z headers. There is no external default-handler call, session import, extraction, file execution, editing, or drag export from this window. Stale/missing output remains unavailable; listing errors appear in the inspector, and closing it cancels an active listing before destroying the worker.

### 12.8 手动端到端

用 `项目xxx_SFX_20260910_Demo_V1.zip` 与 `0910项目xxx_vo_demo.7z` 跑完整流程，确认产出符合 `项目xxx_需求名_SFX&VO_20260910_V1.zip`；完成一次安装 → 使用 → 卸载全周期验收。

临时交付另做一次 Windows 拖出验收：生成后拖到资源管理器与至少一个真实发送客户端，确认复制动作、文件名与内容正确；关闭标签后临时源包删除，已复制到外部的文件保留。

树直接拖出另做整包、部分目录、跨包、目标虚拟结构与大型目录取消验收；第一次拖动即进入系统拖放会话，Windows 在起拖瞬间预探测 URL、应用内部拖放以及经过工具栏/空白区均不得物化，只有光标离开 ZipMerge 且外部目标索取 URL 时允许显示准备进度；准备完成后应在同一手势中继续落放，不能要求第二次拖动，缓存命中后应直接返回 URL。GIF 用包内与磁盘多帧样本确认自动播放、缩放及切换后停止。

### 12.9 RAR 验收边界

RAR4 / RAR5 单卷普通包已用真实固定样本自动回归。加密 RAR、多卷 RAR、恢复记录和损坏分卷仍需补充合法可分发的真实样本；遇到当前 7-Zip 解码器不支持的 RAR 特性时，应显示 `ArchiveError`，不得崩溃或改写源包。

---

## 13. 假设与默认值

- 默认输出格式 `zip`、压缩级别标准（5）、输出不加密；
- 默认主题跟随系统、强调色跟随系统、密度舒适；
- 首次启动布局：按窗口宽度自动选三栏或两栏；
- 多标签写入规则：右键命令写入当前活动标签，无活动标签时新建；同一包在不同标签互不影响，不做跨标签去重；
- 只从压缩包、散文件、文件夹导入；目标树始终为虚拟结构；
- 运行期不新增任何第三方 Python 依赖；
- 开发期需要的外部资产：7-Zip 25.01、Fluent UI System Icons 字体、Noto Sans SC，下载源与许可文件一并入库，构建脚本校验哈希。

---

## 14. 风险与待验证项

| 项 | 说明 | 处理 |
| --- | --- | --- |
| 默认打开方式无法静默设置 | 系统硬约束 | 已设计为「注册 + 引导确认」，需在验收中实测用户操作路径 |
| zip 中文文件名兼容性 | utf-8 与本地代码页的历史差异 | 默认 UTF-8，实测乱码则加兼容开关 |
| RAR 特殊特性 | RAR4 / RAR5 单卷普通包已通过；加密、多卷与恢复记录未覆盖 | 依赖内置 7-Zip 只读解码；补充合法固定样本后加入回归，不提供 RAR 写入 |
| 导出磁盘峰值翻倍 | raw 中转策略 | 已记录；若素材体量过大再考虑流式优化 |
| 7-Zip 授权 | 7z.exe / 7z.dll 为 LGPL | 随包分发 license.txt，安装包附带第三方许可说明 |
| DWM Mica 兼容性 | 旧系统 / 虚拟机不支持 | 静默降级纯色背景 |
| 波形解码性能 | 长音频首次解析耗时 | 哈希缓存 + 多级降采样，必要时异步渐进绘制 |

---

## 15. 建议实施顺序

1. **文档与仓库**：初始化 git，写入本方案、`MEMORY.md`、`AGENTS.md`；
2. **引擎层**：7-Zip 资产获取与校验脚本、`list / extract / create / test` 封装、`-slt` 解析 + 单元测试；
3. **设计系统**：令牌表、QSS 生成、图标字形加载、主题服务、DWM 效果、动效工具；
4. **核心模型**：目标树、会话、导入语义、冲突检测、命名模板 + 单元测试；
5. **主界面**：主窗口、标签、源包面板、目标树视图与拖拽交互、撤销栈、导出栏；
6. **预览模块**：文本、图片，再接入音频播放与波形；
7. **导出链路**：暂存区管理、分源提取、结构落地、打包与比对测试；
8. **系统集成**：注册表服务、设置页、单实例与右键联动；
9. **打包安装**：PyInstaller 配置、Inno Setup 脚本、安装卸载与关联注册全周期验收；
10. **端到端验收**：用真实交付场景跑通并记录结论。

---

*本文档为实施规格。实施过程中若与原假设不符，须同步修订本文件并在 `MEMORY.md` 记录调整原因。*
