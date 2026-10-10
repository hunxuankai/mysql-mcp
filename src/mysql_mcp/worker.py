"""One bounded stdin request, one JSON response, no persistent session."""

from __future__ import annotations

import json
import sys

from mysql_mcp.database import execute_request
from mysql_mcp.errors import failure
from mysql_mcp.serialization import encode_json


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(262145)
        if len(raw) > 262144:
            raise ValueError
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        result = execute_request(payload)
        encoded = encode_json(result, 1048576)
    except (ValueError, UnicodeError):
        encoded = encode_json(
            failure("INVALID_REQUEST", "Invalid worker request or result."), 1048576
        )
    except Exception:
        encoded = encode_json(
            failure("INTERNAL_ERROR", "The database worker could not complete the request."),
            1048576,
        )
    try:
        sys.stdout.buffer.write(encoded + b"\n")
        sys.stdout.buffer.flush()
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
