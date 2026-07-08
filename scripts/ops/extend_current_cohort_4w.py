#!/usr/bin/env python3
"""One-off: extend the current Beginner cohort by 4 weeks.

Dry-run by default. The script identifies the cohort containing the target
members (Winifred, Dara, Biyi), shows the proposed changes, and writes only
with ``--apply``.

This mirrors the existing admin approval side effects when the services are
not reachable from the operator machine:
  - create an approved cohort_extension_requests row
  - move cohorts.end_date by 4 weeks
  - extend enrolled members' academy access without shortening existing access
  - create weekly cohort_class sessions in the half-open window
    (old_end_date, new_end_date], skipping dates that already have a session

Important: direct DB writes do not call communications-service. After the
notification fix is deployed, prefer the normal extension approval API when
services are reachable so new sessions emit booking announcements.

Usage from swimbuddz-backend/:
  python scripts/ops/extend_current_cohort_4w.py
  python scripts/ops/extend_current_cohort_4w.py --apply
  python scripts/ops/extend_current_cohort_4w.py --cohort-id <uuid> --apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_ROOT / ".env.prod"
LOG_DIR = BACKEND_ROOT / "scripts" / "db" / "backfill_logs"
TAG = "extend-current-cohort-4w 2026-07-08"
TARGET_TERMS = ("winifred", "dara", "biyi")
WEEKS = 4
WEEK = timedelta(days=7)
MAX_WEEKS = 60
TITLE_RE = re.compile(r"^\s*Week\s+\d+\s*-\s*(.*)$", re.IGNORECASE)
URL_RE = re.compile(
    r"^(?P<scheme>[^:]+)://(?P<user>[^:]+):(?P<password>.*)@"
    r"(?P<host>[^:/]+):(?P<port>\d+)/(?P<dbname>[^?]+)"
)


def load_conn_params(env_file: Path) -> dict:
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
    match = URL_RE.match(url)
    if not match:
        sys.exit("Could not parse DATABASE_URL components")
    parsed = match.groupdict()
    return {
        "host": parsed["host"],
        "port": int(parsed["port"]),
        "dbname": parsed["dbname"],
        "user": parsed["user"],
        "password": parsed["password"],
        "sslmode": "require",
    }


def one(cur, sql: str, params: tuple = ()):
    cur.execute(sql, params)
    return cur.fetchone()


def all_rows(cur, sql: str, params: tuple = ()):
    cur.execute(sql, params)
    return cur.fetchall()


def find_candidates(cur):
    clauses = []
    params = []
    for term in TARGET_TERMS:
        like = f"%{term}%"
        clauses.append(
            "(m.first_name ILIKE %s OR m.last_name ILIKE %s OR m.email ILIKE %s)"
        )
        params.extend([like, like, like])

    return all_rows(
        cur,
        f"""
        SELECT
            c.id,
            c.name,
            c.start_date,
            c.end_date,
            c.status,
            COUNT(DISTINCT m.id) AS matched_count,
            ARRAY_AGG(DISTINCT m.first_name || ' ' || m.last_name || ' <' || m.email || '>') AS matched_members
        FROM cohorts c
        JOIN enrollments e ON e.cohort_id = c.id
        JOIN members m ON m.id = e.member_id
        WHERE e.status = 'enrolled' AND ({" OR ".join(clauses)})
        GROUP BY c.id, c.name, c.start_date, c.end_date, c.status
        ORDER BY matched_count DESC, c.end_date DESC
        """,
        tuple(params),
    )


def get_cohort(cur, cohort_id: str):
    return one(
        cur,
        """
        SELECT id, name, start_date, end_date, status, coach_id
        FROM cohorts
        WHERE id::text = %s
        """,
        (cohort_id,),
    )


def get_coach_id(cur, cohort) -> str:
    if cohort.get("coach_id"):
        return str(cohort["coach_id"])
    row = one(
        cur,
        """
        SELECT coach_id
        FROM coach_assignments
        WHERE cohort_id = %s
          AND status = 'active'
          AND is_session_override IS NOT TRUE
        ORDER BY CASE WHEN role = 'lead' THEN 0 ELSE 1 END, start_date DESC
        LIMIT 1
        """,
        (cohort["id"],),
    )
    if not row:
        sys.exit("No cohort.coach_id or active coach assignment found; aborting.")
    return str(row["coach_id"])


def get_enrolled(cur, cohort_id):
    return all_rows(
        cur,
        """
        SELECT
            e.id AS enrollment_id,
            e.member_id,
            e.member_auth_id,
            m.first_name,
            m.last_name,
            m.email
        FROM enrollments e
        JOIN members m ON m.id = e.member_id
        WHERE e.cohort_id = %s AND e.status = 'enrolled'
        ORDER BY m.first_name, m.last_name
        """,
        (cohort_id,),
    )


def plan_sessions(cur, cohort_id, old_end, new_end):
    existing = all_rows(
        cur,
        """
        SELECT *
        FROM sessions
        WHERE cohort_id = %s AND session_type = 'cohort_class'
        ORDER BY starts_at DESC
        """,
        (cohort_id,),
    )
    if not existing:
        sys.exit("No existing cohort sessions found; cannot infer schedule.")

    existing_dates = {row["starts_at"].date() for row in existing}
    max_week = max((row["week_number"] or 0) for row in existing)
    template = next(
        (
            row
            for row in existing
            if row["starts_at"] <= old_end and row["week_number"] is not None
        ),
        None,
    )
    template = template or next(
        (row for row in existing if row["starts_at"] <= old_end),
        existing[0],
    )

    duration = template["ends_at"] - template["starts_at"]
    match = TITLE_RE.match(template["title"] or "")
    title_base = match.group(1) if match else (template["title"] or "Session")

    planned = []
    skipped = []
    week_no = max_week
    start = template["starts_at"]
    for _ in range(MAX_WEEKS):
        start = start + WEEK
        if start <= old_end:
            continue
        if start > new_end:
            break
        week_no += 1
        if start.date() in existing_dates:
            skipped.append({"date": start.date().isoformat(), "week_number": week_no})
            continue
        planned.append(
            {
                "id": str(uuid.uuid4()),
                "week_number": week_no,
                "title": f"Week {week_no} - {title_base}",
                "starts_at": start,
                "ends_at": start + duration,
                "template": template,
            }
        )
        existing_dates.add(start.date())

    return planned, skipped


def print_plan(cohort, enrolled, planned_sessions, skipped_sessions, new_end, apply):
    print(f"Mode   : {'APPLY' if apply else 'DRY RUN'}")
    print(f"Cohort : {cohort['name']} ({cohort['id']})")
    print(f"Status : {cohort['status']}")
    print(f"Current end: {cohort['end_date'].isoformat()}")
    print(f"New end    : {new_end.isoformat()}")
    print(f"Enrolled members: {len(enrolled)}")
    for row in enrolled:
        print(f"  - {row['first_name']} {row['last_name']} <{row['email']}>")

    print(f"\nSessions to create: {len(planned_sessions)}")
    for row in planned_sessions:
        print(
            "  - "
            f"{row['title']} | {row['starts_at'].isoformat()} -> {row['ends_at'].isoformat()}"
        )
    if skipped_sessions:
        print(f"Sessions skipped because date exists: {len(skipped_sessions)}")
        for row in skipped_sessions:
            print(f"  - week {row['week_number']} on {row['date']}")


def insert_extension_request(cur, cohort, coach_id, old_end, new_end):
    request_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO cohort_extension_requests (
            id, cohort_id, coach_id, weeks_requested, reason,
            current_end_date, proposed_end_date, status,
            admin_notes, reviewed_at, created_at
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, 'approved',
            %s, now(), now()
        )
        """,
        (
            request_id,
            cohort["id"],
            coach_id,
            WEEKS,
            "Backend one-off extension requested by admin/operator.",
            old_end,
            new_end,
            "Applied by scripts/ops/extend_current_cohort_4w.py",
        ),
    )
    return request_id


