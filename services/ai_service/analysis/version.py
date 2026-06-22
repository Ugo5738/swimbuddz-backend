"""Engine version stamp for the Stroke Lab pipeline.

Stamped into each coach run (coach_result + swim_frame_labels.engine_version) so
a stored result — and the fine-tuning corpus built from it — stays attributable
to the engine that produced it. Bump on any change that can move the read.

History:
  0.1.0 — original kill-gate-winner heuristic engine (frozen 2026-05-13).
  0.2.0 — stroke rate redefined to cycles/min + refractory fix; confidence gate;
          false-praise verdicts removed; LLM low-confidence hedge (2026-06-16).
  0.3.0 — VLM coach is the primary engine; legacy pose/metrics engine retired,
          results are coach-only (2026-06-22).
"""

STROKELAB_ENGINE_VERSION = "0.3.0"
