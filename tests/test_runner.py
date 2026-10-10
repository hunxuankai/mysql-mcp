from __future__ import annotations

import asyncio
import sys
import time
from unittest.mock import AsyncMock

import pytest

from mysql_mcp import runner


def launch_fixture(monkeypatch: pytest.MonkeyPatch, code: str) -> tuple[list, asyncio.Event]:
    original = asyncio.create_subprocess_exec
    processes: list = []
    started = asyncio.Event()

    async def create(*args: object, **kwargs: object):
        process = await original(sys.executable, "-c", code, **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create)
    return processes, started


async def test_deadline_kills_and_reaps_only_its_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    processes, _ = launch_fixture(monkeypatch, "import sys,time; sys.stdin.read(); time.sleep(60)")
    start = time.monotonic()
    result = await runner.run_job({"mode": "write"}, timeout_seconds=0.3, max_output_bytes=1024)
    assert result["ok"] is False
    assert result["error"]["outcome"] == "unknown"
    assert time.monotonic() - start < 5
    assert processes[0].returncode is not None


async def test_cancellation_reaps_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    processes, started = launch_fixture(
        monkeypatch, "import sys,time; sys.stdin.read(); time.sleep(60)"
    )
    task = asyncio.create_task(
        runner.run_job({"mode": "query"}, timeout_seconds=10, max_output_bytes=1024)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None


async def test_excess_output_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    processes, _ = launch_fixture(monkeypatch, "import sys; sys.stdin.read(); print('x'*1000000)")
    result = await runner.run_job({"mode": "query"}, timeout_seconds=5, max_output_bytes=1024)
    assert result["ok"] is False
    assert len(str(result)) < 1024
    assert processes[0].returncode is not None


async def test_stderr_and_invalid_output_do_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    launch_fixture(
        monkeypatch,
        "import sys; sys.stdin.read(); print('SECRET_SENTINEL',file=sys.stderr); print('not-json')",
    )
    result = await runner.run_job({"mode": "query"}, timeout_seconds=5, max_output_bytes=1024)
    assert result["ok"] is False
    assert "SECRET_SENTINEL" not in str(result)


async def test_queued_deadline_expires_before_start(monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def fake_job(*args: object, **kwargs: object) -> dict:
        entered.set()
        await release.wait()
        return {"ok": True, "data": {}}

    mock = AsyncMock(side_effect=fake_job)
    monkeypatch.setattr(runner, "run_job", mock)
    pool = runner.JobPool(1)
    first = asyncio.create_task(
        pool.execute({"mode": "query"}, timeout_seconds=5, max_output_bytes=1024)
    )
    await entered.wait()
    second = await pool.execute({"mode": "write"}, timeout_seconds=0.05, max_output_bytes=1024)
    assert second["error"]["outcome"] == "not_started"
    assert mock.call_count == 1
    release.set()
    await first
