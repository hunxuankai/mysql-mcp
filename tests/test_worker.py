from __future__ import annotations

import json
import subprocess
import sys

import pytest


@pytest.mark.parametrize("body", ["[]", "null", "{broken", '{"mode":"invalid"}'])
def test_worker_rejects_bad_request_without_traceback(body: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mysql_mcp.worker"],
        input=body,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["ok"] is False
    assert "Traceback" not in result.stderr
