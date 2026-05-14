#!/usr/bin/env bash
# File-size check for the SwimBuddz backend.
#
# Targets are documented in docs/CONVENTIONS.md §12.
# Prints every file in violation, classified [soft] or [HARD].
# Non-blocking: always exits 0. Once the hard-cap list is empty, change
# the final exit to `exit "$hard_violations"` to gate CI.

set -uo pipefail
cd "$(dirname "$0")/../.."

SOFT_ROUTER=500
HARD_ROUTER=800
SOFT_MODEL=400
HARD_MODEL=600
SOFT_SCHEMA=500
HARD_SCHEMA=800
SOFT_SERVICE=500
HARD_SERVICE=800
SOFT_LIB=600
HARD_LIB=1000

soft_violations=0
hard_violations=0

check() {
  local path="$1" lines="$2" soft="$3" hard="$4" kind="$5"
  if (( lines > hard )); then
    printf "  [HARD] %-7s %5d lines (cap %d)  %s\n" "$kind" "$lines" "$hard" "$path"
    hard_violations=$((hard_violations + 1))
  elif (( lines > soft )); then
    printf "  [soft] %-7s %5d lines (target %d)  %s\n" "$kind" "$lines" "$soft" "$path"
    soft_violations=$((soft_violations + 1))
  fi
}

echo "Backend file-size check  (see docs/CONVENTIONS.md §12)"
echo ""

# Iterate every .py file once and classify by path. First match wins.
while IFS= read -r f; do
  # Exclusions: migrations, caches, venv, seed data, templates, tests.
  case "$f" in
    *"/alembic/versions/"*|*"/__pycache__/"*|*"/.venv/"*|*"/scripts/seed/"*|*"/templates/"*|*"/tests/"*|*"/test_"*|*"/_test.py")
      continue
      ;;
  esac

  lines=$(wc -l < "$f" | tr -d ' ')

  case "$f" in
    *"/routers/"*)           check "$f" "$lines" "$SOFT_ROUTER"  "$HARD_ROUTER"  "router" ;;
    *"/models/"*)            check "$f" "$lines" "$SOFT_MODEL"   "$HARD_MODEL"   "model" ;;
    *"/schemas/"*)           check "$f" "$lines" "$SOFT_SCHEMA"  "$HARD_SCHEMA"  "schema" ;;
    ./services/*/services/*) check "$f" "$lines" "$SOFT_SERVICE" "$HARD_SERVICE" "service" ;;
    ./libs/*|./mcp/*)        check "$f" "$lines" "$SOFT_LIB"     "$HARD_LIB"     "lib" ;;
  esac
done < <(find . -name "*.py" -type f \
           -not -path "./.venv/*" \
           -not -path "./.ruff_cache/*" \
           -not -path "*/__pycache__/*")

echo ""
echo "  $soft_violations soft, $hard_violations hard."
if (( hard_violations > 0 )); then
  echo "  Hard-cap files must be split. See docs/CONVENTIONS.md §12 for guidance."
fi
exit 0
