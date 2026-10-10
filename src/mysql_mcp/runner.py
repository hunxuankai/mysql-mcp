"""Bound the entire lifetime of disposable database processes."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from contextlib import suppress
from typing import Any

import anyio

from mysql_mcp.errors import failure
from mysql_mcp.serialization import ResultError, encode_json


class OutputLimitError(ValueError):
    pass


async def _read(stream: asyncio.StreamReader, limit: int) -> bytes:
    pieces: list[bytes] = []
    size = 0
    while chunk := await stream.read(min(65536, limit + 1)):
        size += len(chunk)
        if size > limit:
            raise OutputLimitError
        pieces.append(chunk)
    return b"".join(pieces)


async def _write(stream: asyncio.StreamWriter, content: bytes) -> None:
    try:
        stream.write(content)
        await stream.drain()
    finally:
        stream.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await stream.wait_closed()


async def _discard(stream: asyncio.StreamReader | None) -> None:
    if stream is not None:
        while await stream.read(65536):
            pass


async def _cleanup(
    process: asyncio.subprocess.Process | None, tasks: list[asyncio.Task[Any]]
) -> None:
    if process is not None and process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    if process is not None:
        # A paused stdout pipe can keep Process.wait() pending even after the
        # OS process has exited. Drain the killed child's remaining pipe buffers
        # without retaining them before awaiting process completion.
        await asyncio.gather(_discard(process.stdout), _discard(process.stderr))
        await process.wait()


async def run_job(
    payload: dict[str, Any], *, timeout_seconds: float, max_output_bytes: int
) -> dict[str, Any]:
    process: asyncio.subprocess.Process | None = None
    tasks: list[asyncio.Task[Any]] = []
    mutation = payload.get("mode") in {"write", "ddl"}
    try:
        content = encode_json(payload, 262144)
        async with asyncio.timeout(timeout_seconds):
            options: dict[str, Any] = {}
            if sys.platform == "win32":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "mysql_mcp.worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **options,
            )
            assert (
                process.stdin is not None
                and process.stdout is not None
                and process.stderr is not None
            )
            tasks = [
                asyncio.create_task(_write(process.stdin, content)),
                asyncio.create_task(_read(process.stdout, max_output_bytes)),
                asyncio.create_task(_read(process.stderr, 8192)),
            ]
            _, output, _ = await asyncio.gather(*tasks)
            await process.wait()
            if process.returncode != 0:
                return failure(
                    "WORKER_FAILED",
                    "The database worker exited before returning a result.",
                    outcome="unknown" if mutation else None,
                )
            result = json.loads(output)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError
            key = "data" if result["ok"] else "error"
            if not isinstance(result.get(key), dict):
                raise ValueError
            return result
    except TimeoutError:
        return failure(
            "TIMEOUT",
            "The operation exceeded its total time budget.",
            outcome=("unknown" if process else "not_started") if mutation else None,
        )
    except OutputLimitError:
        return failure(
            "WORKER_OUTPUT_LIMIT",
            "The worker exceeded its output byte budget.",
            outcome="unknown" if mutation else None,
        )
    except ResultError:
        return failure(
            "REQUEST_LIMIT",
            "The internal request exceeds its byte budget.",
            outcome="not_started" if mutation else None,
        )
    except (ValueError, UnicodeError, OSError):
        return failure(
            "WORKER_FAILED",
            "A valid database result was not received.",
            outcome=("unknown" if process else "not_started") if mutation else None,
        )
    finally:
        with anyio.CancelScope(shield=True):
            await _cleanup(process, tasks)


class JobPool:
    """Bound active and waiting jobs, with queue time charged to each request."""

    def __init__(self, concurrency: int) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._pending = 0
        self.max_pending = concurrency * 4 + 4

    async def execute(
        self, payload: dict[str, Any], *, timeout_seconds: float, max_output_bytes: int
    ) -> dict[str, Any]:
        mutation = payload.get("mode") in {"write", "ddl"}
        if self._pending >= self.max_pending:
            return failure(
                "BUSY", "The operation queue is full.", outcome="not_started" if mutation else None
            )
        self._pending += 1
        started = False
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._semaphore:
                    started = True
                    remaining = max(0.000001, deadline - asyncio.get_running_loop().time())
                    return await run_job(
                        payload, timeout_seconds=remaining, max_output_bytes=max_output_bytes
                    )
        except TimeoutError:
            return failure(
                "TIMEOUT",
                "The operation exceeded its total time budget.",
                outcome=("unknown" if started else "not_started") if mutation else None,
            )
        finally:
            self._pending -= 1
