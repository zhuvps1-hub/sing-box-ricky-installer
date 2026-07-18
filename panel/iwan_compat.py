#!/usr/bin/env python3
"""Compatibility helpers for the custom sing-box iWAN inbound schema.

The supported Ricky-Hao iWAN core uses ``address_pool`` for the client address
pool. Older panel versions could preserve or submit the TUN-style ``address``
field, which makes the iWAN decoder fail with ``unknown field address``.

This module keeps all compatibility and validation rules in one place so the
HTTP layer, autosave layer, migration tools, and tests share the same model.
"""
from __future__ import annotations

import copy
import ipaddress
from pathlib import Path
from typing import Any

CANONICAL_POOL_FIELD = "address_pool"
LEGACY_POOL_FIELDS = ("address",)
IWAN_PATCH_FIELDS = ("listen", "listen_port", CANONICAL_POOL_FIELD, "mtu", "username", "password")


def is_iwan_inbound(value: Any) -> bool:
    return isinstance(value, dict) and (
        "iwan" in str(value.get("type", "")).lower()
        or "iwan" in str(value.get("tag", "")).lower()
    )


def _single_pool(value: Any) -> str:
    """Return one CIDR string while accepting a legacy one-item list."""
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise ValueError("iWAN 地址池只能填写一个 CIDR")
        value = value[0]
    text = str(value or "").strip()
    if not text:
        raise ValueError("iWAN 地址池不能为空")
    try:
        return str(ipaddress.ip_network(text, strict=False))
    except ValueError as exc:
        raise ValueError("iWAN 地址池必须是有效 CIDR，例如 10.10.10.0/24") from exc


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}必须是整数") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{name}必须在 {minimum} 到 {maximum} 之间")
    return number


def normalize_iwan_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Normalize browser input to the canonical iWAN field set.

    ``address`` is accepted only as a migration alias and is never emitted.
    Unknown UI fields are discarded instead of being copied into sing-box.
    """
    if not isinstance(patch, dict):
        raise ValueError("iWAN 配置必须是对象")

    normalized: dict[str, Any] = {}
    if "listen" in patch and patch["listen"] not in (None, ""):
        listen = str(patch["listen"]).strip()
        if not listen or any(character.isspace() for character in listen):
            raise ValueError("iWAN 监听地址无效")
        normalized["listen"] = listen

    if "listen_port" in patch and patch["listen_port"] not in (None, ""):
        normalized["listen_port"] = _bounded_int(patch["listen_port"], "iWAN 监听端口", 1, 65535)

    pool_value = patch.get(CANONICAL_POOL_FIELD)
    if pool_value in (None, ""):
        for legacy_key in LEGACY_POOL_FIELDS:
            if patch.get(legacy_key) not in (None, ""):
                pool_value = patch[legacy_key]
                break
    if pool_value not in (None, ""):
        normalized[CANONICAL_POOL_FIELD] = _single_pool(pool_value)

    if "mtu" in patch and patch["mtu"] not in (None, ""):
        normalized["mtu"] = _bounded_int(patch["mtu"], "iWAN MTU", 576, 9000)

    if "username" in patch:
        normalized["username"] = str(patch.get("username", "")).strip()
    if "password" in patch:
        normalized["password"] = str(patch.get("password", ""))
    return normalized


def migrate_iwan_inbound(inbound: dict[str, Any]) -> dict[str, Any]:
    """Return a copy using only the canonical pool field.

    Read-time migration is intentionally tolerant: it removes the unsupported
    field without rejecting an already-running configuration. Strict CIDR and
    numeric validation happens when the user saves a patch.
    """
    migrated = copy.deepcopy(inbound)
    if CANONICAL_POOL_FIELD not in migrated:
        for legacy_key in LEGACY_POOL_FIELDS:
            if migrated.get(legacy_key) not in (None, ""):
                value = migrated[legacy_key]
                if isinstance(value, (list, tuple)) and len(value) == 1:
                    value = value[0]
                migrated[CANONICAL_POOL_FIELD] = value
                break
    for legacy_key in LEGACY_POOL_FIELDS:
        migrated.pop(legacy_key, None)
    return migrated


def migrate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied sing-box config with legacy iWAN keys removed."""
    migrated = copy.deepcopy(config)
    inbounds = migrated.get("inbounds", [])
    if not isinstance(inbounds, list):
        return migrated
    migrated["inbounds"] = [
        migrate_iwan_inbound(item) if is_iwan_inbound(item) else item
        for item in inbounds
    ]
    return migrated


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied panel payload with a canonical iWAN patch."""
    if not isinstance(payload, dict):
        raise ValueError("配置请求必须是对象")
    normalized = copy.deepcopy(payload)
    patch = normalized.get("iwan")
    if isinstance(patch, dict) and patch:
        normalized["iwan"] = normalize_iwan_patch(patch)
    return normalized


def same_config_path(left: Any, right: Any) -> bool:
    """Compare paths without requiring either path to exist."""
    try:
        return Path(left).expanduser().absolute() == Path(right).expanduser().absolute()
    except (TypeError, ValueError, OSError):
        return False
