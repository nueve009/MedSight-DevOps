-- The backend's own role gets only what the server needs. The role itself
-- (with its password) is created once by hand: see migrations/README.md.
-- Skipped when the role doesn't exist yet (a fresh local or CI database).
--
-- RLS stays on. The only policies are for medsight_backend, so Supabase's
-- anon and authenticated roles (the public API keys) still open nothing.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medsight_backend') THEN
        GRANT USAGE ON SCHEMA public TO medsight_backend;
        GRANT SELECT, INSERT, UPDATE, DELETE ON parses, answers TO medsight_backend;
        GRANT SELECT, INSERT ON scan_log TO medsight_backend;
        GRANT UPDATE (ocr_text) ON scan_log TO medsight_backend;   -- the 30-day purge only

        CREATE POLICY backend_parses ON parses TO medsight_backend USING (true) WITH CHECK (true);
        CREATE POLICY backend_answers ON answers TO medsight_backend USING (true) WITH CHECK (true);
        CREATE POLICY backend_scan_log ON scan_log TO medsight_backend USING (true) WITH CHECK (true);
    END IF;
END
$$;

REVOKE ALL ON parses, answers, scan_log, schema_version FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON parses, answers, scan_log, schema_version FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON parses, answers, scan_log, schema_version FROM authenticated;
    END IF;
END
$$;
