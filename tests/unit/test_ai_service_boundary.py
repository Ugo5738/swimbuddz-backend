"""Architecture guard for generative provider ownership."""

import ast
from pathlib import Path

from fastapi.routing import APIRoute
from libs.auth.dependencies import require_service_role
from services.ai_service.routers.content import router as ai_content_router

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_PROVIDER_IMPORT_ROOTS = {
    Path("services/ai_service"),
    Path("libs/moderation"),
}
PROVIDER_MODULES = {
    "anthropic",
    "google.generativeai",
    "google.genai",
    "litellm",
    "openai",
}


def _is_allowed(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return any(
        relative == allowed or allowed in relative.parents
        for allowed in ALLOWED_PROVIDER_IMPORT_ROOTS
    )


def _provider_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return {
        module
        for module in imports
        if any(
            module == provider or module.startswith(f"{provider}.")
            for provider in PROVIDER_MODULES
        )
    }


def test_generative_provider_sdks_are_owned_by_ai_service():
    violations: list[str] = []
    for source_root in (ROOT / "services", ROOT / "libs"):
        for path in source_root.rglob("*.py"):
            if _is_allowed(path):
                continue
            imports = _provider_imports(path)
            if imports:
                violations.append(
                    f"{path.relative_to(ROOT)} imports {', '.join(sorted(imports))}"
                )

    assert not violations, "Provider boundary violations:\n" + "\n".join(violations)


def test_content_generation_endpoints_require_service_role():
    content_routes = [
        route for route in ai_content_router.routes if isinstance(route, APIRoute)
    ]

    assert content_routes
    for route in content_routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert require_service_role in dependencies, route.path
