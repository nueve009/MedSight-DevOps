"""
Validate the medicine dataset and build it into data/kb.sqlite, and move it
between the kb/ CSV files and Supabase.

Where the dataset lives (2026-11-09): the kb_* tables in Supabase
(migrations/0005_kb_dataset.sql). Teammates add and fix rows in the Supabase
Table Editor, and a second teammate approves each row there (types their
name in checked_by, ticks `approved`). The deployed backend reads approved
rows straight from Supabase (app/kb_source.py). The CSV files in kb/ are for
importing drafts (--push) and for a reviewed copy in git, the local
kb.sqlite and eval_kb.py (--pull).

Every product is entered from its TheFilipinoDoctor page (or the box or
insert) plus a hand lookup on the FDA portal (see kb/README.md). The rules
are in app/kb_validate.py; this script refuses to build or push if any row
breaks them, because a wrong row would be spoken to a user as "reviewed".

Usage:
    python -m scripts.kb_build                    validate kb/, then build data/kb.sqlite
    python -m scripts.kb_build --check            validate only (run after editing the CSVs)
    python -m scripts.kb_build --check --allow-empty
                                                  CI only, until the team's first rows are merged:
                                                  an empty dataset is a warning, not an error
    python -m scripts.kb_build --check --kb-dir kb/staging/converted
                                                  validate a draft folder with the same rules
    python -m scripts.kb_build --push --kb-dir kb/staging/batch1
                                                  add or update those rows in Supabase, unapproved,
                                                  for a teammate to check (owner connection in
                                                  KB_ADMIN_DATABASE_URL or MIGRATION_DATABASE_URL)
    python -m scripts.kb_build --pull [--csv-out kb]
                                                  build data/kb.sqlite from the approved rows in
                                                  Supabase (and write them as CSVs)
"""
import argparse
import csv
import hashlib
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from app import config
from app.kb_validate import (  # noqa: F401 - re-exported for tests and older callers
    CHECKER_FIELDS,
    INGREDIENT_FIELDS,
    PASSAGE_FIELDS,
    PRODUCT_FIELDS,
    Rows,
    Validator,
    split_list,
    validate_rows,
)

KB_FILES = ("products.csv", "ingredients.csv", "passages.csv")
# Supabase table, its columns and key, in the order rows are written (passages need their product).
TABLES = (("ingredients", "kb_ingredients", INGREDIENT_FIELDS, "generic_name"),
          ("products", "kb_products", PRODUCT_FIELDS, "product_id"),
          ("passages", "kb_passages", PASSAGE_FIELDS, "passage_id"))
# Columns where an empty CSV value is NULL in Postgres (dates, and the unique registration number).
NULL_WHEN_EMPTY = {"reg_expiry", "portal_checked_on", "fda_reg_no"}


def kb_version(kb_dir: Path = config.KB_DIR) -> str:
    """Hash of the CSV contents; changes whenever the dataset changes."""
    digest = hashlib.sha256()
    for name in KB_FILES:
        digest.update((kb_dir / name).read_bytes())
    return digest.hexdigest()[:12]


def read_csv(v: Validator, path: Path, expected_fields: list[str]) -> Rows:
    """Returns [(where, row)]. utf-8-sig tolerates the BOM Excel adds."""
    if not path.exists():
        v.error(path.name, "file is missing")
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != expected_fields:
            v.error(path.name, f"header must be exactly: {','.join(expected_fields)}")
            return []
        rows = []
        for line_no, row in enumerate(reader, start=2):
            if None in row:
                v.error(f"{path.name}:{line_no}", "too many columns (an unquoted comma in a value?)")
                continue
            rows.append((f"{path.name}:{line_no}",
                         {k: (v or "").strip() for k, v in row.items()}))
        return rows


