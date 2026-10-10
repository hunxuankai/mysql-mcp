"""Explicit local configuration with no discovery of credentials or external caches."""

from __future__ import annotations

import copy
import ipaddress
import math
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """A sanitized, user-actionable configuration error."""


@dataclass(frozen=True)
class Limits:
    max_rows: int = 1000
    max_sql_bytes: int = 65536
    max_request_bytes: int = 262144
    max_response_bytes: int = 1048576
    max_cell_bytes: int = 65536
    request_timeout_seconds: float = 15
    max_concurrency: int = 2


@dataclass(frozen=True, repr=False)
class Settings:
    profile: str
    mode: str
    connection: dict[str, Any]
    production: bool
    allow_write: bool
    allow_ddl: bool
    limits: Limits

    @property
    def public_context(self) -> dict[str, str]:
        return {"profile": self.profile, "mode": self.mode, "database": self.connection["database"]}

    def payload(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "connection": copy.deepcopy(self.connection),
            "profile": self.profile,
            "production": self.production,
            "allow_write": self.allow_write,
            "allow_ddl": self.allow_ddl,
            "mode": self.mode,
            "operation": operation,
            "arguments": copy.deepcopy(arguments),
            "limits": asdict(self.limits),
        }


def _mapping(value: object, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or any(key not in allowed for key in value):
        raise ConfigurationError("Configuration contains invalid or unknown fields.")
    return dict(value)


def _string(source: dict[str, Any], key: str, limit: int, *, empty: bool = False) -> str:
    value = source.get(key)
    if (
        not isinstance(value, str)
        or len(value) > limit
        or "\x00" in value
        or (not empty and not value.strip())
    ):
        raise ConfigurationError(f"A valid {key} field is required.")
    return value if key == "password" else value.strip()


def _boolean(source: dict[str, Any], key: str, default: bool) -> bool:
    value = source.get(key, default)
    if type(value) is not bool:
        raise ConfigurationError(f"The {key} field must be a boolean.")
    return value


def parse_limits(source: object) -> Limits:
    defaults = asdict(Limits())
    values = _mapping(source, set(defaults))
    bounds = {
        "max_rows": (1, 1000),
        "max_sql_bytes": (1, 65536),
        "max_request_bytes": (1024, 262144),
        "max_response_bytes": (16384, 1048576),
        "max_cell_bytes": (1, 65536),
        "max_concurrency": (1, 8),
    }
    for key, (lower, upper) in bounds.items():
        value = values.get(key, defaults[key])
        if type(value) is not int or not lower <= value <= upper:
            raise ConfigurationError(f"The {key} limit is outside its supported integer range.")
        defaults[key] = value
    timeout = values.get("request_timeout_seconds", 15)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ConfigurationError(
            "The request timeout must be finite, positive and at most 120 seconds."
        )
    defaults["request_timeout_seconds"] = float(timeout)
    return Limits(**defaults)


def _tls(source: object, host: str, directory: Path) -> dict[str, Any]:
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.lower() == "localhost"
    options = _mapping(source, {"enabled", "ca", "cert", "key"})
    enabled = _boolean(options, "enabled", not local)
    result: dict[str, Any] = {"enabled": enabled, "ca": None, "cert": None, "key": None}
    for key in ("ca", "cert", "key"):
        value = options.get(key)
        if value is not None:
            if not enabled or not isinstance(value, str) or not value:
                raise ConfigurationError("TLS certificate settings are invalid.")
            try:
                path = (directory / value).resolve()
                if not path.is_file():
                    raise ConfigurationError("A configured TLS file is not available.")
            except (OSError, ValueError) as exc:
                raise ConfigurationError("A configured TLS file is not available.") from exc
            result[key] = str(path)
    if bool(result["cert"]) != bool(result["key"]):
        raise ConfigurationError("TLS client certificate and key must be provided together.")
    return result


def load_settings(path: Path, profile: str = "local", mode: str = "query") -> Settings:
    if mode not in {"query", "write", "ddl"}:
        raise ConfigurationError("Unsupported SQL mode.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", profile):
        raise ConfigurationError("The connection profile name is invalid.")
    try:
        with path.open("rb") as stream:
            raw = stream.read(524289)
        if len(raw) > 524288:
            raise ConfigurationError("Configuration file is too large.")
        parsed = tomllib.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError("Configuration file cannot be read as valid UTF-8 TOML.") from exc
    root = _mapping(parsed, {"connections", "limits"})
    connections = root.get("connections")
    if not isinstance(connections, dict) or profile not in connections:
        raise ConfigurationError("The selected connection profile is not configured.")
    source = _mapping(
        connections[profile],
        {
            "host",
            "port",
            "database",
            "user",
            "password",
            "production",
            "allow_write",
            "allow_ddl",
            "accounts",
            "tls",
        },
    )
    host = _string(source, "host", 255)
    database = _string(source, "database", 64)
    port = source.get("port", 3306)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigurationError("The port must be an integer between 1 and 65535.")
    production = _boolean(source, "production", False)
    allow_write = _boolean(source, "allow_write", False)
    allow_ddl = _boolean(source, "allow_ddl", False)
    if (mode == "write" and not allow_write) or (mode == "ddl" and not allow_ddl):
        raise ConfigurationError(
            "The selected mutation mode must be explicitly enabled in configuration."
        )
    credentials = {
        "user": _string(source, "user", 128),
        "password": _string(source, "password", 4096, empty=True),
    }
    accounts = _mapping(source.get("accounts", {}), {"query", "write", "ddl"})
    for account_mode, account in accounts.items():
        values = _mapping(account, {"user", "password"})
        normalized = {
            "user": _string(values, "user", 128),
            "password": _string(values, "password", 4096, empty=True),
        }
        if account_mode == mode:
            credentials = normalized
    connection = {
        "host": host,
        "port": port,
        "database": database,
        **credentials,
        "tls": _tls(source.get("tls", {}), host, path.resolve().parent),
        "connect_timeout": 5,
        "read_timeout": 5,
        "write_timeout": 5,
    }
    return Settings(
        profile,
        mode,
        connection,
        production,
        allow_write,
        allow_ddl,
        parse_limits(root.get("limits", {})),
    )
