"""CLI and official MCP SDK integration."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from mcp import types
from mcp.server import Server, ServerRequestContext

from mysql_mcp import __version__
from mysql_mcp.config import ConfigurationError, Settings, load_settings
from mysql_mcp.errors import failure
from mysql_mcp.runner import JobPool
from mysql_mcp.serialization import ResultError, encode_json
from mysql_mcp.transport import bounded_stdio

LOGGER = logging.getLogger("mysql_mcp.operations")


def tool_definitions(settings: Settings) -> list[types.Tool]:
    definitions: list[tuple[str, str, dict[str, Any], list[str]]]
    sql = {"type": "string", "minLength": 1, "maxLength": settings.limits.max_sql_bytes}
    table = {"type": "string", "minLength": 1, "maxLength": 64}
    if settings.mode == "query":
        definitions = [
            ("list_tables", "List tables in the configured database.", {}, []),
            (
                "describe_table",
                "Describe one table in the configured database.",
                {"table": table},
                ["table"],
            ),
            (
                "show_create_table",
                "Show the CREATE TABLE definition of one table.",
                {"table": table},
                ["table"],
            ),
            (
                "query_sql",
                "Execute one supported read-only MySQL statement.",
                {
                    "sql": sql,
                    "max_rows": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": settings.limits.max_rows,
                        "default": min(200, settings.limits.max_rows),
                    },
                },
                ["sql"],
            ),
        ]
    else:
        definitions = [
            (
                settings.mode + "_sql",
                "Execute one explicitly enabled database mutation.",
                {"sql": sql},
                ["sql"],
            )
        ]
    tools = []
    for name, description, properties, required in definitions:
        if settings.production and settings.mode != "query":
            properties = {
                **properties,
                "confirm_environment": {"type": "string", "const": "production"},
            }
            required = [*required, "confirm_environment"]
        readonly = settings.mode == "query"
        tools.append(
            types.Tool.model_validate(
                {
                    "name": name,
                    "description": f"Connection: {settings.profile}; "
                    f"database: {settings.connection['database']}; "
                    f"mode: {settings.mode}; production: {settings.production}. {description}",
                    "inputSchema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                    "annotations": {
                        "readOnlyHint": readonly,
                        "destructiveHint": not readonly,
                        "idempotentHint": readonly,
                        "openWorldHint": False,
                    },
                }
            )
        )
    return tools


def _result(settings: Settings, value: dict[str, Any]) -> types.CallToolResult:
    data = {**value, "mcp_context": settings.public_context}
    encoded = encode_json(data, settings.limits.max_response_bytes).decode("utf-8")
    return types.CallToolResult.model_validate(
        {
            "content": [{"type": "text", "text": encoded}],
            "structuredContent": data,
            "isError": not value.get("ok", False),
        }
    )


def create_server(settings: Settings) -> Server[None]:
    tools = tool_definitions(settings)
    schemas = {tool.name: Draft202012Validator(tool.input_schema) for tool in tools}
    pool = JobPool(settings.limits.max_concurrency)

    async def list_tools(
        ctx: ServerRequestContext[None], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(
        ctx: ServerRequestContext[None], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        arguments = params.arguments or {}
        if params.name not in schemas or not schemas[params.name].is_valid(arguments):
            return _result(
                settings, failure("INVALID_ARGUMENT", "Tool name or arguments are invalid.")
            )
        start = time.monotonic()
        LOGGER.info(
            json.dumps({"event": "operation_start", "mode": settings.mode, "tool": params.name})
        )
        try:
            outcome = await pool.execute(
                settings.payload(params.name, arguments),
                timeout_seconds=settings.limits.request_timeout_seconds,
                max_output_bytes=settings.limits.max_response_bytes,
            )
            result = _result(settings, outcome)
            encode_json(
                result.model_dump(mode="json", by_alias=True, exclude_unset=True),
                settings.limits.max_response_bytes - 512,
            )
            LOGGER.info(
                json.dumps(
                    {
                        "event": "operation_end",
                        "mode": settings.mode,
                        "tool": params.name,
                        "ok": outcome["ok"],
                        "elapsed_ms": round((time.monotonic() - start) * 1000),
                    }
                )
            )
            return result
        except ResultError:
            return _result(
                settings, failure("RESULT_LIMIT", "Result exceeds the protocol byte budget.")
            )
        except asyncio.CancelledError:
            LOGGER.info(
                json.dumps(
                    {"event": "operation_cancelled", "mode": settings.mode, "tool": params.name}
                )
            )
            raise
        except Exception:
            return _result(
                settings, failure("INTERNAL_ERROR", "The operation could not be completed.")
            )

    return Server(
        "mysql-mcp-router", version=__version__, on_list_tools=list_tools, on_call_tool=call_tool
    )


async def serve(settings: Settings) -> None:
    server = create_server(settings)
    async with bounded_stdio(settings.limits) as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Local MySQL MCP server with explicit SQL modes.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, default=os.environ.get("MYSQL_MCP_CONFIG"))
    parser.add_argument("--connection", default="local")
    parser.add_argument("--mode", choices=("query", "write", "ddl"), default="query")
    parser.add_argument("--check-config", action="store_true")
    options = parser.parse_args()
    try:
        if options.config is None:
            raise ConfigurationError("Provide --config or MYSQL_MCP_CONFIG.")
        settings = load_settings(Path(options.config), options.connection, options.mode)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if options.check_config:
        print(json.dumps({"valid": True, **settings.public_context}))
        return 0
    for namespace in ("mcp", "mcp_types"):
        logger = logging.getLogger(namespace)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.CRITICAL)
    LOGGER.handlers = [logging.StreamHandler(sys.stderr)]
    LOGGER.propagate = False
    LOGGER.setLevel(logging.INFO)
    try:
        asyncio.run(serve(settings))
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("MCP server stopped because its transport could not continue.", file=sys.stderr)
        return 1
    return 0
