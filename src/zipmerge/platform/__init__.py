"""平台集成层：Windows 注册表关联、右键菜单与卸载清理。

只写 HKCU，免管理员，不抢占现有默认程序（PLAN 8）。
"""

from __future__ import annotations

from .registry import (
    HKCU_VALUE,
    REG_DWORD,
    REG_EXPAND_SZ,
    REG_NONE,
    REG_SZ,
    WOW64_64KEY,
    available,
    delete_tree,
    delete_value,
    has_value,
    hkey_current_user,
    key_exists,
    list_subkeys,
    list_values,
    read_value,
    value_type,
    write_value,
)

__all__ = [
    "HKCU_VALUE",
    "REG_DWORD",
    "REG_EXPAND_SZ",
    "REG_NONE",
    "REG_SZ",
    "WOW64_64KEY",
    "available",
    "delete_tree",
    "delete_value",
    "has_value",
    "hkey_current_user",
    "key_exists",
    "list_subkeys",
    "list_values",
    "read_value",
    "value_type",
    "write_value",
]
