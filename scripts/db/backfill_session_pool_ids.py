#!/usr/bin/env python3
"""One-off backfill: set sessions.pool_id from legacy location fields.

Context (2026-06-11): older prod sessions reference the same physical pool
three different ways — pools-registry ``pool_id``, legacy ``location`` enum
(``rowe_park_pool``, ``sunfit_pool``, ...), and free-text ``location_name``
("Rowe Park, Yaba"). ``pool_id`` is the preferred field (see
services/sessions_service/models/core.py); this script fills it in for rows
where the legacy fields map unambiguously to an existing ``pools`` row.

Mapping rules:
  - location = 'rowe_park_pool'        -> Rowe Park pools row
  - location_name ILIKE 'rowe park%%'  -> Rowe Park pools row (only when the
    enum doesn't contradict it: location IS NULL / 'other' / 'rowe_park_pool')
  - location = 'sunfit_pool'           -> Sunfit pools row IF one exists.
    Creates nothing — if no registry row exists, the rows are reported and
    skipped.
  - location_name ILIKE 'siloam%%'     -> Siloam pools row (text-only — there
    is no siloam value in session_location_enum; same enum-conflict guard)

Legacy columns (``location``, ``location_name``, ``location_address``) are
left untouched. Only rows with ``pool_id IS NULL`` are eligible.

Reversibility (precedent: 2026-06-08 attendance backfill): every applied
update is journalled to scripts/db/backfill_logs/<timestamp>.json with the
exact (session_id, new_pool_id) pair. ``--rollback <log>`` sets pool_id back
to NULL only where it still equals the value this script wrote. The journal
is written BEFORE commit, then marked committed after. The tag lives in the
journal, not in session.notes — notes is returned by member-facing response
schemas.

Prod DB access: psql cannot parse the .env.prod pooler URL (scheme is
``postgresql+psycopg`` and the password may contain URL-special chars), so we
parse the components by regex and hand them to psycopg2 individually.

Usage (from swimbuddz-backend/, host python with psycopg2):
  python3 scripts/db/backfill_session_pool_ids.py                 # dry run
  python3 scripts/db/backfill_session_pool_ids.py --apply
  python3 scripts/db/backfill_session_pool_ids.py --rollback scripts/db/backfill_logs/<file>.json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = BACKEND_ROOT / ".env.prod"
LOG_DIR = Path(__file__).resolve().parent / "backfill_logs"

BACKFILL_TAG = "session-pool-id-backfill 2026-06-11"

URL_RE = re.compile(
    r"^(?P<scheme>[^:]+)://(?P<user>[^:]+):(?P<password>.*)@"
    r"(?P<host>[^:/]+):(?P<port>\d+)/(?P<dbname>[^?]+)"
)


def load_conn_params(env_file: Path) -> dict:
    """Parse DATABASE_SESSION_URL (preferred) or DATABASE_URL from env file."""
    env = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")

    url = env.get("DATABASE_SESSION_URL") or env.get("DATABASE_URL")
    if not url:
        sys.exit(f"No DATABASE_SESSION_URL / DATABASE_URL in {env_file}")
    m = URL_RE.match(url)
    if not m:
        sys.exit("Could not parse DATABASE_URL components")
    d = m.groupdict()
    return {
        "host": d["host"],
        "port": int(d["port"]),
        "dbname": d["dbname"],
        "user": d["user"],
        "password": d["password"],
        "sslmode": "require",
    }


def find_pool(cur, pattern: str):
    """Return the single pools row matching pattern, or None / exit on ambiguity."""
    cur.execute(
        "SELECT id, name, slug FROM pools WHERE name ILIKE %s OR slug ILIKE %s",
        (pattern, pattern),
    )
    rows = cur.fetchall()
    if len(rows) > 1:
        names = ", ".join(f"{r['name']} ({r['id']})" for r in rows)
        sys.exit(f"Ambiguous pools match for {pattern!r}: {names} — refusing to guess.")
    return rows[0] if rows else None


def print_grouping(cur, label: str):
    print(
        f"\n=== {label}: sessions by COALESCE(pools.name, location_name, location) ==="
    )
    cur.execute(
        """
        SELECT COALESCE(p.name, s.location_name, s.location::text, '(no location)') AS pool,
               COUNT(*) AS sessions
        FROM sessions s
        LEFT JOIN pools p ON p.id = s.pool_id
        GROUP BY 1 ORDER BY 2 DESC
        """
    )
    for r in cur.fetchall():
        print(f"  {r['pool']:<40} {r['sessions']:>5}")

    print(f"=== {label}: attendance_records by the same grouping ===")
    cur.execute(
        """
        SELECT COALESCE(p.name, s.location_name, s.location::text, '(no location)') AS pool,
               COUNT(*) AS attendance_rows
        FROM attendance_records ar
        JOIN sessions s ON s.id = ar.session_id
        LEFT JOIN pools p ON p.id = s.pool_id
        GROUP BY 1 ORDER BY 2 DESC
        """
    )
    for r in cur.fetchall():
        print(f"  {r['pool']:<40} {r['attendance_rows']:>5}")


def plan_updates(cur):
    """Return (updates, skipped) — mapping decisions for every legacy row."""
    rowe = find_pool(cur, "%rowe park%")
    sunfit = find_pool(cur, "%sunfit%")
    siloam = find_pool(cur, "%siloam%")
    if rowe is None:
        sys.exit("No pools row matching 'Rowe Park' — nothing to map to. Aborting.")
    print(f"Rowe Park registry row: {rowe['name']} ({rowe['id']})")
    if sunfit is None:
        print(
            "NOTE: no pools registry row matches 'Sunfit'. Per instructions, "
            "creating nothing — sunfit_pool sessions will be reported and skipped."
        )
    else:
        print(f"Sunfit registry row:    {sunfit['name']} ({sunfit['id']})")
    if siloam is None:
        print(
            "NOTE: no pools registry row matches 'Siloam' — text rows will be skipped."
        )
    else:
        print(f"Siloam registry row:    {siloam['name']} ({siloam['id']})")

    cur.execute(
        """
        SELECT id, title, starts_at, location::text AS location, location_name
        FROM sessions
        WHERE pool_id IS NULL
          AND (location IS NOT NULL OR location_name IS NOT NULL)
        ORDER BY starts_at
        """
    )
    candidates = cur.fetchall()

    updates, skipped = [], []
    for row in candidates:
        loc, text = row["location"], (row["location_name"] or "")
        target, reason = None, None
        if loc == "rowe_park_pool":
            target, reason = rowe, "location enum = rowe_park_pool"
        elif loc == "sunfit_pool":
            if sunfit:
                target, reason = sunfit, "location enum = sunfit_pool"
            else:
                skipped.append((row, "sunfit_pool enum but no registry row"))
                continue
        elif text.lower().startswith("rowe park"):
            if loc in (None, "other"):
                target, reason = rowe, f"location_name {text!r} matches 'Rowe Park%'"
            else:
                skipped.append(
                    (row, f"text says Rowe Park but enum says {loc} — conflict")
                )
                continue
        elif text.lower().startswith("siloam"):
            if siloam is None:
                skipped.append((row, "Siloam text but no registry row"))
                continue
            if loc in (None, "other"):
                target, reason = siloam, f"location_name {text!r} matches 'Siloam%'"
            else:
                skipped.append(
                    (row, f"text says Siloam but enum says {loc} — conflict")
                )
                continue
        else:
            skipped.append((row, f"no mapping (location={loc}, name={text!r})"))
            continue
        updates.append(
            {
                "session_id": str(row["id"]),
                "title": row["title"],
                "starts_at": row["starts_at"].isoformat(),
                "location": loc,
                "location_name": row["location_name"],
                "old_pool_id": None,
                "new_pool_id": str(target["id"]),
                "pool_name": target["name"],
                "reason": reason,
            }
        )
    return updates, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    parser.add_argument(
        "--rollback", metavar="LOGFILE", help="revert a previous --apply run"
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    args = parser.parse_args()

    conn = psycopg2.connect(**load_conn_params(Path(args.env_file)))
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if args.rollback:
        journal = json.loads(Path(args.rollback).read_text())
        restored = skipped = 0
        for e in journal["updates"]:
            cur.execute(
                "UPDATE sessions SET pool_id = NULL WHERE id = %s AND pool_id = %s",
                (e["session_id"], e["new_pool_id"]),
            )
            if cur.rowcount == 1:
                restored += 1
            else:
                skipped += 1
                print(f"  skipped {e['session_id']} — pool_id changed since backfill")
        conn.commit()
        print(f"Rollback: {restored} restored to NULL, {skipped} skipped.")
        print_grouping(cur, "AFTER ROLLBACK")
        conn.close()
        return

    print_grouping(cur, "BEFORE")
    updates, skipped = plan_updates(cur)

    print(f"\n=== Plan: {len(updates)} sessions to update ===")
    by_pool = {}
    for u in updates:
        by_pool.setdefault(u["pool_name"], []).append(u)
    for pool_name, rows in by_pool.items():
        print(f"  -> {pool_name}: {len(rows)} sessions")
        for u in rows:
            print(
                f"     {u['session_id']}  {u['starts_at'][:10]}  "
                f"loc={u['location']!r:<18} name={u['location_name']!r}  [{u['reason']}]"
            )
    if skipped:
        print(f"\n=== Skipped: {len(skipped)} legacy rows left untouched ===")
        for row, why in skipped:
            print(f"  {row['id']}  {row['starts_at']:%Y-%m-%d}  {why}")

    if not args.apply:
        print("\nDRY RUN — no changes written. Re-run with --apply to execute.")
        conn.close()
        return

    if not updates:
        print("\nNothing to apply.")
        conn.close()
        return

    # Journal first (pending), then update+commit, then mark committed.
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"session_pool_id_backfill_{stamp}.json"
    journal = {
        "tag": BACKFILL_TAG,
        "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        "committed": False,
        "rollback": "python3 scripts/db/backfill_session_pool_ids.py --rollback <this file>",
        "updates": updates,
        "skipped": [{"session_id": str(r["id"]), "why": why} for r, why in skipped],
    }
    log_path.write_text(json.dumps(journal, indent=2))

    applied = 0
    for u in updates:
        cur.execute(
            "UPDATE sessions SET pool_id = %s WHERE id = %s AND pool_id IS NULL",
            (u["new_pool_id"], u["session_id"]),
        )
        if cur.rowcount != 1:
            conn.rollback()
            sys.exit(
                f"ABORT (rolled back): session {u['session_id']} matched "
                f"{cur.rowcount} rows — pool_id no longer NULL?"
            )
        applied += 1
    conn.commit()
    journal["committed"] = True
    log_path.write_text(json.dumps(journal, indent=2))

    print(f"\nAPPLIED: {applied} sessions updated. Journal: {log_path}")
    print_grouping(cur, "AFTER")
    conn.close()


if __name__ == "__main__":
    main()
