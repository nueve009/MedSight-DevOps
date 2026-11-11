-- The review console (app/review.py, scripts/review_console.py): reviewers
-- sign in with their own Supabase account and two-factor code, and the console
-- records who approved what. Approving still happens on the kb_* rows, so the
-- deployed backend picks a change up within KB_REFRESH_S with no deploy.
--
-- Added on top of 0005:
--   * reviewers: who may use the console (only the owner edits it)
--   * kb_reviews: every decision, append-only
--   * kb_evidence: box photos and portal screenshots for a row
--   * kb_fda_lookups: candidates from scripts/fda_lookup.py --push. Candidates
--     only: nothing is spoken from this table
--   * kb_settings: one row, allow_table_editor_approval
--   * review_state and last_edited_by on the kb_* tables
--   * role medsight_review: what the console connects as
--
-- 0005's rules stay: rows start unapproved, an edit un-approves, an edit and
-- its approval are separate saves. New: the approver must differ from the last
-- editor too, and approving needs the review role, or the owner while
-- kb_settings.allow_table_editor_approval is true (the default, so the Table
-- Editor keeps working until the console is in use).
--
-- Not in this file: the Supabase Storage bucket. See the end of the file.

-- --- role ---
-- The login Mon creates inherits from this template (migrations/README.md).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medsight_review') THEN
        CREATE ROLE medsight_review NOLOGIN;
    END IF;
END
$$;

-- --- tables ---

