"""Root-only helper exposing a small fixed operation set over a Unix socket."""
from __future__ import annotations

import grp
import json
import os
import pwd
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .atomic import read_json, write_bytes, write_json, write_text
from .audit import AuditLog
from .locks import FileLock
from .protocol import ProtocolError, receive_message, send_message
from .redaction import redact_text

SERVICE_ALLOWLIST = {"sing-box", "mosdns", "iwan-gateway", "iwan-gateway-helper"}
TEXT_SUFFIXES = {".yaml", ".yml", ".txt", ".list", ".conf", ".rules", ".hosts"}
MAX_TEXT = 1_048_576


class OperationError(RuntimeError):
    pass


class HelperOperations:
    def __init__(
        self,
        *,
        singbox_config: Path,
        singbox_binary: Path,
        singbox_backups: Path,
        mosdns_config: Path,
        mosdns_backups: Path,
        lock_dir: Path,
        audit: AuditLog,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.singbox_config = singbox_config
        self.singbox_binary = singbox_binary
        self.singbox_backups = singbox_backups
        self.mosdns_config = mosdns_config
        self.mosdns_backups = mosdns_backups
        self.lock_dir = lock_dir
        self.audit = audit
        self.runner = runner

    def _run(self, command: list[str], timeout: int = 30) -> tuple[int, str]:
        try:
            result = self.runner(command, capture_output=True, text=True, timeout=timeout, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return 127, str(exc)
        return int(result.returncode), redact_text((result.stdout or "") + (result.stderr or ""))

    def _active(self, service: str) -> bool:
        if service not in SERVICE_ALLOWLIST:
            raise OperationError("服务不在白名单")
        code, _ = self._run(["systemctl", "is-active", "--quiet", service], timeout=10)
        return code == 0

    def _restart(self, service: str) -> tuple[bool, str]:
        if service not in SERVICE_ALLOWLIST:
            raise OperationError("服务不在白名单")
        code, output = self._run(["systemctl", "restart", service], timeout=45)
        if code != 0:
            return False, output
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self._active(service):
                return True, output
            time.sleep(0.25)
        return False, output or f"{service} 未进入 active 状态"

    @staticmethod
    def _json_object(value: Any, maximum: int = 16 * 1024 * 1024) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise OperationError("配置顶层必须是对象")
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(encoded) > maximum:
            raise OperationError("配置超过大小限制")
        return value

    def _backup(self, source: Path, directory: Path, prefix: str, suffix: str) -> Path | None:
        if not source.is_file():
            return None
        directory.mkdir(parents=True, exist_ok=True)
        transaction_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:10]}"
        target = directory / f"{prefix}-{transaction_id}{suffix}"
        shutil.copy2(source, target)
        os.chmod(target, 0o600)
        return target

    def _transaction_json(self, candidate: dict[str, Any], actor: str, remote: str) -> dict[str, Any]:
        transaction_id = uuid.uuid4().hex
        lock = FileLock(self.lock_dir / "singbox.lock", timeout=30)
        with lock:
            original = self.singbox_config.read_bytes() if self.singbox_config.exists() else None
            backup = self._backup(self.singbox_config, self.singbox_backups, "config", ".json")
            self.singbox_config.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix="candidate.", suffix=".json", dir=str(self.singbox_config.parent))
            os.close(descriptor)
            temporary = Path(name)
            try:
                write_json(temporary, candidate, 0o600)
                code, output = self._run([str(self.singbox_binary), "check", "-c", str(temporary)], timeout=30)
                if code != 0:
                    raise OperationError("配置校验失败：\n" + output)
                write_bytes(self.singbox_config, temporary.read_bytes(), 0o600)
                ok, output = self._restart("sing-box")
                if not ok:
                    if original is None:
                        self.singbox_config.unlink(missing_ok=True)
                    else:
                        write_bytes(self.singbox_config, original, 0o600)
                    self._restart("sing-box")
                    raise OperationError("sing-box 启动失败，已恢复旧配置：\n" + output)
                result = {"transaction_id": transaction_id, "backup": backup.name if backup else "首次创建"}
                self.audit.append("singbox.apply", actor=actor, remote=remote, transaction_id=transaction_id, detail=result)
                return result
            except Exception as exc:
                self.audit.append("singbox.apply", actor=actor, remote=remote, ok=False, transaction_id=transaction_id, detail={"error": str(exc)})
                raise
            finally:
                temporary.unlink(missing_ok=True)

    def _safe_mosdns_path(self, name: str) -> Path:
        normalized = name.strip().replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            raise OperationError("mosdns 文件路径无效")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", normalized):
            raise OperationError("mosdns 文件路径包含非法字符")
        root = self.mosdns_config.parent.resolve()
        target = (root / normalized).resolve()
        if target != root and root not in target.parents:
            raise OperationError("mosdns 文件路径越界")
        backup_root = self.mosdns_backups.resolve()
        if target == backup_root or backup_root in target.parents:
            raise OperationError("不能直接编辑备份目录")
        if target.suffix.lower() not in TEXT_SUFFIXES:
            raise OperationError("不允许编辑此文件类型")
        return target

    @staticmethod
    def _text(value: Any, *, allow_empty: bool = False) -> str:
        if not isinstance(value, str):
            raise OperationError("文本内容无效")
        encoded = value.encode("utf-8")
        if len(encoded) > MAX_TEXT or "\0" in value:
            raise OperationError("文本内容无效或超过 1 MB")
        if not allow_empty and not value.strip():
            raise OperationError("文本内容不能为空")
        return value if value.endswith("\n") else value + "\n"

    def _apply_mosdns(self, text: str, actor: str, remote: str) -> dict[str, Any]:
        text = self._text(text)
        if any(line.startswith("\t") for line in text.splitlines()):
            raise OperationError("YAML 缩进不能使用 Tab")
        transaction_id = uuid.uuid4().hex
        with FileLock(self.lock_dir / "mosdns.lock", timeout=30):
            original = self.mosdns_config.read_bytes() if self.mosdns_config.exists() else None
            backup = self._backup(self.mosdns_config, self.mosdns_backups, "config", ".yaml")
            try:
                write_text(self.mosdns_config, text, 0o600)
                ok, output = self._restart("mosdns")
                if not ok:
                    if original is None:
                        self.mosdns_config.unlink(missing_ok=True)
                    else:
                        write_bytes(self.mosdns_config, original, 0o600)
                    self._restart("mosdns")
                    raise OperationError("mosdns 启动失败，已恢复旧配置：\n" + output)
                result = {"transaction_id": transaction_id, "backup": backup.name if backup else "首次创建"}
                self.audit.append("mosdns.apply", actor=actor, remote=remote, transaction_id=transaction_id, detail=result)
                return result
            except Exception as exc:
                self.audit.append("mosdns.apply", actor=actor, remote=remote, ok=False, transaction_id=transaction_id, detail={"error": str(exc)})
                raise

    def dispatch(self, operation: str, payload: dict[str, Any], *, actor: str, remote: str) -> dict[str, Any]:
        if operation == "ping":
            return {"ready": True, "uid": os.geteuid()}
        if operation == "service.status":
            return {name: self._active(name) for name in sorted(SERVICE_ALLOWLIST)}
        if operation == "service.restart":
            service = str(payload.get("service", ""))
            ok, output = self._restart(service)
            self.audit.append("service.restart", actor=actor, remote=remote, ok=ok, detail={"service": service, "output": output})
            if not ok:
                raise OperationError(output or "服务重启失败")
            return {"service": service, "message": output or "已重启"}
        if operation == "singbox.read":
            return {"config": self._json_object(read_json(self.singbox_config, {})), "mtime": int(self.singbox_config.stat().st_mtime) if self.singbox_config.exists() else 0}
        if operation == "singbox.apply":
            return self._transaction_json(self._json_object(payload.get("config")), actor, remote)
        if operation == "singbox.backup":
            with FileLock(self.lock_dir / "singbox.lock"):
                backup = self._backup(self.singbox_config, self.singbox_backups, "manual", ".json")
            if backup is None:
                raise OperationError("sing-box 配置不存在")
            self.audit.append("singbox.backup", actor=actor, remote=remote, detail={"backup": backup.name})
            return {"backup": backup.name}
        if operation == "logs.read":
            service = str(payload.get("service", "sing-box"))
            if service not in SERVICE_ALLOWLIST:
                raise OperationError("服务不在白名单")
            lines = min(max(int(payload.get("lines", 200)), 1), 1000)
            code, output = self._run(["journalctl", "-u", service, "-n", str(lines), "--no-pager", "--output=short-iso"], timeout=12)
            return {"logs": output, "command_ok": code == 0}
        if operation == "network.read":
            _, routes = self._run(["ip", "route", "show"], timeout=8)
            _, ports = self._run(["ss", "-lntup"], timeout=8)
            return {"routes": routes, "ports": ports}
        if operation == "mosdns.read":
            text = self.mosdns_config.read_text(encoding="utf-8") if self.mosdns_config.exists() else ""
            if len(text.encode("utf-8")) > MAX_TEXT:
                raise OperationError("mosdns 配置超过 1 MB")
            backups = []
            if self.mosdns_backups.exists():
                for path in sorted(self.mosdns_backups.glob("*.y*ml"), key=lambda item: item.stat().st_mtime, reverse=True)[:40]:
                    backups.append({"name": path.name, "size": path.stat().st_size, "mtime": int(path.stat().st_mtime)})
            files = []
            root = self.mosdns_config.parent
            if root.exists():
                for path in root.rglob("*"):
                    try:
                        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and self.mosdns_backups.resolve() not in path.resolve().parents and path.stat().st_size <= MAX_TEXT:
                            files.append({"name": path.relative_to(root).as_posix(), "size": path.stat().st_size, "mtime": int(path.stat().st_mtime)})
                    except OSError:
                        continue
            return {"config": text, "available": self.mosdns_config.exists(), "mtime": int(self.mosdns_config.stat().st_mtime) if self.mosdns_config.exists() else 0, "backups": backups, "files": sorted(files, key=lambda item: item["name"])[:200]}
        if operation == "mosdns.apply":
            return self._apply_mosdns(str(payload.get("config", "")), actor, remote)
        if operation == "mosdns.backup":
            with FileLock(self.lock_dir / "mosdns.lock"):
                backup = self._backup(self.mosdns_config, self.mosdns_backups, "manual", ".yaml")
            if backup is None:
                raise OperationError("mosdns 配置不存在")
            return {"backup": backup.name}
        if operation == "mosdns.restore":
            name = str(payload.get("name", ""))
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:yaml|yml)", name):
                raise OperationError("备份文件名无效")
            source = self.mosdns_backups / name
            if not source.is_file():
                raise OperationError("备份不存在")
            return self._apply_mosdns(source.read_text(encoding="utf-8"), actor, remote)
        if operation == "mosdns.file.read":
            name = str(payload.get("name", ""))
            target = self._safe_mosdns_path(name)
            if not target.is_file():
                raise OperationError("文件不存在")
            content = target.read_text(encoding="utf-8")
            if len(content.encode("utf-8")) > MAX_TEXT:
                raise OperationError("文件超过 1 MB")
            return {"name": name, "content": content, "mtime": int(target.stat().st_mtime)}
        if operation == "mosdns.file.save":
            name = str(payload.get("name", ""))
            target = self._safe_mosdns_path(name)
            content = self._text(payload.get("content", ""), allow_empty=True)
            with FileLock(self.lock_dir / "mosdns.lock"):
                if target.exists():
                    file_backups = self.mosdns_backups / "files"
                    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
                    self._backup(target, file_backups, safe_name, target.suffix or ".txt")
                write_text(target, content, 0o640)
            self.audit.append("mosdns.file.save", actor=actor, remote=remote, detail={"name": name})
            return {"name": name}
        if operation == "audit.recent":
            return {"events": self.audit.recent(int(payload.get("limit", 100)))}
        raise OperationError("未知 helper 操作")


