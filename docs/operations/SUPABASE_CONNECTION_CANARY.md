# Members/Chat connection canary — 17 November 2026

## Production activation — 19 November 2026

The owner separately approved the Members-only test. At 03:00 UTC (04:00 Lagos),
only that API was recreated, using its existing image with a one-connection,
zero-overflow cap. Chat and media reuse remain off. The GitHub **production
Environment** variable `MEMBERS_API_DB_USE_LOCAL_POOL` is now true, so normal
GitHub deployments preserve this test; the source-code defaults remain false.

The source revision was `1dc2fc6a76af098a5c4c3b972694b04ad350806a`. The previous
image and non-secret manifest are retained privately under
`/home/deploy/operational-backups/canaries/members-20261119T025926Z`.
For immediate rollback, set the production flag false and recreate only Members
with the pinned image/configuration. Shell deployments must export the flag;
putting it only in a container env file does not control Compose interpolation.

Before activation, a 300.88-second passive sample counted 80 total connections
and 587,325 received TCP payload bytes. Members contributed 11 connections /
73,591 bytes; Chat contributed 33 / 221,220. Service shares vary by scheduled
workload. These encrypted TCP/protocol measurements are not provider-billed
egress. Initial request/socket checks do not prove a sustainable daily rate;
compare a complete provider-usage day before evaluating Chat.

The owner approved expanding the previous media-worker-only **configuration**
scope to a default-off Members API and Chat API canary. This does not authorize
turning it on automatically. No global pooling change or schema migration is
part of this release.

## Measurements and limits

Production was on `cc81aeb516ed1d06adc18bc55211dccaee2669f8` when inspected.
All 35 database-configured containers used transaction-mode port 6543; shared
local pooling was disabled. There was no new restart/OOM pattern. The previous
media retry migration was active: all 19,711 derivative events were unattributed,
917 unmatched originals expired, and the 30 matched records/byte totals remained
intact. The historical expensive media lookup stayed at 20,928 calls.

A passive 06:53:03–06:59:03 UTC sample counted 260 new TCP connections and
2,108,283 received TCP-payload bytes with no observer packet drops. Members
contributed 87 connections / 1,045,928 bytes; Chat 87 / 554,254; Communications
48 / 209,007. Together these were about 86% of connections and received bytes.
The sample includes scheduled bursts but not the hourly report refresh at minute
10. Do not extrapolate it as a complete day's provider bill.

A separate isolated, read-only probe executed eight `SELECT 1` operations in each
mode. NullPool opened eight connections and received 36,232 TCP-payload bytes;
one retained connection received 6,674 bytes (about 82% less), including pre-ping.
This establishes significant connection overhead, not an 82% forecast for normal
application egress. TCP measurements include encrypted TLS/protocol traffic and
possible retransmissions. No packet contents or member records were stored.

## Configuration and budget

| API | Compose/deployment switch | Pool cap per process | Application name |
| --- | --- | --- | --- |
| Members | `MEMBERS_API_DB_USE_LOCAL_POOL` | 1, overflow 0 | `swimbuddz-members-api` |
| Chat | `CHAT_API_DB_USE_LOCAL_POOL` | 1, overflow 0 | `swimbuddz-chat-api` |

Both switches default to **false**. Production currently runs one process for
each API, so enabling both retains at most two client connections for this
canary. Replicas or additional Uvicorn workers multiply that budget; reassess
before scaling. These are Supavisor client connections, not a guarantee of two
dedicated Postgres backends in transaction mode. Other callers still need
capacity. Keep the existing media pilot off during this comparison.

The API overrides preserve psycopg `prepare_threshold=None`, SSL, keepalives,
pool pre-ping, existing timeout/recycle settings and generative/moderation key
boundaries. Workers and all other services retain their previous settings.
`DB_APPLICATION_NAME` is optional (maximum 63 characters) and tags new client
connections for diagnostics where the platform exposes the client name.

## Rollout and rollback

1. Merge the feature to develop, verify CI, and promote develop to main through
   the existing release workflow. Deploy initially with both flags false. Verify
   the prospect announcement error is gone and membership/payment reads agree.
2. For a separately approved canary deployment, set only
   `MEMBERS_API_DB_USE_LOCAL_POOL=true` in the GitHub **production Environment
   variables** (repository variables also work). Both automatic and manual
   deployment workflows forward these switches to Compose. No encrypted
   `.env.prod` edit is required. For shell deployments, export the switch in the
   Compose invocation environment; it is not activated merely inside a
   container's env file.
3. Check the running Members container's flag and one/zero cap, application
   name, health, latency, queue timeouts and connection errors. Compare matched
   time windows and scheduled workloads, then one complete day of actual
   Shared Pooler Egress and log ingestion. Do not reset production statistics.
4. Only after the Members result is acceptable, evaluate Chat independently by
   setting its switch true on a subsequent deployment. Check chat authorization,
   role reconciliation and moderation as well as connection/queue errors.
5. Roll back a canary by setting its switch false and recreating that API through
   the deployment workflow. No data/schema rollback is required. The recovery
   manifest captures the **previous running flags**, rather than the next
   deployment's requested flags, and pins prior images/configuration for a full
   application rollback. See `PRODUCTION_DATABASE_RECOVERY.md`.

The 100 MB/day normal-egress and 20 MB/day log-ingestion targets remain unproven.
The November 16 screenshots show a mixed day with multiple deployments and the
old workload before release, not a clean post-change 24-hour baseline.

## Accompanying confirmed fixes

Announcement filters previously passed the membership label `prospect` into the
community/club/academy database enum, repeatedly failing unread polls (108 such
failures in 00:00–06:30 UTC). Filters now intersect with valid audience values,
preserving existing public-community and programme visibility without granting
entitlements or changing the audience enum/schema.

The busy membership pending-reference and Academy projection routes previously
loaded full profiles before/after mutation (and redundantly on refresh). They
now lock the member without nullable joins, load only membership during mutation,
and join the complete response once. PostgreSQL regressions cover three SELECTs,
full profile serialization, unchanged independent programme periods, no-op
members without membership, and conditional clearing of an older pending
reference. No production financial records are altered by the investigation.
