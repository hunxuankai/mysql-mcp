"""Opt-in MySQL fixtures with exact ownership and no business database writes."""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymysql
import pytest
from pymysql.optionfile import Parser

ROOT = Path(__file__).resolve().parents[2]


@dataclass(repr=False)
class MysqlTarget:
    host: str
    port: int
    database: str
    accounts: dict[str, tuple[str, str]] = field(repr=False)
    admin: Any = field(repr=False)

    def payload(
        self,
        operation: str,
        arguments: dict[str, Any] | None = None,
        *,
        production: bool = False,
    ) -> dict[str, Any]:
        mode = {"write_sql": "write", "ddl_sql": "ddl"}.get(operation, "query")
        user, password = self.accounts[mode]
        return {
            "connection": {
                "host": self.host,
                "port": self.port,
                "user": user,
                "password": password,
                "database": self.database,
                "tls": {"enabled": False, "ca": None, "cert": None, "key": None},
                "connect_timeout": 5,
                "read_timeout": 5,
                "write_timeout": 5,
            },
            "profile": "fixture",
            "production": production,
            "allow_write": True,
            "allow_ddl": True,
            "mode": mode,
            "operation": operation,
            "arguments": arguments or {},
            "limits": {
                "max_rows": 1000,
                "max_sql_bytes": 65536,
                "max_request_bytes": 262144,
                "max_response_bytes": 1048576,
                "max_cell_bytes": 65536,
                "request_timeout_seconds": 15,
                "max_concurrency": 2,
            },
        }

    def config(self, destination: Path) -> Path:
        user, password = self.accounts["query"]
        values = {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": user,
            "password": password,
        }
        lines = ["[connections.fixture]"]
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in values.items())
        lines.extend(["[connections.fixture.tls]", "enabled = false"])
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return destination


def admin_settings() -> dict[str, Any]:
    defaults_file = os.environ.get("MYSQL_MCP_TEST_DEFAULTS_FILE")
    if defaults_file:
        parser = Parser()
        parser.read(defaults_file, encoding="utf-8-sig")
        values: dict[str, Any] = {
            key: parser.get("client", key) for key in ("host", "port", "user", "password")
        }
        values["port"] = int(values["port"])
    else:
        if "MYSQL_MCP_TEST_USER" not in os.environ or "MYSQL_MCP_TEST_PASSWORD" not in os.environ:
            pytest.fail("Explicit MySQL integration credentials are required.")
        values = {
            "host": os.environ.get("MYSQL_MCP_TEST_HOST", "127.0.0.1"),
            "port": int(os.environ.get("MYSQL_MCP_TEST_PORT", "3306")),
            "user": os.environ["MYSQL_MCP_TEST_USER"],
            "password": os.environ["MYSQL_MCP_TEST_PASSWORD"],
        }
    values.update(
        charset="utf8mb4", autocommit=True, connect_timeout=5, read_timeout=10, write_timeout=5
    )
    return values


@pytest.fixture(scope="session")
def mysql_target() -> Iterator[MysqlTarget]:
    if os.environ.get("MYSQL_MCP_INTEGRATION") != "1":
        pytest.skip("Set MYSQL_MCP_INTEGRATION=1 for a disposable MySQL database.")
    settings = admin_settings()
    try:
        admin = pymysql.connect(**settings)
    except pymysql.MySQLError:
        pytest.fail(
            "Cannot connect to the explicitly configured MySQL test instance.", pytrace=False
        )
    identifier = uuid4().hex
    database = f"mcp_test_{identifier}"
    accounts = {
        mode: (f"mcp_{identifier[:12]}_{mode[0]}", secrets.token_urlsafe(24))
        for mode in ("query", "write", "ddl")
    }
    permissions = {
        "query": "SELECT, SHOW VIEW",
        "write": "SELECT, INSERT, UPDATE, DELETE",
        "ddl": "CREATE, ALTER, DROP, INDEX, REFERENCES",
    }
    created_users: list[str] = []
    created_database = False
    record: dict[str, Any] = {
        "database": database,
        "users": [],
        "mysql_version": admin.get_server_info(),
        "cleaned": False,
    }
    manifest = ROOT / ".local/integration-schema.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with admin.cursor() as cursor:
            # No IF NOT EXISTS: successful creation establishes ownership.
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
            created_database = True
            for mode, (user, password) in accounts.items():
                cursor.execute("CREATE USER %s@'%%' IDENTIFIED BY %s", (user, password))
                created_users.append(user)
                cursor.execute(f"GRANT {permissions[mode]} ON `{database}`.* TO %s@'%%'", (user,))
        record["users"] = created_users.copy()
        manifest.write_text(json.dumps(record), encoding="utf-8")
        yield MysqlTarget(settings["host"], settings["port"], database, accounts, admin)
    finally:
        try:
            admin.rollback()
            with admin.cursor() as cursor:
                if created_database:
                    cursor.execute(f"DROP DATABASE `{database}`")
                for user in created_users:
                    cursor.execute("DROP USER %s@'%%'", (user,))
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s",
                    (database,),
                )
                record["database_remaining"] = cursor.fetchone()[0]
                for user in created_users:
                    cursor.execute(
                        "SELECT COUNT(*) FROM mysql.user WHERE User=%s AND Host='%%'", (user,)
                    )
                    if cursor.fetchone()[0] != 0:
                        raise AssertionError("An owned integration account was not removed.")
            record["users"] = created_users.copy()
            record["cleaned"] = record.get("database_remaining") == 0
            manifest.write_text(json.dumps(record), encoding="utf-8")
        finally:
            admin.close()
