"""Generate the per-table database schema reference.

Introspects every service's SQLAlchemy models and emits a Markdown
table inventory grouped by owning service:

    docs/reference/DATABASE_SCHEMA_TABLES.md

This is the auto-generated companion to the hand-curated
docs/reference/DATABASE_SCHEMA.md (which documents conventions, the
string-enum pattern, and shared enum values). Regenerate after model
changes:

    python scripts/db/generate-schema-doc.py

All services share libs.db.base.Base, so importing each service's
models package registers its tables on the one shared metadata. We
attribute a table to the FIRST service whose package defines it;
subsequent appearances are cross-service stub references (e.g. the
`members` table is a thin MemberRef stub in several services) and are
listed under the owner only, with a note.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # swimbuddz-backend
sys.path.insert(0, str(PROJECT_ROOT))
# docs/ lives in the sibling docs repo, one level above swimbuddz-backend.
DOCS_ROOT = PROJECT_ROOT.parent

# Order matters: the first service to define a table is treated as its
# owner. Domain-owning services are listed before consumers.
SERVICES = [
    "members_service",
    "sessions_service",
    "academy_service",
    "attendance_service",
    "payments_service",
    "wallet_service",
    "communications_service",
    "events_service",
    "media_service",
    "transport_service",
    "store_service",
    "ai_service",
    "volunteer_service",
    "pools_service",
    "reporting_service",
    "chat_service",
]

OUTPUT = DOCS_ROOT / "docs" / "reference" / "DATABASE_SCHEMA_TABLES.md"


def _col_type(col) -> str:
    try:
        return str(col.type)
    except Exception:  # pragma: no cover - exotic types
        return col.type.__class__.__name__


def main() -> None:
    from libs.db.base import Base

    # table name -> owning service (first to define it)
    owner: dict[str, str] = {}
    # service -> [table objects]
    by_service: dict[str, list] = {svc: [] for svc in SERVICES}

    for svc in SERVICES:
        before = set(Base.metadata.tables.keys())
        try:
            importlib.import_module(f"services.{svc}.models")
        except Exception as exc:  # pragma: no cover
            print(f"warning: could not import {svc} models: {exc}", file=sys.stderr)
            continue
        after = set(Base.metadata.tables.keys())
        for tname in sorted(after - before):
            owner.setdefault(tname, svc)
            if owner[tname] == svc:
                by_service[svc].append(Base.metadata.tables[tname])

    lines: list[str] = []
    lines.append("# Database Schema — Table Reference (auto-generated)")
    lines.append("")
    lines.append(
        "> **Generated** by `scripts/db/generate-schema-doc.py`. Do not "
        "hand-edit — regenerate after model changes. Conventions, the "
        "string-enum pattern, and shared enum values are documented in the "
        "curated [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md)."
    )
    lines.append("")
    lines.append(
        "Tables are grouped by the service whose models package defines "
        "them. Cross-service references are plain UUID/string columns with "
        "no FK constraint (see SERVICE_COMMUNICATION.md) — a column "
        "pointing at another service's row will NOT show an FK below."
    )
    lines.append("")

    total_tables = 0
    for svc in SERVICES:
        tables = by_service.get(svc, [])
        if not tables:
            continue
        lines.append(f"## {svc}")
        lines.append("")
        for table in sorted(tables, key=lambda t: t.name):
            total_tables += 1
            lines.append(f"### `{table.name}`")
            lines.append("")
            lines.append("| Column | Type | Null | Key | Default |")
            lines.append("|--------|------|------|-----|---------|")
            for col in table.columns:
                key_parts = []
                if col.primary_key:
                    key_parts.append("PK")
                for fk in col.foreign_keys:
                    key_parts.append(f"FK→{fk.target_fullname}")
                if col.index:
                    key_parts.append("idx")
                if col.unique:
                    key_parts.append("uniq")
                default = ""
                if col.server_default is not None:
                    try:
                        default = str(col.server_default.arg)  # type: ignore[attr-defined]
                    except Exception:
                        default = "server_default"
                lines.append(
                    f"| `{col.name}` | {_col_type(col)} | "
                    f"{'' if col.nullable else 'NOT NULL'} | "
                    f"{', '.join(key_parts)} | {default} |"
                )
            # Table-level constraints (unique, check) beyond column flags.
            extra = []
            for c in table.constraints:
                cname = type(c).__name__
                if cname in ("UniqueConstraint", "CheckConstraint") and getattr(
                    c, "name", None
                ):
                    extra.append(f"`{c.name}` ({cname})")
            if extra:
                lines.append("")
                lines.append(f"_Constraints:_ {', '.join(extra)}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"_{total_tables} tables across "
        f"{sum(1 for s in SERVICES if by_service.get(s))} services._"
    )
    lines.append("")

    OUTPUT.write_text("\n".join(lines))
    print(f"Wrote {OUTPUT} ({total_tables} tables)")


if __name__ == "__main__":
    main()
