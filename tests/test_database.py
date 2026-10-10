from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pymysql
import pytest

from mysql_mcp.config import load_settings
from mysql_mcp.database import execute_request


def payload(tmp_path: Path, mode: str = "query") -> dict:
    file = tmp_path / "config.toml"
    file.write_text("""[connections.local]
host="127.0.0.1"
user="fixture"
password="SYNTHETIC_SECRET"
database="fixture"
allow_write=true
allow_ddl=true
""")
    return load_settings(file, mode=mode).payload(mode + "_sql", {"sql": "SELECT 1", "max_rows": 1})


def connection(rows: list[tuple] | None = None) -> tuple[MagicMock, MagicMock, MagicMock]:
    conn, setup, cursor = MagicMock(), MagicMock(), MagicMock()
    setup.__enter__.return_value = setup
    setup.fetchone.return_value = ("STRICT_TRANS_TABLES,ANSI_QUOTES,NO_BACKSLASH_ESCAPES",)
    cursor.__enter__.return_value = cursor
    cursor.description = [("value",)]
    cursor.rowcount = 1
    cursor.fetchone.side_effect = (rows or [(1,)]) + [None]
    conn.cursor.side_effect = [setup, cursor]
    return conn, setup, cursor


def test_query_reads_only_prefix_and_closes_connection(tmp_path: Path) -> None:
    conn, setup, cursor = connection([(1,), (2,), (3,)])
    with patch("mysql_mcp.database.pymysql.connect", return_value=conn) as connect:
        result = execute_request(payload(tmp_path))
    assert result["ok"] is True
    assert result["data"]["rows"] == [[1]]
    assert result["data"]["truncated"] is True
    assert cursor.fetchone.call_count == 2
    cursor.close.assert_not_called()
    conn.close.assert_called_once()
    assert any("READ ONLY" in call.args[0] for call in setup.execute.call_args_list)
    assert connect.call_args.kwargs["local_infile"] is False
    assert connect.call_args.kwargs["read_timeout"] == 5
    assert connect.call_args.kwargs["write_timeout"] == 5


def test_invalid_read_never_connects(tmp_path: Path) -> None:
    request = payload(tmp_path)
    request["arguments"]["sql"] = "DELETE FROM example"
    with patch("mysql_mcp.database.pymysql.connect") as connect:
        assert execute_request(request)["ok"] is False
    connect.assert_not_called()


@pytest.mark.parametrize("limit", [True, 0, 1001, "20"])
def test_row_limit_is_checked_at_runtime(tmp_path: Path, limit: object) -> None:
    request = payload(tmp_path)
    request["arguments"]["max_rows"] = limit
    with patch("mysql_mcp.database.pymysql.connect") as connect:
        assert execute_request(request)["ok"] is False
    connect.assert_not_called()


def test_production_confirmation_precedes_connection(tmp_path: Path) -> None:
    request = payload(tmp_path, "write")
    request["arguments"] = {"sql": "UPDATE example SET value=1"}
    request["production"] = True
    with patch("mysql_mcp.database.pymysql.connect") as connect:
        assert execute_request(request)["error"]["outcome"] == "not_started"
    connect.assert_not_called()


def test_write_commits_and_uses_socket_timeouts(tmp_path: Path) -> None:
    request = payload(tmp_path, "write")
    request["arguments"] = {"sql": "UPDATE example SET value=1"}
    conn, setup, _ = connection()
    with patch("mysql_mcp.database.pymysql.connect", return_value=conn) as connect:
        assert execute_request(request)["ok"] is True
    conn.commit.assert_called_once()
    assert not any("READ ONLY" in call.args[0] for call in setup.execute.call_args_list)
    assert connect.call_args.kwargs["read_timeout"] > 0


def test_mysql_error_does_not_echo_sensitive_values(tmp_path: Path) -> None:
    conn, _, cursor = connection()
    cursor.execute.side_effect = pymysql.OperationalError(2013, "SYNTHETIC_SECRET /private/path")
    with patch("mysql_mcp.database.pymysql.connect", return_value=conn):
        result = execute_request(payload(tmp_path))
    assert result["ok"] is False
    assert result["error"]["mysql_errno"] == 2013
    assert "SYNTHETIC_SECRET" not in repr(result)
    conn.close.assert_called_once()
