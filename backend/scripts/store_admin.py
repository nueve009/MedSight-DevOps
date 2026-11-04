"""
Maintenance for the cache store chosen by CACHE_BACKEND (Supabase on Railway).

Usage (from backend/):
    python -m scripts.store_admin export --out <folder>   # answers and audit log -> <folder>/answers-<UTC time>.jsonl
                                                          # and <folder>/audit-<UTC time>.jsonl
    python -m scripts.store_admin restore <file.jsonl>    # load an export back (same cache_key = replaced)
    python -m scripts.store_admin purge                   # delete expired answers; clear scan_log OCR text
                                                          # older than 30 days

Run export and purge once a night (until Supabase's paid plan has backups),
and keep the export folder private: it holds what was cached, not who
scanned it. The restore steps are in migrations/README.md.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.store import make_store


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def export(store, out_dir: Path) -> Path:
    """Writes the answers and the audit log; returns the answers file (the one restore takes)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    rows = store.export_answers()
    path = out_dir / f"answers-{stamp}.jsonl"
    _write_jsonl(path, rows)
    audit = store.export_audit()
    _write_jsonl(out_dir / f"audit-{stamp}.jsonl", audit)
    print(f"exported {len(rows)} answers and {len(audit)} audit rows to {out_dir}")
    return path


def restore(store, path: Path) -> int:
    with path.open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    n = store.import_answers(rows)
    print(f"restored {n} answers from {path}")
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description="Export, restore or purge the cache store.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_export = sub.add_parser("export")
    p_export.add_argument("--out", type=Path, required=True)
    p_restore = sub.add_parser("restore")
    p_restore.add_argument("file", type=Path)
    sub.add_parser("purge")
    args = parser.parse_args()

    store = make_store()
    try:
        if args.command == "export":
            export(store, args.out)
        elif args.command == "restore":
            restore(store, args.file)
        else:
            print(json.dumps(store.purge()))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
