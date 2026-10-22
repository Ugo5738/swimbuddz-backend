#!/usr/bin/env bash
set -Eeuo pipefail
root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT
sync_script="$(cd "$(dirname "$0")/../deployment" && pwd)/sync-repository.sh"

git init -q --bare "$root/origin.git"
git clone -q "$root/origin.git" "$root/author"
(
 cd "$root/author"
 git config user.name Test
 git config user.email test@example.test
 git checkout -qb main
 echo old > tracked.txt
 echo .env.prod > .gitignore
 git add .
 git commit -qm initial
 git push -q origin main
)
git clone -q -b main "$root/origin.git" "$root/production"
(
 cd "$root/production"
 git config user.name Test
 git config user.email test@example.test
 echo modified > tracked.txt
 echo local > incoming.txt
 echo ignored-secret > .env.prod
 mkdir letsencrypt
 echo certificate > letsencrypt/acme.json
)
(
 cd "$root/author"
 echo release > tracked.txt
 echo upstream > incoming.txt
 git add .
 git commit -qm release
 git push -q origin main
)
cd "$root/production"
git fetch -q origin main
sha="$(git rev-parse origin/main)"
export DEPLOY_PATH="$PWD" DEPLOY_BRANCH=main DEPLOY_SHA="$sha"
export DEPLOY_BACKUP_ROOT="$root/backups"
bash "$sync_script"
test "$(cat tracked.txt)" = release
test "$(cat incoming.txt)" = upstream
test "$(cat .env.prod)" = ignored-secret
test "$(cat letsencrypt/acme.json)" = certificate
test "$(git show stash@{0}:tracked.txt)" = modified
test "$(cat "$root"/backups/*/untracked/incoming.txt)" = local
bash "$sync_script"
test "$(git rev-parse HEAD)" = "$sha"
if DEPLOY_SHA=bad00000 bash "$sync_script"; then
 echo 'invalid release SHA accepted' >&2
 exit 1
fi
test "$(git rev-parse HEAD)" = "$sha"
echo local-change > divergent.txt
git add divergent.txt
git commit -qm server-only
local_sha="$(git rev-parse HEAD)"
bash "$sync_script"
git show-ref | grep -q "^$local_sha refs/deployment-backups/"
echo "Deployment synchronisation regression tests passed"
