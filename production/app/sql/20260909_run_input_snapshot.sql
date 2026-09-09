-- Apply explicitly with psql -v ON_ERROR_STOP=1 -f ... to the intended database.
-- Additive and idempotent. Never backfill old runs from today's mutable catalog.
BEGIN;
ALTER TABLE public.app_case_run ADD COLUMN IF NOT EXISTS input_snapshot JSONB;
COMMENT ON COLUMN public.app_case_run.input_snapshot IS
    'Versioned inputs captured before optimization. NULL means not captured, not zero.';

-- A snapshot belongs to its INSERT. Even NULL -> object backfills are disallowed.
CREATE OR REPLACE FUNCTION public.pulso_preserve_run_inputs() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.input_snapshot IS DISTINCT FROM OLD.input_snapshot THEN
        RAISE EXCEPTION 'Study inputs cannot be changed; create a new execution';
    END IF;
    RETURN NEW;
END;
$$;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'pulso_preserve_run_inputs'
                   AND tgrelid = 'public.app_case_run'::regclass) THEN
        CREATE TRIGGER pulso_preserve_run_inputs BEFORE UPDATE ON public.app_case_run
        FOR EACH ROW EXECUTE FUNCTION public.pulso_preserve_run_inputs();
    END IF;
END;
$$;
COMMIT;
