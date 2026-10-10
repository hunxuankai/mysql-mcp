from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        """[connections.local]
host="127.0.0.1"
port=1
user="fixture"
password="SECRET_SENTINEL"
database="fixture"
""",
        encoding="utf-8",
    )
    return path


async def send(process: asyncio.subprocess.Process, message: object) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message).encode() + b"\n")
    await process.stdin.drain()


async def receive(process: asyncio.subprocess.Process) -> dict:
    assert process.stdout is not None
    line = await asyncio.wait_for(process.stdout.readline(), 8)
    assert line, "Server unexpectedly closed stdout."
    return json.loads(line)


@pytest.fixture
async def process(config_file: Path) -> AsyncIterator[asyncio.subprocess.Process]:
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mysql_mcp",
        "--config",
        str(config_file),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await send(
            child,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert (await receive(child))["id"] == 1
        await send(child, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        yield child
    finally:
        if child.stdin is not None:
            child.stdin.close()
        try:
            await asyncio.wait_for(child.wait(), 3)
        except TimeoutError:
            child.kill()
            await child.wait()


@pytest.mark.parametrize(
    "bad", [[], None, 42, {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": 42}]
)
async def test_invalid_message_then_ping(process: asyncio.subprocess.Process, bad: object) -> None:
    await send(process, bad)
    assert "error" in await receive(process)
    await send(process, {"jsonrpc": "2.0", "id": 9, "method": "ping"})
    assert (await receive(process))["id"] == 9


async def test_invalid_json_then_ping(process: asyncio.subprocess.Process) -> None:
    assert process.stdin is not None
    process.stdin.write(b"{broken\n")
    await process.stdin.drain()
    assert (await receive(process))["error"]["code"] == -32700
    await send(process, {"jsonrpc": "2.0", "id": 9, "method": "ping"})
    assert (await receive(process))["id"] == 9


async def test_oversized_line_then_ping(process: asyncio.subprocess.Process) -> None:
    assert process.stdin is not None
    process.stdin.write(b"x" * 270000 + b"\n")
    await process.stdin.drain()
    assert "error" in await receive(process)
    await send(process, {"jsonrpc": "2.0", "id": 9, "method": "ping"})
    assert (await receive(process))["id"] == 9


async def test_notification_has_no_reply(process: asyncio.subprocess.Process) -> None:
    await send(process, {"jsonrpc": "2.0", "method": "ping"})
    await send(process, {"jsonrpc": "2.0", "id": 9, "method": "ping"})
    assert (await receive(process))["id"] == 9


@pytest.mark.parametrize("identifier", [chr(0xD800), chr(0xDFFF)])
async def test_invalid_utf8_id_then_ping(
    process: asyncio.subprocess.Process, identifier: str
) -> None:
    await send(process, {"jsonrpc": "2.0", "id": identifier, "method": "ping"})
    response = await receive(process)
    assert "error" in response
    assert response["id"] is None
    await send(process, {"jsonrpc": "2.0", "id": 19, "method": "ping"})
    assert (await receive(process))["id"] == 19


async def test_repeated_cancellations_release_pending_slots(
    config_file: Path, tmp_path: Path
) -> None:
    with config_file.open("a", encoding="utf-8") as stream:
        stream.write("\n[limits]\nmax_concurrency=1\n")
    marker = tmp_path / "completed.txt"
    launcher = tmp_path / "cancellable_server.py"
    launcher.write_text(
        """import asyncio, os
from pathlib import Path
from mysql_mcp.runner import JobPool
from mysql_mcp.server import main
async def wait_for_cancel(self, payload, **kwargs):
    try:
        await asyncio.sleep(60)
    finally:
        with Path(os.environ["TEST_CANCEL_MARKER"]).open("a") as stream:
            stream.write("finished\\n")
JobPool.execute = wait_for_cancel
raise SystemExit(main())
""",
        encoding="utf-8",
    )
    env = {**os.environ, "TEST_CANCEL_MARKER": str(marker)}
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        str(launcher),
        "--config",
        str(config_file),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await send(
            child,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "cancellation-test", "version": "1"},
                },
            },
        )
        await receive(child)
        await send(child, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        for index in range(8):
            identifier = index + 2
            await send(
                child,
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "method": "tools/call",
                    "params": {
                        "name": "query_sql",
                        "arguments": {"sql": "SELECT 1"},
                    },
                },
            )
            # A ping confirms the earlier request was dispatched before cancelling it.
            await send(child, {"jsonrpc": "2.0", "id": 100 + index, "method": "ping"})
            assert (await receive(child))["id"] == 100 + index
            await send(
                child,
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {
                        "requestId": identifier,
                    },
                },
            )
            async with asyncio.timeout(5):
                while not marker.exists() or len(marker.read_text().splitlines()) <= index:
                    await asyncio.sleep(0.01)
        await send(child, {"jsonrpc": "2.0", "id": 999, "method": "ping"})
        response = await receive(child)
        assert response["id"] == 999 and "result" in response
    finally:
        if child.stdin:
            child.stdin.close()
        try:
            await asyncio.wait_for(child.wait(), 3)
        except TimeoutError:
            child.kill()
            await child.wait()


async def test_official_client_and_schema_validation(config_file: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mysql_mcp", "--config", str(config_file)]
    )
    async with (
        asyncio.timeout(20),
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=10) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "query_sql",
            "describe_table",
            "show_create_table",
            "list_tables",
        }
        rejected = await session.call_tool("query_sql", {"sql": "SELECT 1", "max_rows": True})
        assert rejected.is_error is True
        rejected_sql = await session.call_tool("query_sql", {"sql": "DELETE FROM example"})
        assert rejected_sql.is_error is True
        assert "SECRET_SENTINEL" not in str(rejected_sql)
        await session.send_ping()


def test_cli_help_version_and_check_config(config_file: Path) -> None:
    for args in (["--help"], ["--version"], ["--config", str(config_file), "--check-config"]):
        result = subprocess.run(
            [sys.executable, "-m", "mysql_mcp", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "SECRET_SENTINEL" not in result.stdout + result.stderr
