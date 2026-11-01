# Database migrations (Supabase / Postgres)

The cache and scan log live in Supabase when `CACHE_BACKEND=postgres`. These
numbered SQL files are the only way tables change: nobody edits tables in the
Supabase dashboard. A file is never edited after it has been applied; a
change is a new, higher-numbered file.

| File | What it does |
|---|---|
| `0001_cache_and_scan_log.sql` | `parses`, `answers`, `scan_log` tables; RLS on |
| `0002_backend_role_grants.sql` | grants and RLS policies for the `medsight_backend` role only |
| `0003_rate_limits.sql` | `rate_limits` counters (hashed buckets), same grants and RLS |

## First setup (once per Supabase project: staging and production)

1. **Create the backend's role** in the Supabase SQL editor, with a long
   random password that goes only into the Railway variable, never into git:
   ```sql
   CREATE ROLE medsight_backend LOGIN PASSWORD '<random password>';
   ```
   Create it **before** step 2, so `0002` can grant it access.
2. **Apply the migrations** with the owner's connection string (Supabase:
   Project Settings, Database, connection string for the `postgres` user):
   ```powershell
   $env:MIGRATION_DATABASE_URL = "<owner connection string>"
   python -m scripts.migrate --check   # lists applied and pending files
   python -m scripts.migrate
   ```
3. **Point the backend at it** (Railway variables):
   - `CACHE_BACKEND=postgres`
   - `DATABASE_URL=postgresql://medsight_backend.<project-ref>:<password>@<pooler host>:5432/postgres`
     (the session pooler; the direct connection works too)
   - `DATABASE_SSLMODE=require` (the default)

The app's public Supabase keys (`anon`, `authenticated`) can't read or write
these tables: RLS is on, and the only policies are for `medsight_backend`.

## If the database is down

Scans keep working. A failed connection or query (2 s and 1 s limits) is a
cache miss, then the database is skipped for 60 s and one `database skipped`
warning is logged. Settings: `DB_CONNECT_TIMEOUT_S`, `DB_QUERY_TIMEOUT_MS`,
`DB_SKIP_AFTER_FAILURE_S`.

## Nightly export and purge

Until the paid plan has backups, run these once a night against each
environment, and keep the export folder private:

```powershell
python -m scripts.store_admin export --out <private folder>
python -m scripts.store_admin purge   # expired answers, finished rate windows, scan_log OCR text older than 30 days
```

## Restore

1. Find the newest export, `answers-<UTC time>.jsonl`.
2. If the project itself was lost, make a new Supabase project and do
   "First setup" above.
3. Load the export with the backend's own settings (`CACHE_BACKEND=postgres`,
   `DATABASE_URL`):
   ```powershell
   python -m scripts.store_admin restore <answers-....jsonl>
   ```
   Rows with the same `cache_key` are replaced; others are left alone.
4. Check: `/health` answers, and a scan of a cached product says `"cache": "hit"`.

Only answers are exported. The parse cache rebuilds itself, and the scan log
isn't restored.
