from __future__ import annotations

from pathlib import Path

import pytest

from mysql_mcp.config import ConfigurationError, load_settings

BASE = """[connections.local]
host="127.0.0.1"
port=3306
database="fixture"
user="reader"
password="SYNTHETIC_SECRET"
"""


def write_config(tmp_path: Path, content: str = BASE) -> Path:
    target = tmp_path / "settings.toml"
    target.write_text(content, encoding="utf-8")
    return target


def test_defaults_and_secret_repr(tmp_path: Path) -> None:
    config = load_settings(write_config(tmp_path))
    assert config.mode == "query"
    assert config.limits.request_timeout_seconds == 15
    assert config.connection["tls"]["enabled"] is False
    assert "SYNTHETIC_SECRET" not in repr(config)
    assert "SYNTHETIC_SECRET" not in repr(config.public_context)


@pytest.mark.parametrize(
    "replacement",
    [
        ("port=3306", "port=true"),
        ("port=3306", "port=65536"),
        ('password="SYNTHETIC_SECRET"', ""),
        ('host="127.0.0.1"', "host=12"),
    ],
)
def test_bad_connection_rejected(tmp_path: Path, replacement: tuple[str, str]) -> None:
    with pytest.raises(ConfigurationError) as error:
        load_settings(write_config(tmp_path, BASE.replace(*replacement)))
    assert "SYNTHETIC_SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "extra",
    [
        '\nunknown="SYNTHETIC_SECRET"',
        "\n[limits]\nmax_rows=true",
        "\n[limits]\nrequest_timeout_seconds=nan",
        "\n[limits]\nmax_concurrency=9",
        "\n[limits]\nmax_request_bytes=1",
        "\n[limits]\nmax_response_bytes=1000",
        '\n[connections.local.tls]\nenabled=true\ncert="missing.pem"',
    ],
)
def test_bad_options_rejected_without_values(tmp_path: Path, extra: str) -> None:
    with pytest.raises(ConfigurationError) as error:
        load_settings(write_config(tmp_path, BASE + extra))
    assert "SYNTHETIC_SECRET" not in str(error.value)


def test_mutations_require_enabling(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(write_config(tmp_path), mode="write")
    with pytest.raises(ConfigurationError):
        load_settings(write_config(tmp_path), mode="ddl")


def test_mode_account_override(tmp_path: Path) -> None:
    content = (
        BASE + "\nallow_write=true\n[connections.local.accounts.write]"
        '\nuser="writer"\npassword="OTHER_SYNTHETIC"'
    )
    config = load_settings(write_config(tmp_path, content), mode="write")
    assert config.connection["user"] == "writer"
    assert config.connection["password"] == "OTHER_SYNTHETIC"


def test_remote_tls_default(tmp_path: Path) -> None:
    config = load_settings(write_config(tmp_path, BASE.replace("127.0.0.1", "mysql.example.com")))
    assert config.connection["tls"]["enabled"] is True


def test_toml_parse_error_is_sanitized(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as error:
        load_settings(write_config(tmp_path, BASE + "\nbroken = SYNTHETIC_SECRET"))
    assert "SYNTHETIC_SECRET" not in str(error.value)
