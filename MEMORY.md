# ZipMerge 记忆库

> 架构决策、平台约束、验证状态与已知死路。修改结论前先看这里，避免重复踩坑。
> 变更记录追加在文末「决策日志」，不要覆盖历史结论。

## 1. 事实来源

| 内容 | 位置 |
| --- | --- |
| 产品与实现规格（唯一） | `PLAN.md` |
| 外部资产 URL 与哈希 | `config/sources.json` |
| 项目规则 | `AGENTS.md` |
| 本文 | `MEMORY.md` |

规格与实现冲突时：先按代码实际情况判断，然后**同步修订 PLAN.md 并在此记录原因**。

## 2. 已验证环境（2026-09-11）

| 项 | 实测 |
| --- | --- |
| Python | 3.13.9（Windows Store 版） |
| PySide6 | 6.10.2 |
| Pillow | 12.1.0 |
| git | 2.55.0.windows.3 |
| PyInstaller | 6.18.0 |
| Inno Setup | 6.7.3（ISCC.exe 位于 `%LOCALAPPDATA%\Programs\Inno Setup 6`） |
| 本机解包工具 | `D:\Program Files\Bandizip\bz.exe` 可用（用于取出 7-Zip 资产） |
| 7-Zip 引擎 | `vendor/7zip/7z.exe` 25.01 (x64)，含 zip/7z/tar/gzip/bzip2/xz 与 Rar、Rar5 解码器 |

资产获取：`python tools/fetch_vendor.py --freeze`；引擎校验：`python tools/verify_7zip.py`。

## 3. 关键技术结论（实测，非推测）

### 3.1 7-Zip 清单文件（listfile）只能在 `--` 之前

- `7z x -o<out> -- <archive> @list.txt` → **静默失败**，7-Zip 把 `@list.txt` 当成字面文件名，输出 `No files to process` 且返回码为 0。
- 可行写法：`-i@<list.txt>` 作为开关放在 `--` 之前，或把 `@list.txt` 放在 `--` 之前。
- 本项目统一使用 `-i@<list.txt>` + `--`，既保留 `--` 对归档路径的保护，又能正确匹配中文名。
- 7-Zip 25.01 直接按 UTF-8 读取清单文件；仍显式加 `-scsUTF-8` 以保证确定性。

### 3.2 打包时用 `cwd=源目录` + `a <out> .\*`

不产生 `./`、盘符或上级目录前缀；空目录（含深层空目录）会被写入压缩包。

### 3.3 `-slt` 输出细节

- 条目路径用反斜杠。**目录识别必须同时看 `Folder` 与 `Attributes`**：zip / tar 目录带 `Folder = +`（zip 无尾分隔符，tar 可能带 `/`）；**7z 格式目录没有 `Folder` 行**，只以 `Attributes = D`（Windows）/ `drwx…`（Unix）体现。只看 `Folder = +` 会把 7z 包内所有目录误判成文件。
- `Modified` 的小数位是 **7 位**，`datetime.strptime('%f')` 只接受 1-6 位，必须先截断。
- 归档自身信息在 `--` 分隔的头块里，条目在 `----------` 之后，各条目以空行分隔。

### 3.4 子进程输出编码

统一 `-sccUTF-8` + `encoding='utf-8'` 解码；PowerShell 控制台默认代码页会显示乱码，属显示层现象，不影响程序。

### 3.5 资产源

- 7-Zip：<https://www.7-zip.org/a/7z2501-x64.exe>（1,643,509 字节）。
- Fluent UI System Icons：`microsoft/fluentui-system-icons` 仓库 `fonts/FluentSystemIcons-Regular.ttf`（2,818,416 字节）与同名 `.json`（9708 个图标，键形如 `ic_fluent_<name>_<size>_regular`）。
- Noto Sans SC：仓库 `notofonts/noto-cjk` 的 `Sans/SubsetOTF/SC/NotoSansSC-{Regular,Medium}.otf`（各约 8.3 MB）。**注意**：`Sans/OTF/SimplifiedChinese/NotoSansSC-Regular.otf` 是 404，正确路径是 `SubsetOTF/SC/`。

### 3.6 设计系统（实测）

- Fluent 图标字体注册后 family 为 `FluentSystemIcons-Regular`；两个 Noto OTF（Regular / Medium）都注册为同一个 family `Noto Sans SC`。
- 图标码点表键形如 `ic_fluent_<name>_<size>_<style>`（`style` = regular / filled），值是私有区码点（如 62518）。**Fluent 原始名常带序号或 `arrow_` 前缀**：`music_note_2`、`arrow_export`、`arrow_import`、`arrow_sync`、`arrow_undo`、`arrow_redo`，因此 `icons.py` 内置 `ALIASES` 提供 PLAN 6.2 的语义短名。
- **QSS 不支持 data URI，也不认字体字形**，勾选框勾选、下拉 / 上下箭头、树展开箭头、单选圆点、标签关闭按钮只能用 PNG。`icons.py::qss_glyph_urls` 用字形渲染 PNG 到 `%TEMP%\ZipMerge\ui\glyphs\` 并返回 `url("...")`；无 GUI 平台时降级为 `image: none`（`build_qss` 的 glyph 令牌默认值）。
- 系统强调色 `HKCU\...\DWM\AccentColor` 是 DWORD **ABGR（0xAABBGGRR）**，取 R 用低 8 位；全 0 视为无效并回退 `#0078D4`。
- 本机实测系统为深色、强调色 `#4C4A48`（跟随壁纸的自动强调色），非 Windows 默认蓝，属正常。

### 3.7 QtMultimedia 的 ffmpeg 后端在部分环境下不会自行加载

- 症状：`QMediaFormat().supportedAudioCodecs(Decode)` 返回空列表，`QAudioDecoder` 不产生任何缓冲，但实际上 `QMediaPlayer` / `QAudioOutput` 仍能构造成功、不报错。
- 判据：`QT_DEBUG_PLUGINS=1` 显示 `ffmpegmediaplugin.dll` 加载失败（报「找不到指定的模块」），Qt 打印 `No QtMultimedia backends found`。
- **干扰项**：该 DLL 自身的依赖其实都能解析到（用自写 PE 导入表解析器验证），用 `ctypes.WinDLL` 直接加载插件本体也能成功。曾据此误判为「缺 DLL / 架构不匹配」。
- 真正原因：PySide6 把 ffmpeg 的 `avcodec-*` / `avformat-*` / `avutil-*` / `swresample-*` / `swscale-*` 放在包根目录，而多媒体插件在 `PySide6/plugins/multimedia/` 子目录。Qt 的 `QLibrary` 加载插件时不带 `LOAD_WITH_ALTERED_SEARCH_PATH`，插件的**传递**依赖因此解析不到。
- 修复：在 **导入 `PySide6.QtMultimedia` 之前** 执行 `os.add_dll_directory(PySide6 包根目录)`，并用 `ctypes.WinDLL` 预加载上述库。见 `ui/waveform.py::preload_multimedia_libs`，模块级 `PRELOADED_LIBS` 暴露成功加载个数。修复后可用解码器为 MP3 / AAC / AC3 / EAC3 / FLAC / Wave / WMA / ALAC。
- **导入顺序是硬约束**：Qt 一旦尝试加载插件失败就不会重试，之后再补 DLL 目录也无效。
- 打包注意：PyInstaller 必须把 PySide6 包根目录下的 `av*.dll` 一并收集，否则用户机器会重现同一问题。

### 3.8 Windows 注册表集成（实测）

- 只写 HKCU（免管理员）。**绝不写扩展名的 (Default) 值**，只在 OpenWithProgids 里加候选并在 Capabilities 里登记，因此不会抢占 Bandizip 等现有默认程序；已单测锁定：注册与撤销后 .zip 的默认值原样保留。
- REG_NONE（空数据值）写进去后 RegQueryValueEx 会失败、read_value 返回 None。**判断 OpenWithProgids 是否已写必须用 has_value / list_values**，用 read_value 会把已注册误判成未注册。
- 系统「默认应用」页的可见性需要两步齐全：HKCU\Software\RegisteredApplications 登记 + Capabilities\FileAssociations 列出扩展名；缺任一步都不可见。
- Windows 10+ 有意不让程序静默抢占默认程序。设置页只能引导用户到「打开方式」/「默认应用」手动确认一次，无法自动完成。
- QSettings 无需 QApplication 即可读写（已实测），因此 --unregister-all 能在无 GUI 的分支里直接执行。

### 3.9 单实例 IPC（QLocalServer / QLocalSocket）实测

- 协议：4 字节大端长度前缀 + JSON UTF-8；管道名 ZipMerge.SingleInstance；一次连接只处理一帧，服务端读完即主动断开。
- **客户端写入后必须再泵一次事件循环**：waitForBytesWritten 对极短载荷常返回 False，不能当「已送达」判据。实现为 write → flush → waitForBytesWritten(10) 后循环 processEvents + waitForDisconnected(10)，以「服务端主动断开」作为送达确认。
- **客户端不可在服务端读完前主动 disconnectFromServer**，否则 Windows 命名管道会丢弃服务端尚未读取的数据。
- 单帧上限 8 MiB；超限帧直接清空该连接的缓冲区，避免异常客户端撑爆内存。

### 3.10 打包与 PyInstaller（实测）

- PyInstaller onedir 产出 `dist/ZipMerge`。**PySide6 的 hook 会整批收集 Qt DLL，`excludes` 对 DLL 不起作用**，必须按文件名剔除未用平台插件（spec 的 `DROP_BINARY_PATTERNS`），否则体积远超 PLAN 的 120–180 MB 区间（实测净化后 142.5 MB）。
- **PATH 上的外来 native 目录会让 PyInstaller 收进同名但不兼容的 DLL**。Codex runtime 的 `dependencies/native`（poppler、libheif 等）在 PATH 上时，其 `icuuc.dll` / `icudt78.dll`（导出符号带 `_78` 版本后缀）被按文件名命中并收进包，而 Qt6Core 需要无后缀的 `ucnv_open` 等符号，于是报 `ImportError: DLL load failed while importing QtWidgets: 找不到指定的程序 (WinError 127)`。修复：构建脚本用净化后的 PATH 调 PyInstaller（`FOREIGN_PATH_MARKERS`），spec 里再把 `icu*.dll`、`ucrtbase.dll`、`api-ms-win-*.dll` 列为剔除模式做守卫，`tools/verify_bundle.py` 命中即判失败。
- **窗口模式（console=False）下 `sys.stdout` 为 None**：argparse 的 `action="version"` 会抛 AttributeError，而 PyInstaller 把未捕获异常弹成**阻塞对话框**，表现为 `--version` 与 `--unregister-all` 挂住不退出。CLI 分支必须走容错输出（`_emit`），拿不到输出通道就静默跳过，绝不因写输出抛异常。
- QtMultimedia 的 ffmpeg DLL 需同时存在于 `_internal` 包根与 `_internal/PySide6`（原因见 3.7 的导入顺序约束）。
- 标准 Inno Setup **不附带**简体中文语言文件，`MessagesFile: compiler:Languages\ChineseSimplified.isl` 会直接编译失败。改为随仓库分发 `installer/languages/ChineseSimplified.isl`（MIT，来自 kira-96/Inno-Setup-Chinese-Simplified-Translation），`[Languages]` 只保留简体中文。