CREATE TABLE reviewers (
    user_id   uuid PRIMARY KEY,             -- auth.users id; no foreign key, so a project reset can't cascade here
    name      text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
    active    boolean NOT NULL DEFAULT true,
    added_on  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE kb_settings (
    id                            boolean PRIMARY KEY DEFAULT true CHECK (id),   -- one row only
    allow_table_editor_approval   boolean NOT NULL DEFAULT true
);
INSERT INTO kb_settings DEFAULT VALUES;

CREATE TABLE kb_reviews (
    id            bigserial PRIMARY KEY,
    table_name    text NOT NULL CHECK (table_name IN ('kb_products', 'kb_ingredients', 'kb_passages')),
    row_key       text NOT NULL,
    reviewer      text NOT NULL CHECK (btrim(reviewer) <> ''),
    decision      text NOT NULL CHECK (decision IN ('approve', 'request_changes')),
    checklist     jsonb NOT NULL DEFAULT '{}'::jsonb,
    note          text NOT NULL DEFAULT '',
    row_snapshot  jsonb NOT NULL,           -- the row exactly as the reviewer saw it
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kb_reviews_row_idx ON kb_reviews (table_name, row_key, created_at);

CREATE TABLE kb_evidence (
    id            bigserial PRIMARY KEY,
    table_name    text NOT NULL CHECK (table_name IN ('kb_products', 'kb_ingredients', 'kb_passages')),
    row_key       text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('box_photo', 'insert', 'portal_screenshot')),
    storage_path  text NOT NULL UNIQUE,
    uploaded_by   text NOT NULL CHECK (btrim(uploaded_by) <> ''),
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kb_evidence_row_idx ON kb_evidence (table_name, row_key);

CREATE TABLE kb_fda_lookups (
    id                  bigserial PRIMARY KEY,
    brand_query         text NOT NULL,
    brand_name          text NOT NULL DEFAULT '',
    generic_name        text NOT NULL DEFAULT '',
    strength            text NOT NULL DEFAULT '',
    dosage_form         text NOT NULL DEFAULT '',
    fda_reg_no          text NOT NULL UNIQUE CHECK (btrim(fda_reg_no) <> ''),
    reg_expiry          date,
    classification      text NOT NULL DEFAULT '',
    classification_raw  text NOT NULL DEFAULT '',
    manufacturer        text NOT NULL DEFAULT '',
    portal_checked_on   date,
    note                text NOT NULL DEFAULT '',
    export_only         boolean NOT NULL DEFAULT false,
    pushed_by           text NOT NULL CHECK (btrim(pushed_by) <> ''),
    pushed_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kb_fda_lookups_brand_idx ON kb_fda_lookups (lower(brand_query));

ALTER TABLE kb_ingredients
    ADD COLUMN review_state text NOT NULL DEFAULT 'pending' CHECK (review_state IN ('pending', 'changes_requested', 'approved')),
    ADD COLUMN last_edited_by text NOT NULL DEFAULT '';
ALTER TABLE kb_products
    ADD COLUMN review_state text NOT NULL DEFAULT 'pending' CHECK (review_state IN ('pending', 'changes_requested', 'approved')),
    ADD COLUMN last_edited_by text NOT NULL DEFAULT '';
ALTER TABLE kb_passages
    ADD COLUMN review_state text NOT NULL DEFAULT 'pending' CHECK (review_state IN ('pending', 'changes_requested', 'approved')),
    ADD COLUMN last_edited_by text NOT NULL DEFAULT '';

-- --- rules ---

-- review_state and last_edited_by aren't part of what the row says.
CREATE OR REPLACE FUNCTION kb_content(r jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
    SELECT r - 'checked_by' - 'description_checked_by' - 'approved' - 'approved_at' - 'review_note'
             - 'created_at' - 'updated_at' - 'review_state' - 'last_edited_by'
$$;

-- As in 0005, plus: the approver must differ from the last editor.
CREATE OR REPLACE FUNCTION kb_check_approvable(tbl text, r jsonb) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    generic text;
BEGIN
    IF btrim(r->>'checked_by') = '' THEN
        RAISE EXCEPTION 'type your name in checked_by before approving';
    END IF;
    IF lower(btrim(r->>'checked_by')) = lower(btrim(r->>'entered_by')) THEN
        RAISE EXCEPTION 'checked_by must be a different person from entered_by (%)', r->>'entered_by';
    END IF;
    IF lower(btrim(r->>'checked_by')) = lower(btrim(coalesce(r->>'last_edited_by', ''))) THEN
        RAISE EXCEPTION 'checked_by must be a different person from the last editor (%)', r->>'last_edited_by';
    END IF;

    IF tbl = 'kb_products' THEN
        IF btrim(r->>'description_checked_by') = '' THEN
            RAISE EXCEPTION 'description_checked_by is empty: who checked the spoken description against the source?';
        END IF;
        IF r->>'classification' = '' THEN
            RAISE EXCEPTION 'classification is empty (OTC, Rx or HR, from the FDA portal)';
        END IF;
        IF coalesce(r->>'fda_reg_no', '') = '' OR r->>'reg_expiry' IS NULL OR r->>'portal_checked_on' IS NULL
           OR btrim(r->>'dosage_form') = '' THEN
            RAISE EXCEPTION 'fda_reg_no, reg_expiry, portal_checked_on and dosage_form are all needed';
        END IF;
        IF (r->>'reg_expiry')::date < current_date THEN
            RAISE EXCEPTION 'FDA registration expired on %; re-check the portal', r->>'reg_expiry';
        END IF;
        IF cardinality(kb_split(r->>'generic_names')) = 0 THEN
            RAISE EXCEPTION 'generic_names is empty';
        END IF;
        IF cardinality(kb_split(r->>'generic_names')) <> cardinality(kb_split(r->>'strengths')) THEN
            RAISE EXCEPTION '% generic name(s) but % strength(s); list them in the same order, separated by |',
                cardinality(kb_split(r->>'generic_names')), cardinality(kb_split(r->>'strengths'));
        END IF;
        FOREACH generic IN ARRAY kb_split(r->>'generic_names') LOOP
            IF NOT EXISTS (SELECT 1 FROM kb_ingredients i WHERE lower(i.generic_name) = lower(generic)
                           OR lower(generic) = ANY (SELECT lower(s) FROM unnest(kb_split(i.synonyms)) AS s)) THEN
                RAISE EXCEPTION 'generic ''%'' is not in kb_ingredients', generic;
            END IF;
        END LOOP;
        PERFORM kb_check_spoken(r->>'spoken_description', true);

    ELSIF tbl = 'kb_ingredients' THEN
        IF btrim(r->>'spoken_description') <> '' THEN
            IF r->>'source_url' NOT LIKE 'https://%' THEN
                RAISE EXCEPTION 'source_url must be an https:// link to the page the description came from';
            END IF;
            PERFORM kb_check_spoken(r->>'spoken_description', false);
        END IF;

    ELSIF tbl = 'kb_passages' THEN
        IF r->>'source_type' = 'tfd' AND r->>'source_ref' !~ '^https://(www\.)?thefilipinodoctor\.com/\S+$' THEN
            RAISE EXCEPTION 'a tfd passage needs the TheFilipinoDoctor page URL in source_ref';
        END IF;
    END IF;
END
$$;

-- Direct members only, on purpose: pg_has_role is always true for a superuser,
-- which would let the owner's session past allow_table_editor_approval = false.
CREATE FUNCTION kb_is_review_role() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT current_user = 'medsight_review'
        OR EXISTS (SELECT 1 FROM pg_auth_members m
                   WHERE m.roleid = 'medsight_review'::regrole
                     AND m.member = (SELECT oid FROM pg_roles WHERE rolname = current_user))
$$;

CREATE OR REPLACE FUNCTION kb_review_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    content_changed boolean;
BEGIN
    NEW.updated_at := now();
    IF TG_OP = 'INSERT' THEN
        IF NEW.approved THEN
            RAISE EXCEPTION 'add the row first; a second teammate approves it afterwards';
        END IF;
        NEW.approved_at := NULL;
        NEW.review_state := 'pending';
        RETURN NEW;
    END IF;

    content_changed := kb_content(to_jsonb(NEW)) IS DISTINCT FROM kb_content(to_jsonb(OLD));

    IF OLD.approved AND content_changed THEN
        -- An approved row was edited: it must be checked again.
        NEW.approved := false;
        NEW.approved_at := NULL;
        NEW.checked_by := '';
        NEW.review_state := 'pending';
        IF TG_TABLE_NAME = 'kb_products' THEN
            NEW.description_checked_by := '';
        END IF;
    ELSIF OLD.approved AND NEW.approved AND NEW.checked_by IS DISTINCT FROM OLD.checked_by THEN
        RAISE EXCEPTION 'untick approved before changing checked_by';
    ELSIF NOT OLD.approved AND NEW.approved THEN
        IF content_changed THEN
            RAISE EXCEPTION 'save your edits first; the row is then approved in a separate step';
        END IF;
        -- Two statements on purpose: the review role can't read kb_settings, and
        -- Postgres checks table permissions for a whole expression up front.
        IF NOT kb_is_review_role() THEN
            IF NOT (SELECT allow_table_editor_approval FROM kb_settings) THEN
                RAISE EXCEPTION 'approve this row in the review console; Table Editor approval is switched off';
            END IF;
        END IF;
        PERFORM kb_check_approvable(TG_TABLE_NAME, to_jsonb(NEW));
        NEW.approved_at := now();
        NEW.review_state := 'approved';
    ELSIF NOT NEW.approved THEN
        NEW.approved_at := NULL;
        -- An edit goes back in the queue; "approved" can't be set without approving.
        IF content_changed OR NEW.review_state = 'approved' THEN
            NEW.review_state := 'pending';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

-- After the new kb_content: under 0005's version this would look like an edit.
UPDATE kb_ingredients SET review_state = 'approved' WHERE approved;
UPDATE kb_products SET review_state = 'approved' WHERE approved;
UPDATE kb_passages SET review_state = 'approved' WHERE approved;

CREATE FUNCTION kb_reviews_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'kb_reviews is append-only';
END
$$;
CREATE TRIGGER kb_reviews_no_change BEFORE UPDATE OR DELETE ON kb_reviews
    FOR EACH ROW EXECUTE FUNCTION kb_reviews_append_only();

-- The console's only way to delete: the person who entered a row, and only if
-- it has never been approved. The console passes the signed-in reviewer's name.
CREATE FUNCTION kb_delete_unapproved(tbl text, key text, actor text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    key_col text := CASE tbl WHEN 'kb_ingredients' THEN 'generic_name'
                             WHEN 'kb_products' THEN 'product_id'
                             WHEN 'kb_passages' THEN 'passage_id' END;
    who text;
    is_approved boolean;
BEGIN
    IF key_col IS NULL THEN
        RAISE EXCEPTION 'unknown table';
    END IF;
    EXECUTE format('SELECT entered_by, approved FROM %I WHERE %I = $1 FOR UPDATE', tbl, key_col)
        INTO who, is_approved USING key;
    IF who IS NULL THEN
        RAISE EXCEPTION 'row not found';
    END IF;
    IF lower(btrim(who)) <> lower(btrim(actor)) THEN
        RAISE EXCEPTION 'only the person who entered a row can delete it';
    END IF;
    IF is_approved OR EXISTS (SELECT 1 FROM kb_review_log l WHERE l.table_name = tbl AND l.row_key = key
                              AND l.after->>'approved' = 'true') THEN
        RAISE EXCEPTION 'this row has been approved before, so it can''t be deleted';
    END IF;
    IF tbl = 'kb_products' AND EXISTS (
        SELECT 1 FROM kb_review_log l JOIN kb_passages p ON p.passage_id = l.row_key
        WHERE l.table_name = 'kb_passages' AND p.product_id = key AND l.after->>'approved' = 'true') THEN
        RAISE EXCEPTION 'a passage of this product has been approved before, so it can''t be deleted';
    END IF;
    EXECUTE format('DELETE FROM %I WHERE %I = $1', tbl, key_col) USING key;
END
$$;
REVOKE ALL ON FUNCTION kb_delete_unapproved(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kb_delete_unapproved(text, text, text) TO medsight_review;

-- --- access ---
-- RLS on. medsight_review gets only what's below: nothing on answers,
-- rate_limits, scan_log or audit_log. medsight_backend is unchanged.

ALTER TABLE reviewers ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_fda_lookups ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON reviewers, kb_settings, kb_reviews, kb_evidence, kb_fda_lookups FROM PUBLIC;

GRANT SELECT, INSERT, UPDATE ON kb_ingredients, kb_products, kb_passages TO medsight_review;
GRANT INSERT ON kb_review_log TO medsight_review;   -- the audit trigger runs as the caller
GRANT SELECT ON reviewers, kb_fda_lookups TO medsight_review;
GRANT SELECT, INSERT ON kb_reviews, kb_evidence TO medsight_review;
GRANT USAGE ON SEQUENCE kb_reviews_id_seq, kb_evidence_id_seq TO medsight_review;

CREATE POLICY review_kb_ingredients_read ON kb_ingredients FOR SELECT TO medsight_review USING (true);
CREATE POLICY review_kb_ingredients_add ON kb_ingredients FOR INSERT TO medsight_review WITH CHECK (NOT approved);
CREATE POLICY review_kb_ingredients_edit ON kb_ingredients FOR UPDATE TO medsight_review USING (true) WITH CHECK (true);
CREATE POLICY review_kb_products_read ON kb_products FOR SELECT TO medsight_review USING (true);
CREATE POLICY review_kb_products_add ON kb_products FOR INSERT TO medsight_review WITH CHECK (NOT approved);
CREATE POLICY review_kb_products_edit ON kb_products FOR UPDATE TO medsight_review USING (true) WITH CHECK (true);
CREATE POLICY review_kb_passages_read ON kb_passages FOR SELECT TO medsight_review USING (true);
CREATE POLICY review_kb_passages_add ON kb_passages FOR INSERT TO medsight_review WITH CHECK (NOT approved);
CREATE POLICY review_kb_passages_edit ON kb_passages FOR UPDATE TO medsight_review USING (true) WITH CHECK (true);
CREATE POLICY review_log_add ON kb_review_log FOR INSERT TO medsight_review WITH CHECK (true);
CREATE POLICY review_reviewers_read ON reviewers FOR SELECT TO medsight_review USING (true);
CREATE POLICY review_fda_read ON kb_fda_lookups FOR SELECT TO medsight_review USING (true);
CREATE POLICY review_reviews_read ON kb_reviews FOR SELECT TO medsight_review USING (true);
CREATE POLICY review_reviews_add ON kb_reviews FOR INSERT TO medsight_review WITH CHECK (true);
CREATE POLICY review_evidence_read ON kb_evidence FOR SELECT TO medsight_review USING (true);
CREATE POLICY review_evidence_add ON kb_evidence FOR INSERT TO medsight_review WITH CHECK (true);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON reviewers, kb_settings, kb_reviews, kb_evidence, kb_fda_lookups FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON reviewers, kb_settings, kb_reviews, kb_evidence, kb_fda_lookups FROM authenticated;
    END IF;
END
$$;

-- --- Supabase Storage (run by hand, once) ---
-- Storage tables exist only in a Supabase project and are owned by Supabase's
-- own role, so this isn't part of the migration. In the SQL editor, after the
-- migration has been applied, run:
--
-- INSERT INTO storage.buckets (id, name, public) VALUES ('kb-evidence', 'kb-evidence', false)
--     ON CONFLICT (id) DO NOTHING;
--
-- CREATE FUNCTION public.kb_is_active_reviewer() RETURNS boolean
-- LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
--     SELECT EXISTS (SELECT 1 FROM reviewers WHERE user_id = auth.uid() AND active)
-- $$;
-- REVOKE ALL ON FUNCTION public.kb_is_active_reviewer() FROM PUBLIC;
-- GRANT EXECUTE ON FUNCTION public.kb_is_active_reviewer() TO authenticated;
--
-- CREATE POLICY kb_evidence_add ON storage.objects FOR INSERT TO authenticated
--     WITH CHECK (bucket_id = 'kb-evidence' AND auth.jwt()->>'aal' = 'aal2' AND public.kb_is_active_reviewer());
-- CREATE POLICY kb_evidence_read ON storage.objects FOR SELECT TO authenticated
--     USING (bucket_id = 'kb-evidence' AND auth.jwt()->>'aal' = 'aal2' AND public.kb_is_active_reviewer());
