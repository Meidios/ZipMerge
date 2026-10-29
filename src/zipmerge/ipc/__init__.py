"""单实例 IPC 层（PLAN 8.4）。"""

from __future__ import annotations

from .single_instance import (
    ACTION_ADD_TO_SESSION,
    ACTION_OPEN,
    ACTIONS,
    MAX_FRAME,
    SERVER_NAME,
    Request,
    SingleInstanceServer,
    decode_request,
    encode_request,
    frame,
    is_running,
    send,
)

__all__ = [
    "ACTION_ADD_TO_SESSION",
    "ACTION_OPEN",
    "ACTIONS",
    "MAX_FRAME",
    "SERVER_NAME",
    "Request",
    "SingleInstanceServer",
    "decode_request",
    "encode_request",
    "frame",
    "is_running",
    "send",
]
