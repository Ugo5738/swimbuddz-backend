# Session Media Vault

The Media Vault is a private, session/date-scoped capture and review product.
It is deliberately separate from the public gallery.

## Data and access model

- Every vault belongs to exactly one session or event and records the capture
  date, venue, upload window, checklist, consent notice, storage limits and
  retention intent.
- `media` volunteer assignments become expiring `contributor` grants.
  `gallery_support` assignments become `curator` grants. Admins can sync these
  assignments or add/revoke grants manually.
- An admin can create an expiring, revocable guest capability link. Only its
  SHA-256 hash is stored. The plaintext link is shown once.
- Originals live under `vaults/<date>/<vault-id>/originals/...` in the private
  bucket. Published items are server-side copied to
  `gallery/vaults/<vault-id>/...` in the public bucket after both editorial
  approval and consent clearance.

## Large-file upload contract

The API initiates S3 multipart uploads and returns a dynamically selected part
size that remains below S3's 10,000-part ceiling. The browser requests signed
part URLs in small windows, uploads three parts concurrently, records every
ETag, retries transient failures, persists resumable state locally, and calls
complete only after all parts succeed. The API then performs `HeadObject` and
accepts the file only when its exact byte length matches the declaration.

The API process never receives or buffers the original bytes. HEIC, HEIF, MOV,
MP4, M4V and normal phone image/video formats are accepted. The original object
is immutable from the product's perspective and is never automatically
transcoded. Curators may explicitly request a smaller review proxy; this writes
to `vault-derivatives/` and leaves the original untouched.

## Review, consent and publication

Curators can filter, search, rate, shortlist, approve/reject, mark consent
clear/restricted, flag sample-fingerprint duplicate candidates, request review
previews, download originals, build asynchronous ZIP exports, and publish
approved/consent-cleared selections. Takedown requests immediately set the
source item to `takedown` consent status so it cannot pass publication checks.
Original ZIP exports stream through a bounded-memory multipart writer directly
into S3, so archive size is not limited by media-worker container disk.

## Transfer ledger

`media_transfer_logs` records actor, vault, object/export, direction, transfer
type, delivery method, time, IP/user-agent, authorized bytes, completed bytes
and measurement source.

- Upload completion is measured by S3 `HeadObject`, so its byte count is exact.
- Presigned download authorization is exact as a maximum and always recorded.
- A browser completion callback can populate completed download bytes, but it
  cannot be treated as billing-grade because a signed S3 request bypasses the
  application. The scheduled media worker ingests S3 server access logs,
  deduplicates AWS request IDs and updates matched ledger rows with
  `measurement_source=s3_access_log` and the actual response bytes.

The dashboard shows reconciled bytes and pending authorized estimates. S3
server access logs are best-effort, so AWS Billing/Cost and Usage Reports remain
authoritative and one vault's usage is not presented as the account-wide
allowance total.

## Production setup

1. Set `STORAGE_BACKEND=s3`, both bucket names and the AWS region.
2. Apply `docs/operations/media-vault-private-bucket-cors.json` to the private
   bucket. `ETag` must remain in `ExposeHeaders` for multipart completion.
3. Adapt the IAM policy placeholders to the production bucket names and attach
   it to the media service/worker identity.
4. Apply the lifecycle file. It only clears stale multipart uploads and
   generated exports/derivatives; it does not delete originals.
5. Follow `docs/operations/MEDIA_VAULT_BANDWIDTH_RECONCILIATION.md` to provision
   the dedicated access-log bucket and enable logging on the private bucket.
6. Keep S3 Block Public Access enabled on both private buckets.
7. Run the media Alembic migration.
8. Deploy/restart both `media-service` and `media-worker`.
9. Test with an iPhone HEIC photo and a multi-part MOV file from each production
   frontend origin.

Private CloudFront delivery is optional. Do not reuse a public distribution for
private originals. It needs Origin Access Control, a trusted key group and
signed URLs/cookies. Setting the three `MEDIA_PRIVATE_CLOUDFRONT_*` variables
activates signed CloudFront downloads; S3 presigned downloads remain the
default.
