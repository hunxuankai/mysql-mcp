"""Release archives must contain the intended package, never local configuration."""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_archives(directory: Path, *, bad_member: str | None = None) -> None:
    wheel = directory / "mysql_mcp_router-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("mysql_mcp/__init__.py", '__version__ = "0.1.0"\n')
        archive.writestr("mysql_mcp/server.py", "def main(): pass\n")
        archive.writestr(
            "mysql_mcp_router-0.1.0.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: mysql-mcp-router\nVersion: 0.1.0\n",
        )
        archive.writestr(
            "mysql_mcp_router-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\nmysql-mcp = mysql_mcp.server:main\n",
        )
        if bad_member is not None:
            archive.writestr(bad_member, "synthetic local data")
    with tarfile.open(directory / "mysql_mcp_router-0.1.0.tar.gz", "w:gz") as archive:
        content = b"Synthetic source archive\n"
        member = tarfile.TarInfo("mysql_mcp_router-0.1.0/README.md")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))


def check(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_distribution.py"), str(directory), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_valid_release_generates_checksums(tmp_path: Path) -> None:
    make_archives(tmp_path)
    result = check(tmp_path, "--tag", "v0.1.0", "--checksums")
    assert result.returncode == 0, result.stderr
    entries = (tmp_path / "SHA256SUMS").read_text().splitlines()
    assert len(entries) == 2
    assert all(len(entry.split()[0]) == 64 for entry in entries)


def test_private_configuration_inside_wheel_rejects_release(tmp_path: Path) -> None:
    make_archives(tmp_path, bad_member="mysql_mcp/config.toml")
    result = check(tmp_path)
    assert result.returncode != 0
    assert "forbidden" in result.stderr.lower()


def test_path_traversal_inside_wheel_rejects_release(tmp_path: Path) -> None:
    make_archives(tmp_path, bad_member="../outside.txt")
    result = check(tmp_path)
    assert result.returncode != 0
    assert "path" in result.stderr.lower()


def test_tag_must_match_project_version(tmp_path: Path) -> None:
    make_archives(tmp_path)
    result = check(tmp_path, "--tag", "v9.9.9")
    assert result.returncode != 0
    assert "version" in result.stderr.lower()
