"""Real MySQL compatibility tests; all state belongs to the session fixture."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymysql
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mysql_mcp.database import execute_request
from mysql_mcp.runner import run_job

pytestmark = pytest.mark.integration


def success(target: Any, operation: str, **arguments: Any) -> dict[str, Any]:
    result = execute_request(target.payload(operation, arguments))
    assert result["ok"] is True, result
    return result["data"]


def table(target: Any) -> str:
    name = f"fixture_{uuid4().hex[:12]}"
    success(target, "ddl_sql", sql=f"CREATE TABLE `{name}` (id INT PRIMARY KEY, label VARCHAR(64))")
    return name


def test_cte_decimal_time_binary_and_large_integer(mysql_target: Any) -> None:
    data = success(
        mysql_target,
        "query_sql",
        sql=(
            "WITH q AS (SELECT 1 AS n) SELECT n, CAST('0.100' AS DECIMAL(20,3)), "
            "CAST('27:05:04.123456' AS TIME(6)), CAST('-00:00:00.000001' AS TIME(6)), "
            "UNHEX('4142'), CAST('18446744073709551615' AS UNSIGNED) FROM q"
        ),
    )
    assert data["rows"] == [
        [
            1,
            "0.100",
            "27:05:04.123456",
            "-00:00:00.000001",
            {"type": "binary", "encoding": "base64", "data": "QUI="},
            "18446744073709551615",
        ]
    ]


def test_all_modes_with_database_enforced_minimum_accounts(mysql_target: Any) -> None:
    name = table(mysql_target)
    success(mysql_target, "write_sql", sql=f"INSERT INTO `{name}` VALUES (1, 'first')")
    success(mysql_target, "write_sql", sql=f"REPLACE INTO `{name}` VALUES (1, 'second')")
    success(mysql_target, "write_sql", sql=f"UPDATE `{name}` SET label='third' WHERE id=1")
    data = success(mysql_target, "query_sql", sql=f"SELECT id, label FROM `{name}`")
    assert data["rows"] == [[1, "third"]]
    reader = mysql_target.payload("query_sql")["connection"]
    with pymysql.connect(
        host=reader["host"],
        port=reader["port"],
        user=reader["user"],
        password=reader["password"],
        database=reader["database"],
        connect_timeout=5,
        read_timeout=5,
        write_timeout=5,
    ) as connection:
        with connection.cursor() as cursor, pytest.raises(pymysql.MySQLError) as failure:
            cursor.execute(f"INSERT INTO `{name}` VALUES (2, 'forbidden')")
        assert failure.value.args[0] == 1142
    success(mysql_target, "write_sql", sql=f"DELETE FROM `{name}` WHERE id=1")
    success(mysql_target, "ddl_sql", sql=f"DROP TABLE `{name}`")


def test_metadata_names_and_schema_evolution(mysql_target: Any) -> None:
    name = "表 空格`" + uuid4().hex[:8]
    quoted = "`" + name.replace("`", "``") + "`"
    success(mysql_target, "ddl_sql", sql=f"CREATE TABLE {quoted} (id INT)")
    success(mysql_target, "ddl_sql", sql=f"ALTER TABLE {quoted} ADD COLUMN label VARCHAR(64)")
    names = success(mysql_target, "list_tables")["rows"]
    assert any(name in row for row in names)
    assert success(mysql_target, "describe_table", table=name)["row_count"] == 2
    assert success(mysql_target, "show_create_table", table=name)["row_count"] == 1
    success(mysql_target, "ddl_sql", sql=f"TRUNCATE TABLE {quoted}")
    success(mysql_target, "ddl_sql", sql=f"DROP TABLE {quoted}")


def test_production_confirmation_preserves_data_until_confirmed(mysql_target: Any) -> None:
    name = table(mysql_target)
    payload = mysql_target.payload(
        "write_sql", {"sql": f"INSERT INTO `{name}` VALUES (1, 'confirmed')"}, production=True
    )
    assert execute_request(payload)["ok"] is False
    assert success(mysql_target, "query_sql", sql=f"SELECT COUNT(*) FROM `{name}`")["rows"] == [[0]]
    payload["arguments"]["confirm_environment"] = "production"
    assert execute_request(payload)["ok"] is True
    assert success(mysql_target, "query_sql", sql=f"SELECT COUNT(*) FROM `{name}`")["rows"] == [[1]]


def test_stream_truncation_and_next_connection(mysql_target: Any) -> None:
    query = (
        "WITH RECURSIVE q(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM q WHERE n<250) SELECT n FROM q"
    )
    data = success(mysql_target, "query_sql", sql=query, max_rows=3)
    assert data["row_count"] == 3
    assert data["truncated"] is True
    assert success(mysql_target, "query_sql", sql="SELECT 42")["rows"] == [[42]]


def test_oversized_value_has_a_controlled_error(mysql_target: Any) -> None:
    result = execute_request(
        mysql_target.payload("query_sql", {"sql": "SELECT REPEAT('x', 65537)"})
    )
    assert result["ok"] is False
    assert "x" * 100 not in json.dumps(result)


@pytest.mark.parametrize(
    ("operation", "sql"),
    [
        ("query_sql", "SELECT * FROM mysql.user"),
        ("query_sql", "SELECT 1; DELETE FROM example"),
        ("query_sql", "SELECT LOAD_FILE('/not-read')"),
        ("ddl_sql", "CREATE USER 'not_created'@'localhost'"),
        ("ddl_sql", "DROP DATABASE information_schema"),
    ],
)
def test_forbidden_operations_are_rejected(mysql_target: Any, operation: str, sql: str) -> None:
    result = execute_request(mysql_target.payload(operation, {"sql": sql}))
    assert result["ok"] is False
    assert result["error"].get("mysql_errno") is None


async def test_locked_mutation_has_a_total_deadline_and_unknown_outcome(mysql_target: Any) -> None:
    name = table(mysql_target)
    success(mysql_target, "write_sql", sql=f"INSERT INTO `{name}` VALUES (1, 'original')")
    admin = mysql_target.admin
    admin.begin()
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM `{mysql_target.database}`.`{name}` WHERE id=1 FOR UPDATE"
            )
        payload = mysql_target.payload(
            "write_sql", {"sql": f"UPDATE `{name}` SET label='blocked' WHERE id=1"}
        )
        started = time.monotonic()
        result = await run_job(payload, timeout_seconds=1.5, max_output_bytes=1048576)
        assert result["ok"] is False
        assert result["error"]["outcome"] == "unknown"
        assert time.monotonic() - started < 6
    finally:
        admin.rollback()
    assert success(mysql_target, "query_sql", sql=f"SELECT label FROM `{name}`")["rows"] == [
        ["original"]
    ]


async def test_official_sdk_client_reads_real_types(mysql_target: Any, tmp_path: Path) -> None:
    config = mysql_target.config(tmp_path / "config.toml")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mysql_mcp", "--config", str(config), "--connection", "fixture"],
    )
    async with (
        asyncio.timeout(30),
        stdio_client(parameters) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=15) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        assert {tool.name for tool in listed.tools} == {
            "list_tables",
            "describe_table",
            "show_create_table",
            "query_sql",
        }
        result = await session.call_tool(
            "query_sql", {"sql": "SELECT CAST('27:00:00' AS TIME), UNHEX('4142')"}
        )
        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["ok"] is True
        assert result.structured_content["data"]["rows"] == [
            ["27:00:00", {"type": "binary", "encoding": "base64", "data": "QUI="}]
        ]
        await session.send_ping()
