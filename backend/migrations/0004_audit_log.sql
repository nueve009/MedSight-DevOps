-- M7: every admin write to the answer cache (app/admin.py): who, what, the
-- row before and after, when. Append-only: the backend role can read and add
-- rows, never change or delete them.

CREATE TABLE audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    admin       text NOT NULL,                   -- the admin key's name, never the key
    action      text NOT NULL CHECK (action IN ('update', 'delete')),
    cache_key   text NOT NULL,
    before      jsonb,                           -- the answers row before the write
    after       jsonb                            -- after it; NULL for a delete
);
CREATE INDEX audit_log_created_idx ON audit_log (created_at);

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON audit_log FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medsight_backend') THEN
        GRANT SELECT, INSERT ON audit_log TO medsight_backend;
        CREATE POLICY backend_audit_read ON audit_log FOR SELECT TO medsight_backend USING (true);
        CREATE POLICY backend_audit_add ON audit_log FOR INSERT TO medsight_backend WITH CHECK (true);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON audit_log FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON audit_log FROM authenticated;
    END IF;
END
$$;
