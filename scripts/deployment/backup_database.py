#!/usr/bin/env python3
"""Private, snapshot-consistent migration backup with an isolated restore gate.

Uses Docker's PostgreSQL tools; never restores or writes to the source database.
The archive contains public + auth, not Storage objects or platform settings.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, unquote, urlsplit
import uuid


COUNTS_SQL = """
SELECT coalesce(jsonb_object_agg(format('%I.%I', n.nspname, c.relname),
  ((xpath('/row/rows/text()', query_to_xml(
    format('SELECT count(*) AS rows FROM %I.%I', n.nspname, c.relname),
    false, true, '')))[1]::text)::bigint), '{}'::jsonb)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname IN ('public', 'auth') AND c.relkind IN ('r', 'p');
"""
ROLES_SQL = """
SELECT coalesce(json_agg('CREATE ROLE ' || quote_ident(rolname) || ';'), '[]')
FROM pg_roles WHERE rolname !~ '^pg_' AND rolname <> 'postgres';
"""


def read_env(path):
    """Read literal dotenv values without executing shell code or interpolation."""
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, raw = line.removeprefix("export ").partition("=")
        if sep and key.strip() in ("DATABASE_SESSION_URL", "DATABASE_DIRECT_URL"):
            parts = shlex.split(raw, comments=True)
            if len(parts) > 1:
                raise ValueError("Environment values containing spaces must be quoted")
            values[key.strip()] = parts[0] if parts else ""
    return values


def pg_environment(values):
    url = values.get("DATABASE_SESSION_URL") or values.get("DATABASE_DIRECT_URL")
    if not url:
        raise ValueError("A session or direct database URL is required for backup")
    parsed = urlsplit(
        url.replace("postgresql+psycopg://", "postgresql://").replace(
            "postgresql+asyncpg://", "postgresql://"
        )
    )
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
        raise ValueError("Invalid PostgreSQL backup URL")
    if parsed.port == 6543:
        raise ValueError(
            "Backup requires session/direct mode, never transaction port 6543"
        )
    result = {
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGDATABASE": unquote(parsed.path.lstrip("/")) or "postgres",
        "PGUSER": unquote(parsed.username or "postgres"),
        "PGPASSWORD": unquote(parsed.password or ""),
        "PGSSLMODE": parse_qs(parsed.query).get("sslmode", ["require"])[0],
        "PGCONNECT_TIMEOUT": "20",
        "PGAPPNAME": "swimbuddz-migration-backup",
        "PGOPTIONS": "-c statement_timeout=300000 -c idle_in_transaction_session_timeout=600000",
    }
    if any("\n" in value or "\r" in value for value in result.values()):
        raise ValueError("Invalid newline in database connection value")
    return result


def run_backup(env_file, destination, image="postgres:17-alpine"):
    destination = Path(destination).resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    log_path = destination / "verification.log"
    archive = destination / "database.dump"
    restore_name = "swimbuddz-restore-" + uuid.uuid4().hex[:12]
    source = None
    source_name = "swimbuddz-backup-" + uuid.uuid4().hex[:12]
    with log_path.open("wb") as log, tempfile.TemporaryDirectory() as secret_dir:
        secret_file = Path(secret_dir) / "postgres.env"
        secret_file.write_text(
            "\n".join(
                f"{key}={value}"
                for key, value in pg_environment(read_env(env_file)).items()
            )
            + "\n"
        )
        secret_file.chmod(0o600)

        def run(args, **kwargs):
            return subprocess.run(args, check=True, stderr=log, **kwargs)

        client = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--add-host=host.docker.internal:host-gateway",
            "--env-file",
            str(secret_file),
            image,
        ]
        try:
            # Keep this read-only transaction alive throughout pg_dump. Counts
            # and the archive see exactly the same committed source snapshot.
            source = subprocess.Popen(
                client[:-1]
                + [
                    "--name",
                    source_name,
                    image,
                    "psql",
                    "-XqAt",
                    "-v",
                    "ON_ERROR_STOP=1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
            )

            def source_query(sql):
                source.stdin.write(sql + "\n")
                source.stdin.flush()
                line = source.stdout.readline().strip()
                if not line:
                    raise RuntimeError("Source snapshot query failed")
                return line

            snapshot = source_query(
                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();"
            )
            counts = json.loads(source_query(COUNTS_SQL))
            roles = json.loads(source_query(ROLES_SQL))
            if not counts or "public.members" not in counts:
                raise RuntimeError("Backup source is missing application tables")
            with archive.open("wb") as output:
                run(
                    client
                    + [
                        "pg_dump",
                        "--format=custom",
                        "--schema=public",
                        "--schema=auth",
                        "--snapshot=" + snapshot,
                        "--lock-wait-timeout=30s",
                    ],
                    stdout=output,
                )
            source.stdin.write("ROLLBACK;\n\\q\n")
            source.stdin.flush()
            if source.wait(timeout=30) != 0:
                raise RuntimeError("Source snapshot session failed")

            run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--network",
                    "none",
                    "--name",
                    restore_name,
                    "-e",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    image,
                ],
                stdout=log,
            )
            deadline = time.monotonic() + 60
            while subprocess.run(
                # The image starts a temporary Unix-socket-only server during
                # initdb, then restarts it. TCP readiness waits for the final
                # server so restoration cannot race that initialization stop.
                [
                    "docker",
                    "exec",
                    restore_name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                ],
                stdout=log,
                stderr=log,
            ).returncode:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Isolated restore database did not become ready")
                time.sleep(1)
            psql = [
                "docker",
                "exec",
                "-i",
                restore_name,
                "psql",
                "-U",
                "postgres",
                "-XqAt",
                "-v",
                "ON_ERROR_STOP=1",
            ]
            # Supabase platform roles/extensions are prerequisites, not archived
            # passwords or a replacement for managed platform configuration.
            bootstrap = (
                "\n".join(roles)
                + """
