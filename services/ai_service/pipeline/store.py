"""Persist a pipeline run so all further work reuses it for free.

The expensive part of a run is the VLM calls (gate, per-frame classification,
coaching). We cache those raw outputs to disk; re-running the pipeline against a
saved run replays them from cache (``$0``) and re-derives everything deterministic
(grouping, hedged count, findings) fresh — so you can tune, render, and score
endlessly without re-paying.

A run folder ``<dir>/``:
  manifest.json   clip id + config + costs + engine version
  frames/         the dense strip JPEGs (re-inspect/re-render, no re-extract)
  vlm_cache.json  the PAID outputs: gate verdict, per-frame labels, coach responses
  result.json     the derived PipelineResult (free to rebuild from the cache)
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path

from services.ai_service.coach.frames import Frame, load_frames, save_frames

try:
    from services.ai_service.analysis.version import STROKELAB_ENGINE_VERSION as _VER
except Exception:  # analysis pkg may be unimportable without ML extras
    _VER = "unknown"


def _enc(o):
    """JSON-encode dataclasses (recursively) and enums."""
    if is_dataclass(o) and not isinstance(o, type):
        o = asdict(o)
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, dict):
        return {k: _enc(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_enc(v) for v in o]
    return o


def save_run(run_dir: str | Path, *, clip_id: str, ctx, result) -> Path:
    """Write the full run (frames + vlm_cache + result + manifest) to ``run_dir``."""
    d = Path(run_dir)
    d.mkdir(parents=True, exist_ok=True)
    strip = ctx.strip or ctx.frames
    save_frames(strip, d / "frames")
    (d / "vlm_cache.json").write_text(json.dumps(ctx.cache or {}, indent=2))
    (d / "result.json").write_text(json.dumps(_enc(result), indent=2))
    manifest = {
        "clip_id": clip_id,
        "engine_version": _VER,
        "profile": result.input_profile.value,
        "gate_tier": result.gate_tier.value,
        "config": _enc(ctx.config),
        "n_frames": len(strip),
        "n_instances": len(ctx.instances),
        "total_cost_usd": result.total_cost_usd,
    }
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return d


def load_run(run_dir: str | Path) -> tuple[dict, list[Frame]]:
    """Return (vlm_cache, strip_frames) so a re-run replays VLM calls for free."""
    d = Path(run_dir)
    cache_path = d / "vlm_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    frames = load_frames(d / "frames")
    return cache, frames
