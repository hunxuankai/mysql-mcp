"""Lossless JSON representations and explicit UTF-8 response budgets."""

from __future__ import annotations

import base64
import json
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any


class ResultError(ValueError):
    """A result cannot be represented within the public result contract."""


def encode_json(value: object, max_bytes: int) -> bytes:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ResultError("Result contains an unsupported value.") from exc
    if len(encoded) > max_bytes:
        raise ResultError("Result exceeds the configured byte limit.")
    return encoded


def encode_value(value: object, max_bytes: int) -> Any:
    encoded: object
    if value is None or isinstance(value, bool):
        encoded = value
    elif isinstance(value, int):
        encoded = value if abs(value) <= (1 << 53) - 1 else str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ResultError("Non-finite numbers are not supported.")
        encoded = value
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise ResultError("Non-finite numbers are not supported.")
        encoded = str(value)
    elif isinstance(value, timedelta):
        micros = (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds
        sign = "-" if micros < 0 else ""
        seconds, fraction = divmod(abs(micros), 1_000_000)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        encoded = f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
        if fraction:
            encoded += f".{fraction:06d}"
    elif isinstance(value, datetime):
        encoded = value.isoformat(sep=" ")
    elif isinstance(value, (date, time)):
        encoded = value.isoformat()
    elif isinstance(value, (bytes, bytearray, memoryview)):
        if len(value) > max_bytes:
            raise ResultError("Field exceeds the configured byte limit.")
        encoded = {
            "type": "binary",
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    elif isinstance(value, str):
        encoded = value
    else:
        raise ResultError("Result contains an unsupported value type.")
    encode_json(encoded, max_bytes)
    return encoded
