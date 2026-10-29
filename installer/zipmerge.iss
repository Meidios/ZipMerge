; ZipMerge 安装脚本（PLAN 11）
;
; 设计要点：
;   * 安装到 %LOCALAPPDATA%\Programs\ZipMerge，PrivilegesRequired=lowest，全程免 UAC；
;   * 安装器调用程序自己的 ShellIntegration 初始化默认右键菜单，不复制注册表实现；
;   * 覆盖安装时用 Restart Manager 关掉运行中的实例，QSettings 与用户设置原样保留；
;   * 卸载先调用 ZipMerge.exe --unregister-all，再走 [Code] 兜底清理已知 HKCU 键路径。
;
; 版本号由 tools/build_release.py 通过 ISCC /DMyAppVersion=x.y.z 注入，缺省值只用于手工编译。

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "ZipMerge"
#define MyAppExeName "ZipMerge.exe"
#define MyAppPublisher "MEI"
#define MyAppDescription "多压缩包合并工作台"
#define MyDistDir "..\dist\ZipMerge"

[Setup]
AppId={{9C1D6A2B-4E31-4F57-9A6E-7B0C5D83E142}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments={#MyAppDescription}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=ZipMerge-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icons\ZipMerge.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 覆盖安装：关掉运行中的实例（Restart Manager），但不自动重启，交给用户决定
CloseApplications=yes
RestartApplications=no
; 只允许装到当前用户可写的目录，避免安装到 Program Files 后无权限
UsePreviousAppDir=yes

[Languages]
; 只提供简体中文：产品界面与下面的 Tasks/Icons/Run 文案都是中文，混排英文没有意义。
; 语言文件随仓库分发（installer\languages\，MIT，源自
; https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation），
; 因为标准 Inno Setup 并不附带简体中文，指向 compiler: 会直接编译失败。
Name: "chinesesimplified"; MessagesFile: "{#AddBackslash(SourcePath)}languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式:"; Flags: unchecked

[Files]
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\release-legal\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "{#MyAppDescription}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "{#MyAppDescription}"; Tasks: desktopicon

[Run]
; 首次安装注入默认菜单；升级/修复安装按原选择重写图标路径与命令。
Filename: "{app}\{#MyAppExeName}"; Parameters: "--install-context-menus"; Flags: runhidden
; DefaultIcon 的 ico 路径在覆盖安装时保持不变，Explorer 容易继续使用旧缓存。
; 程序已经调用 SHChangeNotify；这里再用 Windows 自带工具清理并重建当前用户图标缓存。
Filename: "{sysnative}\ie4uinit.exe"; Parameters: "-ClearIconCache"; Flags: runhidden waituntilterminated skipifdoesntexist
Filename: "{sysnative}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden waituntilterminated skipifdoesntexist
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 优先让程序自己撤销（它认得内置后缀 + 用户自定义后缀）；RunOnceId 保证多语言/多次执行只跑一次
Filename: "{app}\{#MyAppExeName}"; Parameters: "--unregister-all"; Flags: runhidden; RunOnceId: "ZipMergeUnregisterAll"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  { 卸载兜底清理：程序文件已被删除，或 --unregister-all 未能执行时的已知键路径。
    与 src/zipmerge/platform/shell_integration.py 的路径定义保持一致。 }
  ZipMergeProgIDKey = 'Software\Classes\ZipMerge.Archive';
  ZipMergeAppKey = 'Software\ZipMerge';
  ZipMergeAddToSessionKey = 'Software\Classes\*\shell\ZipMerge.AddToSession';
  ZipMergeRegisteredApplications = 'Software\RegisteredApplications';
  { src/zipmerge/util/paths.py 的 ARCHIVE_SUFFIXES；自定义后缀由程序自身负责清理 }
  BuiltinExtensions = '.zip,.7z,.rar,.tar,.gz,.bz2,.xz,.tgz,.cab,.iso';

procedure CleanupIntegrationKeys();
var
  Extensions: TArrayOfString;
  Index: Integer;
begin
  RegDeleteKeyIncludingSubkeys(HKCU, ZipMergeProgIDKey);
  RegDeleteKeyIncludingSubkeys(HKCU, ZipMergeAddToSessionKey);
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\Directory\shell\ZipMerge.AddToSession');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\*\shell\ZipMerge.CompressHere');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\Directory\shell\ZipMerge.CompressHere');
  RegDeleteKeyIncludingSubkeys(HKCU, ZipMergeAppKey);
  RegDeleteValue(HKCU, ZipMergeRegisteredApplications, 'ZipMerge');

  if RegGetSubkeyNames(HKCU, 'Software\Classes\SystemFileAssociations', Extensions) then
  begin
    for Index := 0 to GetArrayLength(Extensions) - 1 do
      RegDeleteKeyIncludingSubkeys(
        HKCU,
        'Software\Classes\SystemFileAssociations\' + Extensions[Index] + '\shell\ZipMerge.Open'
      );
      RegDeleteKeyIncludingSubkeys(
        HKCU,
        'Software\Classes\SystemFileAssociations\' + Extensions[Index] + '\shell\ZipMerge.ExtractHere'
      );
  end;

  if RegGetSubkeyNames(HKCU, 'Software\Classes', Extensions) then
  begin
    for Index := 0 to GetArrayLength(Extensions) - 1 do
      if Pos(Extensions[Index], BuiltinExtensions) > 0 then
        RegDeleteValue(
          HKCU,
          'Software\Classes\' + Extensions[Index] + '\OpenWithProgids',
          'ZipMerge.Archive'
        );
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    CleanupIntegrationKeys();
end;
