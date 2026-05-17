"""Generate the API endpoint reference from the OpenAPI spec.

Reads the combined ``openapi.json`` (produced by
``scripts/api/generate-openapi.py``) and emits a Markdown endpoint
inventory grouped by tag:

    docs/API_ENDPOINTS_GENERATED.md

This is the auto-generated companion to the hand-curated
``docs/API_ENDPOINTS.md`` (which keeps worked examples, auth notes,
and request/response walkthroughs). The generated file guarantees a
complete, never-stale list of every route.

Regenerate after backend changes (openapi.json must be fresh first):

    python scripts/api/generate-openapi.py > openapi.json
    python scripts/api/generate-endpoints-doc.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # swimbuddz-backend
DOCS_ROOT = PROJECT_ROOT.parent
OPENAPI = PROJECT_ROOT / "openapi.json"
OUTPUT = DOCS_ROOT / "docs" / "API_ENDPOINTS_GENERATED.md"

_METHOD_ORDER = ["get", "post", "put", "patch", "delete", "options", "head"]


def main() -> None:
    if not OPENAPI.exists():
        print(
            f"error: {OPENAPI} not found. Run "
            f"`python scripts/api/generate-openapi.py > openapi.json` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    spec = json.loads(OPENAPI.read_text())
    paths = spec.get("paths", {})

    # tag -> list of (path, method, summary, requires_auth)
    by_tag: dict[str, list[tuple[str, str, str, bool]]] = defaultdict(list)
    total = 0

    for path, methods in paths.items():
        for method, op in methods.items():
            if method.lower() not in _METHOD_ORDER:
                continue
            total += 1
            tags = op.get("tags") or ["(untagged)"]
            tag = tags[0]
            summary = (
                op.get("summary")
                or (op.get("description") or "").split("\n")[0]
                or "—"
            ).strip()
            requires_auth = "security" in op or bool(spec.get("security"))
            by_tag[tag].append((path, method.upper(), summary, requires_auth))

    lines: list[str] = []
    lines.append("# API Endpoint Reference (auto-generated)")
    lines.append("")
    lines.append(
        "> **Generated** by `scripts/api/generate-endpoints-doc.py` from "
        "`openapi.json`. Do not hand-edit — regenerate after backend "
        "changes. Worked examples, auth flows, and request/response "
        "walkthroughs live in the curated "
        "[API_ENDPOINTS.md](./API_ENDPOINTS.md)."
    )
    lines.append("")
    lines.append(
        "All paths are shown as exposed through the gateway "
        "(`/api/v1/...`). `internal/*` routes are service-to-service "
        "(service-role JWT, not gateway-exposed)."
    )
    lines.append("")
    lines.append(f"**{total} operations across {len(by_tag)} tags.**")
    lines.append("")

    for tag in sorted(by_tag, key=str.lower):
        ops = by_tag[tag]
        lines.append(f"## {tag}")
        lines.append("")
        lines.append("| Method | Path | Summary |")
        lines.append("|--------|------|---------|")
        for path, method, summary, _auth in sorted(
            ops, key=lambda r: (r[0], _METHOD_ORDER.index(r[1].lower()))
        ):
            safe_summary = summary.replace("|", "\\|")
            lines.append(f"| {method} | `{path}` | {safe_summary} |")
        lines.append("")

    OUTPUT.write_text("\n".join(lines))
    print(f"Wrote {OUTPUT} ({total} operations, {len(by_tag)} tags)")


if __name__ == "__main__":
    main()
