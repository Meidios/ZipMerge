# 0.1.8 最终验证、发布与 issue 收尾 — 2026-11-03

用户已授权：完整验证四项修复、发布新的 GitHub Release 安装包，并在发布后关闭已解决的 bug issue。使用新版本 0.1.8，保留已公开的 0.1.7 资产。实体手势和原设备的未验证内容如实记录，不据此编造完成状态。

1. [x] **最终审查与回归。** 开发环境全量 415/415 再次通过；锁定发布运行时 Python 3.13.14 下 415/415 通过（27.064 秒），`compileall`、7-Zip/RAR 能力检查通过。用户明确反馈“首次拖动成功，内容完整”，已补齐一次真实外拖手势验收。
2. [x] **准备 0.1.8 发布内容。** 唯一版本号、中英文 README/发布说明、许可入口和规格已更新；新标签未占用，已固定修复提交，原始 bugs 材料未加入公开提交。
3. [x] **构建并验证安装包。** 本地锁定运行时构建成功：147.9 MB bundle、EXE 0.1.8、16 项必需资源与 646 份许可资料通过；871 个实际安装器载荷文件逐字节一致。从 EXE 内加载真实模块、包内 Qt/7-Zip 的 75 项回归通过，真实 Windows 主窗口在 5 个模拟倍率下深浅主题/加密导入验证通过。QtTest 仅由构建环境提供测试辅助，不加入发行包。远程 CI 会在公开前重复全部检查。
4. [ ] **发布 GitHub Release。** 推送已验证提交，等待远程构建成功；重新下载公开资产，核对版本、提交、SHA-256 和许可/源码资料的一致性。
   - 发布阻碍处理：原 7-Zip 下载域名在两次远程运行中均 DNS 解析失败。已核对官方 `ip7z/7zip` 25.01 Release 的安装资产与对应源码，哈希/大小全部与既有锁定值一致，安装资产解出的 7z.exe、7z.dll、License.txt 也逐字节一致；只切换两个官方来源 URL，保留版本、哈希和引擎文件。仅推发布分支；默认分支 main 的直接推送被自动审批拒绝，未修改 main。
5. [ ] **关闭已解决的 issue。** 先附上修复版本与验证证据，再关闭已解决条目；遇到仍未通过验收的条目，保留并说明具体缺口。

---

# bugs 问题修复计划 — 2026-11-03

用户已于 2026-11-03 批准开始修复。问题来源：`bugs/BUS.md` 中的 #1–#4；以下编号与原记录保持一致。历史已完成事项保留在后文。

**建议顺序：#3 加密包 → #1 首次外拖 → #4 图标缩放 → #2 母文件夹图标。** #1 的复现记录应在修复开始时就建立；#4 与 #2 共用图标链路，先保证渲染尺寸正确，再验收图标语义。

## 计划准备

- [x] 核对问题、历史修复记录、现有实现和测试覆盖。
  - 结果：4 项均已定位到相关模块；#2 的图标分支和 #3 的密码触发遗漏有静态代码证据，#1 的实际拖放时序与 #4 的受影响设备原因仍需复现。
  - 计划阶段文档验证：`git diff --check` 通过；计划已覆盖全部 4 项，历史清单保留。计划阶段没有修改实现、测试、版本号或系统设置，也没有运行安装器、构建或发布。
  - 验证限制：附件图片未能通过当前网络读取，因此不声称已核对截图中的具体控件。默认 Python 启动受限；通过 `py -3.13` 检查得到 Python 3.13.7，但该解释器缺少 PySide6，临时样本与渲染探针未能运行。历史环境记录不能当作本轮已验证环境。

## #3：加密压缩包无法解压 — P1