### 3.11 zip 条目名编码（实测）

- **7-Zip 写 zip 默认按本地 ANSI 代码页编码条目名**（中文 Windows 上是 GBK），不置通用标志位 0x800，只额外写 Info-ZIP 的 0x7075 Unicode Path 扩展字段。Python `zipfile` / 7-Zip / Bandizip 会读该扩展字段，看起来正常；但按标志位判编码的老工具、以及异地区码机器会乱码。
- **修复：打包 zip 时加 `-mcu`**（`create_archive` 仅对 `zip` 格式追加）。效果：含非 ASCII 的条目名按 UTF-8 写入并置 0x800；纯 ASCII 名保持无标志（各代码页一致，无需标志）。符合 PLAN 12.6「默认按 UTF-8 写入」。
- 取证方式：直接解析中央目录（`build/zip_raw.py`）逐条比对 `flag_bits` 与名称原始字节；再用 7-Zip、Bandizip 7.40（`bz.exe l`）与 shell zip 命名空间（`Shell.Application.NameSpace(<zip>)`，即资源管理器所用解码器）三方复核，中文名均正确。
- 注意：本机是中文区域设置，GBK 与 UTF-8 两种写法在本地工具里都显示正常，仅凭本机显示无法区分二者；UTF-8 + 0x800 才是与区域设置无关的形式。

### 3.12 视频预览依赖的 `QtMultimediaWidgets` 曾被 spec 主动剔除（实测）

- 症状：源码运行一切正常，**只有打包产物**里视频预览不可用——`video_preview.py` 需要 `from PySide6.QtMultimediaWidgets import QVideoWidget`，而 `installer/zipmerge.spec` 的 `UNUSED_QT_MODULES` 与 `DROP_BINARY_PATTERNS` 在「只用音频」的前期假设下把 `QtMultimediaWidgets` / `Qt6MultimediaWidgets*.dll` 列为未使用并剔除。
- 修复：从剔除名单移除该项，并让 `tools/verify_bundle.py` 的 `REQUIRED_FILES` 增加 `PySide6/QtMultimediaWidgets.pyd` 与 `PySide6/Qt6MultimediaWidgets.dll`，缺失即判 bundle 校验失败。实测旧产物（09-11 21:10 构建）确实被这两条判失败，重建后 142.7 MB 产物通过。
- 教训：剔除名单里的每一项都等于「谁都不用」的断言，新增预览后端或 Qt 模块时必须回头复核；源码能跑不能证明打包能跑，`verify_bundle.py` 是唯一能在打包后发现问题的地方，改 spec 必须同步改它。
- **首帧暂停的实现必须解耦调用栈**：ffmpeg 后端在部分机器上从解码线程同步发 frame 信号，若在该信号里直接反向控制 player（pause / setPosition），离屏测试与个别显卡驱动会死锁。现实现是首个有效 frame 到达后 `QTimer.singleShot(0, ...)` 再暂停并回到 0，且先静音再恢复原静音态，避免加载瞬间出声。
- 包内视频提取沿用预览缓存的请求 token 机制：切换节点会取消旧任务，迟到结果按 token 丢弃，不会覆盖当前预览。

### 3.13 用户解压流程（实测）

- 底层 `extract_entries` 的默认行为仍是覆盖，新增 UI 显式传 `overwrite` / `skip`，分别映射 7-Zip `-aoa` / `-aos`，因此预览缓存与导出中转等既有调用不受影响。
- 解压前必须从已挂载清单计算实际落地路径，并按 Windows 大小写不敏感语义检查；文件/目录类型冲突和 `A.txt` / `a.txt` 这类包内冲突不能交给 7-Zip 静默决定。
- 单个整包固定套包名文件夹；单包部分条目直接保持包内路径；跨包始终套独立包名文件夹，同名包依次追加 `_2` / `_3`。取消或失败不回滚已经写入的文件，界面必须明确告知存在部分结果。
- 中文 ZIP 子集、普通 ZIP + 中文密码 7z 跨包整包解压，以及超过 260 字符的中文目标路径均已通过真实 7-Zip 调用验证。

### 3.14 树直接拖出、源树状态与 GIF 预览（实测）

- 首版在 `QDrag` 前用模态进度窗口完成物化，用户松开鼠标时系统拖放尚未开始，因此首次手势必然失效、缓存后的第二次才成功。修复为先启动 `QDrag`，用 `QMimeData.retrieveData("text/uri-list")` 在 Windows 资源管理器于同一次 OLE 会话索取 URL 时延迟物化并返回真实路径；应用内目标只读取自有 MIME，不触发解压。
- 拖出物化沿用导出映射和 7-Zip 提取语义：父子选择去重、空目录保留、目标树重命名生效；跨源包按包名分组并稳定追加 `_2` / `_3`。取消/失败整代清理且不返回 URL，成功代次保留到会话关闭；相同树结构且源文件时间戳未变化时复用会话缓存。
- 源包树收起的根因是目标树 drop 触发会话刷新后，`SourcePanel.refresh()` 执行模型 reset；修复为 reset 前后按 `(source_id, 规范化包内路径)` 保存与恢复展开、选择、当前项和滚动位置，不能通过跳过刷新规避。
- GIF 使用 Qt `QMovie` 播放，Pillow 只负责尺寸/帧数预检；切换预览、清空或关闭会话时必须先停止并解绑旧 movie。PyInstaller 包内已确认包含 `plugins/imageformats/qgif.dll`。

### 3.15 资源管理器「解压到此处」

- 注册位置为每个已关联扩展名的 `HKCU\Software\Classes\SystemFileAssociations\.<ext>\shell\ZipMerge.ExtractHere`，命令为 `ZipMerge.exe --extract-here "%1"`；不写扩展名默认值，仍不抢占默认程序。
- `--extract-here` 必须在单实例检测前短路：它是独立的模态解压任务，不能被转发为主窗口的 `open` / `addToSession` 请求。整包直接写入压缩包所在目录，不套包名层；冲突预检、默认跳过、覆盖选择、密码和取消全部复用应用内解压链路。
- 覆盖安装后只有启动新版程序才能安全获知旧注册状态；若已有 ZipMerge 注册项且 `integration/extract_here` 尚不存在，按默认开启迁移并补写新 verb。设置页提供独立开关，卸载程序与 Inno Setup 兜底均删除该键。

### 3.16 RAR4 / RAR5 正式只读支持（实测）

- 运行时不引入新依赖，统一使用随包 7-Zip 25.01 的 `Rar` / `Rar5` 解码器。`.rar` 已进入唯一后缀事实来源，因此文件选择、拖入识别、系统关联、右键「解压到此处」与压缩包图标共用同一能力。
- 使用 libarchive 测试套件的固定版本 `a4ba070a01e967f7b98998c654dcc082e16fe58c` 样本验证：RAR4 可正确列表并解出文件、目录与空目录；RAR5 可正确列表、完整性测试并解出已知内容。
- RAR 源包已打通导入 → 用户解压、源树拖出物化、目标结构整理 → 导出 zip 的真实链路；源 RAR 永不改写。
- 能力边界是**只读 RAR4 / RAR5**。7-Zip 不提供 RAR 编码器，ZipMerge 不新增 RAR 输出；加密 RAR、多卷 RAR、恢复记录及损坏分卷仍待合法固定样本补测。

### 3.17 主窗口几何与完整布局恢复（实测）

- 正常关闭时用 `QMainWindow.saveGeometry()` 保存窗口位置、大小及最大化状态，首个工作区创建完成后再 `restoreGeometry()`；若恢复过早，首个标签的 `sizeHint` 会覆盖窗口宽度。
- 工作区继续以 `KEY_LAYOUT` / `KEY_LAYOUT_SIZES` 为窗口级事实来源，并补齐底部预览布局的内层源包/目标比例；源包与预览面板的显隐状态分别持久化。
- 面板隐藏后 QSplitter 会报告 `0` 尺寸，因此隐藏前先保存非零比例，关闭时不让隐藏态的零值覆盖它；下次重新显示可恢复原比例。
- Qt 会在显示器或 DPI 变化时把恢复窗口约束回可见区域；无效几何配置返回默认 `1180 × 720`，不得阻止启动。离屏平台与真实 Windows 平台的关闭→重建→恢复均已通过。

### 3.18 内部拖拽与外部物化触发隔离（实测）

- Windows OLE 会在 `QDrag.exec()` 刚开始时预读取已声明的 `text/uri-list`；旧实现把任意 `retrieveData()` 调用都视为资源管理器请求，因此鼠标刚起拖就弹出物化进度，即使最终只在应用内移动。
- `LazyFileMimeData` 现在需要两个条件同时成立才允许物化：`QDrag.targetChanged` 已表明目标离开本应用，并且 `QApplication.widgetAt(QCursor.pos())` 确认光标下没有 ZipMerge 控件。后一个条件用于区分“真正离开应用”和“应用内非接收区也返回空目标”。
- 主窗口、源包面板和源包树遇到 `application/x-zipmerge-*` 时直接按内部拖拽忽略，不再进入 URL 分支；目标树仍优先处理 `MIME_NODES` / `MIME_SOURCE`。外部资源管理器索取 URL 的链路与缓存复用保持不变。

### 3.19 安装后默认右键菜单与首次关联引导（实测）

- Inno Setup 在 `[Run]` 中隐藏调用 `ZipMerge.exe --install-context-menus`；命令只调用 `ShellIntegration.initialize_install_defaults()`，注册表实现仍保持单一事实来源。首次安装默认开启并注入“添加到当前合并会话 / 用 ZipMerge 打开 / 解压到此处”三个菜单；覆盖安装则按现有范围与开关重写路径，不再因初始化标记而跳过升级。
- 格式专用右键菜单现覆盖全部受支持后缀，不再依赖用户勾选的关联集合；用户拒绝文件关联后仍可使用右键操作。`OpenWithProgids` 与 `Capabilities` 仍只由关联选择控制，绝不写扩展名默认值。
- 安装器设置一次性待引导标记。首次正常启动先询问是否关联全部受支持格式，再询问是否设为默认；选择默认但尚未关联时先登记候选，然后打开 `ms-settings:defaultapps`。Windows 10/11 的 `UserChoice` 哈希保护决定最终默认项必须由用户在系统页确认，程序不能静默完成。
- `install_defaults_initialized` 是持久化幂等标记：升级/修复安装保留三个菜单的既有开关，不把用户已关闭的选项重新打开；卸载仍由 `--unregister-all` 加安装器兜底清理。

### 3.20 树视图取消交替行背景（实测）

- 目标结构树不再启用 `alternatingRowColors`；全局 `QAbstractItemView` 的备用交替色也与普通内容底色统一，避免文件行出现一明一暗的斑马纹。
- 行分隔线、悬停、选中与焦点反馈保持不变，只调整普通行背景。

