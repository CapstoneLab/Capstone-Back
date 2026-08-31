from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import asyncpg


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


def migration_sort_key(path: Path) -> tuple[int, str]:
    match = re.match(r"^v(\d+)_", path.name)
    if not match:
        raise ValueError(f"invalid migration filename: {path.name}")
    return int(match.group(1)), path.name


async def apply_migrations() -> None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required to apply database migrations")
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn)
    try:
        # Prevent two application replicas from applying the same migration
        # concurrently during a rolling deployment. The lock is released when
        # this connection closes, including on failures.
        await connection.execute(
            "SELECT pg_advisory_lock(hashtext($1::text))",
            "capstone-back-schema-migrations",
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(255) PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {
            row["version"]
            for row in await connection.fetch("SELECT version FROM schema_migrations")
        }
        for migration_path in sorted(
            MIGRATIONS_DIR.glob("v*.sql"), key=migration_sort_key
        ):
            version = migration_path.name
            if version in applied:
                continue
            sql = migration_path.read_text(encoding="utf-8")
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES($1)",
                    version,
                )
            print(f"[migration] applied {version}")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(apply_migrations())
