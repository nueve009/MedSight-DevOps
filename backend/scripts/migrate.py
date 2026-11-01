"""
Apply the numbered SQL files in migrations/ to a Postgres database, in order.

Each file runs in its own transaction and is recorded in schema_version, so a
file is applied once and a failed one changes nothing. Files are never edited
after they're applied: a change is a new, higher-numbered file.

Usage (from backend/):
    python -m scripts.migrate --check     # list applied and pending files, change nothing
    python -m scripts.migrate             # apply the pending files

The connection string comes from MIGRATION_DATABASE_URL: the database owner
(Supabase's "postgres" user), not the backend's own role, which can't create
tables. DATABASE_SSLMODE applies here too.
"""
import argparse
import os
import re
import sys
from pathlib import Path

from app import config

MIGRATIONS_DIR = config.BASE_DIR / "migrations"
FILE_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     integer PRIMARY KEY,
    name        text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def migration_files(folder: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    """(version, path) for every migration file, in order. A gap, a repeated
    number or a badly named .sql file is an error."""
    files = []
    for path in sorted(folder.glob("*.sql")):
        m = FILE_RE.match(path.name)
        if not m:
            raise ValueError(f"{path.name}: migration files are named NNNN_lowercase_words.sql")
        files.append((int(m.group(1)), path))
    versions = [v for v, _ in files]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(f"migration numbers must run 1, 2, 3, ... with no gaps or repeats; found {versions}")
    return files


def pending(applied: set[int], files: list[tuple[int, Path]]) -> list[tuple[int, Path]]:
    return [(v, p) for v, p in files if v not in applied]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--check", action="store_true", help="list applied and pending files only")
    parser.add_argument("--dir", type=Path, default=MIGRATIONS_DIR)
    args = parser.parse_args()

    url = os.environ.get("MIGRATION_DATABASE_URL", "")
    if not url:
        sys.exit("Set MIGRATION_DATABASE_URL to the database owner's connection string.")
    files = migration_files(args.dir)

    import psycopg
    with psycopg.connect(url, sslmode=config.DATABASE_SSLMODE, connect_timeout=10, autocommit=True) as conn:
        conn.execute(_BOOTSTRAP)
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
        todo = pending(applied, files)
        print(f"Applied: {sorted(applied) or 'none'}")
        print(f"Pending: {[p.name for _, p in todo] or 'none'}")
        if args.check:
            return 0
        for version, path in todo:
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_version (version, name) VALUES (%s, %s)", (version, path.name))
            print(f"applied {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