### 3.21 导出事务、即时取消与大目录预检（实测）

- 导出不再直接对最终路径执行 `7z a`。程序先在同一目标目录创建隐藏唯一候选包，完整成功后用 `os.replace()` 原子提交；取消或失败会删除候选包与暂存区，已有目标文件保持原样，因此覆盖不会残留旧条目，也不会暴露半成品。
- 7-Zip 子进程由独立守护线程每 20 ms 观察取消事件；即使 stdout 长时间没有换行，也会主动终止进程并抛出专用 `ArchiveCancelled`。取消会贯穿列表、提取、物化和打包链路，不再被误报为普通失败。
- 磁盘文件夹在导出预检时递归展开，内部文件参与数量、大小与大小写不敏感路径冲突计算；软链接跳过，文件仍在执行阶段从原路径实时复制。
- 源包条目按规范化包内路径建立会话级索引，导出预估和预览不再反复线性扫描。10,000 条目预估由 2.183 秒降至 0.0122 秒（同机单次对照，约 179 倍）。
- 压缩包列表读取移入 `QThread`，避免大型包导入时阻塞主界面；源包/目标树的延迟刷新与滚动恢复改用有 QObject 所有权的计时器，标签关闭会自动取消回调。
- 仍被目标树引用的源包禁止移除，避免产生悬空来源；成功移除源包后清空撤销栈，防止 redo 恢复已失效引用。
- 输出密码由导出栏设置，只保存在当前会话内存；7z 格式使用 `-mhe=on` 同时加密文件名，zip 保持格式自身能力。

### 3.22 深色主题采用 Windows 11 中性灰表面层级（实测）

- 原深色主题约 81% 画面落在冷调 `#12161C`，且自定义 `QWidget` 面板虽有 `role="panel"`，未启用 `WA_StyledBackground` 时并不会可靠绘制 QSS 背景；仅替换令牌无法让面板层出现。
- 深色令牌改为 `#202020` 窗口底板、`#2B2B2B` 面板、`#323232` 控件，容器边界用 `#1D1D1D`；树、空态和普通预览透明落在面板上，图片 / 视频 / 波形仍保留独立暗画布。四个主面板显式启用 styled background。
- 系统或用户强调色作为原始设置保留；调色板显示色若与最亮常用表面的对比度不足 3:1，深色向白、浅色向黑逐级混合。本机 `#4C4A48` 在深色控件表面由约 1.6:1 修正为 `#7C7A79`、达到 3:1，避免工具栏图标和焦点框消失。
- 1180×760 空态实拍的主色占比为 `#2B2B2B` 71.76%、`#202020` 17.65%、`#323232` 6.18%；已加载音频、浅色主题、设置页，以及 170% / 200% 缩放真实 Windows Qt 截图均无错位。全量单测 351 / 351 与 `compileall` 通过。
- 已按当前工作树重建 ZipMerge 0.1.4：`dist/ZipMerge` 144.8 MB 并通过 `verify_bundle.py`，打包 EXE `--version` 正常退出；安装包 51.97 MB，SHA-256 为 `A229B32D7EC6EDEF6D3CA851CD957CBECB00E32F6FEB4E7F893510FC817D1A3E`。

### 3.23 目标结构同名文件在编辑时解决（实测）

- 同名文件不再等到导出才处理：源包条目拖入、磁盘文件拖入 / 右键加入、内部移动、复制粘贴、摊平与重命名都把一次操作的冲突汇总后只提示一次。
- 比较沿用 Windows 大小写不敏感语义；「覆盖旧文件」以后到者替换先到者，「重命名新文件」沿用 `name_1.ext` 规则，取消不修改树。覆盖、自动改名与被替换旧节点作为同一个复合命令进入撤销栈，Ctrl+Z 可完整恢复。
- 本轮只允许文件覆盖文件；目录重名和文件 / 目录路径冲突继续保留导出层的安全策略，不用“覆盖”删除整个目录子树。
- 已按包含本功能的当前工作树干净重建 ZipMerge 0.1.4：`dist/ZipMerge` 144.8 MB，bundle 校验与打包 EXE `--version` 通过；安装包 51.97 MB，SHA-256 为 `FC009D41DBAA7E01CB7EC9FC9A7490A0B4D2E75D880E6B5F5DF72E94B39E4D22`。本轮只生成安装包，未执行安装。

### 3.24 视频选中即就绪，但不再自动播放解码（实测）

- 卡死风险来自两层：目标树选中视频会立即 `setSource + play`，磁盘视频还完全绕过大小限制；此外 `QVideoSink.videoFrameChanged` 可能由解码线程发出，旧实现会在回调里读取 `QMediaPlayer.duration()`，线程边界不够明确。普通 H.264 在本机可正常播放，因此不是所有视频都必现，风险主要受文件编码、分辨率、体积和显卡驱动路径影响。
- 最终交互没有增加「加载视频」前置步骤：磁盘视频选中后立即 `setSource`，但不自动 `play`；播放器和空格快捷键已经可用，用户直接按空格才开始解码和播放。小型包内视频后台提取完成后同样进入待播放状态；只有超过 512 MB 自动提取上限的包内视频仍需明确点击加载。
- 视频帧信号强制用 `QueuedConnection` 投递到控件所属 UI 线程；frame 回调不再可能从解码线程跨线程读取播放器状态。旧的“自动播放到首帧再暂停 / 归零”链路已删除，因为它正是选中动作触发解码和播放器反向控制的来源。
- 定向视频回归 15 / 15、全量单测 360 / 360 与 `compileall` 通过。真实 Windows Qt 探针确认：选中 1080p H.264/AAC 视频后 source 已设置、状态保持 `StoppedState` 且尚无视频帧；一次空格后进入 `PlayingState` 并解出 1920×1080 帧，事件循环持续响应。
- 已按最终交互干净重建 ZipMerge 0.1.4：`dist/ZipMerge` 144.8 MB，bundle 校验与打包 EXE `--version` 通过；安装包 51.98 MB，SHA-256 为 `15C567B7F1E78E15B0A1FFFDD35E19CFA7743FAAEB3D7D03603D9AD12D06BFC5`。本轮只生成安装包，未执行安装。

### 3.25 音频信息栏与分声道波形（实测）

- 音频信息栏由笼统的「音频 · 大小」改为「格式 · 声道 · 采样率 · 源位深 · 大小」；例如真实 48 kHz / 24-bit stereo PCM 显示 `WAV · stereo · 48kHz · 24bit · 562.6 KB`，mono 显示 `mono`。WAV 的声道、采样率与位深直接通过标准库读取 RIFF/WAVE 头，不能把 Qt 解码后为便于计算而扩展成的 `s32` / float 当作源位深。非 WAV 格式固定显示容器名，并由 QAudioDecoder 解码格式补齐声道和采样率；取不到的源位深留空，不臆造。
- 原峰值累积把交错多声道样本合成一套 min/max，stereo 必然只能画成单行。现改为以「每声道帧」为桶宽，L/R 各自保留峰值数组与多级金字塔；WaveformView 按声道分 lane 绘制并共享一条播放头，mono 一行、stereo 上下两行，中间有弱分隔线。
- 48 kHz / 24-bit stereo WAV 已真实解码并确认两套独立峰值；真实 Windows 760×360 预览截图确认双行振幅差异、分隔线和完整信息栏。音频定向测试 44 / 44、全量单测 367 / 367 与 `compileall` 通过。
- 已按当前工作树重建 ZipMerge 0.1.4：`dist/ZipMerge` 144.8 MB，bundle 校验与打包 EXE `--version` 通过；安装包 51.97 MB，SHA-256 为 `1B7F66B0243E78BAE6E2468D76875FAB0E7D619F1A2183B67E249ECA5B7825A3`。本轮只生成安装包，未执行安装。

### 3.26 树直接拖出不能依赖 `QDrag.targetChanged(None)`（实测）

- 3.18 的“双重门控”结论已被真实 Windows 行为推翻：`QDrag.targetChanged` 追踪的是本应用内接受拖放的 Qt 对象；若拖拽从未经过一个接受载荷的应用内目标，当前目标会从始至终保持 `None`，直接进入 Explorer / 桌面不会产生 `targetChanged(None)`。
- 旧实现把 `_external_target` 初始为 `False`，仅在上述信号到达后才允许 `retrieveData("text/uri-list")` 物化。源包树直接拖到桌面时因此始终返回空 URL，表现为松手后没有任何文件；这与条目扩展名无关，`.dll` 经同一 7-Zip 物化链路已验证可完整落地且字节一致。
- 外部判定现只使用 `QApplication.widgetAt(QCursor.pos())`：Windows 起拖预探测时光标仍在 ZipMerge 控件上，不物化；外部程序真正索取数据时光标下无本应用控件，才物化。应用内目标继续优先处理 `application/x-zipmerge-*`，因此不会读取 URL 或触发解压。
- 回归测试不再伪造 `targetChanged(None)`，直接覆盖“起拖预探测 → 无目标信号 → 外部索取 URL”，并保留内部拖动不物化、缓存复用和包内文件物化覆盖。
- 包内 `.dll` 已加入正式回归并逐字节核对；全量单测 369 / 369、`compileall` 与真实 Windows Qt 主窗口启动探针通过。
- 已按当前工作树重建 ZipMerge 0.1.5：`dist/ZipMerge` 144.8 MB 并通过 `verify_bundle.py`，打包 EXE `--version` 正常；安装包 `installer/Output/ZipMerge-Setup-0.1.5.exe` 51.9 MB，SHA-256 为 `5FA22001627C409FEE000487B9F6C4D291534FD59003A657130EDE4C06898C59`。检测到用户已有 ZipMerge 进程，未强制关闭或执行安装。

### 3.27 Explorer 桌面会缓存首次 `CF_HDROP`（用户实测 + 源码取证）

- 3.26 仅删除 `targetChanged(None)` 仍不足：用户实测同一个包内 `.dll` 拖到 OneCommander 有效、拖到 Windows 桌面无效。差异说明 OneCommander 会在进入目标后重新索取 URI，而 Explorer 桌面使用了拖拽开始时取得并缓存的空 `CF_HDROP`。
- Qt 6.10.2 的 `QWindowsMimeURI::convertFromMime` 在 Windows 请求 `CF_HDROP` 时调用 `mimeData->urls()` 并立即把当时的本地路径写入 `DROPFILES`；这一轮返回空后，ZipMerge 无法要求 Explorer 丢弃缓存并重取。
- 最终方案是在 `QDrag.exec()` 已启动后，第一次 `text/uri-list` 数据请求内立即物化并返回真实路径。这样进度窗口仍处于同一次 OLE 拖放会话，不会重现“QDrag 前准备导致首次手势丢失”，同时 Explorer 第一次取得的就是有效 `CF_HDROP`。
- 取舍：Windows OLE 可能在目标明确前预取 URI，因此最终只做应用内拖动时也可能产生一次临时物化；内部接收区仍只解析 `application/x-zipmerge-*`，不会把临时 URL 当作新文件导入，物化结果由会话缓存复用并在会话关闭时清理。
- Explorer 首次预取、直接外拖缓存复用、包内 `.dll` 字节一致性等定向测试 82 / 82 通过；全量单测 369 / 369、`compileall` 与真实 Windows Qt 主窗口启动探针通过。自动化无法代替用户完成 Explorer 桌面上的实体鼠标手势，因此最终桌面投放仍需安装后人工确认。
- 已按最终修复重建 ZipMerge 0.1.5：`dist/ZipMerge` 144.8 MB 并通过 `verify_bundle.py`，打包 EXE `--version` 正常；安装包 `installer/Output/ZipMerge-Setup-0.1.5.exe` 54,427,752 字节，SHA-256 为 `152CEB6B927A472A46BCA53469E2F658E6FF91A606DCBA3B09DFD50F3774789C`。检测到已有 ZipMerge 进程，未强制关闭或执行安装。

