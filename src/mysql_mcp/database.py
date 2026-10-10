"""One connection and one explicitly authorized database operation per request."""

from __future__ import annotations

import ssl
from contextlib import suppress
from typing import Any

import pymysql
from pymysql.cursors import SSCursor

from mysql_mcp.config import ConfigurationError, Limits, parse_limits
from mysql_mcp.errors import failure
from mysql_mcp.policy import PolicyError, quote_identifier, validate_sql
from mysql_mcp.serialization import ResultError, encode_json, encode_value

OPERATIONS = {
    "query": {"list_tables", "describe_table", "show_create_table", "query_sql"},
    "write": {"write_sql"},
    "ddl": {"ddl_sql"},
}


def _statement(payload: dict[str, Any], limits: Limits) -> tuple[str, int]:
    mode, operation = payload["mode"], payload["operation"]
    if mode not in OPERATIONS or operation not in OPERATIONS[mode]:
        raise PolicyError("Tool is not available in the configured mode.")
    args = payload.get("arguments", {})
    if not isinstance(args, dict):
        raise PolicyError("Tool arguments must be an object.")
    if mode in {"write", "ddl"}:
        if payload.get("allow_" + mode) is not True:
            raise PolicyError("Mutation mode is not enabled.")
        if payload.get("production") is True and args.get("confirm_environment") != "production":
            raise PolicyError("Production mutation requires explicit production confirmation.")
    row_limit = args.get("max_rows", min(200, limits.max_rows))
    if type(row_limit) is not int or not 1 <= row_limit <= limits.max_rows:
        raise PolicyError("max_rows must be an integer within the configured row limit.")
    if operation == "list_tables":
        sql = "SHOW TABLES"
        row_limit = limits.max_rows
    elif operation in {"describe_table", "show_create_table"}:
        table = args.get("table")
        if not isinstance(table, str):
            raise PolicyError("A table name is required.")
        prefix = "DESCRIBE " if operation == "describe_table" else "SHOW CREATE TABLE "
        sql = prefix + quote_identifier(table)
        row_limit = limits.max_rows
    else:
        candidate = args.get("sql")
        if not isinstance(candidate, str):
            raise PolicyError("SQL must be a string.")
        sql = candidate
    database = payload["connection"]["database"]
    return validate_sql(sql, mode, database, limits.max_sql_bytes), row_limit


def _connection_options(source: dict[str, Any]) -> dict[str, Any]:
    result = {key: source[key] for key in ("host", "port", "user", "password", "database")}
    for key in ("connect_timeout", "read_timeout", "write_timeout"):
        value = source.get(key, 5)
        if type(value) not in (int, float) or not 0 < value <= 120:
            raise ConfigurationError("Database transport timeout is invalid.")
        result[key] = value
    result.update(charset="utf8mb4", autocommit=False, client_flag=0, local_infile=False)
    tls = source.get("tls", {})
    if tls.get("enabled") is True:
        context = ssl.create_default_context(cafile=tls.get("ca"))
        if tls.get("cert"):
            context.load_cert_chain(tls["cert"], tls.get("key"))
        result["ssl"] = context
    return result


def _initialize(connection: Any, mode: str, limits: Limits) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@SESSION.sql_mode")
        result = cursor.fetchone()
        modes = (result[0] or "").split(",") if result else []
        modes = [
            item for item in modes if item.upper() not in {"ANSI_QUOTES", "NO_BACKSLASH_ESCAPES"}
        ]
        cursor.execute("SET SESSION sql_mode = %s", (",".join(modes),))
        if mode == "query":
            cursor.execute(
                "SET SESSION MAX_EXECUTION_TIME = %s",
                (max(1, min(10000, int(limits.request_timeout_seconds * 1000))),),
            )
            cursor.execute("START TRANSACTION READ ONLY")


def _read(cursor: Any, limit: int, limits: Limits) -> dict[str, Any]:
    columns = [str(item[0]) for item in cursor.description or []]
    budget = len(encode_json(columns, limits.max_response_bytes)) + 128
    rows: list[list[Any]] = []
    truncated = False
    for index in range(limit + 1):
        row = cursor.fetchone()
        if row is None:
            break
        if index == limit:
            truncated = True
            break
        encoded = [encode_value(value, limits.max_cell_bytes) for value in row]
        budget += len(encode_json(encoded, limits.max_response_bytes)) + 1
        if budget > limits.max_response_bytes:
            raise ResultError("Result exceeds the configured byte limit.")
        rows.append(encoded)
    result = {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}
    encode_json(result, limits.max_response_bytes)
    return result


def _close(connection: Any, cursor: Any) -> None:
    # PyMySQL has no public SSCursor abort. Close its one-shot transport first;
    # then retire the result so destructors cannot drain a closed socket.
    with suppress(Exception):
        connection.close()
    if cursor is not None:
        result = getattr(cursor, "_result", None)
        if result is not None:
            result.unbuffered_active = False
            result.connection = None
        cursor.connection = None


def execute_request(payload: dict[str, Any]) -> dict[str, Any]:
    connection: Any = None
    cursor: Any = None
    mutation = payload.get("mode") in {"write", "ddl"}
    sent = False
    try:
        limits = parse_limits(payload.get("limits", {}))
        sql, row_limit = _statement(payload, limits)
        connection = pymysql.connect(**_connection_options(payload["connection"]))
        _initialize(connection, payload["mode"], limits)
        if not mutation:
            cursor = connection.cursor(SSCursor)
            cursor.execute(sql)
            data = _read(cursor, row_limit, limits)
        else:
            cursor = connection.cursor()
            sent = True
            cursor.execute(sql)
            connection.commit()
            data = (
                {"rows_affected": int(cursor.rowcount)}
                if payload["mode"] == "write"
                else {"status": "ok"}
            )
        return {"ok": True, "data": data}
    except PolicyError as exc:
        return failure("SQL_REJECTED", str(exc), outcome="not_started" if mutation else None)
    except ConfigurationError:
        return failure(
            "INVALID_CONFIG",
            "Database configuration or limits are invalid.",
            outcome="not_started" if mutation else None,
        )
    except ResultError:
        return failure("RESULT_LIMIT", "Result cannot be represented within the configured limits.")
    except pymysql.MySQLError as exc:
        number = exc.args[0] if exc.args and type(exc.args[0]) is int else None
        return failure(
            "MYSQL_ERROR",
            "MySQL could not complete the operation.",
            mysql_errno=number,
            outcome=("unknown" if sent else "not_started") if mutation else None,
        )
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        return failure(
            "INVALID_REQUEST",
            "The database request could not be processed.",
            outcome=("unknown" if sent else "not_started") if mutation else None,
        )
    finally:
        if connection is not None:
            _close(connection, cursor)