def validate(kb_dir: Path = config.KB_DIR, allow_empty: bool = False,
             for_review: bool = False) -> tuple[Validator, dict]:
    """Validate the three CSVs in kb_dir. for_review: drafts for Supabase, so the
    checker's columns may be empty (the checker fills them when approving)."""
    v = Validator()
    products = read_csv(v, kb_dir / "products.csv", PRODUCT_FIELDS)
    ingredients = read_csv(v, kb_dir / "ingredients.csv", INGREDIENT_FIELDS)
    passages = read_csv(v, kb_dir / "passages.csv", PASSAGE_FIELDS)
    validate_rows(products, ingredients, passages, v, allow_empty=allow_empty, for_review=for_review)
    data = {
        "products": [row for _, row in products],
        "ingredients": [row for _, row in ingredients],
        "passages": [row for _, row in passages],
    }
    return v, data


def build(data: dict, version: str, db_path: Path | None = None) -> None:
    """Write a fresh kb.sqlite, then swap it in so readers never see a half-built file."""
    db_path = db_path or config.KB_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".tmp")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(f"""
            CREATE TABLE products ({", ".join(f"{f} TEXT NOT NULL" for f in PRODUCT_FIELDS)},
                                   PRIMARY KEY (product_id));
            CREATE TABLE ingredients ({", ".join(f"{f} TEXT NOT NULL" for f in INGREDIENT_FIELDS)},
                                      PRIMARY KEY (generic_name));
            CREATE TABLE passages ({", ".join(f"{f} TEXT NOT NULL" for f in PASSAGE_FIELDS)},
                                   PRIMARY KEY (passage_id));
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            -- Lexical search over names, aliases and passage text (for people and tools, not matching).
            CREATE VIRTUAL TABLE search USING fts5(product_id UNINDEXED, passage_id UNINDEXED, content);
        """)
        for table, fields in (("products", PRODUCT_FIELDS), ("ingredients", INGREDIENT_FIELDS),
                              ("passages", PASSAGE_FIELDS)):
            conn.executemany(
                f"INSERT INTO {table} VALUES ({', '.join('?' for _ in fields)})",
                [[row[f] for f in fields] for row in data[table]],
            )
        conn.executemany(
            "INSERT INTO search VALUES (?, '', ?)",
            [(p["product_id"], " ".join([p["brand_name"], p["aliases"].replace("|", " "),
                                         p["generic_names"].replace("|", " ")]))
             for p in data["products"]],
        )
        conn.executemany(
            "INSERT INTO search VALUES (?, ?, ?)",
            [(p["product_id"], p["passage_id"], p["text"]) for p in data["passages"]],
        )
        conn.execute("INSERT INTO meta VALUES ('kb_version', ?)", (version,))
        conn.execute("INSERT INTO meta VALUES ('built_on', ?)", (date.today().isoformat(),))
        conn.commit()
    finally:
        conn.close()
    tmp_path.replace(db_path)


