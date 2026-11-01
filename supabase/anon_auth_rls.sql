-- MedSight app: History and My medicines, one owner per row.
--
-- Run once in the Supabase SQL editor (staging first), AFTER turning on
-- Authentication > Sign In / Providers > "Allow anonymous sign-ins".
-- Each app install signs in anonymously (src/services/supabase.ts) and gets
-- its own user ID. These rules let it read and write only its own rows; the
-- public anon key on its own opens nothing.
--
-- Rows written before this change have no user_id. No one can read them
-- afterwards (they belonged to device IDs that every app could see). Delete
-- them with the last statement once you're sure.

-- 1. An owner column on both tables, filled in by the database itself.
ALTER TABLE public.history
    ADD COLUMN IF NOT EXISTS user_id uuid DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE CASCADE;
ALTER TABLE public.my_medicines
    ADD COLUMN IF NOT EXISTS user_id uuid DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE CASCADE;

-- The app no longer sends device_id.
ALTER TABLE public.history ALTER COLUMN device_id DROP NOT NULL;
ALTER TABLE public.my_medicines ALTER COLUMN device_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS history_user_scanned_idx ON public.history (user_id, scanned_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS my_medicines_user_name_key ON public.my_medicines (user_id, lower(name));

-- 2. Row-level security: replace whatever policies exist with "own rows only".
ALTER TABLE public.history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.my_medicines ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE p record;
BEGIN
    FOR p IN SELECT policyname, tablename FROM pg_policies
             WHERE schemaname = 'public' AND tablename IN ('history', 'my_medicines')
    LOOP
        EXECUTE format('DROP POLICY %I ON public.%I', p.policyname, p.tablename);
    END LOOP;
END
$$;

CREATE POLICY history_select_own ON public.history FOR SELECT TO authenticated
    USING (user_id = (SELECT auth.uid()));
CREATE POLICY history_insert_own ON public.history FOR INSERT TO authenticated
    WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY history_delete_own ON public.history FOR DELETE TO authenticated
    USING (user_id = (SELECT auth.uid()));

CREATE POLICY my_medicines_select_own ON public.my_medicines FOR SELECT TO authenticated
    USING (user_id = (SELECT auth.uid()));
CREATE POLICY my_medicines_insert_own ON public.my_medicines FOR INSERT TO authenticated
    WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY my_medicines_update_own ON public.my_medicines FOR UPDATE TO authenticated
    USING (user_id = (SELECT auth.uid())) WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY my_medicines_delete_own ON public.my_medicines FOR DELETE TO authenticated
    USING (user_id = (SELECT auth.uid()));

-- Signed-out requests (the bare anon key) get nothing at all.
REVOKE ALL ON public.history, public.my_medicines FROM anon;

-- 3. Keep only the newest 50 scans per user.
CREATE OR REPLACE FUNCTION public.trim_history_per_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    DELETE FROM public.history
    WHERE user_id = NEW.user_id
      AND id NOT IN (SELECT id FROM public.history WHERE user_id = NEW.user_id
                     ORDER BY scanned_at DESC LIMIT 50);
    RETURN NULL;
END
$$;
REVOKE ALL ON FUNCTION public.trim_history_per_user() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS history_trim_per_user ON public.history;
CREATE TRIGGER history_trim_per_user AFTER INSERT ON public.history
    FOR EACH ROW EXECUTE FUNCTION public.trim_history_per_user();
-- The old per-device trim trigger (if any) now does nothing, since new rows
-- have no device_id. It can be dropped by name from Database > Triggers.

-- 4. Later, once you're sure: remove the old rows nobody can read any more.
-- DELETE FROM public.history WHERE user_id IS NULL;
-- DELETE FROM public.my_medicines WHERE user_id IS NULL;
