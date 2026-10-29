"""7-Zip 子进程封装与 `-slt` 列表解析。

设计要点（对应 PLAN 7.1）：

* 所有路径经清单文件或 `--` 传递，规避中文 / 空格 / 引号转义问题；
* 超长路径自动加 `\\\\?\\` 前缀；
* 耗时可取消，进度按行实时回报；
* 失败统一抛 :class:`ArchiveError` 并携带原始输出。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Literal, Sequence

from ..util.paths import long_path, normalize_entry_path, sevenzip_exe
from .errors import ArchiveCancelled, ArchiveError

ProgressCallback = Callable[[int, str], None]

_PERCENT_RE = re.compile(r"^\s*(\d{1,3})%")
_SEPARATOR_RE = re.compile(r"^-{5,}$")
# 7-Zip 的时间戳小数位为 7 位，Python 的 %f 只接受 1-6 位。
_LONG_FRACTION_RE = re.compile(r"^(.*\.\d{6})\d+$")

# 打包时使用的通配符：在 cwd=源目录 的前提下展开，包内不产生额外前缀。
_PACK_MASK = ".\\*"


@dataclass(frozen=True)
class ArchiveEntry:
    """压缩包内的单个条目。"""

    path: str
    size: int = 0
    packed_size: int = 0
    is_dir: bool = False
    mtime: datetime | None = None
    crc: str | None = None
    encrypted: bool = False
    attributes: str = ""
    method: str = ""

    @property
    def name(self) -> str:
        return self.path.rsplit("\\", 1)[-1]

    @property
    def parent(self) -> str:
        return self.path.rsplit("\\", 1)[0] if "\\" in self.path else ""


@dataclass
class ArchiveListing:
    """单个压缩包的列表结果与包级信息。"""

    entries: list[ArchiveEntry] = field(default_factory=list)
    archive_type: str = ""
    physical_size: int = 0
    path: str = ""

    @property
    def has_encrypted_entries(self) -> bool:
        return any(entry.encrypted for entry in self.entries)


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    text = _LONG_FRACTION_RE.sub(r"\1", text)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_int(value: str) -> int:
    text = value.strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def _build_entry(fields: dict[str, str]) -> ArchiveEntry | None:
    raw_path = fields.get("Path", "")
    if not raw_path:
        return None
    attributes = fields.get("Attributes", "").strip()
    # zip/tar 目录带 Folder = + 或尾分隔符；7z 格式目录没有 Folder 行，
    # 只以属性位体现（Windows 为 D / DR，Unix 为 drwx…），必须一并识别。
    is_dir = (
        fields.get("Folder", "-").strip() == "+"
        or raw_path.endswith(("\\", "/"))
        or attributes[:1] in ("D", "d")
    )
    path = normalize_entry_path(raw_path)
    if not path:
        return None
    crc = fields.get("CRC", "").strip() or None
    return ArchiveEntry(
        path=path,
        size=_parse_int(fields.get("Size", "")),
        packed_size=_parse_int(fields.get("Packed Size", "")),
        is_dir=is_dir,
        mtime=_parse_datetime(fields.get("Modified", "")),
        crc=crc,
        encrypted=fields.get("Encrypted", "-").strip() == "+",
        attributes=attributes,
        method=fields.get("Method", "").strip(),
    )


def parse_slt(output: str) -> ArchiveListing:
    """解析 `7z l -slt` 的文本输出。

    头部区块（`--` 分隔）提供包级信息，条目区块（`----------` 分隔）逐条解析。
    """
    listing = ArchiveListing()
    fields: dict[str, str] = {}
    in_entries = False
    in_header = False

    def flush() -> None:
        nonlocal fields
        if in_entries and fields:
            entry = _build_entry(fields)
            if entry is not None:
                listing.entries.append(entry)
        elif in_header and fields:
            listing.archive_type = fields.get("Type", "").strip()
            listing.physical_size = _parse_int(fields.get("Physical Size", ""))
            listing.path = fields.get("Path", "").strip()
        fields = {}

    for raw_line in output.splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if _SEPARATOR_RE.match(stripped):
            flush()
            in_entries = True
            in_header = False
            continue
        if stripped == "--":
            flush()
            in_header = True
            continue
        if not stripped:
            flush()
            continue
        if in_entries or in_header:
            key, sep, value = line.partition(" = ")
            if sep:
                fields[key.strip()] = value

    flush()
    return listing


def _base_command() -> list[str]:
    exe = sevenzip_exe()
    if not exe.exists():
        raise ArchiveError(f"找不到 7-Zip 引擎：{exe}")
    return [str(exe)]


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(
    args: Sequence[str],
    *,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    cwd: str | os.PathLike[str] | None = None,
    encoding: str = "utf-8",
) -> str:
    """执行 7z.exe，逐行回报进度，失败时抛 :class:`ArchiveError`。"""
    if cancel is not None and cancel.is_set():
        raise ArchiveCancelled("操作已取消")
    cmd = _base_command() + list(args)
    env = os.environ.copy()
    env.setdefault("LANG", "en_US.UTF-8")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding=encoding,
            errors="replace",
            cwd=str(cwd) if cwd is not None else None,
            creationflags=_creation_flags(),
            env=env,
        )
    except OSError as exc:  # pragma: no cover - 环境异常
        raise ArchiveError(f"无法启动 7-Zip：{exc}") from exc

    collected: list[str] = []
    watcher_stop = threading.Event()
    killed_for_cancel = threading.Event()
    watcher: threading.Thread | None = None
    if cancel is not None:
        def watch_cancel() -> None:
            # stdout.readline() 在 Windows 管道上可能长期阻塞；取消必须从另一线程
            # 直接终止进程，才能让读取端立刻返回。
            while not watcher_stop.wait(0.02):
                if not cancel.is_set():
                    continue
                if proc.poll() is None:
                    try:
                        proc.kill()
                        killed_for_cancel.set()
                    except OSError:
                        pass
                return

        watcher = threading.Thread(
            target=watch_cancel,
            name="ZipMerge-7z-cancel",
            daemon=True,
        )
        watcher.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            collected.append(line)
            if on_progress is not None:
                match = _PERCENT_RE.match(line)
                if match:
                    on_progress(int(match.group(1)), line.strip())
        returncode = proc.wait()
    finally:
        watcher_stop.set()
        if watcher is not None:
            watcher.join(timeout=0.2)
        if proc.poll() is None:  # pragma: no cover - 异常路径
            proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()

    output = "".join(collected)
    if killed_for_cancel.is_set():
        raise ArchiveCancelled("操作已取消", output=output, returncode=returncode)
    if returncode != 0:
        detail = _last_error_line(output) or "7-Zip 执行失败"
        raise ArchiveError(detail, output=output, returncode=returncode)
    return output


def _last_error_line(output: str) -> str:
    for line in reversed(output.splitlines()):
        text = line.strip()
        if text and not _PERCENT_RE.match(text) and not text.startswith(("Scanning", "1 file")):
            return text
    return ""


def _password_switch(password: str | None) -> list[str]:
    if password is None:
        return []
    return ["-p" + password]


def list_with_info(
    archive: str | os.PathLike[str],
    password: str | None = None,
    *,
    cancel: threading.Event | None = None,
) -> ArchiveListing:
    """列出压缩包内容，同时返回包级信息。"""
    target = long_path(archive)
    args = ["l", "-slt", "-sccUTF-8", *(_password_switch(password)), "--", target]
    output = _run(args, cancel=cancel)
    listing = parse_slt(output)
    if not listing.entries and not listing.archive_type:
        raise ArchiveError("无法解析压缩包内容", output=output)
    return listing


def list_entries(
    archive: str | os.PathLike[str],
    password: str | None = None,
    *,
    cancel: threading.Event | None = None,
) -> list[ArchiveEntry]:
    """列出压缩包内的全部条目。"""
    return list_with_info(archive, password=password, cancel=cancel).entries


def _write_listfile(path: Path, names: Iterable[str]) -> None:
    with path.open("w", encoding="utf-8", newline="\r\n") as handle:
        for name in names:
            handle.write(name + "\n")


def _prepare_listfile(
    directory: Path, paths: Sequence[str] | None, prefix: str = ".zipmerge-entries"
) -> tuple[Path | None, list[str]]:
    """把条目名写入 UTF-8 清单文件，返回 (清单路径, 规范化条目名)。"""
    names = [normalize_entry_path(p) for p in (paths or [])]
    names = [name for name in names if name]
    if not names:
        return None, []
    target_dir = Path(directory).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    listfile = target_dir / f"{prefix}-{os.getpid()}-{os.urandom(4).hex()}.txt"
    _write_listfile(listfile, names)
    return listfile, names


def extract_entries(
    archive: str | os.PathLike[str],
    paths: Sequence[str] | None,
    out_dir: str | os.PathLike[str],
    *,
    password: str | None = None,
    overwrite: Literal["overwrite", "skip"] = "overwrite",
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """把指定条目解压到 out_dir，保留包内目录结构。

    ``paths`` 为空或 None 时整包解压。条目名经 UTF-8 清单文件传递；
    ``overwrite`` 显式选择覆盖已有文件或跳过已有文件。
    """
    target = long_path(archive)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    overwrite_switch = {"overwrite": "-aoa", "skip": "-aos"}.get(overwrite)
    if overwrite_switch is None:
        raise ValueError(f"不支持的解压覆盖模式：{overwrite}")
    args = [
        "x",
        "-y",
        overwrite_switch,
        "-sccUTF-8",
        "-scsUTF-8",
        *(_password_switch(password)),
        f"-o{out}",
    ]
    listfile, names = _prepare_listfile(out.parent, paths)
    if listfile is not None:
        # 必须作为开关出现在 -- 之前，放在 -- 之后会被当成字面文件名。
        args.append(f"-i@{listfile}")
    args += ["--", target]
    try:
        _run(args, on_progress=on_progress, cancel=cancel)
    finally:
        if listfile is not None and listfile.exists():
            try:
                listfile.unlink()
            except OSError:  # pragma: no cover - 清理失败不影响结果
                pass


def create_archive(
    out_path: str | os.PathLike[str],
    fmt: str,
    src_dir: str | os.PathLike[str],
    *,
    level: int = 5,
    password: str | None = None,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    paths: Sequence[str] | None = None,
) -> None:
    """Pack a directory's contents, or selected literal paths relative to it."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "a",
        f"-t{fmt}",
        f"-mx{level}",
        "-y",
        "-sccUTF-8",
        *(["-spd"] if paths is not None else []),
        # zip 默认按本地 ANSI 代码页写条目名（中文系统上是 GBK），跨机器/跨工具易乱码；
        # PLAN 12.6 要求默认 UTF-8，-mcu 会同时置上通用标志位 0x800。
        *(["-mcu"] if fmt.lower() == "zip" else []),
        *(["-mhe=on"] if password and fmt.lower() == "7z" else []),
        *(_password_switch(password)),
        "--",
        str(out),
        *(paths if paths is not None else [_PACK_MASK]),
    ]
    _run(args, on_progress=on_progress, cancel=cancel, cwd=src_dir)


