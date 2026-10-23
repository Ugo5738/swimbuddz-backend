"""Benchmark real dashboard orchestration against deterministic fake services.

No network or database access. Run from the repository root:
    DATABASE_URL=postgresql+psycopg://test:test@localhost/test PYTHONPATH=. \
        python scripts/performance/benchmark_dashboards.py --output result.json

These numbers measure orchestration, not production API or browser latency.
"""

import argparse
import asyncio
import hashlib
import json
import math
import platform
import statistics
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import httpx
from starlette.requests import Request

from services.gateway_service.app.routers import dashboard


class DelayedClient:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds

    async def get(self, path: str, headers: dict | None = None) -> httpx.Response:
        await asyncio.sleep(self.delay_seconds)
        payload = [] if path in {"/sessions/", "/announcements/"} else {}
        return httpx.Response(200, json=payload)


def percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "p99_ms": round(ordered[math.ceil(len(ordered) * 0.99) - 1], 3),
    }


async def benchmark(samples: int, delay_ms: float) -> dict:
    request = Request({"type": "http", "headers": []})
    client = DelayedClient(delay_ms / 1000)
    results = {}
    with (
        patch.object(dashboard.clients, "members_client", client),
        patch.object(dashboard.clients, "sessions_client", client),
        patch.object(dashboard.clients, "communications_client", client),
    ):
        for name, operation in (
            ("admin_dashboard_stats", dashboard.get_admin_dashboard_stats),
            ("member_dashboard_aggregator", dashboard.get_member_dashboard),
        ):
            await operation(request, current_user=None)  # one unmeasured warmup
            durations = []
            for _ in range(samples):
                started = time.perf_counter()
                await operation(request, current_user=None)
                durations.append((time.perf_counter() - started) * 1000)
            results[name] = {**percentiles(durations), "samples_ms": durations}
    return {
        "kind": "synthetic_orchestration_no_network_no_database",
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "dashboard_source_sha256": hashlib.sha256(
            Path(dashboard.__file__).read_bytes()
        ).hexdigest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "samples": samples,
        "warmups_per_workflow": 1,
        "injected_delay_per_service_ms": delay_ms,
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--delay-ms", type=float, default=25)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1 or args.delay_ms < 0:
        parser.error("samples must be positive and delay must be nonnegative")
    report = asyncio.run(benchmark(args.samples, args.delay_ms))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                name: percentiles(value["samples_ms"])
                for name, value in report["results"].items()
            },
            indent=2,
        )
    )