class HelperServer:
    def __init__(self, socket_path: Path, operations: HelperOperations, allowed_uid: int | None = None, socket_gid: int | None = None) -> None:
        self.socket_path = socket_path
        self.operations = operations
        self.allowed_uid = allowed_uid
        self.socket_gid = socket_gid
        self.stop_event = threading.Event()
        self.server: socket.socket | None = None

    def _peer(self, connection: socket.socket) -> tuple[int, int, int]:
        if not hasattr(socket, "SO_PEERCRED"):
            return (-1, -1, -1)
        return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))

    def _handle(self, connection: socket.socket) -> None:
        with connection:
            try:
                pid, uid, gid = self._peer(connection)
                if self.allowed_uid is not None and uid not in (0, self.allowed_uid):
                    raise PermissionError("helper 客户端 UID 未授权")
                request = receive_message(connection)
                operation = str(request.get("operation", ""))
                payload = request.get("payload", {})
                if not isinstance(payload, dict):
                    raise ProtocolError("payload 必须是对象")
                actor = str(payload.pop("_actor", f"uid:{uid}"))[:128]
                remote = str(payload.pop("_remote", f"pid:{pid};gid:{gid}"))[:128]
                result = self.operations.dispatch(operation, payload, actor=actor, remote=remote)
                send_message(connection, {"ok": True, "result": result})
            except Exception as exc:
                try:
                    send_message(connection, {"ok": False, "error": redact_text(str(exc), 4000)})
                except OSError:
                    pass

    def serve_forever(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server = server
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o660)
        if self.socket_gid is not None:
            os.chown(self.socket_path, 0, self.socket_gid)
        server.listen(32)
        server.settimeout(1.0)
        try:
            while not self.stop_event.is_set():
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self._handle, args=(connection,), daemon=True).start()
        finally:
            server.close()
            self.socket_path.unlink(missing_ok=True)

    def shutdown(self) -> None:
        self.stop_event.set()


