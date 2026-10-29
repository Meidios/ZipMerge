"""统一的压缩包错误模型，并附带常见故障的中文解释。"""

from __future__ import annotations


class ArchiveError(Exception):
    """7-Zip 调用失败或列表解析失败。

    始终携带原始 7-Zip 输出，便于问题定位与「查看详情」展示。
    """

    def __init__(self, message: str, *, output: str = "", returncode: int | None = None):
        super().__init__(message)
        self.message = message
        self.output = output
        self.returncode = returncode
        self.hint = describe_failure(output)

    def __str__(self) -> str:  # pragma: no cover - 仅用于展示
        if self.hint:
            return f"{self.message}（{self.hint}）"
        return self.message


class ArchiveCancelled(ArchiveError):
    """用户主动取消了一次 7-Zip 操作。"""


_HINTS: tuple[tuple[str, str], ...] = (
    ("cannot open the file as archive", "文件不是有效的压缩包，或格式不受支持"),
    ("is not supported archive", "压缩包格式不受支持"),
    ("wrong password", "密码错误"),
    ("can not open encrypted archive", "该压缩包已加密，需要提供密码"),
    ("data error in encrypted file", "加密数据校验失败，密码可能不正确或文件已损坏"),
    ("the archive is corrupted", "压缩包已损坏"),
    ("can not open output file", "无法写入输出文件，请检查目标目录是否可写或文件是否被占用"),
    ("access is denied", "访问被拒绝，请检查权限或文件是否被其他程序占用"),
    ("the process cannot access the file", "文件被其他程序占用"),
    ("no files to process", "没有可处理的文件"),
    ("unsupported method", "压缩包使用了当前解码器不支持的算法"),
    ("headercrc error", "压缩包头校验失败，文件可能已损坏"),
    ("there is no space", "磁盘空间不足"),
)


_PASSWORD_MARKERS: tuple[str, ...] = (
    "wrong password",
    "can not open encrypted archive",
    "cannot open encrypted archive",
    "enter password",
    "password is incorrect",
)


def is_password_failure(output: str) -> bool:
    """判断 7-Zip 的失败输出是否因为缺少或输错密码。

    加密包在提供密码前无法列出内容，UI 需要据此弹出密码输入框（PLAN 12.2）。
    只匹配 7-Zip 的英文原始输出，不依赖界面文案。
    """
    if not output:
        return False
    lowered = output.lower()
    return any(marker in lowered for marker in _PASSWORD_MARKERS)


def describe_failure(output: str) -> str:
    """从原始输出中提取一句可读的中文提示，无法识别时返回空串。"""
    if not output:
        return ""
    lowered = output.lower()
    for needle, hint in _HINTS:
        if needle in lowered:
            return hint
    return ""
