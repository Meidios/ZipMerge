"""HKCU 注册表薄封装。

所有函数都接受「根键 + 键路径」，统一使用 64 位视图（`KEY_WOW64_64KEY`），
这样 32 位 Python 也能读写系统默认的关联项；任何 `OSError` 都静默降级成
「失败 / 无值」，调用方不必到处写 try（PLAN 8）。
"""

from __future__ import annotations

try:  # pragma: no cover - 本产品仅 Windows，此分支只用于文档构建
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

#: 64 位注册表视图
WOW64_64KEY = 0x0100

#: winreg 缺席时 HKCU 的常量值
HKCU_VALUE = 0x80000001

#: 常用值类型（Windows 固定常量，避免 winreg 缺席时无法引用）
REG_NONE = 0
REG_SZ = 1
REG_EXPAND_SZ = 2
REG_DWORD = 4


def available() -> bool:
    return winreg is not None


def hkey_current_user():
    return winreg.HKEY_CURRENT_USER if winreg is not None else HKCU_VALUE  # type: ignore[union-attr]


def _flags(base: int) -> int:
    return base | WOW64_64KEY


def write_value(root, path: str, name: str, value, kind: int = REG_SZ) -> bool:
    """写入 / 覆盖一个值；`name` 为空串表示默认值。成功返回 True。"""
    if winreg is None:
        return False
    try:
        with winreg.CreateKeyEx(root, path, 0, _flags(winreg.KEY_WRITE)) as key:
            winreg.SetValueEx(key, name, 0, kind, value)
        return True
    except OSError:
        return False


def read_value(root, path: str, name: str = "", default=None):
    if winreg is None:
        return default
    try:
        with winreg.OpenKey(root, path, 0, _flags(winreg.KEY_READ)) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def value_type(root, path: str, name: str = "") -> int | None:
    """读取值类型（如 `REG_NONE`）；键或值不存在时返回 None。"""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, path, 0, _flags(winreg.KEY_READ)) as key:
            return winreg.QueryValueEx(key, name)[1]
    except OSError:
        return None


def has_value(root, path: str, name: str = "") -> bool:
    """值名是否存在（不关心类型与数据）。

    REG_NONE 空值无法通过 read_value 观察到（QueryValueEx 会失败），因此
    判断 OpenWithProgids 这类「值名即数据」的项必须走这里。
    """
    return value_type(root, path, name) is not None


def delete_value(root, path: str, name: str = "") -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(root, path, 0, _flags(winreg.KEY_SET_VALUE)) as key:
            winreg.DeleteValue(key, name)
        return True
    except OSError:
        return False


def key_exists(root, path: str) -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(root, path, 0, _flags(winreg.KEY_READ)):
            return True
    except OSError:
        return False


def list_subkeys(root, path: str) -> list[str]:
    if winreg is None:
        return []
    names: list[str] = []
    try:
        with winreg.OpenKey(root, path, 0, _flags(winreg.KEY_READ)) as key:
            index = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
    except OSError:
        return []
    return names


def list_values(root, path: str) -> list[str]:
    if winreg is None:
        return []
    names: list[str] = []
    try:
        with winreg.OpenKey(root, path, 0, _flags(winreg.KEY_READ)) as key:
            index = 0
            while True:
                try:
                    names.append(winreg.EnumValue(key, index)[0])
                except OSError:
                    break
                index += 1
    except OSError:
        return []
    return names


def delete_tree(root, path: str) -> bool:
    """自底向上递归删除键及其全部子键与值；键不存在时视为成功（幂等）。"""
    if winreg is None:
        return False
    if not key_exists(root, path):
        return True
    for child in list_subkeys(root, path):
        delete_tree(root, f"{path}\\{child}")
    for name in list_values(root, path):
        delete_value(root, path, name)
    try:
        winreg.DeleteKeyEx(root, path, WOW64_64KEY, 0)
        return True
    except OSError:
        try:
            winreg.DeleteKey(root, path)
            return True
        except OSError:
            return False