def _uid_from_env() -> int | None:
    value = os.environ.get("IWAN_HELPER_ALLOWED_UID", "").strip()
    if value:
        return int(value)
    user = os.environ.get("IWAN_PANEL_USER", "iwan-gateway")
    try:
        return pwd.getpwnam(user).pw_uid
    except KeyError:
        return None


def _gid_from_env() -> int | None:
    value = os.environ.get("IWAN_HELPER_GID", "").strip()
    if value:
        return int(value)
    group = os.environ.get("IWAN_PANEL_GROUP", "iwan-gateway")
    try:
        return grp.getgrnam(group).gr_gid
    except KeyError:
        return None


def build_operations() -> HelperOperations:
    data_dir = Path(os.environ.get("IWAN_PANEL_DATA_DIR", "/var/lib/iwan-gateway"))
    return HelperOperations(
        singbox_config=Path(os.environ.get("SINGBOX_CONFIG", "/etc/sing-box/config.json")),
        singbox_binary=Path(os.environ.get("SINGBOX_BINARY", "/usr/local/bin/sing-box")),
        singbox_backups=Path(os.environ.get("SINGBOX_BACKUP_DIR", "/etc/sing-box/backups")),
        mosdns_config=Path(os.environ.get("MOSDNS_CONFIG", "/etc/mosdns/config.yaml")),
        mosdns_backups=Path(os.environ.get("MOSDNS_BACKUP_DIR", "/etc/mosdns/backups")),
        lock_dir=Path(os.environ.get("IWAN_HELPER_LOCK_DIR", "/run/iwan-gateway/locks")),
        audit=AuditLog(Path(os.environ.get("IWAN_AUDIT_LOG", str(data_dir / "audit.jsonl")))),
    )


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("特权 helper 必须以 root 运行")
    socket_path = Path(os.environ.get("IWAN_HELPER_SOCKET", "/run/iwan-gateway/helper.sock"))
    HelperServer(socket_path, build_operations(), allowed_uid=_uid_from_env(), socket_gid=_gid_from_env()).serve_forever()


if __name__ == "__main__":
    main()
