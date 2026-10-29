"""ZipMerge 入口：参数解析、残留清理、单实例转发与主窗口启动。

用法：
  python main.py [压缩包 ...]             打开一个或多个压缩包（文件关联走这里）
  python main.py --add-to-session <文件>  把文件加入已运行实例的目标树（右键菜单）
  python main.py --extract-here <压缩包>  将内容直接解压到压缩包所在目录
  python main.py --install-context-menus   首次安装时注入默认右键菜单并安排启动引导
  python main.py --unregister-all         撤销全部 Windows 集成（卸载时调用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMessageBox  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402

from zipmerge import APP_NAME, __version__  # noqa: E402
from zipmerge.core import ExtractionSelection, MergeSession, build_extraction_plan  # noqa: E402
from zipmerge.engine import ArchiveError, is_password_failure  # noqa: E402
from zipmerge.ipc import (  # noqa: E402
    ACTION_ADD_TO_SESSION,
    ACTION_OPEN,
    Request,
    SingleInstanceServer,
    is_running,
    send,
)
from zipmerge.platform.shell_integration import ShellIntegration  # noqa: E402
from zipmerge.ui.design import ThemeService  # noqa: E402
from zipmerge.ui.main_window import MainWindow  # noqa: E402
from zipmerge.ui.extraction_runner import run_extraction_dialog  # noqa: E402
from zipmerge.ui.integration_onboarding import run_post_install_integration_prompt  # noqa: E402
from zipmerge.util.settings import (  # noqa: E402
    DEFAULT_INTEGRATION_EXTRACT_HERE,
    KEY_INTEGRATION_EXTRACT_HERE,
    KEY_TEMP_ROOT,
    Settings,
)
from zipmerge.util.paths import asset_path, is_archive_path  # noqa: E402
from zipmerge.util.tempdir import default_root, purge_stale  # noqa: E402


def _attach_parent_console():
    """打包成 console=False 后没有标准输出，只能尝试挂到父进程的控制台。

    `sys.stdout` 为 None 时直接交给 argparse 的 version 动作会抛 AttributeError；
    PyInstaller 在窗口模式下会把未捕获异常弹成**阻塞对话框**，于是
    `ZipMerge.exe --version` 与卸载时调用的 `--unregister-all` 都会挂住不退出。
    """
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(-1):
            return None
        return open("CONOUT$", "w", encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        return None


def _emit(text: str) -> None:
    """命令行分支的输出：没有可用输出通道就静默跳过，绝不因为写输出而抛异常。"""
    stream = sys.stdout or _attach_parent_console()
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except (OSError, ValueError):
        pass


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog=APP_NAME, description="多压缩包合并工作台")
    parser.add_argument("paths", nargs="*", help="启动时要打开的压缩包")
    parser.add_argument("--compress-here", action="store_true", help="直接在所选文件或目录旁创建 ZIP")
    parser.add_argument(
        "--extract-here",
        action="store_true",
        help="把压缩包内容直接解压到其所在目录（右键菜单「解压到此处」）",
    )
    parser.add_argument(
        "--add-to-session",
        action="store_true",
        help="把文件加入已运行实例的目标树（右键菜单「添加到当前合并会话」）",
    )
    parser.add_argument(
        "--install-context-menus",
        action="store_true",
        help="首次安装时注入默认右键菜单并安排启动引导",
    )
    parser.add_argument(
        "--unregister-all",
        action="store_true",
        help="撤销全部 Windows 集成（关闭文件关联、右键菜单与单实例注册）",
    )
    parser.add_argument("--version", action="store_true", help="打印版本号后退出")
    return parser.parse_args(argv)


def _configure_app(app: QApplication) -> None:
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    icon = QIcon(str(asset_path("icons", "ZipMerge.ico")))
    if not icon.isNull():
        app.setWindowIcon(icon)


def _run_extract_here(paths: list[str], settings: Settings) -> int:
    """独立右键解压流程；不加入会话，也不受主窗口单实例状态影响。"""
    if not paths:
        QMessageBox.warning(None, "无法解压", "没有收到要解压的压缩包路径。")
        return 2
    succeeded = 0
    for raw in paths:
        archive = Path(raw).resolve()
        if not archive.is_file() or not is_archive_path(archive):
            QMessageBox.warning(None, "无法解压", f"不是受支持的压缩包：{archive}")
            continue
        temp_root = Path(str(settings.value(KEY_TEMP_ROOT, "") or default_root()))
        session = MergeSession(name=f"解压-{archive.stem}", temp_root=temp_root)
        try:
            try:
                session.import_archive(archive)
            except ArchiveError as exc:
                if not is_password_failure(exc.output):
                    raise
                password, ok = QInputDialog.getText(
                    None,
                    "需要密码",
                    f"{archive.name} 已加密，请输入密码：",
                    QLineEdit.EchoMode.Password,
                )
                if not ok or not password:
                    continue
                session.import_archive(archive, password=password)
            source_id = next(iter(session.sources))
            plan = build_extraction_plan(
                session.sources,
                [ExtractionSelection(source_id)],
                archive.parent,
                wrap_whole_archives=False,
            )
            if run_extraction_dialog(None, plan) is not None:
                succeeded += 1
        except ArchiveError as exc:
            box = QMessageBox(QMessageBox.Icon.Critical, "解压失败", exc.message)
            if exc.output:
                box.setDetailedText(exc.output)
            box.exec()
        finally:
            session.close()
    return 0 if succeeded else 1


def _dispatch_request(window: MainWindow, request: Request) -> None:
    """把单实例请求落到窗口：open 挂源包，addToSession 写目标树。"""
    tab = window.current_tab() or window.new_tab()
    if request.action == ACTION_ADD_TO_SESSION:
        tab.target_panel.import_disk_paths(list(request.paths))
    else:
        tab.source_panel.add_archives(request.paths)
    window.raise_()
    window.activateWindow()


def main(argv=None) -> int:
    args = _parse_args(argv)

    if args.version:
        _emit(f"{APP_NAME} {__version__}")
        return 0

    if args.compress_here:
        from zipmerge.core.compress_here import compress_here

        try:
            if not args.paths:
                raise ValueError("没有收到要打包的文件或目录。")
            for path in args.paths:
                _emit(str(compress_here(path)))
        except (OSError, ValueError, ArchiveError) as exc:
            app = QApplication.instance() or QApplication([])
            _configure_app(app)
            QMessageBox.warning(None, "打包失败", str(exc))
            return 1
        return 0

    if args.unregister_all:
        removed = ShellIntegration(Settings()).unregister()
        _emit(f"已清除 {len(removed)} 项 Windows 集成。")
        return 0

    if args.install_context_menus:
        initialized = ShellIntegration(Settings()).initialize_install_defaults()
        _emit("已初始化 Windows 右键菜单。" if initialized else "已保留现有 Windows 集成设置。")
        return 0

    if args.extract_here:
        app = QApplication(sys.argv if argv is None else [sys.argv[0], *argv])
        _configure_app(app)
        settings = Settings()
        ShellIntegration(settings).sync_suffixes()
        return _run_extract_here(args.paths, settings)

    action = ACTION_ADD_TO_SESSION if args.add_to_session else ACTION_OPEN
    # 已有实例：把动作转给它后立即退出（无参数再次启动也走这里，只把窗口带到前台）
    if is_running():
        send(action, args.paths)
        return 0

    app = QApplication(sys.argv if argv is None else [sys.argv[0], *argv])
    # 任务栏 / 标题栏图标设到 QApplication 上，任务栏分组与 Alt+Tab 才认。
    _configure_app(app)

    settings = Settings()
    # 自定义识别后缀要在建窗口前生效，源包面板与关联候选都读这张表（PLAN 8.2）
    integration = ShellIntegration(settings)
    integration.sync_suffixes()
    # 覆盖安装后刷新已有注册项，把新版本新增的 verb 补到当前用户注册表。
    status = integration.status()
    if status.registered:
        stored_extract = settings.value(KEY_INTEGRATION_EXTRACT_HERE, None)
        extract_here = (
            DEFAULT_INTEGRATION_EXTRACT_HERE
            if stored_extract is None else bool(settings.value(KEY_INTEGRATION_EXTRACT_HERE, True, bool))
        )
        integration.apply(
            status.extensions,
            add_to_session=status.add_to_session,
            open_with=status.open_with,
            extract_here=extract_here,
        )
    temp_root = Path(str(settings.value(KEY_TEMP_ROOT, "") or default_root()))
    purge_stale(temp_root)

    theme = ThemeService(app)
    window = MainWindow(settings=settings, theme=theme)

    server = SingleInstanceServer()
    server.request_received.connect(lambda request: _dispatch_request(window, request))
    server.listen()

    theme.apply_to(app)
    theme.start_watching()
    window.show()

    # 只由安装器设置一次性标记；源码运行、便携运行和后续普通启动不会打扰用户。
    run_post_install_integration_prompt(window, settings, integration)

    if args.paths:
        _dispatch_request(window, Request(action=action, paths=tuple(args.paths)))

    try:
        return app.exec()
    finally:
        theme.stop_watching()
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