def extend_members(cur, enrolled, new_end):
    for row in enrolled:
        cur.execute(
            """
            INSERT INTO member_memberships (
                id, member_id, primary_tier, active_tiers,
                academy_paid_until, community_paid_until, club_paid_until,
                created_at, updated_at
            )
            VALUES (
                %s, %s, 'academy', ARRAY['academy', 'club', 'community'],
                %s, now() + interval '1 year', now() + interval '3 months',
                now(), now()
            )
            ON CONFLICT (member_id) DO UPDATE SET
                academy_paid_until = GREATEST(
                    COALESCE(member_memberships.academy_paid_until, %s),
                    %s
                ),
                community_paid_until = GREATEST(
                    COALESCE(
                        member_memberships.community_paid_until,
                        now() + interval '1 year'
                    ),
                    now() + interval '1 year'
                ),
                club_paid_until = GREATEST(
                    COALESCE(
                        member_memberships.club_paid_until,
                        now() + interval '3 months'
                    ),
                    now() + interval '3 months'
                ),
                active_tiers = ARRAY['academy', 'club', 'community'],
                primary_tier = 'academy',
                updated_at = now()
            """,
            (str(uuid.uuid4()), row["member_id"], new_end, new_end, new_end),
        )


def insert_sessions(cur, cohort_id, planned_sessions):
    for row in planned_sessions:
        template = row["template"]
        cur.execute(
            """
            INSERT INTO sessions (
                id, session_type, status, title, description, notes,
                starts_at, ends_at, timezone,
                pool_id, location, location_name, location_address,
                capacity, pool_fee, ride_share_fee,
                allows_guests, max_guests_per_booking,
                cohort_id, event_id, pod_id,
                week_number, lesson_title,
                published_at, created_at, updated_at
            )
            VALUES (
                %s, 'cohort_class', 'scheduled', %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, NULL, NULL,
                %s, %s,
                now(), now(), now()
            )
            """,
            (
                row["id"],
                row["title"],
                template["description"],
                template["notes"],
                row["starts_at"],
                row["ends_at"],
                template["timezone"],
                template["pool_id"],
                template["location"],
                template["location_name"],
                template["location_address"],
                template["capacity"],
                template["pool_fee"],
                template["ride_share_fee"],
                template["allows_guests"],
                template["max_guests_per_booking"],
                cohort_id,
                row["week_number"],
                template["lesson_title"],
            ),
        )


