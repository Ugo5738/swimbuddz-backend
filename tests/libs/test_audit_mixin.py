"""Contract test for libs/common/audit.py (B4).

Asserts the canonical audit-log shape is stable: column names, SQL
types, nullability, the namespacing helper, and the actor-parser
behavior. Future store + chat PRs adopt this same mixin, so any
accidental drift in the shape will break this test before it ships
to the dependent services.

Pure introspection — no DB required.
"""

from __future__ import annotations

import uuid
from typing import get_type_hints

import pytest
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from libs.common.audit import (
    DOMAIN_CHAT,
    DOMAIN_STORE,
    DOMAIN_WALLET,
    AuditLogMixin,
    AuditLogRead,
    make_action,
    parse_uuid_or_none,
)
from libs.db.base import Base


# ── Domain constants ────────────────────────────────────────────────
def test_domain_constants_are_stable() -> None:
    """These strings are persisted in every audit row's `domain`
    column. Changing them would orphan all historical data, so the
    test is intentionally a literal-equality assertion — not a
    behavioral one."""
    assert DOMAIN_WALLET == "wallet"
    assert DOMAIN_STORE == "store"
    assert DOMAIN_CHAT == "chat"


# ── make_action helper ──────────────────────────────────────────────
def test_make_action_namespaces_under_domain() -> None:
    assert make_action(DOMAIN_WALLET, "freeze") == "wallet.freeze"
    assert make_action(DOMAIN_STORE, "price_changed") == "store.price_changed"
    assert make_action(DOMAIN_CHAT, "message_deleted") == "chat.message_deleted"


# ── parse_uuid_or_none helper ───────────────────────────────────────
def test_parse_uuid_or_none_accepts_uuid_string() -> None:
    s = "60019cbb-5c88-4f87-8d3b-cd051ace2e54"
    result = parse_uuid_or_none(s)
    assert result == uuid.UUID(s)


def test_parse_uuid_or_none_accepts_uuid_instance() -> None:
    u = uuid.uuid4()
    assert parse_uuid_or_none(u) is u


def test_parse_uuid_or_none_returns_none_for_non_uuid_string() -> None:
    # Mirror real-world seed data ("seed-admin") and human admin IDs.
    assert parse_uuid_or_none("seed-admin") is None
    assert parse_uuid_or_none("not-a-uuid") is None


def test_parse_uuid_or_none_returns_none_for_none() -> None:
    assert parse_uuid_or_none(None) is None


def test_parse_uuid_or_none_returns_none_for_unparseable_types() -> None:
    # Whatever the input — an int, a dict, a list — if it doesn't parse
    # as a UUID, we want None back rather than an exception. Writers
    # always also set actor_label, so falling through to None is fine.
    assert parse_uuid_or_none(42) is None
    assert parse_uuid_or_none({"id": "x"}) is None


# ── AuditLogMixin SQL shape ─────────────────────────────────────────
# A minimal concrete model so we can introspect the table the mixin
# would produce. Keeping it local — defining it at module scope adds
# it to Base.metadata, which is fine for an isolated test model.
class _FixtureAuditLog(AuditLogMixin, Base):
    __tablename__ = "_test_audit_log_fixture"


# Names + types every service's audit table must have after adopting
# the mixin. The keys are SQL column names, the values describe the
# expected (SQLAlchemy column type python_type, nullability).
_CANONICAL_COLUMNS = {
    "id": (uuid.UUID, False),
    "domain": (str, False),
    "entity_type": (str, False),
    "entity_id": (uuid.UUID, False),
    "action": (str, False),
    "actor_id": (uuid.UUID, True),
    "actor_label": (str, True),
    "old_value": (dict, True),
    "new_value": (dict, True),
    "reason": (str, True),
    "ip_address": (str, True),
    "created_at": (None, False),  # datetime — python_type via DateTime
}


def test_mixin_exposes_canonical_columns() -> None:
    """Exactly these columns, no more no less."""
    actual = {c.name for c in _FixtureAuditLog.__table__.columns}
    expected = set(_CANONICAL_COLUMNS)
    assert actual == expected, (
        f"canonical shape drift — extra: {actual - expected}; "
        f"missing: {expected - actual}"
    )


@pytest.mark.parametrize("col_name,expected", _CANONICAL_COLUMNS.items())
def test_column_nullability_matches_canonical(col_name: str, expected: tuple) -> None:
    _py_type, expected_nullable = expected
    col = _FixtureAuditLog.__table__.c[col_name]
    assert (
        col.nullable is expected_nullable
    ), f"{col_name}: expected nullable={expected_nullable}, got {col.nullable}"


def test_uuid_columns_use_postgres_uuid_type() -> None:
    """id, entity_id, actor_id all need pgUUID with as_uuid=True so
    SQLAlchemy hands back uuid.UUID instances rather than strings."""
    for col_name in ("id", "entity_id", "actor_id"):
        col = _FixtureAuditLog.__table__.c[col_name]
        assert isinstance(col.type, UUID), f"{col_name} is not UUID, got {col.type!r}"
        assert col.type.as_uuid is True


def test_jsonb_columns_use_postgres_jsonb_type() -> None:
    """old_value/new_value are JSONB so Postgres-native operators work
    in queries; downgrading to JSON would silently lose indexing."""
    for col_name in ("old_value", "new_value"):
        col = _FixtureAuditLog.__table__.c[col_name]
        assert isinstance(col.type, JSONB), f"{col_name} is not JSONB, got {col.type!r}"


def test_string_columns_have_expected_lengths() -> None:
    """Length caps matter for index size + storage. Pin them so a
    careless edit (e.g. dropping String(45) → String() on ip_address)
    surfaces here, not in production."""
    expected_lengths = {
        "domain": 32,
        "entity_type": 64,
        "action": 128,
        "actor_label": 255,
        "ip_address": 45,
    }
    for col_name, expected_len in expected_lengths.items():
        col = _FixtureAuditLog.__table__.c[col_name]
        assert isinstance(col.type, String), f"{col_name} is not String"
        assert col.type.length == expected_len, (
            f"{col_name}: expected String({expected_len}), got "
            f"String({col.type.length})"
        )


def test_id_is_primary_key() -> None:
    pk_cols = [c.name for c in _FixtureAuditLog.__table__.primary_key.columns]
    assert pk_cols == ["id"]


# ── AuditLogRead Pydantic shape ─────────────────────────────────────
def test_pydantic_schema_field_names_match_sql_shape() -> None:
    """AuditLogRead must expose the same field set as the mixin."""
    pydantic_fields = set(AuditLogRead.model_fields)
    sql_fields = set(_CANONICAL_COLUMNS)
    assert pydantic_fields == sql_fields, (
        f"AuditLogRead vs SQL drift — extra: {pydantic_fields - sql_fields}; "
        f"missing: {sql_fields - pydantic_fields}"
    )


def test_pydantic_schema_nullability_matches_sql_shape() -> None:
    """Each Pydantic field's Optional-ness must match its SQL column."""
    hints = get_type_hints(AuditLogRead)
    for col_name, (_py_type, sql_nullable) in _CANONICAL_COLUMNS.items():
        hint = hints[col_name]
        is_optional = type(None) in getattr(hint, "__args__", ())
        assert is_optional is sql_nullable, (
            f"{col_name}: SQL nullable={sql_nullable}, "
            f"Pydantic Optional={is_optional}"
        )
