"""压缩包引擎层：7-Zip 子进程封装与列表解析。"""

from .errors import ArchiveCancelled, ArchiveError, describe_failure, is_password_failure
from .sevenzip import (
    ArchiveEntry,
    ArchiveListing,
    create_archive,
    delete_entries,
    extract_entries,
    list_entries,
    list_with_info,
    parse_slt,
    test_archive,
)

__all__ = [
    "ArchiveEntry",
    "ArchiveListing",
    "ArchiveError",
    "ArchiveCancelled",
    "describe_failure",
    "is_password_failure",
    "parse_slt",
    "list_entries",
    "list_with_info",
    "extract_entries",
    "create_archive",
    "delete_entries",
    "test_archive",
]
