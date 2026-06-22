# Stroke Lab golden-set labeling guide

The engine ships numbers with **no measured accuracy**. This golden set fixes
that: a handful of hand-labeled clips + `scorecard.py` give every metric an
error vs ground truth, against the design doc's own gates (stroke rate within
10% of manual count, one-sided breathing detected, zero "you're doing great"
false positives).

## What to collect

**Target: ~12–15 clips to start** (expand to 30–50 over time):

- **~10 "good" clips** — usable side-on footage, spanning variety so we don't
  overfit to one pool: different pools/lighting, a left-breather, a right-breather,
  a bilateral breather, a flat swimmer and a strong-roll swimmer, and ideally one
  sprint and one distance clip.
- **~3–5 "bad" clips** — deliberately hard, to prove the engine *degrades
  honestly* instead of emitting a confident wrong number: head-on (not side-on),
  murky/underwater, swimmer too far/small, deck spectators in frame, too short.

**Where to get them:** your own cohort/session footage is the best source
(real conditions, rights-cleared) — supplement with a few deliberately-bad clips
you film on purpose. Don't use random internet clips (licensing + relevance).

## How to label each clip

1. Drop the video in `clips/<filename>` (git-ignored — large files stay out of git).
2. Add a row to `manifest.csv`: `clip_id,filename,source,notes`.
3. Create `labels/<clip_id>.json` (committed — this is the ground truth):

```json
{
  "clip_id": "cohort_rightbreather_01",
  "usable": true,
  "stroke_cycles": 18,
  "breaths_left": 1,
  "breaths_right": 4,
  "roll_bucket": "moderate",
  "notes": "side-on, 25m, right-side breather"
}
```

**Definitions (pin these so two labelers agree):**
- **`stroke_cycles`** — count one arm's hand entries across the whole clip. One
  cycle = one left + one right arm action. (This is cycles, the convention — not
  "every arm = 1".)
- **`breaths_left` / `breaths_right`** — a breath = head clearly turned to that
  side to inhale (mouth out of the water). Count each side.
- **`roll_bucket`** — coarse eyeball: `flat` (barely rolls), `moderate`, or
  `strong` (rolls well past 45°).
- **`usable`** — `false` for the deliberately-bad clips (head-on / murky / too
  far / spectators). The engine should degrade to "couldn't reliably read" on these.

**Inter-rater check:** have a second person label the first ~5 clips independently;
if stroke-cycle counts disagree by more than ~1, tighten the definition before
labeling the rest.

## Run the eval

Validate Stage-1 recovery segmentation against the labels (pure CV, no API).
Needs cv2, so run **inside the ai-worker container**:

```bash
docker compose exec ai-worker-public \
  python -m services.ai_service.validation.recovery_eval --golden-root ~/Downloads/strokelab2/golden
```

It reports detected-vs-expected recoveries per clip + MAE + within-±1 rate. Re-run
after any segmentation change and diff the delta — that's how we prove a change
helped instead of guessing. (The legacy metrics `scorecard` was retired with the
pose engine.)