DROP SCHEMA public CASCADE;
CREATE SCHEMA extensions;
CREATE EXTENSION pgcrypto WITH SCHEMA extensions;
CREATE EXTENSION "uuid-ossp" WITH SCHEMA extensions;
"""
            )
            run(psql, input=bootstrap.encode(), stdout=log)
            with archive.open("rb") as input_file:
                run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        restore_name,
                        "pg_restore",
                        "-U",
                        "postgres",
                        "--dbname=postgres",
                        "--exit-on-error",
                        "--single-transaction",
                        "--no-owner",
                    ],
                    stdin=input_file,
                    stdout=log,
                )
            restored = json.loads(
                run(psql, input=COUNTS_SQL.encode(), stdout=subprocess.PIPE).stdout
            )
            if restored != counts:
                raise RuntimeError("Restored table counts differ from source snapshot")
            versions = run(
                psql,
                input=b"""
SELECT coalesce(jsonb_object_agg(c.relname, query_to_xml(
    format('TABLE public.%I', c.relname), false, true, '')::text), '{}'::jsonb)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='r' AND c.relname LIKE 'alembic_version%';
""",
                stdout=subprocess.PIPE,
            ).stdout
            digest = hashlib.sha256()
            with archive.open("rb") as archived:
                for block in iter(lambda: archived.read(1024 * 1024), b""):
                    digest.update(block)
            checksum = digest.hexdigest()
            manifest = {
                "verified_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "scope": ["public", "auth"],
                "postgres_image": image,
                "archive_sha256": checksum,
                "archive_bytes": archive.stat().st_size,
                "table_counts": counts,
                "migration_versions": json.loads(versions),
                "restore_verified": True,
            }
            (destination / "verified.json").write_text(
                json.dumps(manifest, indent=2) + "\n"
            )
        finally:
            if source and source.poll() is None:
                source.terminate()
                try:
                    source.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    source.kill()
                    source.wait()
            # Only this invocation's disposable restore container/volume.
            subprocess.run(
                ["docker", "rm", "-fv", restore_name, source_name],
                stdout=log,
                stderr=log,
            )
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--image", default="postgres:17-alpine")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        destination = run_backup(args.env_file, args.destination, args.image)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Database errors can contain credentials or record contents. Keep the
        # detailed diagnostic only in the private backup directory.
        print(
            "Backup/restore verification failed; deployment must stop. "
            "Inspect the private verification.log in the backup destination.",
            file=sys.stderr,
        )
        return 1
    print(f"Verified migration recovery backup: {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
