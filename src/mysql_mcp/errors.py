"""Small, stable error envelopes that never include raw exception strings."""

from typing import Any


def failure(
    code: str, message: str, *, outcome: str | None = None, mysql_errno: int | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if outcome is not None:
        error["outcome"] = outcome
    if mysql_errno is not None:
        error["mysql_errno"] = mysql_errno
    return {"ok": False, "error": error}
