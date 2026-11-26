"""Guard deployment-isolated service packages against cross-service imports.

The production Store and Ledger images contain only their own service folder
plus `libs/`. Repository-wide Python imports can pass local tests yet crash
those images at process startup. This is an explicit architectural contract.

Keep the guard in the normal unit-test suite as well as the isolated boot tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROTECTED = ("store_service", "ledger_service", "media_service", "gateway_service")

# Tables owned by SwimBuddz business services that the detachable ledger_service
# must NEVER query via raw SQL or ORM (ADR-023).
FORBIDDEN_LEDGER_SQL_TABLES = (
    "payments",
    "paystack_settlements",
    "enrollments",
    "academy_class_plans",
    "academy_class_allocations",
    "enrollment_installments",
    "club_enrollments",
    "store_orders",
    "store_order_items",
    "store_inventory_items",
    "store_inventory_valuations",
    "store_supplier_invoices",
    "store_trade_receivables",
    "members",
    "sessions",
)


def _forbidden_imports(source: str, owner: str) -> list[tuple[int, str]]:
    """Reject references to another service, including deferred imports.

    AST catches imports inside functions and TYPE_CHECKING blocks as well as
    top-level imports. Calls to importlib.import_module and __import__ with
    literal service module names are checked too.
    """
    parsed = ast.parse(source)
    allowed = "services." + owner
    problems: list[tuple[int, str]] = []

    def check(name: str, line: int) -> None:
        if name.startswith("services.") and not (
            name == allowed or name.startswith(allowed + ".")
        ):
            problems.append((line, name))

    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check(alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            check(node.module, node.lineno)
            # "from services import payments_service" bypasses a module-only check
            if node.module == "services":
                for alias in node.names:
                    check("services." + alias.name, node.lineno)
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            dynamic = (
                isinstance(function, ast.Name) and function.id == "__import__"
            ) or (
                isinstance(function, ast.Attribute)
                and function.attr == "import_module"
                and isinstance(function.value, ast.Name)
                and function.value.id == "importlib"
            )
            if dynamic and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str):
                    check(value, node.lineno)
    return problems


@pytest.mark.parametrize("service", PROTECTED)
def test_isolated_service_never_imports_a_different_service(service: str) -> None:
    folder = ROOT / "services" / service
    assert folder.is_dir(), f"Protected service missing: {folder}"
    failures: list[str] = []
    for path in sorted(folder.rglob("*.py")):
        # Python packages included in each service image are also inspected.
        if "__pycache__" in path.parts:
            continue
        for lineno, module in _forbidden_imports(path.read_text("utf-8"), service):
            failures.append(f"{path.relative_to(ROOT)}:{lineno} imports {module}")
    assert not failures, (
        f"{service} image copies only libs/ and services/{service}/. "
        "Cross-service Python imports will crash its isolated container. "
        "Move shared contracts to libs/, or use authenticated HTTP/event projections.\n"
        + "\n".join(failures)
    )


def test_static_guard_detects_imports_inside_deferred_functions() -> None:
    source = """\
from services.store_service.models import Product

def later():
    from services.ledger_service.app.deps import require_ledger_role
    import services.payments_service.models
    from services import academy_service
    importlib.import_module("services.members_service.models")
"""
    violations = _forbidden_imports(source, "store_service")
    names = {name for _, name in violations}
    assert "services.ledger_service.app.deps" in names
    assert "services.payments_service.models" in names
    assert "services.academy_service" in names
    assert "services.members_service.models" in names
    assert "services.store_service.models" not in names


def test_ledger_service_never_queries_non_ledger_domain_tables() -> None:
    """ADR-023: ledger_service must stay 100% detachable with zero raw SQL against SwimBuddz tables."""
    import re

    folder = ROOT / "services" / "ledger_service"
    table_pattern = re.compile(
        r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(?:public\.)?("
        + "|".join(re.escape(t) for t in FORBIDDEN_LEDGER_SQL_TABLES)
        + r")\b",
        re.IGNORECASE,
    )
    violations: list[str] = []
    for path in sorted(folder.rglob("*.py")):
        if "__pycache__" in path.parts or "alembic" in path.parts:
            continue
        text = path.read_text("utf-8")
        for match in table_pattern.finditer(text):
            lineno = text.count("\n", 0, match.start()) + 1
            violations.append(
                f"{path.relative_to(ROOT)}:{lineno} references non-ledger table {match.group(1)!r}"
            )
    assert not violations, (
        "ledger_service must remain strictly detachable (ADR-023). "
        "Move cross-domain projections into gateway_service calling service-owned HTTP endpoints.\n"
        + "\n".join(violations)
    )
