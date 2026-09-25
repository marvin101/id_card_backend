"""Fail CI when release metadata or direct dependency pins drift."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
VERSION_FILE_RE = re.compile(r'^__version__ = "([^"]+)"\s*$')
EXACT_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?==[^\s;]+"
    r"(?:\s*;\s*.+)?$"
)


def _read_version(version_file: str) -> tuple[str | None, list[str]]:
    match = VERSION_FILE_RE.fullmatch(version_file)
    if not match:
        return None, ["app/version.py must contain only an exact __version__ assignment"]
    version = match.group(1)
    if not SEMVER_RE.fullmatch(version):
        return version, [f"app/version.py is not valid Semantic Versioning: {version}"]
    return version, []


def _check_requirement_pins(requirements: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for relative_path, content in requirements.items():
        for line_number, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("-r "):
                continue
            if not EXACT_REQUIREMENT_RE.fullmatch(line):
                errors.append(
                    f"{relative_path}:{line_number} must use an exact == pin: {line}"
                )
    return errors


def validate_release_files(
    *,
    version_file: str,
    requirements: dict[str, str],
    changelog: str,
    readme: str,
    checklist: str,
    tag: str | None = None,
) -> list[str]:
    version, errors = _read_version(version_file)
    errors.extend(_check_requirement_pins(requirements))
    if version is None:
        return errors

    if not re.search(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
        changelog,
        flags=re.MULTILINE,
    ):
        errors.append(f"CHANGELOG.md has no dated [{version}] release heading")
    if f"CampusID v{version}" not in readme:
        errors.append(f"README.md does not identify CampusID v{version} as current")
    if f"candidate as `{version}`" not in checklist:
        errors.append(f"RELEASE_CHECKLIST.md does not identify candidate {version}")

    if tag:
        expected_tag = f"v{version}"
        if tag != expected_tag:
            errors.append(f"release tag {tag!r} must equal {expected_tag!r}")
        unreleased = re.search(
            r"^## \[Unreleased\]\s*(.*?)(?=^## \[)",
            changelog,
            flags=re.MULTILINE | re.DOTALL,
        )
        if unreleased and re.search(r"^- ", unreleased.group(1), flags=re.MULTILINE):
            errors.append("CHANGELOG.md [Unreleased] still contains release notes")

    return errors


def collect_errors(root: Path, tag: str | None = None) -> list[str]:
    return validate_release_files(
        version_file=(root / "app" / "version.py").read_text(encoding="utf-8"),
        requirements={
            relative_path: (root / relative_path).read_text(encoding="utf-8")
            for relative_path in ("requirements.txt", "requirements-dev.txt")
        },
        changelog=(root / "CHANGELOG.md").read_text(encoding="utf-8"),
        readme=(root / "README.md").read_text(encoding="utf-8"),
        checklist=(root / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8"),
        tag=tag,
    )


def _github_tag() -> str | None:
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        return os.environ.get("GITHUB_REF_NAME") or ""
    return None


def main() -> int:
    errors = collect_errors(ROOT, _github_tag())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Release metadata and direct dependency pins are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
