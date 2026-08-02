# Media Vault bandwidth reconciliation

The application records every original/export download authorization
immediately. The media worker then reads S3 server access logs every 15 minutes,
deduplicates requests by AWS request ID, matches each request to the most likely
authorization using object key, signed-URL time window, IP address and user
agent, and stores the bytes S3 actually sent.

S3 access-log delivery is delayed and best-effort. A matched request is an AWS
measurement, but a missing request is not proof that no data was sent. The
dashboard therefore keeps authorized bytes as a conservative estimate until an
AWS request replaces them. AWS Billing/Cost and Usage Reports remain
authoritative for the account-wide bill and shared free allowance.

## Provision the log destination

Create a dedicated, private general-purpose S3 bucket in the same AWS account
and Region as `AWS_S3_BUCKET_PRIVATE`. Do not use the media source bucket as its
own log destination, because that creates a logging loop. The destination must:

- use Bucket owner enforced Object Ownership;
- have S3 Block Public Access enabled;
- use SSE-S3 rather than default SSE-KMS;
- not have Object Lock default retention or Requester Pays enabled.

Replace all placeholders in these files before applying them:

- `media-vault-log-bucket-policy.json`
- `media-vault-s3-access-logging.json`
- `media-vault-log-bucket-lifecycle.json`
- `media-vault-iam-policy.json`

`REPLACE_MEDIA_WORKER_PRINCIPAL_ARN` must be the exact IAM role/user used by
the deployed media worker. The log-bucket policy grants that one principal
read-only access to the log prefix. The equivalent statements in
`media-vault-iam-policy.json` can instead be attached to the worker identity;
one of these two read grants is sufficient.

Apply and verify the configuration:

```bash
aws s3api put-bucket-policy \
  --bucket REPLACE_LOG_BUCKET \
  --policy file://docs/operations/media-vault-log-bucket-policy.json

aws s3api put-bucket-lifecycle-configuration \
  --bucket REPLACE_LOG_BUCKET \
  --lifecycle-configuration file://docs/operations/media-vault-log-bucket-lifecycle.json

aws s3api put-bucket-logging \
  --bucket REPLACE_PRIVATE_BUCKET \
  --bucket-logging-status file://docs/operations/media-vault-s3-access-logging.json

aws s3api get-bucket-logging --bucket REPLACE_PRIVATE_BUCKET
```

Ensure the media worker can list and read only the configured log prefix using
the log-bucket resource policy or the adapted identity policy.

## Application configuration

Set the following in the media service and media worker production environment:

```dotenv
MEDIA_VAULT_ACCESS_LOG_BUCKET=REPLACE_LOG_BUCKET
MEDIA_VAULT_S3_ACCESS_LOG_PREFIX=media-vault/s3/
MEDIA_VAULT_ACCESS_LOG_LOOKBACK_DAYS=7
MEDIA_VAULT_ACCESS_LOG_MAX_OBJECTS_PER_RUN=500
```

Apply the media Alembic migration, then restart both `media-service` and
`media-worker`. The dashboard reports whether reconciliation is configured and
the last successfully processed log time. Log delivery can take a few hours.

## Verification

1. Authorize and complete an original download from a member curator account.
2. Confirm an `authorized` row appears immediately in `media_transfer_logs`.
3. Wait for S3 to deliver a log object under `media-vault/s3/`.
4. Run the scheduled job or invoke
   `reconcile_vault_bandwidth()` from the media worker environment.
5. Confirm the transfer is `completed`, `measurement_source` is
   `s3_access_log`, and `bytes_transferred` equals the sum of matching AWS log
   requests (including range requests and retries).
6. Confirm the request exists in `media_access_log_events` and the source file
   exists in `media_access_log_objects` with `status=completed`.

If private CloudFront delivery is activated later, do not use S3 origin logs as
internet-egress measurements. Add CloudFront standard-log ingestion and switch
the reconciliation source for `cloudfront_signed` transfers before relying on
the internal dashboard for those downloads.
