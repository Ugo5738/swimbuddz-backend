#!/usr/bin/env python3
"""One-off: give Dara + Winifred 2 more weeks to pay their blocking installment.

Context (2026-06-20): Founder directive "give dara 2 more weeks, same with
winifred" — both are Apr-2026 Beginner Freestyle cohort members who would be
(Dara) / already are (Winifred) blocked from their OWN cohort over an unpaid
installment. "Blocked from paying -> give 2 more weeks" = push the blocking
installment's ``due_at`` out 14 days. NOT a waiver — they still owe ₦50,000.

  - Dara (enrollment 72bf643e...): installment #3 due 2026-06-24 (pending) ->
    2026-07-08. She is paid/active now; GRACE_HOURS=72 means she'd be suspended
    ~Jun 27 otherwise. Only the due_at moves; her derived state is unchanged.
  - Winifred (enrollment 513557b5...): installment #2 due 2026-06-17 is MISSED
    and already past the 72h grace -> she is access_suspended=True right now.
    Push due_at -> 2026-07-01 and reset status missed->pending, then re-derive
    the enrollment exactly as services/academy_service/services/installments.py
    would: access_suspended=False, payment_status=paid, missed_installments_count=0.
    (Idempotent: mark_overdue_installments only re-misses when due_at+72h<=now,
    and the new due_at is in the future, so the next auto-sync is a no-op.)

Prod DB access mirrors scripts/db/backfill_session_pool_ids.py: psql can't parse
the .env.prod pooler URL, so parse components by regex and hand to psycopg2.

Reversible: writes the pre-change rows to scripts/db/backfill_logs/<ts>.json
BEFORE commit. ``--rollback <log>`` restores the exact prior values.

Usage (from swimbuddz-backend/, host python with psycopg2):
  python3 scripts/db/grace_dara_winifred_installments.py            # dry run
  python3 scripts/db/grace_dara_winifred_installments.py --apply
  python3 scripts/db/grace_dara_winifred_installments.py --rollback scripts/db/backfill_logs/<file>.json
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
ENV_FILE = BACKEND_ROOT / ".env.prod"
LOG_DIR = Path(__file__).resolve().parent / "backfill_logs"
TAG = "grace-dara-winifred-installments 2026-06-20"

DARA_ENR = "72bf643e-2df4-4cc9-9ee3-921f6b247b43"
WINI_ENR = "513557b5-8294-4b30-8c5e-5b87066b0f94"

URL_RE = re.compile(
    r"^(?P<scheme>[^:]+)://(?P<user>[^:]+):(?P<password>.*)@"
    r"(?P<host>[^:/]+):(?P<port>\d+)/(?P<dbname>[^?]+)"
)


def load_conn_params(env_file):
    env = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
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


def fetch_inst(cur, enr, n):
    cur.execute(
        "SELECT id, installment_number, status, due_at FROM enrollment_installments "
        "WHERE enrollment_id::text=%s AND installment_number=%s",
        (enr, n),
    )
    return cur.fetchone()


def fetch_enr(cur, enr):
    cur.execute(
        "SELECT id, status, payment_status, access_suspended, "
        "missed_installments_count FROM enrollments WHERE id::text=%s",
        (enr,),
    )
    return cur.fetchone()


def dump(label, row):
    print(f"  {label}: " + " | ".join(f"{k}={row[k]}" for k in row.keys()))


def apply(cur, dry):
    journal = {"tag": TAG, "installments": [], "enrollments": []}

    # --- DARA: installment #3 due_at + 14 days (status untouched) ---
    d3 = fetch_inst(cur, DARA_ENR, 3)
    print("\nDARA installment #3 (before):")
    dump("inst", d3)
    journal["installments"].append(
        {
            "id": str(d3["id"]),
            "due_at": d3["due_at"].isoformat(),
            "status": d3["status"],
        }
    )
    if not dry:
        cur.execute(
            "UPDATE enrollment_installments SET due_at = due_at + interval '14 days', "
            "updated_at = now() WHERE id=%s",
            (d3["id"],),
        )

    # --- WINIFRED: installment #2 due_at + 14 days, missed -> pending ---
    w2 = fetch_inst(cur, WINI_ENR, 2)
    print("\nWINIFRED installment #2 (before):")
    dump("inst", w2)
    journal["installments"].append(
        {
            "id": str(w2["id"]),
            "due_at": w2["due_at"].isoformat(),
            "status": w2["status"],
        }
    )
    if not dry:
        cur.execute(
            "UPDATE enrollment_installments SET due_at = due_at + interval '14 days', "
            "status = 'pending', updated_at = now() WHERE id=%s",
            (w2["id"],),
        )

    # --- WINIFRED enrollment: lift suspension (re-derived state) ---
    we = fetch_enr(cur, WINI_ENR)
    print("\nWINIFRED enrollment (before):")
    dump("enr", we)
    journal["enrollments"].append(
        {
            "id": str(we["id"]),
            "payment_status": we["payment_status"],
            "access_suspended": we["access_suspended"],
            "missed_installments_count": we["missed_installments_count"],
        }
    )
    if not dry:
        cur.execute(
            "UPDATE enrollments SET access_suspended=false, payment_status='paid', "
            "missed_installments_count=0, updated_at=now() WHERE id=%s",
            (we["id"],),
        )

    return journal


def show_after(cur):
    print("\n--- AFTER ---")
    dump("DARA inst#3", fetch_inst(cur, DARA_ENR, 3))
    dump("WINI inst#2", fetch_inst(cur, WINI_ENR, 2))
    dump("WINI enr   ", fetch_enr(cur, WINI_ENR))


def rollback(cur, log_path):
    data = json.loads(Path(log_path).read_text())
    print(f"Rolling back: {data['tag']}")
    for inst in data["installments"]:
        cur.execute(
            "UPDATE enrollment_installments SET due_at=%s, status=%s, updated_at=now() "
            "WHERE id=%s",
            (inst["due_at"], inst["status"], inst["id"]),
        )
        print(
            f"  restored installment {inst['id']} -> due_at={inst['due_at']} status={inst['status']}"
        )
    for enr in data["enrollments"]:
        cur.execute(
            "UPDATE enrollments SET payment_status=%s, access_suspended=%s, "
            "missed_installments_count=%s, updated_at=now() WHERE id=%s",
            (
                enr["payment_status"],
                enr["access_suspended"],
                enr["missed_installments_count"],
                enr["id"],
            ),
        )
        print(
            f"  restored enrollment {enr['id']} -> payment_status={enr['payment_status']} "
            f"access_suspended={enr['access_suspended']}"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", metavar="LOG")
    args = ap.parse_args()

    conn = psycopg2.connect(**load_conn_params(ENV_FILE))
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if args.rollback:
        rollback(cur, args.rollback)
        conn.commit()
        show_after(cur)
        conn.close()
        return

    dry = not args.apply
    journal = apply(cur, dry)

    if dry:
        print("\n[DRY RUN] no changes committed. Re-run with --apply.")
        conn.rollback()
        conn.close()
        return

    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"grace_dara_winifred_{ts}.json"
    log_path.write_text(json.dumps(journal, indent=2))
    print(f"\nJournal written: {log_path}")
    conn.commit()
    print("COMMITTED.")
    show_after(cur)
    conn.close()


if __name__ == "__main__":
    main()
