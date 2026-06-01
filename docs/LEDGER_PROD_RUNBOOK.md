# Ledger Service — Production Deployment Runbook

What's automated by the CD pipeline vs. what an operator must do once, to bring
`ledger_service` live in production.

> **Prod status (2026-06-01): seeded and live.** Org
> `837da376-6700-4c8d-9000-24a81ebccaf9` (SwimBuddz, NGN), 68-account
> `sports_club` chart of accounts, owner `admin@admin.com`.
> `LEDGER_DEFAULT_ORG_ID` is stored in the encrypted `.env.prod`
> (`.env.prod.gpg`) and read via `env_file` — not pinned in compose. The steps
> below are the canonical procedure for **a fresh environment** (staging, a new
> region, or a re-seed).

## Automated by `.github/workflows/deploy.yml` (on push to `main`)

1. **Image build** — `swimbuddz-ledger-service` is in the build matrix, so the
   image is built + pushed to Docker Hub alongside the other services.
2. **Migrations** — the deploy step runs `migrate-prod.sh --all`, which now
   includes `ledger_service` (creates the ledger tables + RLS) **and**
   `payments_service` (the `ledger_post_failures` dead-letter table).
3. **Service up** — `docker-compose.prod.yml` has a `ledger-service` block, and
   the gateway `depends_on` it; `docker compose ... up -d` starts it. The gateway
   already proxies `/api/v1/admin/finance/*` → ledger-service.

## One-time operator steps (the part CD can't do)

The org + chart of accounts must be **seeded** once, and `LEDGER_DEFAULT_ORG_ID`
must be set in the prod env. Do this to avoid the chicken-and-egg (the seed can
use a pre-chosen id):

1. **Pre-generate the org UUID:**
   ```bash
   python -c "import uuid; print(uuid.uuid4())"
   ```
2. **Add it to `.env.prod`** and re-encrypt (see `DEPLOY_ENV_GPG.md`):
   ```
   LEDGER_DEFAULT_ORG_ID=<that-uuid>
   ```
   `.env.prod` is shared by all services, so both `ledger-service` (for
   `resolve_org_id`) and `payments-service` (the emitter) pick it up.
3. **Deploy** (merge `develop` → `main`). CI builds the image, runs migrations,
   brings the service up with `LEDGER_DEFAULT_ORG_ID` set.
4. **Seed the org** (once), via the **gateway** image. The `ledger-service`
   image doesn't carry the top-level `scripts/` dir, but the gateway image does
   (it also runs `migrate-prod.sh`):
   ```bash
   docker compose -f docker-compose.prod.yml run --rm --no-deps \
     gateway python scripts/seed/ledger_org.py
   ```
   The seed reads `LEDGER_DEFAULT_ORG_ID`, creates the SwimBuddz org with that
   id, seeds the `sports_club` chart of accounts, and creates the owner
   `LedgerUser` (by `ADMIN_EMAIL`). Idempotent — safe to re-run.
   (On images built **before** the `coa_templates` package-data fix, prepend
   `-e PYTHONPATH=/app` so the CoA YAML resolves from the source-tree copy.)

After step 4: `/admin/finance/*` resolves, and payment emits post live.

## Before the seed (expected, harmless)

Between the service coming up (step 3) and the seed (step 4):
- `/api/v1/admin/finance/*` returns **503** ("Ledger organization not configured").
- Payment emits **dead-letter** into `ledger_post_failures` (payments themselves
  still succeed — the dead-letter + replay design protects them).

Drain the backlog after seeding (also via the gateway image — `scripts/` isn't
in the `payments-service` image either):
```bash
docker compose -f docker-compose.prod.yml run --rm --no-deps \
  gateway python scripts/ledger/replay_ledger_failures.py
```

## RLS note

RLS policies are enabled + forced on every ledger table, but the prod DB role
likely has `BYPASSRLS` (like dev's `postgres`), so RLS is inert. That's fine
while SwimBuddz is the only org. **Before onboarding a second (B2B) org**,
provision a non-`BYPASSRLS` role and repoint the ledger connection — see the
"non-BYPASSRLS role" task. App-level `org_id` filtering is the active guard
until then.

---

*Last updated: 2026-06-02*
