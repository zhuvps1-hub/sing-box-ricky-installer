"""Bounded length-prefixed JSON protocol for the local privileged helper."""
from __future__ import annotations

import json
import socket
import struct
from typing import Any

MAX_MESSAGE = 20 * 1024 * 1024
_HEADER = struct.Struct("!I")


class ProtocolError(ValueError):
    pass


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ProtocolError("连接在消息完成前关闭")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_message(connection: socket.socket, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE:
        raise ProtocolError("消息超过大小限制")
    connection.sendall(_HEADER.pack(len(payload)) + payload)


def receive_message(connection: socket.socket) -> dict[str, Any]:
    length = _HEADER.unpack(_receive_exact(connection, _HEADER.size))[0]
    if length <= 0 or length > MAX_MESSAGE:
        raise ProtocolError("消息长度无效")
    try:
        value = json.loads(_receive_exact(connection, length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("消息不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("消息顶层必须是对象")
    return value
