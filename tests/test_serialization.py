from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from mysql_mcp.serialization import ResultError, encode_json, encode_value


@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal("0.100"), "0.100"),
        (2**64 - 1, str(2**64 - 1)),
        (timedelta(hours=27), "27:00:00"),
        (timedelta(microseconds=-1), "-00:00:00.000001"),
        (timedelta(hours=1, microseconds=123), "01:00:00.000123"),
        (b"AB", {"type": "binary", "encoding": "base64", "data": "QUI="}),
        (bytearray(b"AB"), {"type": "binary", "encoding": "base64", "data": "QUI="}),
        (date(2026, 1, 2), "2026-01-02"),
        (datetime(2026, 1, 2, 3, 4, 5), "2026-01-02 03:04:05"),
        (None, None),
        (True, True),
        (1, 1),
        (1.5, 1.5),
    ],
)
def test_database_values(value: object, expected: object) -> None:
    assert encode_value(value, 65536) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_unsupported_values_fail_without_illegal_json(value: object) -> None:
    with pytest.raises(ResultError):
        encode_value(value, 65536)


def test_capacity_uses_utf8_bytes() -> None:
    with pytest.raises(ResultError):
        encode_value("界" * 10, 20)
    with pytest.raises(ResultError):
        encode_json({"value": "界" * 10}, 20)
