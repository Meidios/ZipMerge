@echo off
setlocal
set "vswhere=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%vswhere%" goto :missing
for /f "usebackq tokens=*" %%i in (`"%vswhere%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "vsroot=%%i"
if not defined vsroot goto :missing
call "%vsroot%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
set "out=%~dp0..\..\build\shell"
if not exist "%out%" mkdir "%out%"
pushd "%out%" || exit /b 1
cl /nologo /std:c++17 /EHsc /O2 /MT /W4 /WX /utf-8 /DUNICODE /D_UNICODE /LD "%~dp0pack_command.cpp" /link /out:ZipMergeShell.dll /def:"%~dp0exports.def" /Brepro shell32.lib shlwapi.lib ole32.lib advapi32.lib runtimeobject.lib
set "code=%errorlevel%"
if not "%code%"=="0" goto :done
if /i not "%~1"=="--tests" goto :done
cl /nologo /std:c++17 /EHsc /utf-8 /DUNICODE /D_UNICODE "%~dp0..\..\tests\native\pack_menu.cpp" /Fe:verify_menu.exe /link shell32.lib ole32.lib advapi32.lib user32.lib
set "code=%errorlevel%"
:done
popd
exit /b %code%
:missing
echo Building the Explorer menu requires Visual Studio C++ x64 Build Tools and a Windows SDK. 1>&2
exit /b 1
