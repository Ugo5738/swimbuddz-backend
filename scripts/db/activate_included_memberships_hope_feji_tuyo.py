#!/usr/bin/env python3
"""Activate included Annual Community Membership for Accelerated Cohort & Tuyo.

Context (2026-11-05):
1. Accelerated Swimming Programme (fdfbe8c3-253b-46c6-bc8a-159d96c75da5):
   Founder directive: The Accelerated Swimming Programme tuition includes the
   1-year Annual SwimBuddz Community Membership. Set cohort policy override to
   'included', update enrolled learner snapshots, and activate community_paid_until
   for Hope Iyamu (paid ₦170k) and Feji Edafetano (paid ₦100k).

2. Tuyo (member 89cd6218-7dda-47e8-b4a8-232801a7796f):
   Registered as a couple with Chiamaka Ukwuoma on the Oct 2026 Yaba cohort
   (cohort policy: 'included'). Tuyo's initial ₦50,000 installment was migrated
   from an incorrect cohort checkout, so his community_paid_until was missed.
   Align Tuyo's community_paid_until with Chiamaka's (2027-10-07 19:57:52 UTC).

Usage:
  python3 scripts/db/activate_included_memberships_hope_feji_tuyo.py          # dry-run
  python3 scripts/db/activate_included_memberships_hope_feji_tuyo.py --apply  # execute
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import psycopg
from psycopg.rows import dict_row

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_ROOT / ".env.prod"
LOG_DIR = Path(__file__).resolve().parent / "backfill_logs"

COHORT_ACCELERATED_ID = "fdfbe8c3-253b-46c6-bc8a-159d96c75da5"
HOPE_MEMBER_ID = "dee1f4ec-8b66-4095-adee-dde6b5db4110"
HOPE_ENR_ID = "aa4ee37e-afa6-47f4-bba3-7ab177926e58"
FEJI_MEMBER_ID = "7953f999-f22c-4aab-926b-74ff838ad8aa"
FEJI_ENR_ID = "f4b785fe-a865-4dcb-8e19-9252ab83b3b4"
TUYO_MEMBER_ID = "89cd6218-7dda-47e8-b4a8-232801a7796f"
CHIAMAKA_COMMUNITY_PAID_UNTIL = "2027-10-07T19:57:52.180499+00:00"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the changes in DB (default: dry run)",
    )
    return parser.parse_args()


def get_connection_str():
    with open(ENV_FILE) as f:
        for line in f:
            if line.startswith("DATABASE_URL="):
                url = line.strip().split("=", 1)[1].strip("\"'")
                return url.replace("postgresql+psycopg://", "postgresql://")
    raise RuntimeError("DATABASE_URL not found in .env.prod")


def main():
    args = parse_args()
    conn_str = get_connection_str()
    print(
        f"Mode: {'APPLY (COMMITTING TO DB)' if args.apply else 'DRY RUN (NO CHANGES SAVED)'}"
    )

    with psycopg.connect(conn_str, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # 1. Inspect Cohort
            cur.execute(
                "SELECT id, name, membership_policy_override FROM cohorts WHERE id = %s;",
                (COHORT_ACCELERATED_ID,),
            )
            cohort = cur.fetchone()
            print(
                f"\nCohort: {cohort['name']} (Current policy: {cohort['membership_policy_override']}) -> Target: 'included'"
            )

            # 2. Inspect Enrollments
            cur.execute(
                """
                SELECT id, member_id, membership_policy_snapshot, status, payment_status
                FROM enrollments
                WHERE id IN (%s, %s);
            """,
                (HOPE_ENR_ID, FEJI_ENR_ID),
            )
            enrs = cur.fetchall()
            for e in enrs:
                print(
                    f"Enrollment {e['id']}: Current snapshot = {e['membership_policy_snapshot']} -> Target: 'included'"
                )

            # 3. Inspect Member Memberships
            cur.execute(
                """
                SELECT mm.member_id, m.email, m.first_name, m.last_name, mm.community_paid_until,
                       mm.academy_paid_until, mm.active_tiers, mm.declared_tiers, mm.primary_tier
                FROM member_memberships mm
                JOIN members m ON m.id = mm.member_id
                WHERE mm.member_id IN (%s, %s, %s);
            """,
                (TUYO_MEMBER_ID, HOPE_MEMBER_ID, FEJI_MEMBER_ID),
            )
            mm_rows = cur.fetchall()
            print("\nCurrent Member Memberships:")
            for m in mm_rows:
                print(
                    f"  {m['first_name']} {m['last_name']} ({m['email']}): community_paid_until = {m['community_paid_until']}"
                )

            # Log snapshot for backup
            snapshot = {
                "cohort": dict(cohort),
                "enrollments": [dict(e) for e in enrs],
                "member_memberships": [
                    {
                        k: (v.isoformat() if isinstance(v, datetime) else v)
                        for k, v in dict(m).items()
                    }
                    for m in mm_rows
                ],
            }

            if not args.apply:
                print(
                    "\n[DRY RUN] No database writes performed. Pass --apply to execute."
                )
                return

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            log_file = LOG_DIR / f"membership_activation_hope_feji_tuyo_{ts}.json"
            with open(log_file, "w") as f:
                json.dump(snapshot, f, indent=2, default=str)
            print(f"\nSaved pre-change backup to: {log_file}")

            # 1. Update Cohort policy
            cur.execute(
                """
                UPDATE cohorts
                SET membership_policy_override = 'included', updated_at = NOW()
                WHERE id = %s;
            """,
                (COHORT_ACCELERATED_ID,),
            )
            print(f"Updated cohorts ({cur.rowcount} row)")

            # 2. Update Enrollments snapshot
            cur.execute(
                """
                UPDATE enrollments
                SET membership_policy_snapshot = 'included', updated_at = NOW()
                WHERE id IN (%s, %s);
            """,
                (HOPE_ENR_ID, FEJI_ENR_ID),
            )
            print(f"Updated enrollments ({cur.rowcount} rows)")

            # 3. Update Member Memberships
            # Tuyo: match Chiamaka (2027-10-07 19:57:52 UTC)
            cur.execute(
                """
                UPDATE member_memberships
                SET community_paid_until = %s,
                    active_tiers = ARRAY['academy', 'community'],
                    declared_tiers = ARRAY['academy', 'community'],
                    primary_tier = 'academy',
                    updated_at = NOW()
                WHERE member_id = %s;
            """,
                (CHIAMAKA_COMMUNITY_PAID_UNTIL, TUYO_MEMBER_ID),
            )
            print(f"Updated Tuyo membership ({cur.rowcount} row)")

            # Hope: 1 year from activation (2027-11-03 12:51:48 UTC)
            cur.execute(
                """
                UPDATE member_memberships
                SET community_paid_until = '2027-11-03 12:51:48.703429+00:00',
                    active_tiers = ARRAY['academy', 'community'],
                    declared_tiers = ARRAY['academy', 'community'],
                    primary_tier = 'academy',
                    updated_at = NOW()
                WHERE member_id = %s;
            """,
                (HOPE_MEMBER_ID,),
            )
            print(f"Updated Hope membership ({cur.rowcount} row)")

            # Feji: 1 year from activation (2027-11-05 08:12:54 UTC)
            cur.execute(
                """
                UPDATE member_memberships
                SET community_paid_until = '2027-11-05 08:12:54.541693+00:00',
                    active_tiers = ARRAY['academy', 'community'],
                    declared_tiers = ARRAY['academy', 'community'],
                    primary_tier = 'academy',
                    updated_at = NOW()
                WHERE member_id = %s;
            """,
                (FEJI_MEMBER_ID,),
            )
            print(f"Updated Feji membership ({cur.rowcount} row)")

            conn.commit()
            print("\nSuccessfully applied all updates and committed to database!")


if __name__ == "__main__":
    main()
