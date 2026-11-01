-- M4: fixed-window rate counters for app/ratelimit.py (per device, per
-- address, the daily LLM budget). Buckets hold hashes, never raw device IDs
-- or addresses. Finished windows are deleted by scripts/store_admin.py purge.

CREATE TABLE rate_limits (
    bucket        text NOT NULL,       -- e.g. "device:<hash>", "addr:<hash>", "llm:global"
    window_start  bigint NOT NULL,     -- Unix seconds
    window_s      integer NOT NULL,    -- 60 or 86400
    count         integer NOT NULL,
    PRIMARY KEY (bucket, window_start)
);

ALTER TABLE rate_limits ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON rate_limits FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medsight_backend') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON rate_limits TO medsight_backend;
        CREATE POLICY backend_rate_limits ON rate_limits TO medsight_backend USING (true) WITH CHECK (true);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON rate_limits FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON rate_limits FROM authenticated;
    END IF;
END
$$;
