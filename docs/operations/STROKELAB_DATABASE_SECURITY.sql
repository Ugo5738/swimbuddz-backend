-- StrokeLab restricted Data API access.
-- Applied and independently verified on swimbuddz-prod on 2026-11-21.
-- Apply through controlled schema migration in each other environment.
-- These tables are owned by FastAPI/SQLAlchemy services, not directly by
-- browser Supabase clients. Service-role/Postgres access is retained.
BEGIN;
ALTER TABLE public.swim_analysis_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.swim_analysis_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.swim_frame_labels ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analyzer_credit_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analyzer_credit_ledger ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE
  public.swim_analysis_jobs,
  public.swim_analysis_results,
  public.swim_frame_labels,
  public.analyzer_credit_accounts,
  public.analyzer_credit_ledger
FROM anon, authenticated, PUBLIC;
COMMIT;

-- Post-deploy acceptance: all rows must be RLS=true,
-- anon_select=false, auth_select=false, service_select=true.
SELECT c.relname AS table_name,
       c.relrowsecurity AS rls,
       has_table_privilege('anon', c.oid, 'SELECT') AS anon_select,
       has_table_privilege('authenticated', c.oid, 'SELECT') AS auth_select,
       has_table_privilege('service_role', c.oid, 'SELECT') AS service_select
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN (
    'swim_analysis_jobs','swim_analysis_results','swim_frame_labels',
    'analyzer_credit_accounts','analyzer_credit_ledger'
  )
ORDER BY c.relname;

-- NOTE: This fixes only StrokeLab's five tables. The production Supabase
-- advisor flagged 268 public-schema tables when reviewed. Audit them service
-- by service; a blanket 268-table permission flip can break app features.
