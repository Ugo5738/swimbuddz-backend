"""Engine version stamp for the Stroke Lab analysis pipeline.

Bump on ANY change that can move a metric, so the validation scorecard
(services/ai_service/validation/) can record which engine produced a number and
regressions stay attributable to a version.

History:
  0.1.0 — original kill-gate-winner heuristic engine (frozen 2026-05-13).
  0.2.0 — stroke rate redefined to cycles/min + refractory fix; confidence gate;
          false-praise verdicts removed; LLM low-confidence hedge (2026-06-16).
"""

STROKELAB_ENGINE_VERSION = "0.2.0"
