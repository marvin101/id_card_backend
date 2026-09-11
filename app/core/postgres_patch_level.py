"""Verify the database against the August 2026 PostgreSQL security baseline."""

from dataclasses import dataclass

from sqlalchemy import text


# PostgreSQL 18.6, 17.11, 16.15, 15.19, and 14.24 contain the fixes released
# on 2026-08-13. PostgreSQL's numeric version format is MMmmpp for v10+.
MINIMUM_SECURE_VERSION_NUM = {
    14: 140024,
    15: 150019,
    16: 160015,
    17: 170011,
    18: 180006,
}


@dataclass(frozen=True)
class PatchAssessment:
    is_secure: bool
    message: str


def assess_postgres_version(version_num: int) -> PatchAssessment:
    major = version_num // 10000
    minimum = MINIMUM_SECURE_VERSION_NUM.get(major)
    if minimum is None:
        return PatchAssessment(
            False,
            f"PostgreSQL major {major} is outside the reviewed 14-18 security baseline",
        )
    if version_num < minimum:
        required = f"{major}.{minimum % 10000}"
        return PatchAssessment(False, f"upgrade required: minimum secure release is {required}")
    return PatchAssessment(True, "meets the 2026-08-13 PostgreSQL security baseline")


def main() -> int:
    from app.core.database import engine

    with engine.connect() as connection:
        version, version_num_text = connection.execute(
            text(
                "SELECT current_setting('server_version'), "
                "current_setting('server_version_num')"
            )
        ).one()

    version_num = int(version_num_text)
    assessment = assess_postgres_version(version_num)
    print(f"PostgreSQL server version: {version} ({version_num})")
    print(assessment.message)
    return 0 if assessment.is_secure else 2


if __name__ == "__main__":
    raise SystemExit(main())
