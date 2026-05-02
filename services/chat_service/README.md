# Chat Service

Real-time, persistent, role-aware messaging across SwimBuddz.

**Port:** 8016
**Status:** Phase 0 — scaffolding (service boots with `/health` only)

---

## Overview

The Chat Service provides in-app chat for every SwimBuddz surface that needs group or direct communication: Academy cohort channels, Club pod channels, event channels, transport trip channels, coach ↔ parent DMs, support DMs, location/community channels, and alumni.

It is **not** the notifications service — push notifications are dispatched via `communications_service`. Chat emits events that the notification dispatcher picks up.

## Design doc

Full architecture, data model, safeguarding rules, integration points with other services, and phased rollout are in:

**[docs/design/CHAT_SERVICE_DESIGN.md](../../../docs/design/CHAT_SERVICE_DESIGN.md)**

Read it before making changes here.

## Current state (Phase 0)

What exists:

- Service scaffold (`Dockerfile`, `app/main.py`, `alembic/`, empty `models/` `routers/` `schemas/` `services/` packages)
- Registered in `docker-compose.yml` on port 8016
- Proxied by `gateway_service` on `/api/v1/chat/*` and `/api/v1/admin/chat/*`
- Alembic configured with an empty `SERVICE_TABLES` set, ready for Phase 1 models
- `/health` endpoint

What doesn't exist yet (Phase 1+):

- All chat models, routers, schemas
- Real-time transport (Supabase Realtime)
- Safeguarding enforcement
- Moderation providers (OpenAI text, AWS Rekognition image)
- Frontend integration

## Running locally

```bash
# From swimbuddz-backend/
docker compose up chat-service

# Health check
curl http://localhost:8016/health
# → {"status":"ok","service":"chat"}
```

## Migrations (Phase 1+)

Once models are added, generate migrations like every other service:

```bash
./scripts/db/migrate.sh chat_service "description"
./scripts/db/reset.sh dev
```

**Remember:** when adding a new model, update `services/chat_service/alembic/env.py` — import the model AND add its table name to `SERVICE_TABLES`. Without this, Alembic won't detect the new table.

## Related

- Design: [docs/design/CHAT_SERVICE_DESIGN.md](../../../docs/design/CHAT_SERVICE_DESIGN.md)
- Notifications (separate service): [docs/design/NOTIFICATION_ARCHITECTURE.md](../../../docs/design/NOTIFICATION_ARCHITECTURE.md)
- Service registry: [docs/reference/SERVICE_REGISTRY.md](../../../docs/reference/SERVICE_REGISTRY.md)
