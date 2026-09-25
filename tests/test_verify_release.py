from scripts.verify_release import validate_release_files


def _release_files(**overrides):
    files = {
        "version_file": '__version__ = "1.2.3"\n',
        "requirements": {
            "requirements.txt": "fastapi==1.2.3\n",
            "requirements-dev.txt": "-r requirements.txt\npytest==9.1.1\n",
        },
        "changelog": "## [Unreleased]\n\n## [1.2.3] - 2026-09-25\n",
        "readme": "CampusID v1.2.3\n",
        "checklist": "candidate as `1.2.3`\n",
    }
    files.update(overrides)
    return files


def test_release_files_are_consistent():
    assert validate_release_files(**_release_files()) == []


def test_tag_must_match_version_and_unreleased_notes_must_be_empty():
    errors = validate_release_files(
        **_release_files(
            changelog=(
                "## [Unreleased]\n\n- pending\n\n"
                "## [1.2.3] - 2026-09-25\n"
            ),
            tag="v1.2.4",
        )
    )

    assert "release tag 'v1.2.4' must equal 'v1.2.3'" in errors
    assert "CHANGELOG.md [Unreleased] still contains release notes" in errors


def test_requirements_must_be_exactly_pinned():
    errors = validate_release_files(
        **_release_files(
            requirements={
                "requirements.txt": "fastapi>=1\n",
                "requirements-dev.txt": "-r requirements.txt\npytest==9.1.1\n",
            }
        )
    )
    assert errors == [
        "requirements.txt:1 must use an exact == pin: fastapi>=1"
    ]