def write_journal(cohort, new_end, request_id, planned_sessions, enrolled):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = (
        LOG_DIR
        / f"extend_current_cohort_4w_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    payload = {
        "tag": TAG,
        "cohort_id": str(cohort["id"]),
        "old_end_date": cohort["end_date"].isoformat(),
        "new_end_date": new_end.isoformat(),
        "extension_request_id": request_id,
        "created_session_ids": [row["id"] for row in planned_sessions],
        "enrolled_member_ids": [str(row["member_id"]) for row in enrolled],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cohort-id")
    args = parser.parse_args()

    conn = psycopg2.connect(**load_conn_params(ENV_FILE))
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        if args.cohort_id:
            cohort = get_cohort(cur, args.cohort_id)
            if not cohort:
                sys.exit(f"Cohort not found: {args.cohort_id}")
        else:
            candidates = find_candidates(cur)
            if len(candidates) != 1:
                print("Could not identify exactly one cohort. Candidates:")
                for row in candidates:
                    print(
                        f"  - {row['name']} ({row['id']}) "
                        f"matches={row['matched_count']} members={row['matched_members']}"
                    )
                sys.exit("Re-run with --cohort-id <uuid>.")
            cohort = get_cohort(cur, str(candidates[0]["id"]))

        old_end = cohort["end_date"]
        new_end = old_end + timedelta(weeks=WEEKS)
        enrolled = get_enrolled(cur, cohort["id"])
        planned_sessions, skipped_sessions = plan_sessions(
            cur, cohort["id"], old_end, new_end
        )
        print_plan(
            cohort, enrolled, planned_sessions, skipped_sessions, new_end, args.apply
        )

        if not args.apply:
            conn.rollback()
            print("\nDry run complete. Re-run with --apply to write.")
            return

        coach_id = get_coach_id(cur, cohort)
        request_id = insert_extension_request(cur, cohort, coach_id, old_end, new_end)
        cur.execute(
            "UPDATE cohorts SET end_date = %s, updated_at = now() WHERE id = %s",
            (new_end, cohort["id"]),
        )
        extend_members(cur, enrolled, new_end)
        insert_sessions(cur, cohort["id"], planned_sessions)
        journal = write_journal(cohort, new_end, request_id, planned_sessions, enrolled)
        conn.commit()
        print(f"\nApplied. Journal: {journal}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
