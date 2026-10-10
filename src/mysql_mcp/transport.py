"""Bounded UTF-8 framing around the official SDK's session streams."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, BinaryIO, TypeGuard

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import types
from mcp.shared.message import ServerMessageMetadata, SessionMessage

from mysql_mcp.config import Limits
from mysql_mcp.serialization import ResultError, encode_json


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("Non-finite JSON number.")


def _valid_id(value: object) -> TypeGuard[str | int]:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return False
    return (type(value) is int and abs(value) <= (1 << 53) - 1) or (
        isinstance(value, str) and 0 < len(value) <= 128
    )


@asynccontextmanager
async def bounded_stdio(
    limits: Limits,
) -> AsyncIterator[
    tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
    ]
]:
    wire_input: BinaryIO = sys.stdin.buffer
    wire_output: BinaryIO = sys.stdout.buffer
    original_stdout = sys.stdout
    sys.stdout = sys.stderr
    incoming, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, outgoing = anyio.create_memory_object_stream[SessionMessage](0)
    pending: set[str | int] = set()
    write_lock = anyio.Lock()
    max_pending = limits.max_concurrency * 4 + 4

    async def release_unanswered(request_id: str | int) -> None:
        # Called by the SDK only after cancellation/cleanup has settled.
        pending.discard(request_id)

    async def write(message: dict[str, Any]) -> None:
        try:
            content = encode_json(message, limits.max_response_bytes - 1) + b"\n"
        except ResultError:
            request_id = message.get("id")
            content = (
                encode_json(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id if _valid_id(request_id) else None,
                        "error": {
                            "code": -32001,
                            "message": "Response exceeds the configured byte limit.",
                        },
                    },
                    limits.max_response_bytes - 1,
                )
                + b"\n"
            )

        def send_bytes() -> None:
            wire_output.write(content)
            wire_output.flush()

        async with write_lock:
            await anyio.to_thread.run_sync(send_bytes, abandon_on_cancel=True)

    async def error(code: int, text: str, request_id: object = None) -> None:
        await write({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": text}})

    async def reader() -> None:
        async with incoming:
            while True:
                raw = await anyio.to_thread.run_sync(
                    wire_input.readline, limits.max_request_bytes + 1, abandon_on_cancel=True
                )
                if not raw:
                    return
                if len(raw) > limits.max_request_bytes:
                    while raw and not raw.endswith(b"\n"):
                        raw = await anyio.to_thread.run_sync(
                            wire_input.readline,
                            limits.max_request_bytes + 1,
                            abandon_on_cancel=True,
                        )
                    await error(-32001, "Request exceeds the configured byte limit.")
                    continue
                if not raw.strip():
                    continue
                try:
                    message = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
                except (ValueError, UnicodeError, RecursionError):
                    await error(-32700, "Invalid JSON.")
                    continue
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    await error(-32600, "Invalid JSON-RPC message.")
                    continue
                method = message.get("method")
                request_id = message.get("id")
                notification = "id" not in message and isinstance(method, str)
                if not isinstance(method, str):
                    await error(-32600, "Invalid JSON-RPC method.")
                    continue
                if notification and not method.startswith("notifications/"):
                    continue
                if not notification and not _valid_id(request_id):
                    await error(-32600, "Invalid JSON-RPC request id.")
                    continue
                if message.get("params") is not None and not isinstance(message["params"], dict):
                    if not notification:
                        await error(-32602, "Parameters must be an object.", request_id)
                    continue
                try:
                    parsed = types.jsonrpc_message_adapter.validate_python(message, by_name=False)
                except (ValueError, TypeError, RecursionError):
                    if not notification:
                        await error(-32600, "Invalid JSON-RPC message.", request_id)
                    continue
                metadata = None
                if not notification:
                    assert _valid_id(request_id)
                    if request_id in pending:
                        await error(
                            -32600, "A request with this id is already in flight.", request_id
                        )
                        continue
                    if len(pending) >= max_pending:
                        await error(-32000, "Too many pending requests.", request_id)
                        continue
                    pending.add(request_id)
                    metadata = ServerMessageMetadata(
                        on_request_unanswered=partial(release_unanswered, request_id)
                    )
                await incoming.send(SessionMessage(parsed, metadata=metadata))

    async def writer() -> None:
        async with outgoing:
            async for envelope in outgoing:
                message = envelope.message.model_dump(
                    mode="json", by_alias=True, exclude_unset=True
                )
                await write(message)
                request_id = message.get("id")
                if _valid_id(request_id):
                    pending.discard(request_id)

    try:
        async with read_stream, write_stream, anyio.create_task_group() as group:
            group.start_soon(reader)
            group.start_soon(writer)
            try:
                yield read_stream, write_stream
            finally:
                group.cancel_scope.cancel()
    finally:
        sys.stdout = original_stdout