def delete_entries(
    archive: str | os.PathLike[str],
    paths: Sequence[str],
    *,
    password: str | None = None,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """删除包内条目。仅用于本程序新建的包，源包永不被就地改写。"""
    names = [normalize_entry_path(p) for p in paths if normalize_entry_path(p)]
    if not names:
        return
    target = long_path(archive)
    listfile = Path(archive).resolve().parent / f".zipmerge-del-{os.getpid()}-{os.urandom(4).hex()}.txt"
    _write_listfile(listfile, names)
    args = [
        "d",
        "-y",
        "-sccUTF-8",
        "-scsUTF-8",
        *(_password_switch(password)),
        f"-i@{listfile}",
        "--",
        target,
    ]
    try:
        _run(args, on_progress=on_progress, cancel=cancel)
    finally:
        if listfile.exists():
            try:
                listfile.unlink()
            except OSError:  # pragma: no cover
                pass


def test_archive(
    archive: str | os.PathLike[str],
    *,
    password: str | None = None,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> bool:
    """完整性测试。损坏或密码错误返回 False，不抛异常。"""
    target = long_path(archive)
    args = ["t", "-sccUTF-8", *(_password_switch(password)), "--", target]
    try:
        _run(args, on_progress=on_progress, cancel=cancel)
    except ArchiveCancelled:
        raise
    except ArchiveError:
        return False
    return True


def cleanup_stale_listfiles(directory: str | os.PathLike[str]) -> None:
    """清理可能残留的临时清单文件。"""
    for pattern in (".zipmerge-list-*.txt", ".zipmerge-del-*.txt"):
        for path in Path(directory).glob(pattern):
            try:
                path.unlink()
            except OSError:  # pragma: no cover
                pass


def which_7zip() -> str | None:
    """返回 7z.exe 路径，缺失时返回 None（供 UI 提示使用）。"""
    exe = sevenzip_exe()
    if exe.exists():
        return str(exe)
    found = shutil.which("7z")
    return found
