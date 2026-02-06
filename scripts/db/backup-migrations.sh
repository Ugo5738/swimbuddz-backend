#!/bin/bash
set -e

# Backup existing Alembic migration files for all services without deleting them.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

SERVICES=(
  "academy_service"
  "attendance_service"
  "communications_service"
  "events_service"
  "media_service"
  "members_service"
  "payments_service"
  "sessions_service"
  "transport_service"
)

TIMESTAMP=$(date +"%Y%m%d%H%M%S")
BACKUP_ROOT="$PROJECT_ROOT/scripts/backups/migrations_$TIMESTAMP"
mkdir -p "$BACKUP_ROOT"

echo "Backing up migrations to $BACKUP_ROOT"

for svc in "${SERVICES[@]}"; do
  VERSIONS_DIR="$PROJECT_ROOT/services/${svc}/alembic/versions"
  if [ -d "$VERSIONS_DIR" ]; then
    TARGET_DIR="$BACKUP_ROOT/$svc"
    mkdir -p "$TARGET_DIR"
    find "$VERSIONS_DIR" -type f -name "*.py" ! -name ".keep" -exec cp {} "$TARGET_DIR"/ \;
    echo "  ✓ $svc"
  else
    echo "  ✗ Skipping $svc (no versions dir)"
  fi
done

echo "Done."