### 3.28 外拖占位路径兼顾 Explorer 缓存与内部零物化（用户实测）

- 3.27 的“首次 URI 请求立即物化”解决了 Explorer 桌面无响应，但 Windows 会在目标明确前预取 `CF_HDROP`，因此纯应用内拖动也必然触发 7-Zip 和进度框；这不是内部 drop 处理器误读 URL，而是源数据对象过早执行了昂贵渲染。
- 最终方案不再在 `retrieveData()` 中物化。起拖前只创建与所选顶层同名的空文件 / 空目录，并让首次 `CF_HDROP` 缓存这些已经存在的稳定路径；拖动期间每 15 ms 检查 `QApplication.widgetAt(QCursor.pos())`，光标真正离开 ZipMerge 后才把真实内容物化到同一路径。Explorer 不需要重取 URL，内部手势也不会解压。
- 若手势始终在应用内，`QDrag.exec()` 返回后立即删除本次占位代次；若已经外拖，则保留并复用正常物化缓存。光标曾离开应用再返回时可能已经开始物化，这是对真实外拖意图的合理处理，不影响“全程应用内不弹窗”的目标。
- 自动回归模拟了 Explorer 起拖预取：外拖时预取路径与最终路径完全相同且真实内容已填入；内部时 `_run()` 零调用、占位路径在拖放结束后不存在。拖出定向 3 / 3、相关链路 82 / 82、全量单测 369 / 369、`compileall` 与真实 Windows Qt 主窗口启动探针均通过。
- 已按占位方案重建 ZipMerge 0.1.5：`dist/ZipMerge` 144.8 MB 并通过 `verify_bundle.py`，打包 EXE `--version` 正常；安装包 `installer/Output/ZipMerge-Setup-0.1.5.exe` 54,426,678 字节，SHA-256 为 `EE6EF4DB9EFEFB8DB0AFE0B3973F850EA94B8CE875A3351F8CFEEFDFEEF14C54`。本轮未执行安装。

## 4. 架构决策