- [x] 补齐密码获取与传递，覆盖能够列出目录的加密 ZIP。
  - **已知依据：** `SourcePanel.add_archives()` 对成功读取目录的包直接挂载，只有列表失败才调用 `_retry_with_password()`；`main.py::_run_extract_here()` 也仅在列表失败时询问密码。ZIP 可以公开文件名而加密文件内容，因此存在成功挂载但 `SourceArchive.password` 仍为空的路径。
  - **与报错的关系：** 引擎在密码为 `None` 时不传 `-p`，且标准输入为 `DEVNULL`，这与原记录中的 `Enter password` / `Break signaled` 相符。尚未使用原始问题包复现，不把所有加密包失败都归为同一原因。
  - **修复方向：** 沿用现有 `has_encrypted_entries`、密码输入框与会话密码存储，在受影响入口的读取成功分支补上密码处理；在实际解密操作中验证密码，不能用“再次列表成功”证明密码正确。支持输错后重试和取消，普通包不增加询问；密码继续只留在会话内存。
  - **影响位置：** `ui/source_panel.py`、`main.py`、必要的公共密码处理入口，以及相关核心模型/引擎错误分类；检查 `extraction_runner.py`、`preview_panel.py`、`core/materialize.py`、`core/exporter.py` 的密码传递。优先复用现有逻辑，不新增运行期依赖。
  - **验证方式：** 用内置 7-Zip 创建中文名/中文目录样本，覆盖 ZipCrypto ZIP、AES ZIP、仅内容加密的 7z、加密文件名的 7z，以及未加密包。验证首次输入、错误密码后重试、取消、会话记忆开关、混合多包；覆盖界面解压、右键解压、预览、树外拖和合并导出，解出内容逐字节比对。加密 RAR 的结论仅限已有合法样本，不扩大支持声明。
  - **完成标准：** 正确密码可完成操作；错误密码能明确反馈并重试；取消不会变成普通故障；不再向无交互输入的 7-Zip 留下密码询问；源包哈希不变。
  - **状态 / 验证结果：** 已完成；原始 `Enter password` / `Break signaled` 已用真实加密 ZIP 复现。8 项新增真实样本回归通过，覆盖密码重试/取消、后台验证、四类加密包、无匹配条目不能算验证成功，以及预览/解压/物化/合并导出/右键解压的内容比对；确认密码未写入 INI。真实 Windows 主窗口在 5 个模拟倍率下均成功导入中文加密 ZIP/7z。最终 415/415 全量回归通过。

## #1：拖到外部需要拖两次 — P1，交互风险最高

- [x] 修复冷缓存下首次落放，验证真实外部接收结果。
  - **已知依据：** `DragExportController.start()` 起拖前创建空文件/空目录并暴露路径；15 ms 定时器发现光标离开应用后，才调用 `_prepare_paths()` 填入内容。物化期间 `_run()` 打开模态进度窗口。当前依然存在“目标已取得占位路径”和“内容尚未就绪”的时间窗口。
  - **待确认：** 首次失败究竟发生在 Explorer 读取占位内容、进度窗口改变鼠标/拖放状态，还是其他 OLE 时序；静态检查不能判定。历史上“先解压再起拖”“光标/目标信号门控”“首次 URI 请求解压”“占位路径”已经轮换过，不能直接重用旧方案并宣称修好。
  - **修复方向：** 先以真实手势建立冷缓存复现，记录不含敏感内容的拖放与物化事件顺序，再选择符合 Qt/Windows 文件传递约定的最小修改。目标必须拿到完整内容；不能仅靠缩短轮询间隔或把第二次缓存命中当成功。若证据表明 Qt 现有路径不足，再评估 Windows 官方延迟传递机制，避免引入第三方依赖或未经验证的 COM 框架。
  - **影响位置：** `ui/drag_export.py`、必要的 `core/materialize.py` / 会话拖出目录生命周期，以及源树、目标树入口；不改导入语义与虚拟目标结构。
  - **验证方式：** 补充当前 `_FakeDrag` 未覆盖的读取时机、快速松键、取消/失败、占位内容暴露回归。真实 Windows 从源包列表和目标树分别拖到 Explorer 文件夹、桌面；覆盖单文件、目录、空目录、多选、跨包、中文路径、慢解压、缓存命中和全程内部拖动。每次首次测试都使用新会话或清空测试缓存，落地后核对路径、数量和内容哈希。
  - **完成标准：** 首次手势即可交付完整文件，无须二次拖动；不能留下零字节占位文件或空壳目录；全程应用内拖动不启动解压；失败/取消不报告成功；临时文件按会话规则清理。
  - **状态 / 验证结果：** 已替换 Windows 占位/轮询方案；原生 OLE 的格式询问与枚举不解压，仅 `GetData(CF_HDROP)` 请求时准备完整文件再返回，进度窗口不激活也不设模态。真实 Windows Shell `IFileOperation` 已在首次读取延迟数据时复制中文目录、文件及空目录，内容比对通过。新增原生 ABI 测试覆盖内部载荷零解压、首次数据完整、取消/失败。Computer Use 跨窗口手势受工具限制；随后用户明确实测反馈“首次拖动成功，内容完整”。一次真实手势已通过，未宣称覆盖所有目标与大型目录组合。

## #4：部分设备 UI 图标过大 — P2，先验证 DPI 假设

