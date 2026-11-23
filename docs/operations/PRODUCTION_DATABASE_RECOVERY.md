# Production migration recovery gate

`deploy-production.sh` captures the running release before replacing the checkout
or decrypting its environment. After pulling images, and **before migrations or
service replacement**, `backup_database.py` must successfully:

1. Connect through `DATABASE_SESSION_URL` or `DATABASE_DIRECT_URL` (5432).
   Transaction-mode port 6543 is rejected; the snapshot must stay on one backend.
2. Open a read-only repeatable-read transaction, export its snapshot, count every
   `public`/`auth` table and dump both schemas using that same snapshot.
3. Restore the archive into a fresh PostgreSQL 17 container with no network.
   Preserve archive ACLs/policies; bootstrap platform role names and the required
   crypto/UUID extensions locally, without copying role passwords.
4. Compare **all** restored table counts to the snapshot counts. Record the
   archive SHA-256, size, migration versions and verification time in
   `database/verified.json`. Remove the disposable container and its volume.

Any failure stops deployment. There is no skip-backup deployment flag.
PostgreSQL tools run through `postgres:17-alpine`, matching production's major
version; failure to pull/run it also stops deployment. Detailed errors stay in
the private `database/verification.log`, because database errors may contain
sensitive information. Connection credentials are passed through a temporary
0600 Docker env file and removed on exit, never placed in command arguments.

## Retention and scope

Recovery points live outside Git at
`/home/deploy/operational-backups/releases/<UTC timestamp>-<PID>/` (override the
parent with deployment-shell `RECOVERY_ROOT`). Directories are 0700 and files
0600. Each deployment creates a new point, and `deploy-production.sh` separates
**database/manifest retention** from **local Docker rollback image retention**:

- **Database backups & release manifests (~6 MB each):** Never deleted
  automatically. Keep at least 5+ verified points (`database/database.dump`,
  `database/verified.json`, `.env.prod`, `compose-shell.env`, `release.json`)
  on disk.
- **Local Docker rollback images (`prune_rollback_images.py`):** Automatically
  retains all currently running container images, active canary/incident pins
  under `/home/deploy/operational-backups/canaries/*/image-pin.json` (plus any
  release directory containing a `.keep-images` marker), required infrastructure
  images (`postgres:17-alpine`, `redis:7-alpine`, `traefik:v3`), the in-flight
  release's newly captured point, and the latest **2 distinct verified**
  rollback image sets (`ROLLBACK_IMAGE_RETENTION_COUNT=2`, configurable).
  Older `swimbuddz-rollback:*` tags, superseded failed-attempt tags, and
  orphaned non-running `ugodaniels/swimbuddz-*:latest` leftovers are untagged
  and pruned automatically before `compose pull` and after successful
  deployment, recording a `retired-images.json` receipt in each retired
  verified point directory.
- **Pre-pull disk headroom gate:** Before `compose pull`, deployment verifies at
  least `MIN_DEPLOY_FREE_GIB` (default `15` GiB) free on Docker's storage mount
  after pruning so a release fails fast instead of filling the root disk
  mid-pull.

### Recovering from a full Docker filesystem

1. Check the mount holding the deployment checkout and Docker's data. When
   Docker uses the containerd image store, check its mount as well.

   ```bash
   df -h /home/deploy/swimbuddz-backend "$(docker info --format '{{.DockerRootDir}}')"
   df -h /var/lib/containerd
   df -i /var/lib/containerd
   docker system df
   ```

2. Run `python3 scripts/deployment/prune_rollback_images.py --dry-run` (and then
   `--apply`) to retire any local rollback tags beyond the retained window,
   orphaned failed-pull `:latest` images, dangling images, and unused build
   cache while preserving all running images, canary/incident pins, the latest
   retained verified image sets, and every database backup. To pin an extra
   incident release indefinitely, touch `.keep-images` inside that release
   directory before pruning.
3. Once the host has at least `MIN_DEPLOY_FREE_GIB` free for the release and
   backup verification, retry the failed deploy job. Check the verified
   restore, service status, public gateway health, canary pool flags, and
   post-deploy disk usage.

The private point includes the prior `.env.prod`, Compose `.env` when present,
Compose file, shell interpolation values (`compose-shell.env`), running image
IDs, local rollback tags and Compose image override.
Do not upload these files/dumps as CI artifacts, commit them, or paste them into
support tickets. CI uses synthetic data only.

The archive covers application/payment/member/media records and Supabase Auth
database records. It does **not** copy S3/Storage objects, platform configuration,
JWT signing/encryption keys, external bank records or Redis. It remains on the
server's persistent disk: this protects a migration rollout, not loss of that
server or a complete Supabase disaster. Separate encrypted off-host disaster
recovery remains an operational requirement; do not call this point-in-time
recovery or assume it captures transactions committed after the snapshot.

## Rollback and partial deployment

On any migration/start/verification failure, stop the release and inspect the
private logs. Migrations run per service and can partially complete. Check each
`alembic_version*` table against the saved versions before acting. Never retry a
partially completed financial operation or automatically restore a live archive.

For the November 16 release, the only new migration is media
`a16f4b92c817` (previous `f18a47d20311`): additive retry columns/index plus
classification of derivative evidence, with no record/byte deletion. The prior
application can run with these additional columns left in place. Prefer an
application rollback with the additive schema retained. An operator-approved
media downgrade is available if separately necessary; do not downgrade other
services blindly.

To roll back application images, pause writers/workers first, select the exact
verified point, restore its private environment into the deployment directory,
and run Compose with its saved file plus `rollback-images.json`. Use the original
project name and `--project-directory /home/deploy/swimbuddz-backend` so runtime
volume paths remain correct. Restore the saved Compose `.env` and pass
`--env-file <point>/compose-shell.env` for the captured shell interpolation values.
`pull_policy: never` uses the locally pinned
images rather than mutable `latest`. Verify health and member/payment/media
reads before restarting workers. Reconcile source changes through Git, not
container edits.

If data recovery is required, enter a maintenance window, stop **all** database
writers including workers/payment webhooks and Auth writes, take another backup
of the current state, and account for every transaction after the selected
snapshot. Validate restoration into a separate target first. Restore only the
affected schema/data under an explicit recovery plan; restoring Auth into managed
Supabase also requires its platform-aware procedure and ownership review. Do
not run the isolated-drill bootstrap against production or use a blanket
`DROP SCHEMA` on the live project. Reopen writes only after payment/ledger,
membership, attendance, media accounting and login checks agree.

## Release evidence — November 16, 2026

The production drill at **08:27:22 UTC** restored **304 tables / 62,175 rows**
from a **6,005,872-byte** archive. Every table count matched the exported
snapshot. SHA-256:
`e5f6d23c6996c1771442e2eb828955b0f8274db8c910a76819d5194abe2ba60b`.
The point is `readiness-20261116`; 37 running-image rollback tags were captured.
The disposable restore container/volume was removed and production remained on
the previous release. The deployment gate creates a **fresh** backup at release
time; this earlier drill is evidence, not a substitute for that gate.
