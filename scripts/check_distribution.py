"""Check release archives without extracting them, and optionally write SHA256SUMS."""

from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {".git", ".local", ".venv", ".superpowers", "__pycache__", "node_modules"}
FORBIDDEN_FILES = {".env", "config.toml", "mysql-connections.toml", "secrets.json"}


def validate_member(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError("Archive contains an unsafe path.")
    if any(part in FORBIDDEN_PARTS for part in path.parts):
        raise ValueError("Archive contains a forbidden local directory.")
    if path.name in FORBIDDEN_FILES or path.name.endswith((".local.toml", ".log", ".pyc")):
        raise ValueError("Archive contains a forbidden local file.")


def validate_wheel(path: Path, project: str, version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in names:
            validate_member(name)
        prefix = f"{project.replace('-', '_')}-{version}.dist-info/"
        metadata = archive.read(prefix + "METADATA").decode("utf-8")
        if f"Name: {project}\n" not in metadata or f"Version: {version}\n" not in metadata:
            raise ValueError("Wheel metadata does not match the project version.")
        entry_points = archive.read(prefix + "entry_points.txt").decode("utf-8")
        if "mysql-mcp = mysql_mcp.server:main" not in entry_points:
            raise ValueError("Wheel is missing the mysql-mcp command.")
        required = {"mysql_mcp/__init__.py", "mysql_mcp/server.py"}
        if not required.issubset(names):
            raise ValueError("Wheel is missing the Python package.")


def validate_sdist(path: Path, project: str, version: str) -> None:
    expected_root = f"{project.replace('-', '_')}-{version}"
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            validate_member(member.name)
            if PurePosixPath(member.name).parts[0] != expected_root:
                raise ValueError("Source archive has an unexpected root path.")
            if not member.isfile() and not member.isdir():
                raise ValueError("Source archive contains a forbidden special entry.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--checksums", action="store_true")
    arguments = parser.parse_args()
    try:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        name, version = project["name"], project["version"]
        if arguments.tag is not None and arguments.tag != f"v{version}":
            raise ValueError("Release tag does not match the project version.")
        prefix = f"{name.replace('-', '_')}-{version}"
        wheels = list(arguments.directory.glob("*.whl"))
        sources = list(arguments.directory.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sources) != 1:
            raise ValueError("Release requires exactly one wheel and one source archive.")
        if not wheels[0].name.startswith(prefix + "-") or sources[0].name != prefix + ".tar.gz":
            raise ValueError("Archive filenames do not match the project version.")
        validate_wheel(wheels[0], name, version)
        validate_sdist(sources[0], name, version)
        if arguments.checksums:
            lines = [
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
                for path in sorted([wheels[0], sources[0]])
            ]
            (arguments.directory / "SHA256SUMS").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
        print(f"Validated {name} {version}: wheel and source archive.")
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, tarfile.TarError) as error:
        # These errors are about controlled release artifacts, never database credentials.
        print(f"Release validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
