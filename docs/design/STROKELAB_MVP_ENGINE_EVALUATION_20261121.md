# StrokeLab: minimum valuable coaching product and evaluation plan
Date: 2026-11-21. Status: engineering proposal — not a claim of validated accuracy.

## First customer promise
"Upload a clear side-on freestyle video. Get your single most useful *visible*
technique correction, a supporting video moment, and one safe drill to practise.
Then record a second swim to see whether your coach-confirmed issue improved."

Do NOT promise exact SPM, quantitative body-roll angles, underwater catch quality,
fatigue inference, or automated milestone/Club readiness certification from a few
above-water frames.

## Engine architecture
1. Ingest the original (iPhone HEVC/MOV included) straight to private S3; store
   an immutable original and a separate bounded working representation. For files
   over 250 MiB use S3-to-720p remote transcode of the first 120 seconds, rather
   than copying a multi-gigabyte original into a 2 GiB worker. Keep metadata:
   duration, dimensions, codec, frame rate, upload hash, consent, model version.
   A 2 GiB single S3 PUT works on stable connections, but reliable mobile uploads
   need multipart, per-part retries, resume, abort and lifecycle cleanup.
2. Run the *quality and safety gate* BEFORE expensive coaching. Verify freestyle,
   one trackable swimmer, view angle, visibility, focus and minimum temporal
   coverage. Reject/return credit when footage cannot support a defensible read.
3. Use computer vision for *time*: detection, tracks and wrist/shoulder/hip
   sequences. Sample densely where the arm enters, recovers and breathes; do not
   treat 8 stills as reliable motion measurements. Make segmentation confidence
   measurable on labelled clips.
4. Use a video-capable VLM for motion/context, and selected high-resolution
   keyframes/crops for the anatomy of an observed moment. A contact sheet can
   establish sequence context but is not a replacement for temporal video:
   small combined frames lose detail and confuse the model about timing.
   Candidate benchmark: Gemini 3.6/3.7 Flash native video against current
   GPT-4o still-frame baseline; for visual adjudication compare a current
   OpenAI vision model on timestamped crops. Test price, latency, refusal and
   correctness; do not switch production models just because a model is newer.
5. Rank at most 1-3 **specific, visible** findings. Each must have timestamped
   source evidence, confidence calibrated from evaluation, a plain-English
   explanation and a beginner-safe drill. Never create a fault to fill the list.
   Keep a human coach review/override path separate from automated suggestions.
6. Close the loop: drill assignment -> practice -> resubmit -> compare the same
   concern across clips -> optionally coach-approve improvement. Connect to
   Academy lessons/drills as *supporting evidence*, not automatic milestones.

## Acceptance gates (proposed, must be measured)
- Build a consented, stratified set of 100+ videos: multiple phones, lighting,
  body types, ability levels, angles, free/non-freestyle, and difficult footage.
- Have at least two qualified coaches independently annotate usable footage,
  top issues, source timestamps, and safe drills. Adjudicate disagreements.
- Aim for >=80% leading-fix agreement on clips where coaches agree; >=95%
  correct refusal on genuinely unusable clips; <5% unsupported high-confidence
  claims; no unsafe drills; report agreement denominators, confidence intervals
  and failure cases. These are targets, NOT measured outcomes.
- Re-run the same set on every candidate model/prompt/segmenter release.
  Score false praise, evidence validity, severity calibration and refusal
  separately, not just whether the JSON parses.
- Track p50/p95 total turnaround and costs; include retry charges. Validate
  real iPhone uploads over mobile and Wi-Fi before claiming 2 GiB reliability.

## Unit economics
Actual product COGS includes model tokens, failed/retried model calls, video
download/upload/egress, S3 retention, transcode CPU, email delivery, payment
processor fees, customer support, fraud and credits refunded. Compare both
cost per submitted job and **cost per successfully delivered report**.

The service has a per-attempt model-call limit, per-attempt spend budget, and
rolling daily model-spend circuit breaker. These are safeguards, not proof of
profitability. Store job_id with every provider usage event and publish p95
model cost and conversion margin in the admin dashboard. Choose price only
after measuring a representative first cohort.

## Release sequence
1. Contain direct database exposure (RLS + revoke anon/auth grants on analysis
   and credits); migrate changes durably. Audit the rest of the shared schema
   service by service, rather than flipping 268 unrelated production tables.
2. Verify email for any free or paid submission; test purchase -> verification
   -> direct S3 upload -> queue -> coaching -> email -> retry/refund.
3. Ship job cleanup, stale recovery and real credit reconciliation checks;
   verify 30-day deletion against both DB and S3, including multipart remnants.
4. Introduce multipart/resumable S3 uploads before marketing 2 GiB mobile upload
   as reliable. Keep large-file feature behind controlled rollout until tested.
5. Coach-blinded evaluation -> small Academy pilot -> optional paid pilot.
