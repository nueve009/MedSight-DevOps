-- M3: the cache and scan log (plan section 4). Applied by scripts/migrate.py.
-- Row-level security is on with no policies, so Supabase's public anon and
-- authenticated keys open nothing; the backend uses its own role (0002).

CREATE TABLE parses (
    cache_key  text PRIMARY KEY,                 -- utils.raw_text_key
    result     jsonb NOT NULL,                   -- ParseResult.to_dict()
    cached_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE answers (
    cache_key    text PRIMARY KEY,               -- utils.answer_key: brand + every ingredient and strength
    tier         text NOT NULL CHECK (tier IN ('general', 'web')),   -- reviewed/ingredient come from kb/
    product      jsonb NOT NULL,                 -- ParsedProduct.to_dict()
    description  text NOT NULL,
    source_urls  jsonb NOT NULL DEFAULT '[]'::jsonb,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'pending_review', 'rejected')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz                     -- web answers: created_at + 30 days; NULL = never
);
CREATE INDEX answers_status_idx ON answers (status);
CREATE INDEX answers_expires_idx ON answers (expires_at) WHERE expires_at IS NOT NULL;

CREATE TABLE scan_log (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at          timestamptz NOT NULL DEFAULT now(),
    request_id          text,
    device_hash         text,                    -- hashed on arrival; raw device IDs are never stored
    ocr_text            text,                    -- cleared after 30 days (scripts/store_admin.py purge)
    status              text,
    rescan_reason       text,
    tier                text,
    cache               text,
    parse_cache         text,
    match               text,
    matched_product_id  text,
    kb_version          text,
    client              text,
    platform            text,
    app_version         text,
    record              jsonb NOT NULL           -- the pipeline's full record, without the OCR text
);
CREATE INDEX scan_log_created_idx ON scan_log (created_at);

ALTER TABLE parses ENABLE ROW LEVEL SECURITY;
ALTER TABLE answers ENABLE ROW LEVEL SECURITY;
ALTER TABLE scan_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_version ENABLE ROW LEVEL SECURITY;