def write_csvs(data: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, _, fields, _ in TABLES:
        with open(out_dir / f"{name}.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(data[name])


def admin_conninfo() -> str:
    return os.environ.get("KB_ADMIN_DATABASE_URL") or os.environ.get("MIGRATION_DATABASE_URL", "")


def push(data: dict, conninfo: str) -> dict:
    """Add or update rows in Supabase by their key, in one transaction.

    Rows always arrive unapproved: the checker's columns are never written, and
    the database un-approves an approved row whose values change. Unchanged
    rows are left alone (they stay approved). Rows that are only in Supabase
    are kept. Returns {table: {"added": n, "updated": n, "unchanged": n, "only_in_db": [keys]}}.
    """
    import psycopg

    report = {}
    with psycopg.connect(conninfo, sslmode=config.DATABASE_SSLMODE, connect_timeout=10,
                         prepare_threshold=None) as conn, conn.transaction():
        for name, table, fields, key in TABLES:
            cols = [f for f in fields if f not in CHECKER_FIELDS]
            others = [c for c in cols if c != key]
            sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                   f"ON CONFLICT ({key}) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in others)} "
                   f"WHERE ({', '.join(f'{table}.{c}' for c in others)}) IS DISTINCT FROM "
                   f"({', '.join(f'EXCLUDED.{c}' for c in others)}) "
                   "RETURNING (xmax = 0) AS added")
            counts = {"added": 0, "updated": 0, "unchanged": 0}
            for row in data[name]:
                cur = conn.execute(sql, [None if c in NULL_WHEN_EMPTY and not row[c] else row[c] for c in cols])
                result = cur.fetchone()
                counts["unchanged" if result is None else "added" if result[0] else "updated"] += 1
            pushed = {row[key] for row in data[name]}
            counts["only_in_db"] = sorted(r[0] for r in conn.execute(f"SELECT {key} FROM {table}")
                                          if r[0] not in pushed)
            report[table] = counts
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the dataset, build data/kb.sqlite, sync with Supabase.")
    parser.add_argument("--check", action="store_true", help="validate only; don't build")
    parser.add_argument("--allow-empty", action="store_true",
                        help="with --check: an empty dataset is a warning (CI until the first rows land)")
    parser.add_argument("--kb-dir", type=Path, default=None,
                        help="folder with the three CSVs (default: kb/)")
    parser.add_argument("--push", action="store_true",
                        help="add or update the CSV rows in Supabase, unapproved, for review")
    parser.add_argument("--pull", action="store_true",
                        help="build data/kb.sqlite from the approved rows in Supabase")
    parser.add_argument("--csv-out", type=Path, default=None,
                        help="with --pull: also write the approved rows as CSVs into this folder")
    args = parser.parse_args()
    if args.allow_empty and not args.check:
        sys.exit("--allow-empty only works with --check; an empty kb.sqlite is never built.")
    if sum([args.check, args.push, args.pull]) > 1:
        sys.exit("Choose one of --check, --push and --pull.")
    if args.csv_out and not args.pull:
        sys.exit("--csv-out only works with --pull.")
    if args.pull:
        sys.exit(pull(args.csv_out))
    kb_dir = args.kb_dir or config.KB_DIR

    v, data = validate(kb_dir, allow_empty=args.allow_empty, for_review=args.push)
    for w in v.warnings:
        print(f"warning  {w}")
    for e in v.errors:
        print(f"ERROR    {e}")

    counts = ", ".join(f"{len(rows)} {name}" for name, rows in data.items())
    brands = len({p["brand_name"].lower() for p in data["products"]})
    if v.errors:
        print(f"\n{len(v.errors)} error(s) in {kb_dir} ({counts}). Fix them and run again.")
        sys.exit(1)

    print(f"{kb_dir} is valid: {counts} ({brands} brands).")
    if args.push:
        conninfo = admin_conninfo()
        if not conninfo:
            sys.exit("Set KB_ADMIN_DATABASE_URL (or MIGRATION_DATABASE_URL) to the database owner's connection string.")
        for table, c in push(data, conninfo).items():
            print(f"{table}: {c['added']} added, {c['updated']} updated (now waiting for approval), "
                  f"{c['unchanged']} unchanged")
            if c["only_in_db"]:
                print(f"  only in Supabase (kept): {', '.join(c['only_in_db'])}")
        print("Rows are unapproved until a second teammate approves them in the Supabase Table Editor.")
    elif not args.check:
        config.ensure_dirs()
        version = kb_version(kb_dir)
        build(data, version)
        print(f"Built {config.KB_DB_PATH} (kb_version {version}).")


def pull(csv_out: Path | None) -> int:
    from app.kb_source import servable_from_postgres

    conninfo = config.KB_DATABASE_URL or config.DATABASE_URL or os.environ.get("MIGRATION_DATABASE_URL", "")
    if not conninfo:
        print("Set KB_DATABASE_URL, DATABASE_URL or MIGRATION_DATABASE_URL.")
        return 1
    data, version, dropped = servable_from_postgres(conninfo)
    for e in dropped:
        print(f"not served  {e}")
    if not data["products"]:
        print("No approved products in Supabase yet; nothing built.")
        return 1
    config.ensure_dirs()
    build(data, version)
    print(f"Built {config.KB_DB_PATH} from Supabase: {len(data['products'])} products, "
          f"{len(data['ingredients'])} ingredients, {len(data['passages'])} passages (kb_version {version}).")
    if csv_out:
        write_csvs(data, csv_out)
        print(f"Wrote the approved rows to {csv_out}/.")
    return 0


if __name__ == "__main__":
    main()
