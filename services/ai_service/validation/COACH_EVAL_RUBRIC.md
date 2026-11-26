# Stroke Lab Coach-Labeled Evaluation Rubric (`bakeoff_eval.py`)

This rubric defines how SwimBuddz coaches independently annotate consented clips to benchmark Stroke Lab model/architecture candidates before changing production defaults (`ADR-013`).

## 1. Recommended Dataset Phases

Do **not** block initial benchmarking on 100 clips. Build in two phases:

- **Phase 1 Pilot (25–30 clips — ~1 week):**
  - **18–20 usable freestyle clips** across real SwimBuddz Academy & Club pools (Lagos lighting, indoor/outdoor glare, varied body types & ability levels, side-on + elevated deck angles, left/right/bilateral breathers).
  - **7–10 genuinely unusable / catastrophic clips** (head-on, underwater-only, non-freestyle breaststroke/backstroke, too far/blurry, empty pool, extreme camera shake) to prove `>=95%` refusal accuracy.
- **Phase 2 Expansion (100+ clips — rolling):**
  - Continuously append consented Academy/Club clips and hard edge cases discovered in production.

## 2. Independent Multi-Coach Labeling Protocol

Have **at least 2 qualified coaches** label every clip **independently** (blinded to each other and to AI outputs). `bakeoff_eval.py` reports coach-to-coach inter-rater agreement first so acceptance bars are anchored in real coaching consensus.

Create one JSON file per clip in `<labels-dir>/<clip_id>.json`:

```json
{
  "clip_id": "yaba_side_adult_01",
  "video_file": "yaba_side_adult_01.mp4",
  "camera_view": "side-on",
  "coaches": [
    {
      "coach_id": "coach_ugo",
      "usable": true,
      "leading_fix_area": "head_breath",
      "secondary_fix_areas": ["body_line", "recovery_elbow"],
      "catastrophic_unusable": false,
      "notes": "Lifts chin forward on right-side breath; drops hips slightly."
    },
    {
      "coach_id": "coach_2",
      "usable": true,
      "leading_fix_area": "head_breath",
      "secondary_fix_areas": ["body_line"],
      "catastrophic_unusable": false,
      "notes": "Head lift before breathing is the #1 TI foundation fix."
    }
  ]
}
```

### Allowed `leading_fix_area` Values (Closed TI Rubric)
- `body_line` (sinking hips/legs, piked, arched — Priority 1)
- `head_breath` (lifted head / forward gaze / head-up breathing — Priority 1)
- `body_rotation` (flat/belly-down, limited roll — Priority 2)
- `entry_reach` (short/rushed lead hand or overreach; **never** side-view crossover — Priority 3)
- `recovery_elbow` (dropped or wide recovery — Priority 3)
- `none` (usable clip with no clear high-confidence above-water fault)

## 3. Running the Bake-Off

```bash
PYTHONPATH=. .venv/bin/python -m services.ai_service.validation.bakeoff_eval \
  --clips-root ~/Downloads/strokelab_eval/clips \
  --labels-dir ~/Downloads/strokelab_eval/labels \
  --configs A_gpt4o_stills,B_gemini38_chunks_4fps,C_gemini38_chunks_4fps_hybrid,D_gemini38_hybrid_adjudicated,E_gemini35_lite_chunks_4fps_hybrid \
  --repeats 3 \
  --out bakeoff_scorecard.json
```

## 4. Acceptance Targets Before Model Promotion

| Metric | Target |
| --- | --- |
| Leading-fix agreement (clips where coaches agree) | `>= 80%` |
| Catastrophic refusal rate (unusable footage) | `>= 95%` |
| Unsupported high-confidence claims (`conf >= 0.70`) | `< 5%` |
| ADR-013 honesty violations (banned numbers, crossover, invisible underwater) | `0%` |
| Repeatability across 3 runs on identical clips | `>= 85%` |
| Post-promo (Jan 2027) cost per delivered report | Measured & within target margin |
