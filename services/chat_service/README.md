# Chat Service

SwimBuddz Chat Service provides real-time, persistent, role-aware messaging across cohorts, pods, events, trips, and DMs.

## Features

- Cohort, event, and other group channels (membership derived from upstream services)
- Mentions, reactions, replies, edits, soft-delete
- Image attachment uploads with pre-deliver moderation (AWS Rekognition)
- Pre-persist text moderation (OpenAI Moderation)
- Push notifications fan out via communications_service
- Reports queue + admin moderation (`/admin/chat/*`)
- Internal s2s reconciliation API (`/internal/chat/*`)
- Safeguarding: hard-delete in minor channels gated to `safeguarding_admin`

## API Endpoints

### Channels (member)
- `GET /chat/channels` - List my active channels
- `GET /chat/channels/{id}` - Channel detail
- `POST /chat/channels/{id}/read` - Mark-read up to message
- `POST /chat/channels/{id}/mute` - Mute notifications
- `POST /chat/channels/{id}/leave` - Leave (manual memberships only)

### Messages (member)
- `GET /chat/channels/{id}/messages` - Cursor-paginated history
- `POST /chat/channels/{id}/messages` - Send (idempotent via client UUID)
- `PATCH /chat/messages/{id}` - Edit own
- `DELETE /chat/messages/{id}` - Soft-delete own
- `POST /chat/messages/{id}/reactions` - Add reaction
- `DELETE /chat/messages/{id}/reactions/{emoji}` - Remove own reaction
- `POST /chat/messages/{id}/reports` - Report message

### Attachments (member)
- `POST /chat/attachments` - Upload image (pre-deliver moderation)

### Admin / Moderator
- `GET /admin/chat/channels/{id}` - Inspect any channel
- `POST /admin/chat/channels/{id}/archive` - Archive
- `PATCH /admin/chat/channels/{id}/members/{mid}` - Change role
- `DELETE /admin/chat/channels/{id}/members/{mid}` - Soft-remove
- `DELETE /admin/chat/messages/{id}` - Hard-delete (gated for minor channels)
- `GET /admin/chat/reports` - Reports queue
- `PATCH /admin/chat/reports/{id}` - Resolve / dismiss / assign
- `GET /admin/chat/audit` - Audit log
- `GET /admin/chat/safeguarding/health` - Safeguarding-admin role check

### Internal (service-to-service)
- `POST /internal/chat/channels/ensure` - Idempotent create-or-fetch by parent entity
- `POST /internal/chat/memberships/reconcile` - Add/remove member from upstream parent change

## Database Tables

- `chat_channels` - Channels (group / broadcast / direct)
- `chat_channel_members` - Membership rows (soft-leave only)
- `chat_messages` - Messages (soft-delete only)
- `chat_message_reactions` - Reactions (composite PK)
- `chat_message_reports` - Moderation queue
- `chat_audit_log` - Append-only audit trail

## Upstream Integrations

- `academy_service` calls `channels/ensure` on cohort create and `memberships/reconcile` on enrollment / dropout
- `events_service` calls `channels/ensure` on event create and `memberships/reconcile` on RSVP create / update

## Environment Variables

See `.env.dev` for required configuration:
- `DATABASE_URL` - PostgreSQL connection string
- `OPENAI_API_KEY` - Optional; enables text moderation
- `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` - Optional; enables image moderation
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` - Required for attachment uploads to `chat-attachments` bucket

## Running

```bash
# Via Docker
docker-compose up chat-service

# Standalone (dev)
cd services/chat_service
uvicorn app.main:app --host 0.0.0.0 --port 8016 --reload
```

## Port

Default: `8016`

## Related

- Design: [docs/design/CHAT_SERVICE_DESIGN.md](../../../docs/design/CHAT_SERVICE_DESIGN.md)
- Moderation lib: [libs/moderation/README.md](../../libs/moderation/README.md)
- API endpoints: [docs/API_ENDPOINTS.md](../../docs/API_ENDPOINTS.md) §16
