"""Isolated pose recovery runner — runs in a SEPARATE process so an OOM or timeout
kills only THIS child, never the worker that spawned it (yolov8-pose at the frame
density accuracy needs can exceed the worker's memory cap on a small box). Decodes
the clip's own dense frames, runs the deterministic counter, and prints the
RecoveryResult as one JSON line on stdout. A non-zero exit / no output / a kill by
the parent's watchdog ⇒ the caller treats pose as unavailable (the VLM count stands).

    python -m services.ai_service.coach.pose_runner <clip_path> <max_frames>
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "reason": "usage"}))
        return 2
    clip = sys.argv[1]
    max_frames = int(sys.argv[2])

    # Heavy deps (cv2/torch/ultralytics) load only here, in the child process.
    from services.ai_service.coach.pose import count_recoveries
    from services.ai_service.pipeline.components.pose_recovery import _decode_dense

    frames, times = _decode_dense(clip, max_frames=max_frames)
    if not frames:
        print(json.dumps({"ok": False, "reason": "no_frames"}))
        return 0

    r = count_recoveries(frames, times)
    print(
        json.dumps(
            {
                "ok": True,
                "count": r.count,
                "confidence": r.confidence,
                "detection_rate": r.detection_rate,
                "near_wrist_conf": r.near_wrist_conf,
                "refused": r.refused,
                "peaks_s": list(r.peaks_s),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
