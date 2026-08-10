# SwimBuddz Server Migration Runbook

This runbook assumes:

- PostgreSQL and media storage remain external.
- Redis may start empty on the new server.
- `api.swimbuddz.com` remains the production API hostname.
- The old and new servers must never run background workers simultaneously.

## Stage 1 — Prepare the repository

Create a branch:

```bash
git checkout -b infra/provider-independent-deployment
```

Copy this kit into the root of `swimbuddz-backend`:

```text
scripts/deployment/_common.sh
scripts/deployment/bootstrap-server.sh
scripts/deployment/deploy-production.sh
scripts/deployment/stop-workers.sh
scripts/deployment/start-workers.sh
scripts/deployment/stop-stack.sh
scripts/deployment/verify-production.sh
.github/workflows/deploy-server.yml
```

Make the scripts executable:

```bash
chmod +x scripts/deployment/*.sh
```

Apply `docker-compose-changes.txt`.

Before merging, create these GitHub Environments:

### `production`

Environment secrets:

```text
SERVER_HOST       = current DigitalOcean IP
SERVER_USERNAME   = current deployment username
SERVER_SSH_KEY    = current DigitalOcean private SSH key
```

### `candidate`

Later, add:

```text
SERVER_HOST       = new provider's server IP
SERVER_USERNAME   = deploy
SERVER_SSH_KEY    = private SSH key for the new server
```

Keep these existing repository secrets:

```text
DOCKER_USERNAME
DOCKER_PASSWORD
REPO_URL
ENV_FILE_GPG_PASSPHRASE
LE_EMAIL
```

Apply `existing-workflow-changes.txt` so automatic deployments use the
`production` GitHub Environment instead of DigitalOcean-specific secret names.

Commit and push:

```bash
git add .
git commit -m "Make production deployment provider independent"
git push -u origin infra/provider-independent-deployment
```

Open and merge the pull request only after the `production` environment
contains the current DigitalOcean credentials.

## Stage 2 — Prove the new scripts on DigitalOcean

Run the manual **Deploy SwimBuddz Server** workflow with:

```text
target_environment: production
api_host: api.swimbuddz.com
start_workers: true
branch: main
```

Confirm:

```text
https://api.swimbuddz.com/health
```

returns:

```json
{"status":"ok","service":"gateway"}
```

Do not continue until the provider-independent script successfully redeploys
the existing DigitalOcean production server.

## Stage 3 — Create and bootstrap the new server

Provision an Ubuntu 22.04 or 24.04 server.

Recommended minimum for the current all-in-one stack:

```text
8 vCPU
16 GB RAM
80 GB SSD
Static public IPv4
```

From your computer, copy the bootstrap script:

```bash
scp scripts/deployment/bootstrap-server.sh root@NEW_SERVER_IP:/tmp/
```

Run it:

```bash
ssh root@NEW_SERVER_IP
REPO_URL=https://github.com/Ugo5738/swimbuddz-backend.git \
  bash /tmp/bootstrap-server.sh
exit
```

Reconnect once as `deploy`:

```bash
ssh deploy@NEW_SERVER_IP
docker version
docker compose version
exit
```

## Stage 4 — Create a candidate hostname

In DNS, create:

```text
candidate-api.swimbuddz.com  A  NEW_SERVER_IP
```

Set TTL to 300 seconds.

Also reduce the TTL for `api.swimbuddz.com` to 300 seconds before cutover.

Add the new server credentials to the GitHub `candidate` Environment.

## Stage 5 — Deploy the candidate without workers

Run the manual **Deploy SwimBuddz Server** workflow:

```text
target_environment: candidate
api_host: candidate-api.swimbuddz.com
start_workers: false
branch: main
```

This starts:

- Traefik
- Fresh Redis
- Gateway
- All domain services required by the gateway

It deliberately does not start background workers.

Check:

```bash
curl -fsS https://candidate-api.swimbuddz.com/health
```

Expected:

```json
{"status":"ok","service":"gateway"}
```

Review the new server:

```bash
ssh deploy@NEW_SERVER_IP
cd /home/deploy/swimbuddz-backend
./scripts/deployment/verify-production.sh
docker ps
docker logs --tail 100 swimbuddz_gateway
```

Because the candidate server has no workers, it should not send prompt emails,
birthday emails, reports, AI jobs, or other scheduled jobs.

## Stage 6 — Switch traffic

Use this order. Do not reverse it.

1. Confirm the candidate health endpoint.
2. Change the DNS A record:

```text
api.swimbuddz.com  ->  NEW_SERVER_IP
```

3. Wait at least one TTL period and confirm DNS:

```bash
dig +short api.swimbuddz.com
```

4. Confirm production health:

```bash
curl -fsS https://api.swimbuddz.com/health
```

5. Stop workers on DigitalOcean:

```bash
ssh deploy@OLD_DIGITALOCEAN_IP
cd /home/deploy/swimbuddz-backend
./scripts/deployment/stop-workers.sh
```

6. Start workers on the new server:

```bash
ssh deploy@NEW_SERVER_IP
cd /home/deploy/swimbuddz-backend
./scripts/deployment/start-workers.sh
```

7. Verify the new server:

```bash
./scripts/deployment/verify-production.sh
```

8. Stop the complete old stack:

```bash
ssh deploy@OLD_DIGITALOCEAN_IP
cd /home/deploy/swimbuddz-backend
./scripts/deployment/stop-stack.sh
```

Do not delete the DigitalOcean droplet yet.

## Stage 7 — Make the new server the normal production target

Update the GitHub `production` Environment:

```text
SERVER_HOST       = NEW_SERVER_IP
SERVER_USERNAME   = deploy
SERVER_SSH_KEY    = new server's private SSH key
```

Run the manual workflow once with:

```text
target_environment: production
api_host: api.swimbuddz.com
start_workers: true
branch: main
```

From then on, normal pushes to `main` deploy to the new server.

## Stage 8 — Rollback

If the new server fails before important new work accumulates:

1. Stop its workers:

```bash
ssh deploy@NEW_SERVER_IP
cd /home/deploy/swimbuddz-backend
./scripts/deployment/stop-workers.sh
```

2. Point `api.swimbuddz.com` back to the DigitalOcean IP.
3. Start the DigitalOcean stack:

```bash
ssh deploy@OLD_DIGITALOCEAN_IP
cd /home/deploy/swimbuddz-backend
START_WORKERS=true ./scripts/deployment/deploy-production.sh
```

4. Restore the `production` GitHub Environment credentials to DigitalOcean.

Redis will be fresh after rollback. Permanent database and media records remain.

## Stage 9 — Decommission DigitalOcean

Keep the old server for at least seven days.

Only delete it after confirming:

- Login and registration
- Session booking
- Payments and webhooks
- Email sending
- Uploads and downloads
- Admin functions
- Prompt-email cron
- AI and media jobs
- Backups and monitoring