| 决策 | 原因 |
| --- | --- |
| 引擎层不依赖 Qt | 纯 `subprocess` 封装，可被单元测试直接调用；UI 再包 QThread |
| 进度按行解析 `^\s*(\d{1,3})%` | 7-Zip 进度行随输出刷新，足够驱动进度条与取消检查 |
| 取消用 `threading.Event` | 引擎不感知 UI，UI 只需传入事件对象 |
| 失败统一 `ArchiveError` | 携带原始输出 + 中文 `hint`，UI 层不再解析文本 |
| 路径过长自动加 `\\?\` | 仅在 ≥250 字符时加，避免影响普通路径与 7-Zip 的相对路径解析 |

## 5. 验证状态

| 范围 | 状态 |
| --- | --- |
| 7-Zip 资产与解码器校验 | ✅ 通过 |
| RAR4 / RAR5 只读链路（格式入口、列表、完整性、目录识别、解压、拖出物化、合并导出、损坏包异常） | ✅ 6 项真实固定样本回归通过；输出仍限定 zip / 7z |
| `-slt` 解析（中文、目录、加密标志、7 位小数时间） | ✅ 10 项单测通过 |
| 7z 格式目录识别（无 `Folder` 行时的属性位回归） | ✅ 通过 |
| zip / 7z 打包→列表→解压→哈希比对（含中文名、空格名、深层空目录） | ✅ 通过 |
| 指定条目子集解压（中文名、整目录） | ✅ 通过 |
| 损坏包检测、非压缩包报错 | ✅ 通过 |
| 核心模型（目标树、撤销栈、导入语义、冲突、命名模板） | ✅ 全部通过 |
| 导出链路双源端到端（导入→摊平→改名→移动→冲突改名→打包→逐文件 SHA256、空目录保留、散文件/散文件夹、取消） | ✅ 通过 |
| 设计系统（令牌、QSS 生成、颜色运算、令牌键完整性、图标字形、字体注册、主题服务状态与信号、DWM 静默降级） | ✅ 32 项单测通过；深色强调色 ≥ 3:1，深浅主题主文字 ≥ 7:1、次级文字 ≥ 4.5:1 |
| 目标树 Qt 模型 + 源包条目导入语义（MIME、行数、类型/大小列、重命名拒绝） | ✅ 通过 |
| 源包面板、目标面板、导出栏（离屏冒烟 `PROBE OK` + 单测） | ✅ 通过 |
| 导出执行链路（QThread worker、进度/取消、冲突对话框、导出前重名询问） | ✅ 通过 |
| 预览面板（文本 UTF-8/GBK、图片 Pillow 解码、元信息、LRU 提取缓存） | ✅ 通过 |
| UI 主界面（多标签工作区、窗口几何、完整分割比例与面板显隐恢复、布局切换、主题回写、导入刷新、撤销重做） | ✅ 主窗口模块 25 项测试 + 真实 Windows 恢复探针通过 |
| 内部/外部树拖拽触发隔离 | ✅ 起拖预探测、内部目标、内部源→目标、应用内空目标、外部目标与缓存复用共 6 项定向用例在离屏/真实 Windows Qt 平台通过 |
| 安装后系统集成初始化与首次引导 | ✅ 三菜单默认注入/幂等、关联接受/拒绝、默认应用接受/拒绝及未关联补登记均有自动回归；Inno Setup 6.7.3 编译通过 |
| 程序入口 `main.py` / `run.bat`（参数、离屏启动、UI 包导出） | ✅ 通过 |
| 首标签页调色板下发（`_apply_theme()` 早于首个 `new_tab()` 的启动期回归） | ✅ 单测锁定 |
| 音频信息与波形播放（WAV 源格式 / 声道 / 采样率 / 位深、QAudioDecoder 分声道峰值金字塔、QMediaPlayer、指纹 LRU 缓存） | ✅ 48kHz / 24bit stereo 与 44.1kHz / 16bit mono 实测；mono 单行、stereo 双行；真实 Windows 截图通过 |
| 视频预览（选中即就绪但不自动播放、空格直接播放、`QVideoWidget`、进度、时间、音量 / 静音、全屏、Esc 退出） | ✅ 通过（H.264/AAC 中文名 MP4 走真实 VideoPreview；MOV / AVI / MKV 容器烟雾通过） |
| 包内视频后台提取与请求隔离（小文件自动提取后待播放、超阈值先给加载入口、切换节点取消旧任务、迟到结果按 token 丢弃） | ✅ 通过：阈值拦截、取消、迟到结果隔离、错误分支均有单测 |
| 系统集成（PLAN 8.5 键布局、注册 / 撤销 / 状态、右键两项独立、不改扩展名默认值） | ✅ 14 项单测通过 |
| 单实例 IPC（长度前缀分帧、JSON 往返、送达确认、二次启动转发） | ✅ 11 项单测通过 |
| 设置对话框（五组、状态回显、自定义后缀校验、apply 一次性落盘、一键撤销、临时占用） | ✅ 10 项单测通过 |
| 程序入口（参数解析、单实例请求分发、--unregister-all 短路） | ✅ 6 项单测通过 |
| 全量单测回归 | ✅ 367 / 367 通过 |
| 打包与安装（PyInstaller + 内置 7-Zip 资产 + Inno Setup） | ✅ 打包校验通过；安装→运行→注册→单实例→卸载全周期实测通过（见 3.10） |
| zip 条目名 UTF-8 编码（PLAN 12.6） | ✅ 通过：非 ASCII 名带 0x800 标志位 + UTF-8 字节；7-Zip / Bandizip / 资源管理器解码器三方复核无乱码（见 3.11） |
| 交付场景端到端（PLAN 12.8：双源导入→摊平→改名→导出） | ✅ 离屏驱动真实 UI 路径 14 / 14 通过（产出名、SHA256、空目录、7z 列表与完整性校验） |
| 长路径与大体量（PLAN 12.7） | ✅ 路径部分通过（356 字符绝对路径的打包→列表→解包→哈希全等）；多 GB 素材未实测 |
| 系统集成实机可见性（「默认应用」页、右键「显示更多选项」、设置页勾选注册） | ⏳ 注册表键与命令行链路已实测，GUI 交互路径未实测 |
| 本轮 16 项修复与新增（PLAN 6.5 / 6.6 / 6.8 / 7.3 / 7.4 / 7.6 / 7.7 / 8.3） | ✅ 全量单测 244 / 244 通过；导出目录自动跟随、模板库、目标树多选 / 空白右键 / 展开态保持、导入目录记忆均有新增单测 |
| 交付场景端到端（PLAN 12.8）改用测试专用设置 | ✅ 14 / 14 通过；运行后真实 HKCU 与 `%TEMP%` 均无残留 |
| 系统集成图标使用安装目录绝对路径（问题 10） | ✅ ProgID `DefaultIcon` 使用 `ZipMergeArchive.ico`；`ZipMerge.AddToSession`、`.zip` 的 `ZipMerge.Open` / `ExtractHere` 使用 `ZipMerge.ico`；两枚 ico 均实测可加载 16–256 px 八档尺寸 |
| 重新打包安装（本轮修复落到已安装实例） | ✅ `ZipMerge-Setup-0.1.0.exe` 静默安装成功（日志 `Installation process succeeded`）；安装文件与 `dist\ZipMerge` SHA256 全等；`ZipMerge.exe --version` 正常退出（code 0，无挂起） |
| 工具栏单按钮布局切换修复 | ✅ 246 / 246 单测、真实 Windows 窗口截图探针、142.5 MB bundle 校验与 49.8 MB 安装包重建通过；本轮未覆盖安装 |
| 会话临时交付包与跨应用拖放 | ✅ 265 / 265 全量单测；中文名 ZIP / 7z 实际生成→解包→内容比对、独立 generation、失败回滚、过期禁拖、单本地 URL + `CopyAction`、标签 / 程序关闭强制清理均通过；真实 Windows 窗口截图探针、142.5 MB bundle 校验与 49.8 MB 安装包重建通过；本轮未覆盖安装 |
| 视频预览与临时交付包的整体打包支持（`QtMultimediaWidgets` 解除剔除，见 3.12） | ✅ 280 / 280 全量单测；真实 Windows 窗口截图探针（中文名 MP4 首帧 + 临时包卡片 ready / 过期 / 缺失三态）通过；142.7 MB bundle 校验通过（`QtMultimediaWidgets.pyd` 与 `Qt6MultimediaWidgets.dll` 已在包内，`ZipMerge.exe --version` 正常退出）；49.8 MB 安装包重建通过；本轮未覆盖安装 |
| 深浅主题视觉升级 | ✅ 31 项设计单测 + 284 / 284 全量单测；1660×874 深浅三栏及「底部预览 + 紧凑密度 + 150% 字号」真实 Qt 截图通过；142.7 MB bundle 校验通过，49.8 MB 安装包已重建 |
| 用户解压（整包 / 条目 / 跨包、冲突预检、进度 / 取消） | ✅ 中文 ZIP 子集、带中文密码 7z、多包独立目录、覆盖 / 跳过、大小写与类型冲突、超 260 字符中文目标路径通过；302 / 302 全量单测、真实 Windows 窗口启动与截图通过；142.7 MB bundle 与 49.8 MB 安装包重建、`verify_bundle.py` 通过 |
| 树直接拖出、源树状态、GIF 与模板栏修复（0.1.1） | ✅ 312 / 312 全量单测；真实 Windows Qt 窗口截图确认 `（自定义）` 完整且模板按钮单箭头；物化线程、本地 URL、内部 MIME、缓存、中文 ZIP、空目录、大小写冲突与取消清理通过；142.8 MB bundle 校验及 GUI 启动通过，49.8 MB 安装包生成成功。实际鼠标拖到资源管理器未自动化，仍需安装后人工手势复核。 |
| 资源管理器「解压到此处」（0.1.2） | ✅ 临时 HKCU 注册/撤销、命令行短路、整包直落同级目录、设置开关与卸载兜底均有回归测试；315 / 315 全量单测通过；142.8 MB bundle 校验及 GUI 启动通过，49.9 MB 安装包生成并静默覆盖安装成功。内置 10 种格式的 HKCU verb/命令/安装目录图标实测正确，`.zip` 默认值仍为 `Bandizip.zip`。 |
| 首次拖出手势修复（0.1.3） | ✅ 回归锁定物化发生在 `QDrag.exec()` 内，且应用内拖动不物化；88 项相关测试与 317 / 317 全量单测通过；142.8 MB bundle 校验、`--version` 和真实 GUI 启动通过，49.9 MB 安装包生成成功。实际资源管理器鼠标手势无法自动化，仍保留人工交互复核风险。 |
| 安装默认三项右键菜单与首次关联引导（0.1.3） | ✅ 临时 HKCU 覆盖默认注入、幂等升级、保留用户关闭项、关联/默认应用四种选择组合与命令行 GUI 前短路；336 / 336 全量单测、`compileall`、142.8 MB bundle 校验与 Inno Setup 编译通过。安装包 49.9 MB，SHA256 `3A55F4F763219856CC712209B16E28F5DB5A93A0A097FEF4F10AE0C0BD1E99AC`；为避免改动当前真实文件关联，本轮未执行安装器实装。 |
| 文件树统一普通行背景（0.1.3） | ✅ 目标树真实 Qt 控件属性与深色 QSS 回归通过；337 / 337 全量单测通过，142.8 MB bundle 校验、`ZipMerge.exe --version` 与 49.9 MB 安装包重建通过。当前安装包 SHA256 `B4CD3930DDC12172021D89A47E64BE5206ACBAB1542263B4F04E61C12843DEAC`。 |
| 导出可靠性与大包响应优化（0.1.4） | ✅ 348 / 348 全量单测与 `compileall` 通过；静默子进程取消、事务化覆盖/取消回滚、磁盘目录内部冲突、7z 文件名加密、异步导入、Qt 延迟回调销毁均有回归；10,000 条目预估 0.0122 秒。142.9 MB bundle 校验、`--version` 与真实 GUI 启动通过；49.9 MB 安装包生成成功，SHA256 `B15480D99C941137F8A90777DB67B74B8DF516BD9F0804581257F71969982B4C`。本轮未执行安装器实装。 |
| 双图标与覆盖安装缓存刷新（0.1.4） | ✅ 350 / 350 全量单测通过；144.8 MB bundle 校验、已安装 GUI 启动、两枚 ICO 与 EXE 哈希一致。52.0 MB 安装包已静默覆盖安装；模拟旧 `DefaultIcon` 后可自动改写为安装目录 `ZipMergeArchive.ico`，`SHChangeNotify`、`ie4uinit -ClearIconCache` / `-show` 均成功且无需重启。安装包 SHA256 `C8CE06D1C40046C1090342D947C2A12CA69EBB0B2E4715A1739705918B319907`。 |
| 目标结构同名文件即时处理 | ✅ 360 / 360 全量单测与 `compileall` 通过；批量计数、大小写折叠、覆盖 / 自动改名 / 取消、拖入 / 重命名 / 移动 / 摊平及撤销均有回归，真实 Windows Qt 对话框探针通过；144.8 MB bundle 校验及 51.97 MB 安装包构建通过。 |
| 视频按空格开始播放与音频分声道预览 | ✅ 视频选中不主动解码但无需「加载视频」，空格直接播放；音频显示真实格式 / 声道 / 采样率 / WAV 源位深，mono 单行、stereo 双行。367 / 367 全量单测、真实 Windows Qt 音视频探针、144.8 MB bundle 校验及 51.97 MB 安装包重建通过。 |
| 用户交付双图标与 0.1.5 安装包 | ✅ 两枚 ICO 均含 16–256 px 八档尺寸，仓库与 bundle 内哈希和交付源逐一一致；367 / 367 全量单测、144.8 MB bundle 校验、`ZipMerge.exe --version` 与版本资源均为 0.1.5。安装包 51.89 MB，SHA256 `8FEEEBD282B39D1A481C9707C92A06FF0FF967810E3C3A920BB61395B7CC8DC9`。 |

## 6. 已知限制与未验证项

- 清单文件里的条目名按 7-Zip 的「掩码」语义匹配，文件名含 `*`、`?`、`[` 时可能被当作通配符。现实中罕见，暂不处理。
- RAR4 / RAR5 单卷普通包已有固定样本回归；加密 RAR、多卷 RAR、恢复记录和损坏分卷尚未覆盖。
- 未在 170%/200% DPI、多屏、Mica 不支持的环境下实测。
- QtMultimedia 的 DLL 预加载只在当前这台机器（PySide6 6.10.2 / Qt 6.10.2）实测；若 PySide6 改变 ffmpeg 库的打包位置，预加载的文件名模式需同步调整。
- 音频波形已覆盖 mono / stereo；超过双声道的素材会按实际声道数继续分行，但 5.1 / 7.1 的声道标签、布局与大体量性能尚未专项验证。
- 视频只实测了 H.264/AAC 的 MP4 与 MOV / AVI / MKV 容器烟雾；HEVC / AV1 / ProRes / 10-bit 等编码能否解码取决于随 PySide6 分发的 Qt FFmpeg 后端，未逐一实测。硬件加速解码未单独验证。
- 视频全屏在单显示器、100% 缩放下验证；多屏与高 DPI 下的全屏行为未实测。
- 打包脚本已净化 PATH 避开外来 native 目录（见 3.10）；换机器或 CI 上构建时需确认该保护仍生效，否则会静默收进不兼容同名 DLL。
- 安装 → 运行 → 注册 → 单实例转发 → 卸载的链路已实测，但设置页勾选注册、右键菜单在「显示更多选项」中的真实可见性与点击体验属 GUI 交互，未在自动化环境中实测。
- 中文名核验用各自解码器完成（7-Zip CLI、Bandizip `bz.exe`、shell zip 命名空间），未在资源管理器 / Bandizip 的 GUI 窗口里肉眼确认；且本机为中文区域设置，无法反映异地区码机器上的表现。
- 长路径只用 356 字符的绝对路径验证过；多 GB 素材下的磁盘峰值与耗时未实测。`create_archive` 的 `cwd` 未做扩展长度前缀处理（导出走短路径暂存根，故未暴露），源目录本身超长时需另行处理。
- 临时交付卡片已验证 Windows 本地文件 URL MIME 与仅复制动作，并完成真实窗口状态探针；拖到资源管理器、微信 / 企业微信或邮件客户端的最终接收行为仍需人工实机验收，不同客户端对系统拖放的支持程度不由 ZipMerge 控制。
- 本轮修复已重新打包安装（`python tools/build_release.py` → `installer\Output\ZipMerge-Setup-0.1.0.exe` 静默安装）：`%LOCALAPPDATA%\Programs\ZipMerge` 的 `ZipMerge.exe` 与 `_internal\assets\icons\ZipMerge.ico` 与新构建产物 SHA256 全等。系统集成的 `Icon` 已按新 `_icon()` 指向**安装目录**的 ico（`<安装目录>\_internal\assets\icons\ZipMerge.ico,0`），ProgID `DefaultIcon`、`ZipMerge.AddToSession`、`.zip` 的 `ZipMerge.Open` 三处均为该值；资源管理器若仍显示旧图标属 shell 图标缓存，需重启 explorer 或重新登录。
- 系统集成 `Icon` 指向源码目录 ico 会让「移动仓库」与「重新打包」后图标失效，因此注册时应按已安装（frozen）路径写入：frozen 下 `app_root()` 即 `sys._MEIPASS`（`<安装目录>\_internal`）。当前注册值已落到安装目录，不依赖仓库位置。
- `tests/test_waveform.py` 中用 `addCleanup(shutil.rmtree, tmp, True)` 的波形面板 / 预览用例（`zipmerge-wave-panel-*`、`zipmerge-wave-preview-*`）会在 `%TEMP%` 残留一个空目录：`deleteLater()` 要等下一轮事件循环才真正析构，清理时 QMediaPlayer 仍占用 wav 文件，`ignore_errors=True` 静默跳过了删除。不影响用例结果，属测试卫生问题。

## 7. 决策日志

| 日期 | 决策 | 原因 |
| --- | --- | --- |
| 2026-09-11 | 引擎层先于 UI 落地并配单测 | 解析与路径行为风险最高，先用可自动化验证的方式固定事实 |
| 2026-09-11 | 用 `-i@listfile` 传条目名 | 见 3.1，`--` 之后的 `@listfile` 会静默失效 |
| 2026-09-11 | 目录识别加入 `Attributes` 判据 | 见 3.3，7z 格式目录无 `Folder` 行，曾被误判为文件并导致导出失败 |
| 2026-09-11 | 摊平（`ReplaceNode`）记录替换节点原父子位置 | 替换节点来自被摊平节点的子树，不脱离原父节点会导致 children 与 parent 链不一致、撤销后结构损坏 |
| 2026-09-11 | 内容标识关键词一律按词边界匹配 | 子串匹配会让 `voice` 命中 `invoice`，产生错误建议 |
| 2026-09-11 | QSS 用 `string.Template` 由令牌表生成，令牌缺失直接 `KeyError` | 用 f-string / `%` 会和 QSS 的 `{}` 冲突；`substitute` 的严格性正好被测试用来卡住不完整映射 |
| 2026-09-11 | 需要图片的 QSS 子控件用运行时渲染的 PNG，而非 Unicode 或自绘 | QSS 既不认字体字形也不支持 data URI；用内置 Fluent 字形渲染才符合「不用临时绘制代替正式设计资产」 |
| 2026-09-11 | 主题监听用 `RegNotifyChangeKeyValue` + 后台线程 + Qt 队列信号 | 该 API 是阻塞式通知，必须放非 UI 线程；跨线程 emit 由 Qt 自动排队投递回主线程 |
| 2026-09-11 | 动效统一受 `SPI_GETCLIENTAREAANIMATION` 控制，关闭时直接落终值 | PLAN 6.7 要求；同时避免无谓动画影响低配机 |
| 2026-09-11 | `dwmapi` 全部封装为返回布尔值，失败静默降级 | Mica / 圆角在旧系统与虚拟机上不可用，不能让 UI 崩或报错 |
| 2026-09-11 | 导出栏内容标识建议只在「用户未编辑且当前为空」时自动填入 | 用户手动填写后不得被后续导入覆盖，否则会静默改写用户输入 |
| 2026-09-11 | 冲突对话框对 `PATH_CLASH` 行不提供「覆盖」选项 | 覆盖会删除目标目录的既有子树，风险高于重名文件，仅允许重命名/跳过 |
| 2026-09-11 | 预览提取缓存用 LRU + 安全相对路径（过滤 `..`/盘符） | 避免源包内恶意路径写到临时目录之外，同时限制磁盘占用 |
| 2026-09-11 | `QFormLayout` 等 C++ 子对象必须挂在长生命周期的 widget 上 | 曾把布局装在临时 widget 上，widget 被 GC 后布局成悬空对象，切换预览页即 `RuntimeError` |
| 2026-09-11 | `QKeySequence(QKeySequence.StandardKey.X)` 必须显式包一层 | `StandardKey` 是枚举而非 `QKeySequence`，直接传给 `setShortcut` 不生效 |
| 2026-09-11 | 删除 / 重命名快捷键由 `TargetPanel` 自身持有，菜单项不重复绑定 | 同一窗口内两处绑定相同序列会触发 ambiguous shortcut，行为不确定 |
| 2026-09-11 | 布局偏好（`KEY_LAYOUT` / `KEY_LAYOUT_SIZES`）为窗口级，统一下发到所有标签 | 切换布局是全局意图；每标签各存一套会让用户困惑且难以预测 |
| 2026-09-11 | 关闭最后一个标签后自动新建空会话 | 避免出现无标签、无处操作的空窗口 |
| 2026-09-11 | 目标面板 `refresh()` 同时刷新统计与空态 | 之前只有 `reload_now` 更新，会话变化后统计数字会滞后 |
| 2026-09-11 | 导入 QtMultimedia 前先预加载 ffmpeg 共享库 | 见 3.7；Qt 插件加载器不带 `LOAD_WITH_ALTERED_SEARCH_PATH`，插件的传递依赖解析不到会静默退化为「无后端」 |
| 2026-09-15 | 峰值桶按每声道帧独立累积，缓存保存各声道金字塔 | 交错样本合并只能得到单行总包络，无法表达 stereo 左右声道差异；分声道后 mono/stereo 才能稳定对应一行/两行 |
| 2026-09-11 | 峰值缓存键用「文件大小 + 头尾各 64 KiB」的 SHA1，而非整个文件哈希 | 长音频做全文件哈希要读完整文件，代价与收益不成比例；头尾足以区分真实文件 |
| 2026-09-11 | `PeakData.columns()` 在桶数少于目标宽度时不补齐 | 补零会伪造出「静音」波形，直接返回现有列更诚实 |
| 2026-09-11 | 新建标签页时显式下发当前调色板 | `_apply_theme()` 在首个 `new_tab()` 之前执行，首个标签曾保留构造函数里的默认强调色（表现为波形用默认蓝而非系统强调色） |
| 2026-09-11 | 系统集成只写 HKCU，且绝不写扩展名的 (Default) 值 | 免管理员；不抢占 Bandizip 等现有默认程序，只在「打开方式」里增加候选 |
| 2026-09-11 | OpenWithProgids 的存在性判断用 has_value | 见 3.8，REG_NONE 空值读不回，read_value 会把已注册误判成未注册 |
| 2026-09-11 | 单实例 IPC 用长度前缀分帧，并以「服务端主动断开」当送达确认 | 见 3.9，readAll 会截断、waitForBytesWritten 对短载荷不可靠 |
| 2026-09-11 | 设置对话框只有 apply() 写盘，取消不留半套修改 | 注册表与设置必须一致；边改边写会出现「勾了没生效」或反例 |
| 2026-09-11 | 自定义后缀在 apply() 里先落表、再撤销被移除项 | 先 set_custom_suffixes 才能让 candidate_extensions 覆盖新后缀，撤销才有对象 |
| 2026-09-11 | 关闭标签页时是否清理临时文件改为设置项（默认仍清理） | 用户可能需要保留临时目录排查问题；默认行为保持 PLAN 8.6 |
| 2026-09-11 | SettingsDialog 复用 main_window 的 LAYOUTS / MODE_LABELS | 布局与主题模式名称保持单一事实来源，避免两处常量漂移 |
| 2026-09-11 | 打包时净化 PATH，摘掉 codex-runtimes 等外来 native 目录 | 见 3.10：PyInstaller 按文件名解析二级依赖，会收进同名不兼容 DLL 导致 QtWidgets 加载失败 |
| 2026-09-11 | spec 与校验脚本按文件名剔除 icu*/ucrtbase/api-ms-win-* | 守卫：即使 PATH 再被污染也不让不兼容系统库进包，且让校验能直接判失败 |
| 2026-09-11 | CLI 输出统一走 `_emit` 容错 | 见 3.10：窗口模式下 sys.stdout 为 None，异常会被 PyInstaller 变成阻塞对话框，让 --version / --unregister-all 挂死 |
| 2026-09-11 | 安装包内置简体中文语言文件，且只保留中文 | 标准 Inno Setup 不含中文 isl，指向 compiler: 会编译失败；产品文案全中文，混排英文无意义 |
| 2026-09-11 | 卸载兜底清理同时删除 `HKCU\Software\ZipMerge` | 该键含 QSettings 与 Capabilities；卸载以「无残留」为准（PLAN 12.5），与覆盖安装保留设置互不冲突 |
| 2026-09-11 | 打包 zip 时加 `-mcu` 强制 UTF-8 条目名 | 见 3.11：7-Zip 默认按本地 ANSI 代码页写名，跨区域设置机器易乱码；PLAN 12.6 要求默认 UTF-8 |
| 2026-09-11 | e2e / 验收脚本一律用独立 `Settings("ZipMergeTest", "ZipMergeTest")` 并自清理临时目录 | 问题 3 / 6 的真根因：`acceptance_e2e.py` 用真实 `Settings()` 把 `export/output_dir` 写成 `%TEMP%\zipmerge-e2e-*\out` 且从不清理，残留的临时目录又被当成源包 |
| 2026-09-11 | 导出输出目录默认跟随首个源包目录，用户选定后停用（`export/output_dir_auto`） | PLAN 问题 4；只在真实选择（键入 / 浏览 / 设置页改动）时置 0——实测 `editingFinished` 单凭失焦也会触发，不能当「用户改了目录」的依据 |
| 2026-09-11 | 命名模板库存 QSettings JSON（`export/templates` / `export/active_template`），旧 `naming/*` 键继续作为写入来源 | 不新增文件格式与依赖；模板库为空时用旧键构造「默认」条目，老用户设置无损迁移 |
| 2026-09-11 | 「添加压缩包」记忆上次目录（`import/last_dir`），对话框与拖放共用 `add_archives` | 补充项 11；两条入口走同一函数，记忆行为才一致 |
| 2026-09-11 | 空格 = 播放 / 暂停，只注册在目标树与预览面板内（`WidgetWithChildrenShortcut`） | 文本输入控件自带空格语义，窗口级快捷键会抢键（PLAN 问题 8） |
| 2026-09-11 | 双击 / 右键「打开」走系统默认程序；重命名只保留 F2 与右键 | 双击与 `SelectedClicked` 编辑触发冲突，去掉后者才能让双击专职打开（新增 1） |
| 2026-09-11 | 目标树 `refresh()` 前后按 uid 收集 / 还原展开集与当前项 | 拖放触发的 model reset 会清空展开态（问题 7）；连接必须晚于 `view.setModel()`，否则视图自身的 reset 先行清空 |
| 2026-09-11 | 右键点已选中项用 `setCurrentIndex(..., NoUpdate)` 保多选；空白处右键先清空选区回根 | 选中集即操作集（问题 2）；空白右键的真正目标是根目录（问题 5） |
| 2026-09-11 | 导出栏固定高度贴底、三栏吸收多余高度；标签栏弃用 `QTabWidget.documentMode` | 界面 2 / 3：`documentMode` 把标签栏与内容压进同一行高度，pane 顶边线会压住标签 |
| 2026-09-11 | 系统集成 `Icon` 指向 `assets/icons/ZipMerge.ico` 绝对路径，仅缺失时回退 exe | 问题 10：源码运行时 exe 是 python.exe，指向它只会显示 Python 图标 |
| 2026-09-11 | `DISK_FILE` / `DISK_DIR` 在导出时从原磁盘路径实时复制，导入不建快照 | 源目录后续改动能直接带出，避免生成一份与源脱节的数据副本（PLAN 7.6 第 5 步） |
| 2026-09-11 | 主工具栏用一个「切换布局」动作在三栏并排 / 两栏底部预览之间循环，不再放源包与预览的显隐开关 | 工具栏入口表达的是完整工作区预设切换；单独折叠面板仍可从「视图」菜单操作，避免把两种不同层级的行为混为一谈 |
| 2026-09-11 | 每次临时导出写入独立 `deliveries/generation-*`，仅成功后切换当前引用并清理旧 generation | 保持客户可见文件名不变，同时避免重名询问、失败破坏上一份可用包，以及 Windows 外部程序占用旧文件时阻塞重新生成 |
| 2026-09-11 | 临时包内容变化后只标记过期并禁拖，不立即删除；拖出只发送单个本地文件 URL 且仅允许 `CopyAction` | 旧文件可能已被外部客户端读取，立即删除会打断发送；复制语义避免目标程序移动或删除会话文件 |
| 2026-09-11 | 关闭标签或程序时始终清理 `deliveries`，不受普通临时目录保留设置影响；即时删除失败交给下次 `purge_stale()` | 交付包是明确的会话短期产物，不能因调试用保留设置长期占盘；Windows 文件锁下清理必须允许静默兜底 |
| 2026-09-11 | `temp/clean_on_close` 通过 `QSettings.value(..., type=bool)` 读取 | 直接 `bool("false")` 会得到 `True`，导致用户关闭自动清理后仍错误清理整个会话目录 |
| 2026-09-12 | 视频预览用 `QVideoWidget` + `QMediaPlayer`，不自绘帧、不走 QGraphicsVideoItem | 复用同一套 QMediaPlayer 播放 / 定位 / 音量语义，音频页已有实现可直接对照；`QVideoWidget` 由 Qt 负责缩放与宽高比 |
| 2026-09-12 | 包内视频默认自动提取上限 512 MB，超限显示「加载」入口 | 预览是即时行为，几十 GB 的包内视频自动解到临时目录会瞬间吃满磁盘；此限制只控制后台提取，不再等同于自动解码 |
| 2026-09-12 | 视频页 `Esc` 退出全屏、预览内按空格播放 / 暂停 | 与音频预览保持同一套快捷键语义，避免同一个面板两套操作习惯 |
| 2026-09-12 | 深浅主题统一改为冷调高对比令牌，强调色仍跟随系统 | 只替换语义颜色与状态样式，不动布局度量；强调色文字按 WCAG 相对亮度在黑 / 白之间选择，兼顾自定义亮色强调色的可读性 |
| 2026-09-13 | 用户解压先生成纯 Python 落地计划再启动 7-Zip；跨包按包名分目录，已有文件默认跳过 | 把父子选择去重、Windows 大小写冲突和最终目标路径在执行前确定；避免多包同名文件互相覆盖，同时不改变引擎既有调用的默认行为 |
| 2026-09-14 | 源包树与目标结构树先启动 QDrag，再按需延迟提供 `text/uri-list`；成功代次保留到会话关闭 | QDrag 前弹模态准备窗口会消耗首次鼠标手势，导致必须拖两次；资源管理器索取数据时再物化可保持同一次 OLE 拖放，应用内拖动也无需解压 |
| 2026-09-13 | GIF 动图使用 QMovie，模板菜单保留 Fluent 图标并仅隐藏该按钮的原生 menu-indicator | QMovie 尊重原帧时长/循环且无需新增依赖；定向 QSS 避免影响其他菜单按钮 |
| 2026-09-13 | 「解压到此处」按关联扩展名注册独立静态 verb，并在单实例检测前执行 | 菜单只作用于可识别压缩包；直接解压任务不应打开主工作台或改变当前会话 |
| 2026-09-14 | RAR4 / RAR5 作为正式只读输入格式，真实样本以 uuencode 文本和固定哈希入库 | 7-Zip 只提供 RAR 解码器；固定上游版本与哈希既能持续回归，又不引入 RAR 编码器或运行时依赖 |
| 2026-09-14 | 窗口几何在首个工作区创建后恢复；布局保存覆盖内外分割比例与面板显隐 | 避免首标签 `sizeHint` 覆盖已恢复宽度，同时防止隐藏面板产生的 0 尺寸破坏用户原有比例 |
| 2026-09-14 | 外部拖出物化必须同时满足“QDrag 目标在应用外”和“光标下无本应用控件” | Windows OLE 起拖即探测 URL，且应用内非接收区也会报告空目标；双重门控才能避免内部拖拽误解压 |
| 2026-09-14 | 安装器只调用程序入口初始化系统集成；三菜单默认开启，格式关联与菜单范围解耦 | 保持 ShellIntegration 为注册表唯一实现；用户拒绝关联时右键功能仍可用，升级也不覆盖已有开关 |
| 2026-09-15 | 导出先写同目录候选包，成功后原子替换最终路径 | `7z a` 会更新旧包而非重建；候选提交同时解决旧条目残留、取消半成品和已有文件被破坏的问题 |
| 2026-09-15 | 取消由独立观察线程终止 7-Zip，并使用专用取消异常贯穿核心链路 | 逐行读取 stdout 无法保证及时检查取消；把取消与失败分型后 UI 才能准确反馈并可靠清理 |
| 2026-09-15 | 磁盘目录在预检阶段递归展开，源包条目建立会话级路径索引 | 原来的目录黑盒会漏报内部冲突与大小，反复线性查找又让大包预估退化为 O(n²) |
| 2026-09-15 | 应用图标与关联文件图标拆分为 `ZipMerge.ico` / `ZipMergeArchive.ico` | 任务栏应用身份用「多包汇入整理箱」，资源管理器文件类型用「折角文档 + 拉链」；共用色彩但轮廓不同，避免默认关联后两者混淆 |
| 2026-09-15 | 覆盖安装重写既有系统集成并刷新当前用户图标缓存 | 旧用户的初始化标记不能阻止新 `DefaultIcon` 生效；注册后用同步 `SHChangeNotify(SHCNE_ASSOCCHANGED)`，安装器再调用 `ie4uinit -ClearIconCache/-show`，无需重启 Explorer 或重新登录 |
| 2026-09-16 | 0.1.5 使用用户交付的双 ICO 作为应用与文件类型图标 | `P:\Codex\icon\ZipMerge.ico` 映射到应用/EXE/安装器图标，`ZipMerge_File.ico` 映射到内部规范名 `assets/icons/ZipMergeArchive.ico`；两者均含 16/20/24/32/48/64/128/256 px 八档尺寸 |
| 2026-09-15 | 深色主题改用 Windows 11 中性灰表面层级，并对过暗 / 过亮强调色做 3:1 可见性修正 | 冷调近黑和不绘制的自定义面板让界面只能靠亮描边分层；系统自动强调色又可能与表面接近，必须同时修复表面绘制和非文本对比度 |
| 2026-09-15 | 目标树编辑入口在操作发生时汇总同名文件并选择覆盖或自动改名 | 只在导出时解决会让目标结构本身长期保留歧义；把选择落为可撤销复合命令，才能让树上的当前结果与最终导出一致且允许误操作恢复 |
| 2026-09-15 | 视频选中时只设置 source、不自动 play；空格直接播放；frame 信号强制排队回 UI 线程 | 保留单键播放效率，同时不让选中动作主动触发解码；跨线程读取 QMediaPlayer 会放大 Qt FFmpeg 与显卡后端的死锁风险 |
| 2026-09-19 | 皮肤改为 Auralux 风格的中性灰专业工作台，并新增 `chrome` / `canvas` 表面令牌 | 顶底栏、工作面板、抬升控件和媒体画布需要四级稳定层次；强调色只用于标题、悬停、焦点和选中，避免大面积蓝底与亮描边造成持续视觉噪声 |
| 2026-09-19 | 工作区外沿、卡片间距与分割条占位统一为 8px | 外层布局原为 0 边距 / 0 间距，导致导出卡片与上下区域粘连；统一沟槽后两套布局的卡片边界保持同一节奏 |
| 2026-09-26 | 树直接拖出只以光标是否位于本应用控件上区分内部预探测与外部数据请求 | `QDrag.targetChanged` 只反映应用内 Qt 拖放目标，直接进入 Explorer / 桌面时不会可靠发送 `None`；继续依赖它会让外拖返回空 URL |
| 2026-09-26 | Explorer 兼容优先：首次 `text/uri-list` 请求立即物化，不再先返回空 URL | Explorer 桌面会缓存首次 `CF_HDROP`，光标离开应用后再补 URL 只对会重新索取数据的第三方文件管理器有效；内部拖动可能多一次缓存物化是可控代价 |
| 2026-09-26 | 外拖使用稳定占位路径，离开应用后才填入真实内容 | 同时满足 Explorer 首次 `CF_HDROP` 不能为空与纯内部拖动不能触发 7-Zip；路径从预取到投放保持不变，避免依赖目标重取 URI |

## 8. 2026-09-14 稳定性压测缺陷处置

- ✅ 7-Zip 静默输出期间取消延迟：已由独立取消观察线程修复，自动回归约 0.1 秒返回。
- ✅ 打包取消误报失败并残留目标包/暂存区：已用专用取消异常和统一清理修复。
- ✅ 覆盖导出向旧压缩包增量追加：已改为候选包完整生成后原子替换。
- ✅ 快速关闭标签触发已销毁 Qt 对象回调：延迟任务已改为具备 QObject 所有权的 `QTimer`，销毁竞态有回归覆盖。
- 修复后基线：全量单测 348 / 348 通过；`compileall` 通过。多 GB 素材取消时延、磁盘峰值和真实资源管理器拖放手势仍属于人工/大体量验证范围。

## 9. 2026-09-19 发布构建验证

- ZipMerge 0.1.5 已在当前源码与 Auralux 皮肤基础上重新生成 onedir 与 Inno Setup 安装包。
- `tools/verify_bundle.py` 通过：包内 12 项关键资源齐全，7-Zip 25.01 含 Rar / Rar5，ffmpeg 共享库同时存在于包根与 PySide6 目录。
- 打包版 `ZipMerge.exe --version` 返回 `ZipMerge 0.1.5`；窗口进程启动后保持响应。
- 产物：`dist/ZipMerge/` 144.8 MB；`installer/Output/ZipMerge-Setup-0.1.5.exe` 51.9 MB。


## 10. 2026-10-22 confirmed UI fixes

- The source panel uses the existing source model in archive-only presentation mode: roots remain backed by complete archive subtrees for whole-archive drag materialization, but child indexes are unavailable to the panel. Only the archive icon/title is visible; locating a target entry selects its owning archive. The default model mode retains internal-entry support for existing non-panel consumers.
- Nested target selection markers have two contributors: the generic selected-item accent border and native branch selection visible through a transparent/translucent branch background. Keep accent borders on flat source/list selections, and fill selected branches with `selection_solid`, computed from the same layer/accent/opacity as the selection token. Changing branch border/image alone does not remove the native marks.
- Validation: 371/371 unittest tests, compileall, and git diff --check passed. A native Windows main window imported a Chinese-named ZIP with nested Chinese directories/files; dark/light file and folder selection screenshots and ancestor pixel checks passed. The rendered regression test exercises both themes and both node types. Probe settings were isolated from user settings.
- Delivery boundary: source changes only; no installer rebuild/install, high-DPI/multi-monitor sweep, or physical Explorer drop gesture was performed in this repair.


## 11. 2026-10-22 temporary archive preview

- `TemporaryArchiveCard` now includes a right-aligned Preview button. It uses the existing `QDesktopServices.openUrl` mechanism to open the actual generated file in the system default application, without regenerating output or adding an archive viewer/dependency. The default handler determines which application appears.
- The button is enabled only for ready, existing output. Clicking revalidates the path; missing files switch the card to missing state. Stale and cleared cards cannot open output. Failed launches show a warning while leaving the ready file available for retry/drag. The button consumes its own click and retains an arrow cursor independently of the card's drag cursor.
- Verification: 5 new regressions cover actual button clicks without drag, stale rejection/regeneration, missing files, initial/cleared state, and launch failure. Related tests: 71 passed; full unittest suite: 376/376 passed; compileall and diff checks passed. Native Windows startup generated real Chinese-named ZIP and 7z deliveries, extracted and checked their text contents, clicked Preview with the OS launch intercepted, checked dark/light screenshots, disabled stale output, and verified close cleanup. Probe settings were isolated.
- Limits: the external default application's rendering was not exercised; no installer was rebuilt or installed. Preview follows the existing session lifetime, so the temporary archive is still removed on session close.

## 12. 2026-10-22 installer delivery

- Rebuilt the current working tree with `python tools/build_release.py --clean`, retaining version 0.1.5. Includes the archive-only source list, clean target indentation selection, and the temporary archive Preview button.
- PyInstaller onedir and Inno Setup compilation succeeded. `tools/verify_bundle.py` passed: 144.8 MB bundle, all 12 required resources, Rar/Rar5 decoders, duplicate ffmpeg locations, and executable version 0.1.5. EXE file/product version resources also match 0.1.5; frozen archive code includes `preview_archive` and `_preview_button`.
- Installer: `installer/Output/ZipMerge-Setup-0.1.5.exe`; 54,461,654 bytes; SHA-256 `B6392D9C32572F3C9CF28EFBEB95A3172C12714300F834FF54D051FE95C59E0D`.
- Built and verified only. An installed ZipMerge process was already running; it was left running and the installer was not executed. Source validation immediately before packaging: 376/376 tests and native Windows UI checks passed.


## 13. 2026-10-22 quick ZIP and isolated inspection (0.1.6)

- The earlier external Preview implementation in section 11 is superseded: when ZipMerge is the archive default handler, external opening loops back into its current session. Preview now opens an `ArchiveInspector` dialog and lists the actual output through the existing cancellable background listing worker. It never calls the system default handler, creates/imports a merge session, extracts files, or executes entries. Qt standard items expose only selectable/enabled flags; no editing, drag export, or file action exists.
- Output passwords are captured in the temporary card's session memory when an archive is generated, so encrypted 7z headers can be inspected without an extra prompt. Closing during listing requests cancellation and waits for worker completion before closing the dialog.
- `ShellIntegration` adds `ZipMerge.CompressHere` under HKCU file (`*`) and `Directory` shell keys, using the application icon, `MultiSelectModel=Single`, and a quoted `--compress-here` command. It is installed/upgraded by the existing context-menu initialization and removed by both application cleanup and installer fallback. Default archive associations are untouched.
- The independent CLI action runs before IPC and GUI initialization on success. It uses the bundled 7-Zip to pack one literal file/directory from its parent, retaining the directory wrapper and ZIP UTF-8 names. A sibling temporary directory contains the candidate; publication refuses existing output, using `_2`, `_3`, etc. No success or confirmation dialog appears. Existing files, directories, source archives, and concurrent invocations are protected from overwrite.
- Source validation: 386/386 tests passed, compileall and diff checks passed. Tests include Chinese files/directories, empty directories, only-selected-content inclusion, existing output and directory collision, parallel invocations, staging failure cleanup, quoted temporary-HKCU registration/unregistration, IPC/GUI bypass, actual ZIP and encrypted 7z inspection, corrupt listing feedback, cancel-on-close, double-click non-execution, and unchanged source archives.
- Native Windows verification invoked the CLI while the installed application was running, clicked the real temporary Preview button, read real ZIP/encrypted 7z output, checked dark/light inspector screenshots, and asserted zero external-handler calls or session imports. No physical Explorer menu mouse gesture or high-volume/DPI sweep was performed.

### 0.1.6 installer verification and delivery

- Clean PyInstaller + Inno Setup build succeeded. `tools/verify_bundle.py` passed with a 144.9 MB bundle, all 12 required assets, Rar/Rar5 codecs, ffmpeg libraries, and `ZipMerge.exe --version` reporting 0.1.6.
- The frozen EXE independently created Chinese-directory ZIP files twice, automatically suffixed the second result, preserved empty directories, and round-tripped the selected file bytes. Frozen archive inspection confirmed the new core/dialog modules are included and the temporary-card code no longer references `QDesktopServices`.
- Delivered `D:\Synching\Codex\ZipMerge-Setup-0.1.6.exe`, 54,461,136 bytes (51.94 MiB). Copy and build SHA-256 match: `9DB9A4DECD70D46CF7C68CFA807DB8285BFD139859ED92A7C742DC88616B5C2F`.
- No installation was executed, no existing installed process was stopped, and no real user default-file-association values were changed during verification.


## 14. 2026-10-22 folder Add to Session correction (0.1.7)

- The earlier statement that Add to Current Merge Session was preserved was incomplete: `*` covers files, not directory selections. The user screenshot showed a folder menu. Read-only inspection of this machine confirmed the existing preference is true, the file verb exists, and the Directory verb is missing. This is a registration coverage gap, not a disabled preference or an Explorer cache diagnosis.
- `add_to_session_keys()` now supplies both file and Directory keys. Registration, status detection, disabling, and unregistration use the same set; installer fallback also removes the Directory key. The command, menu label, and existing user preference remain shared. Upgrade initialization adds the missing directory verb without re-enabling a preference the user disabled. Existing disk-directory session import is reused without changing import behavior.
- Validation: 33 targeted registry/entry tests passed; full suite 389/389 passed; compileall and diff checks passed. New regressions cover matching file/folder commands, shared disable/uninstall cleanup, upgrade repair, disabled-preference preservation, and a Chinese directory dispatched into the current target session. Registry writes in tests use temporary HKCU prefixes; the real user keys were only inspected.

- Release delivery: clean 0.1.7 build and bundle verification passed (144.9 MB, 12 resources, EXE version 0.1.7). Frozen code includes the file/Directory helper. Installer delivered to `D:\Synching\Codex\ZipMerge-Setup-0.1.7.exe`, 54,454,150 bytes (51.93 MiB); copied SHA-256 matches build: `2CC9AC5526E10D06F17E9673F28EE175E0853E43E3E02E61EDAB31F20FA67A9A`. Installer execution and a physical Explorer menu click were not performed; upgrading invokes the existing context-menu initialization to repair the missing folder verb.

## 15. 2026-10-29 public source preparation

- The user authorized publishing the current project to `Meidios/ZipMerge` and explicitly selected MIT. Project copyright remains MEI (Meidios); upstream components retain their original licenses.
- Separate Chinese and English READMEs and contribution guides describe the actual 0.1.7 behavior, Windows x64 scope, current Chinese-only UI, read-only RAR support, temporary delivery lifetime, and source setup prerequisites. The source bootstrap requires an existing 7-Zip or Bandizip installation to unpack the downloaded 7-Zip installer.
- Public source excludes engine/font binaries, local probe output, build products, and obsolete visual previews. Full upstream notices for Fluent icons, Noto fonts, Pillow, and the installer translation are retained under `licenses/`; URLs and hashes are recorded separately as `license_texts` in `config/sources.json`.
- The public snapshot is based on the existing GitHub initialization commit; older local development history is retained locally on `master`, rather than publishing all historical development snapshots.
- A source-only copy with 116 candidate files bootstrapped resources successfully from the existing download cache with fixed SHA-256 verification. All 389 tests passed in 21.856 seconds on Python 3.13.14, PySide6 6.10.2, and Pillow 12.1.0. 7-Zip verification, compileall, the actual 0.1.7 version entry, and 40 local documentation links/anchors passed. No new runtime behavior was introduced.
- This publication is source-only. Before uploading an installer, collect full notices and corresponding source information for its actual Python/Qt/FFmpeg binaries; the historical installer component summary does not replace those materials.
- Repository About editing is not available in the connected GitHub tool set. Browser interaction was stopped by the Windows computer-use safety check because the current URL could not be established reliably. Prepared bilingual About text and topics are recorded in `.github/REPOSITORY_METADATA.md`; do not report them as applied without external verification.
- Publication succeeded: commit `4998f0f` was pushed as a fast-forward to public `origin/main`, and the public GitHub API confirmed the matching SHA, default branch `main`, and recognized MIT license. The local working branch is `codex/open-source`, tracking `origin/main`; `master` retains the older development history. No force push was used.
- Post-publication verification detected Git's first-stage newline normalization of Pillow's upstream license. The original wheel license was restored and restaged under `.gitattributes`' byte-preserving license rule. Keep license hashes based on original bytes, and do not strip upstream whitespace merely to satisfy a formatting check.

## 16. 2026-10-29 Windows binary release preparation

- The user explicitly authorized publishing the 0.1.7 installer with complete third-party materials and separate Chinese/English release notes. Audit found unused Qt Virtual Keyboard in the old bundle; Qt lists this module as GPL/commercial-only. The user explicitly approved removing it and rebuilding without changing application features/version. Its DLL, binding and input plugin are now excluded and guarded. The already-excluded Qt PDF module's orphan image plugin is also excluded.
- Actual audited runtime: Python 3.13.14, OpenSSL 3.0.21, Qt/PySide6/Shiboken6 6.10.2, Pillow 12.1.0, PyInstaller 6.18.0, hooks 2026.0. FFmpeg shared libraries report 7.1.2 and LGPL-2.1-or-later; their MSVC configuration enables shared libraries and zlib 1.3.1, with no GPL/nonfree/version3 flags.
- `config/sources.json` now locks 13 corresponding source archives, original Microsoft VC runtime and Inno Setup license inputs, and a development-only innounp verification tool. `tools/prepare_release_legal.py` verifies versions/hashes and assembles 646 notice files plus a hash inventory, including upstream LICENSES, attributions, the actual FFmpeg configuration/library hashes, and LGPL replacement/rebuild instructions. Runtime dependencies remain PySide6 and Pillow only.
- Public builds include the complete tree under `_internal/legal/` and MIT/notice entry files at the installation root. Sources are distributed unmodified beside the installer in a 7z sidecar; notices are also a separate ZIP. Qt/FFmpeg/Python source and original legal files are retained, including Qt's FFmpeg provisioning scripts. PyInstaller bootloader licensing is documented with its distribution exception; Microsoft runtime terms remain proprietary.
- Validation: 396/396 tests passed in 18.152 seconds; compileall/diff checks passed. The 147.8 MB bundle passes 16 resource checks, legal file integrity, FFmpeg byte correspondence, forbidden-module checks, and frozen EXE --version 0.1.7. A read-only Inno payload extraction found 870 files matching the verified bundle and notices byte-for-byte; no installation or shell registration ran. Native Windows Qt startup/screenshot used isolated settings and passed. A hidden frozen-process probe did not expose a visible window handle and is not recorded as a successful frozen GUI test. A stale-PID cleanup attempt was rejected by automatic approval review before execution; subsequent read-only inspection found no ZipMerge processes, and source UI validation exited itself normally.
- GitHub connector exposes no Release creation/upload tool. The release workflow builds on `codex/release-*` pushes or explicit manual dispatch, with pinned official checkout/setup-python actions, exact Python/build dependencies and Inno Setup 6.7.3. It verifies tests, bundle and actual installer payload before creating/uploading a draft and publishing it. Public releases are never automatically overwritten. CI rebuilds from the exact publication commit; public asset checksums must be checked against that build, not the local probe build.
- First CI attempt stopped before building/publishing because Chocolatey had no Inno Setup 6.7.3 package. The workflow now downloads the official immutable `jrsoftware/issrc` release installer and verifies its GitHub-reported SHA-256 (`9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732`) using the same locked download helper, then installs it only on the disposable Windows runner. The local machine only downloaded and verified that compiler installer; it was not executed locally.
