-- Web answers in the review console (app/review.py, "Web answers" tab).
--
-- Web-tier answers are spoken with their warning while they wait in `answers`.
-- A reviewer can now reject one or send it to the reviewed dataset as pending
-- rows. Sending never approves anything: a second reviewer still does that.
--
-- Added on top of 0006:
--   * answers.page_text: the page the summary was made from. Answers cached
--     earlier keep ''.
--   * kb_web_sends: which answer became which product, so a sent answer leaves
--     the tab. Its own table: a column on kb_products would count as content
--     (kb_content) and un-approve rows.
--   * audit_log.action also allows 'reject' and 'send_to_kb'
--   * medsight_review may read web answers, set one to rejected, and add its
--     own audit rows. It still has nothing on scan_log (OCR text),
--     rate_limits, or general-tier answers.
--
-- Run this BEFORE deploying the code that goes with it: that code writes
-- page_text, and a missing column would trip the store's circuit breaker.

ALTER TABLE answers ADD COLUMN page_text text NOT NULL DEFAULT '';

ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_check
    CHECK (action IN ('update', 'delete', 'reject', 'send_to_kb'));

-- No foreign key to answers: expired answers are purged, and the record stays.
-- Deleting the unapproved product removes the link, so the answer can be sent again.
CREATE TABLE kb_web_sends (
    cache_key   text PRIMARY KEY,
    product_id  text NOT NULL REFERENCES kb_products (product_id) ON UPDATE CASCADE ON DELETE CASCADE,
    sent_by     text NOT NULL CHECK (btrim(sent_by) <> ''),
    sent_at     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE kb_web_sends ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON kb_web_sends FROM PUBLIC;

GRANT SELECT ON answers TO medsight_review;
GRANT UPDATE (status) ON answers TO medsight_review;
GRANT INSERT ON audit_log TO medsight_review;
GRANT SELECT, INSERT ON kb_web_sends TO medsight_review;

CREATE POLICY review_answers_read ON answers FOR SELECT TO medsight_review USING (tier = 'web');
CREATE POLICY review_answers_reject ON answers FOR UPDATE TO medsight_review
    USING (tier = 'web') WITH CHECK (tier = 'web' AND status = 'rejected');
CREATE POLICY review_audit_add ON audit_log FOR INSERT TO medsight_review
    WITH CHECK (action IN ('reject', 'send_to_kb'));
CREATE POLICY review_web_sends_read ON kb_web_sends FOR SELECT TO medsight_review USING (true);
CREATE POLICY review_web_sends_add ON kb_web_sends FOR INSERT TO medsight_review WITH CHECK (true);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON kb_web_sends FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON kb_web_sends FROM authenticated;
    END IF;
END
$$;