- [x] 统一图标的逻辑尺寸与物理像素，补齐多倍率资源与缓存验证。
  - **已知依据：** `IconSet.pixmap()` 按设备像素比创建物理位图并调用 `setDevicePixelRatio()`，随后又把已乘倍率的 `physical` 用作字体大小和绘制矩形。这里存在重复放大的明确嫌疑。当前倍率只取主屏，`glyph_png()` 的缓存文件名也未区分倍率。
  - **待确认：** 问题设备的 Windows 缩放、显示器组合、程序版本及实际异常控件；不能仅凭“部分设备”就排除字体注册或资产缺失。原附件仍需在可访问时核对。
  - **修复方向：** 先复现同一图标在不同倍率下的结果；依照 Qt 的逻辑坐标绘制规则修正渲染，保持物理位图足够清晰。检查 QIcon、QLabel 字形与 QSS PNG 三条路径，并处理不同倍率/旧缓存复用与窗口跨屏问题；沿用 Fluent 正式资产和设计令牌。
  - **参考依据：** [Qt High DPI](https://doc.qt.io/qt-6/highdpi.html)、[QPixmap::setDevicePixelRatio](https://doc.qt.io/qt-6/qpixmap.html#setDevicePixelRatio)。实现时以项目实际 Qt 6.10.2 运行结果复核。
  - **影响位置：** `ui/design/icons.py` 及确有必要的图标缓存刷新入口；不调整布局、交互、全局字号或 Windows 缩放设置来掩盖问题。
  - **验证方式：** 独立进程覆盖 100% / 125% / 150% / 170% / 200% 缩放，比较逻辑占位、字形边界、清晰度与裁切；检查工具栏、树图标、下拉箭头、勾选框、关闭按钮，覆盖深浅主题、两种密度、旧缓存启动与跨不同缩放屏幕移动。模拟倍率与真实 Windows/受影响设备验收分别记录。
  - **完成标准：** 图标按系统缩放正常变清晰，与控件比例一致，不额外放大或裁切；100% 显示保持现有视觉；跨屏后尺寸与清晰度正确。
  - **状态 / 验证结果：** 已完成源码修复；修复前 125%–200% 字形触及位图右/下边界；修复后 100% / 125% / 150% / 170% / 200% / 300% / 400% 的逻辑尺寸、字形边界和无裁切检查通过。QIcon 提供多倍率资源，QSS 缓存更换渲染版本并生成 @2x/@3x/@4x，旧缓存不复用。真实 Windows Qt 平台另以 `QT_SCALE_FACTOR` 覆盖前 5 个倍率，10 张深浅主题截图已生成，已查看 100% 浅色与 200% 深色截图，20px 图标逻辑尺寸全程保持 20px。最终 415/415 全量回归通过；真实混合 DPI 多屏与原受影响设备仍未覆盖。

## #2：原始母文件夹应显示压缩包图标 — P2，修改范围最小

- [x] 让目标树中的源压缩包外层节点使用压缩包图标。
  - **已知依据：** 源包列表的根项已经使用 `folder_zip`；目标树 `icon_name_for()` 首先检查 `node.is_dir`，导致源包外层节点直接返回普通文件夹图标。按当前代码推断，问题主要在目标树；需对照原截图确认。
  - **修复方向：** 用来源类型、有效源包引用和空包内路径识别源压缩包外层节点；显示现有 `folder_zip` 图标。不得只按层级或文件名后缀识别，也不把所有源包内部目录都改成压缩包图标。
  - **影响位置：** `ui/target_model.py` 的图标选择及相关模型用例；优先复用已有节点元数据，不为单个图标新增模型字段。
  - **验证方式：** 源包外层、包内普通目录、磁盘目录、自建目录分别检查；外层节点改名、移动、复制、撤销/重做后仍按来源识别；摊平后不把普通子目录误标。结合 #4 在深浅主题与不同倍率下真实启动确认。
  - **完成标准：** 仅代表原始源包的外层节点显示压缩包图标；图标变化不改变导出目录结构或节点行为。
  - **状态 / 验证结果：** 已完成；来源身份识别测试覆盖外层节点改名、移动、复制、撤销与摊平，普通包内目录、自建目录和磁盘目录保持正确图标；真实 Windows 主窗口截图确认外层显示压缩包图标、子目录仍为文件夹。最终 415/415 全量回归通过。

## 修复后的统一验收

- [x] 每项修复完成后立即运行对应测试，并将实际结果写回该条目。
- [x] 仓库 `.venv` 使用 Python 3.13.7、锁定的 PySide6 6.10.2 与 Pillow 12.1.0。最终全量 415/415 测试通过（26.578 秒，无跳过）；`compileall` 和 `git diff --check` 通过。QSettings/注册表测试沿用测试前缀，未安装或修改真实系统关联。
- [x] 完成真实 Windows Qt 主窗口启动、5 个模拟倍率/深浅主题截图、中文加密样本端到端、Windows Shell 原生首次文件复制及内容比对。测试截图与日志在忽略目录 `.tmp/ui-*`、`.tmp/full-tests-final.txt`；临时验证窗口已退出。
- [x] 用户已确认一次首次外拖实体手势成功且内容完整。原受影响设备、混合 DPI 多屏及全部外部目标组合仍不作为已完成验收。
- [x] 已按最终实现更新 `PLAN.md`，在 `MEMORY.md` 追加结论，注明旧拖放方案已被取代与剩余验证边界。

本轮不包含版本号变更、安装包发布、安装或注册表修改。后续若安排打包，必须另做 frozen EXE 的加密解压、首次外拖和 DPI 验证，不能把源码测试结果直接当作安装包验收。执行中若发现四项之外的新问题，先加入清单说明，取得批准后再改。

---

# 0.1.7 安装包正式发布 — 2026-10-29

本轮用户已授权：补齐必要第三方许可资料，将 0.1.7 安装包发布到 GitHub Releases，发布说明分开中文和英文。

1. **核对现有安装包和依赖。** 已完成：现有安装器 54,454,150 字节；当前环境 Python 3.13.14、PySide6 6.10.2、Pillow 12.1.0、PyInstaller 6.18.0。实际 FFmpeg 7.1.2，库自报 LGPL-2.1-or-later，已读取编译参数。旧包误收未使用的 Qt Virtual Keyboard（GPL/商业许可），用户已明确批准剔除后重建，保持功能与版本号不变。GitHub 连接器没有 Release 写入接口，将使用 GitHub Actions 发布。
2. **补齐安装包许可资料。** 已完成：13 份对应源码已下载并锁定哈希；自动生成 643 份许可与声明、实际版本清单、FFmpeg 编译参数与 DLL 哈希、LGPL 替换共享库说明。构建流程强制生成并收录资料，安装根目录附 MIT 和第三方说明。剔除已获批准的 Qt Virtual Keyboard；现有 Qt PDF 排除规则同步剔除原本无法加载的孤立 qpdf 插件。7 项新增防回归测试通过，覆盖源码污染、非法路径、许可变化和缺失/篡改声明。
3. **重建并验证 0.1.7。** 已完成：396/396 单测通过（18.152 秒）；最终 bundle 147.8 MB，16 项关键资源、完整许可哈希、FFmpeg DLL 与清单对应、排除模块检查通过，EXE 返回 0.1.7。只读解出最终安装器，870 个载荷文件与 bundle、许可逐字节一致；独立设置的真实 Windows Qt 主窗口启动和截图通过，未执行安装或注册。最终许可树 646 份声明文件外加哈希索引。已生成安装器、许可 ZIP、源码 7z 和校验和；GitHub 将从同一提交重新构建并发布，最终校验和以远程资产为准。
4. **发布正式 Release。** 已完成：北京时间 2026-10-29 15:46 正式公开 `https://github.com/Meidios/ZipMerge/releases/tag/v0.1.7`，标签对应 `6ae3d15`。GitHub workflow 成功，398 项测试中 396 通过、2 项按既有环境条件跳过；完整打包、许可校验和 873 个安装器载荷逐字节检查通过。已重新下载全部公开资产，核对 GitHub digest 与 SHA256SUMS、13 份源码档案、源码/许可清单一致性、实际安装器许可和正式图标。公开安装器 56,819,250 字节，SHA-256 `cb31765693fc0f2b1b0a1017564c028d52927f8bc00e982cff9fdb41fcc09445`；同页已提供许可 ZIP、对应源码 7z 和校验和，中英文说明分节。未执行安装器或修改本机系统关联。

**发布前补充修复（用户已批准，已完成）：** generation 清理两侧统一按 `resolve()` 比较。新增真实 Windows 8.3 别名下的中文 ZIP/7z 导出→提交→解包→字节比对→旧代次清理回归，修复前两个格式均能捕获误删、修复后通过。路径测试改为比较同一实际目录，主题测试明确使用深色。101 项相关测试通过；完整套件在真实短路径 TEMP 下 397/397 通过（17.354 秒），compileall 与 diff 检查通过。保留版本号 0.1.7，继续正式发布。

5. **替换中英文首页的旧图标。** 已完成：正式 `ZipMerge.ico` 与用户原件、已安装程序 SHA-256 完全一致（217cad25…）。从其 256×256 最大帧无损导出 `assets/icons/app-readme.png`，中英文 README 均改用该图片；像素逐字节一致，正式 ICO 未改动，diff 检查通过。仅推送 main，不触发尚未获批修复的 Release 构建。

---

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
