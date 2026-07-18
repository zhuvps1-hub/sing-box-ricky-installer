"""Non-privileged client for the root helper Unix socket."""
from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

from .protocol import receive_message, send_message


class HelperError(RuntimeError):
    pass


class HelperClient:
    def __init__(self, socket_path: Path, timeout: float = 45.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = {"operation": str(operation), "payload": payload or {}}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout)
            try:
                connection.connect(str(self.socket_path))
                send_message(connection, request)
                response = receive_message(connection)
            except (OSError, ValueError) as exc:
                raise HelperError(f"特权 helper 不可用：{exc}") from exc
        if not response.get("ok"):
            raise HelperError(str(response.get("error") or "helper 操作失败"))
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise HelperError("helper 返回格式无效")
        return result

    def ping(self) -> bool:
        try:
            return bool(self.call("ping").get("ready"))
        except HelperError:
            return False
