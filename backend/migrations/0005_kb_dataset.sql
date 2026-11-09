-- The reviewed medicine dataset, kept in Supabase (it used to live only in
-- kb/*.csv and data/kb.sqlite). Teammates add and fix rows in the Supabase
-- Table Editor; a second teammate approves each row there by typing their
-- name in checked_by and ticking `approved`. The backend reads approved rows
-- only (app/kb_source.py), so a row nobody approved is never spoken as
-- "reviewed".
--
-- Columns match the CSV columns in kb/README.md, so scripts/kb_build.py can
-- import CSV drafts (--push) and export approved rows (--pull).
--
-- What the database enforces (kb_review_guard below):
--   * rows start unapproved; a row can't be inserted already approved
--   * approving needs checked_by, a different name from entered_by, and the
--     row must pass the dataset rules (spoken description under 300
--     characters and without doses, one strength per ingredient, a listed
--     classification, an unexpired registration, known ingredients, ...)
--   * changing any value of an approved row un-approves it and clears the
--     checker's name, so every edit is checked again
--   * an edit and its approval can't be one save: the person who edits isn't
--     the one who approves
-- Every insert, update and delete is copied into kb_review_log.
--
-- The Table Editor runs as the database owner, so the database can't tell
-- which teammate clicked: checked_by is the name the checker types. The log
-- keeps every change with its time.

CREATE TABLE kb_ingredients (
    generic_name        text PRIMARY KEY CHECK (btrim(generic_name) <> ''),
    synonyms            text NOT NULL DEFAULT '',     -- other names, separated by |
    pnf_listed          text NOT NULL DEFAULT 'no' CHECK (pnf_listed IN ('yes', 'no')),
    pnf_forms           text NOT NULL DEFAULT '',
    pnf_page            text NOT NULL DEFAULT '',
    spoken_description  text NOT NULL DEFAULT '',     -- optional; '' = listed for its names only
    source_url          text NOT NULL DEFAULT '',
    entered_by          text NOT NULL CHECK (btrim(entered_by) <> ''),
    checked_by          text NOT NULL DEFAULT '',
    approved            boolean NOT NULL DEFAULT false,
    approved_at         timestamptz,
    review_note         text NOT NULL DEFAULT '',     -- the checker's comments; never spoken
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE kb_products (
    product_id              text PRIMARY KEY CHECK (product_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    brand_name              text NOT NULL CHECK (btrim(brand_name) <> ''),
    aliases                 text NOT NULL DEFAULT '',  -- separated by |
    generic_names           text NOT NULL,             -- every active ingredient, separated by |
    strengths               text NOT NULL,             -- one per ingredient, same order, separated by |
    dosage_form             text NOT NULL DEFAULT '',
    classification          text NOT NULL DEFAULT '' CHECK (classification IN ('', 'OTC', 'Rx', 'HR')),
    fda_reg_no              text UNIQUE,               -- typed by hand from the FDA portal, never scraped
    reg_expiry              date,
    manufacturer            text NOT NULL DEFAULT '',
    portal_checked_on       date,
    entered_by              text NOT NULL CHECK (btrim(entered_by) <> ''),
    checked_by              text NOT NULL DEFAULT '',
    spoken_description      text NOT NULL DEFAULT '',
    description_checked_by  text NOT NULL DEFAULT '',
    approved                boolean NOT NULL DEFAULT false,
    approved_at             timestamptz,
    review_note             text NOT NULL DEFAULT '',
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE kb_passages (
    passage_id   text PRIMARY KEY CHECK (btrim(passage_id) <> ''),
    product_id   text NOT NULL REFERENCES kb_products (product_id) ON UPDATE CASCADE ON DELETE CASCADE,
    section      text NOT NULL CHECK (section IN ('indications', 'warnings')),
    text         text NOT NULL CHECK (btrim(text) <> ''),
    source_type  text NOT NULL CHECK (source_type IN ('box', 'insert', 'pnf', 'tfd')),
    source_ref   text NOT NULL CHECK (btrim(source_ref) <> ''),
    entered_by   text NOT NULL CHECK (btrim(entered_by) <> ''),
    checked_by   text NOT NULL DEFAULT '',
    approved     boolean NOT NULL DEFAULT false,
    approved_at  timestamptz,
    review_note  text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kb_passages_product_idx ON kb_passages (product_id);

CREATE TABLE kb_review_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    table_name  text NOT NULL,
    row_key     text NOT NULL,
    action      text NOT NULL,                 -- insert, update, delete
    db_user     text NOT NULL DEFAULT current_user,
    before      jsonb,
    after       jsonb
);
CREATE INDEX kb_review_log_created_idx ON kb_review_log (created_at);

-- --- rules ---

-- Same patterns as app/utils.py contains_dosing (\y is a word boundary here).
CREATE FUNCTION kb_contains_dosing(t text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(t, '') ~* '\y\d+([.,]\d+)?\s*(mg|mcg|µg|g|ml|iu|units?|tablets?|tabs?|capsules?|caps?|teaspoons?|tsp|tablespoons?|tbsp|drops?|puffs?|sachets?)\y'
        OR coalesce(t, '') ~* '\yevery\s+\d+\s*(to\s+\d+\s*)?(hours?|hrs?)\y'
        OR coalesce(t, '') ~* '\y(once|twice|thrice|\d+\s+times)\s+(a|per|each)\s+day\y'
        OR coalesce(t, '') ~* '\y(daily|maximum|max\.?|recommended)\s+dose\y'
$$;

-- 'A | B |' -> {'A','B'}
CREATE FUNCTION kb_split(t text) RETURNS text[] LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(array_agg(btrim(p)), '{}') FROM unnest(string_to_array(coalesce(t, ''), '|')) AS p
    WHERE btrim(p) <> ''
$$;

CREATE FUNCTION kb_check_spoken(t text, required boolean) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF required AND btrim(t) = '' THEN
        RAISE EXCEPTION 'spoken_description is empty';
    END IF;
    IF length(t) > 300 THEN
        RAISE EXCEPTION 'spoken_description is % characters; keep it under 300', length(t);
    END IF;
    IF kb_contains_dosing(t) THEN
        RAISE EXCEPTION 'spoken_description looks like it gives a dose or schedule; '
                        'descriptions say what a medicine is for, never how much to take';
    END IF;
END
$$;

-- The checks a row must pass before it can be approved. Mirrors
-- app/kb_validate.py; the backend runs those checks again when it loads.
CREATE FUNCTION kb_check_approvable(tbl text, r jsonb) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    generic text;
BEGIN
    IF btrim(r->>'checked_by') = '' THEN
        RAISE EXCEPTION 'type your name in checked_by before approving';
    END IF;
    IF lower(btrim(r->>'checked_by')) = lower(btrim(r->>'entered_by')) THEN
        RAISE EXCEPTION 'checked_by must be a different person from entered_by (%)', r->>'entered_by';
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

-- Columns that aren't part of what the row says (changing them doesn't need a new check).
CREATE FUNCTION kb_content(r jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
    SELECT r - 'checked_by' - 'description_checked_by' - 'approved' - 'approved_at' - 'review_note'
             - 'created_at' - 'updated_at'
$$;

CREATE FUNCTION kb_review_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    content_changed boolean;
BEGIN
    NEW.updated_at := now();
    IF TG_OP = 'INSERT' THEN
        IF NEW.approved THEN
            RAISE EXCEPTION 'add the row first; a second teammate approves it afterwards';
        END IF;
        NEW.approved_at := NULL;
        RETURN NEW;
    END IF;

    content_changed := kb_content(to_jsonb(NEW)) IS DISTINCT FROM kb_content(to_jsonb(OLD));

    IF OLD.approved AND content_changed THEN
        -- An approved row was edited: it must be checked again.
        NEW.approved := false;
        NEW.approved_at := NULL;
        NEW.checked_by := '';
        IF TG_TABLE_NAME = 'kb_products' THEN
            NEW.description_checked_by := '';
        END IF;
    ELSIF OLD.approved AND NEW.approved AND NEW.checked_by IS DISTINCT FROM OLD.checked_by THEN
        RAISE EXCEPTION 'untick approved before changing checked_by';
    ELSIF NOT OLD.approved AND NEW.approved THEN
        IF content_changed THEN
            RAISE EXCEPTION 'save your edits first; the row is then approved in a separate step';
        END IF;
        PERFORM kb_check_approvable(TG_TABLE_NAME, to_jsonb(NEW));
        NEW.approved_at := now();
    ELSIF NOT NEW.approved THEN
        NEW.approved_at := NULL;
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION kb_review_audit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    key_col text := CASE TG_TABLE_NAME WHEN 'kb_ingredients' THEN 'generic_name'
                                       WHEN 'kb_products' THEN 'product_id' ELSE 'passage_id' END;
BEGIN
    INSERT INTO kb_review_log (table_name, row_key, action, before, after)
    VALUES (TG_TABLE_NAME,
            CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD)->>key_col ELSE to_jsonb(NEW)->>key_col END,
            lower(TG_OP),
            CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
            CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END);
    RETURN NULL;
END
$$;

CREATE TRIGGER kb_ingredients_guard BEFORE INSERT OR UPDATE ON kb_ingredients
    FOR EACH ROW EXECUTE FUNCTION kb_review_guard();
CREATE TRIGGER kb_products_guard BEFORE INSERT OR UPDATE ON kb_products
    FOR EACH ROW EXECUTE FUNCTION kb_review_guard();
CREATE TRIGGER kb_passages_guard BEFORE INSERT OR UPDATE ON kb_passages
    FOR EACH ROW EXECUTE FUNCTION kb_review_guard();
CREATE TRIGGER kb_ingredients_audit AFTER INSERT OR UPDATE OR DELETE ON kb_ingredients
    FOR EACH ROW EXECUTE FUNCTION kb_review_audit();
CREATE TRIGGER kb_products_audit AFTER INSERT OR UPDATE OR DELETE ON kb_products
    FOR EACH ROW EXECUTE FUNCTION kb_review_audit();
CREATE TRIGGER kb_passages_audit AFTER INSERT OR UPDATE OR DELETE ON kb_passages
    FOR EACH ROW EXECUTE FUNCTION kb_review_audit();

-- --- access ---
-- RLS on. The backend role may read approved rows only, and change nothing.
-- The app's public keys (anon, authenticated) open nothing.

ALTER TABLE kb_ingredients ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_products ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_passages ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_review_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON kb_ingredients, kb_products, kb_passages, kb_review_log FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medsight_backend') THEN
        GRANT SELECT ON kb_ingredients, kb_products, kb_passages TO medsight_backend;
        CREATE POLICY backend_kb_ingredients ON kb_ingredients FOR SELECT TO medsight_backend USING (approved);
        CREATE POLICY backend_kb_products ON kb_products FOR SELECT TO medsight_backend USING (approved);
        CREATE POLICY backend_kb_passages ON kb_passages FOR SELECT TO medsight_backend USING (approved);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON kb_ingredients, kb_products, kb_passages, kb_review_log FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON kb_ingredients, kb_products, kb_passages, kb_review_log FROM authenticated;
    END IF;
END
$$;
