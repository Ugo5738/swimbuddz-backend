#!/usr/bin/env bash
# Synchronise a production checkout without losing server-local changes.
# The caller must fetch origin/$DEPLOY_BRANCH before running this script.
set -Eeuo pipefail
umask 077

: "${DEPLOY_PATH:?DEPLOY_PATH must be set}"
: "${DEPLOY_BRANCH:?DEPLOY_BRANCH must be set}"

cd "$DEPLOY_PATH"
[ -d .git ] || { echo "Deployment checkout is missing .git: $DEPLOY_PATH" >&2; exit 1; }

remote_commit="$(git rev-parse --verify "refs/remotes/origin/$DEPLOY_BRANCH^{commit}")"
target_commit="$remote_commit"

# CI deploys the commit that passed tests and produced Docker images.
# Manual deployments without DEPLOY_SHA use the selected branch's fetched head.
if [ -n "${DEPLOY_SHA:-}" ]; then
  git cat-file -e "$DEPLOY_SHA^{commit}" || {
    echo "Requested release commit is not available locally: $DEPLOY_SHA" >&2
    exit 1
  }
  git merge-base --is-ancestor "$DEPLOY_SHA" "$remote_commit" || {
    echo "Requested release commit does not belong to origin/$DEPLOY_BRANCH" >&2
    exit 1
  }
  target_commit="$(git rev-parse --verify "$DEPLOY_SHA^{commit}")"
fi

backup_root="${DEPLOY_BACKUP_ROOT:-$(dirname "$DEPLOY_PATH")/swimbuddz-deployment-backups}"
backup_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
backup_dir="$backup_root/$backup_id"

# A server-only commit must remain reachable if checkout -B moves a branch.
if ! git merge-base --is-ancestor HEAD "$target_commit"; then
  backup_ref="refs/deployment-backups/$backup_id"
  git update-ref "$backup_ref" HEAD
  echo "Preserved divergent checkout commit at $backup_ref"
fi

# Stash tracked changes only: -u could remove untracked runtime data.
if ! git diff --quiet || ! git diff --cached --quiet; then
  git stash push -m "pre-deployment tracked changes $backup_id"
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Tracked changes remain after stash; aborting deployment." >&2
    exit 1
  fi
  echo "Preserved tracked changes in git stash: $backup_id"
fi

# Move only untracked files that collide with tracked files in the incoming
# release. Unrelated local files remain untouched. Never run git clean here.
declare -A target_files=()
while IFS= read -r -d '' path; do
  target_files["$path"]=1
done < <(git ls-tree -r -z --name-only "$target_commit")

while IFS= read -r -d '' path; do
  if [[ -v 'target_files[$path]' ]]; then
    case "$path" in
      letsencrypt/*|.env.prod|.env.prod.*)
        echo "Refusing to replace production runtime file: $path" >&2
        exit 1
        ;;
    esac
    mkdir -p "$(dirname "$backup_dir/untracked/$path")"
    mv -- "$path" "$backup_dir/untracked/$path"
    echo "Preserved conflicting untracked file: $path ($backup_dir/untracked)"
  fi
done < <(git ls-files --others --exclude-standard -z)

# Never overwrite ignored files, even if a later commit introduces a tracked
# file at the same path as a local secret.
git checkout --no-overwrite-ignore -B "$DEPLOY_BRANCH" "$target_commit"
actual="$(git rev-parse HEAD)"
[ "$actual" = "$target_commit" ] || { echo "Deployment checkout SHA mismatch" >&2; exit 1; }
echo "Deployment checkout ready: $DEPLOY_BRANCH @ $actual"
