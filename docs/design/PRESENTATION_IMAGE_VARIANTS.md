# Presentation Image Variants

Presentation images are composed before they are attached to a profile, article,
email, academy program, store product, challenge, or homepage slot. Evidence,
payment proofs, private vault media, gallery originals, documents, and videos do
not use this workflow.

## Storage Contract

1. The frontend shows one standard editor. The media purpose controls only the
   crop aspect, crop shape, and output dimensions.
2. Nothing uploads until the user confirms the composition.
3. `media_service` validates a versioned transformation recipe, stores the
   unmodified source in private storage, and creates the exact-size derivative.
4. The owning service stores the derivative `media_id`. The derivative links to
   its source through `media_items.source_media_id` for future re-cropping.
5. Preserved sources are excluded from public media lists and can be resolved
   only by their uploader or an admin.

The standard editor provides crop/reposition/zoom, 90-degree rotation, fine
straightening, horizontal and vertical flips, tonal controls, restrained filter
presets, undo/redo/reset, and press-and-hold original comparison. Processing is
deterministic and follows this order: EXIF orientation, flip, rotation, crop,
resize, adjustments/filter, encode.

Every derivative stores `transformation_recipe` in `metadata_info`. Recipe
version 1 contains the normalized crop, total rotation, flips, manual tonal
adjustments, filter name, and filter strength. New recipe versions must be
introduced rather than changing the meaning of existing fields.

External URL registration is not available for crop-controlled purposes. This
prevents an uncropped URL from bypassing the presentation contract.

Article block images use this same workflow. Evidence, payment proofs,
documents, private-vault originals, gallery originals, and source videos bypass
the editor. A publishing derivative or video poster may use the editor without
altering its protected original.

## Presets

| Purpose | Output | Aspect |
| --- | ---: | ---: |
| Profile photo | 800 x 800 | 1:1 |
| Academy/program cover | 1600 x 900 | 16:9 |
| Article and digest image | 1200 x 675 | 16:9 |
| Category/collection image | 1200 x 900 | 4:3 |
| Product image | 1200 x 1200 | 1:1 |
| Badge image | 512 x 512 | 1:1 |
| Challenge example image | 1200 x 675 | 16:9 |
| Homepage banner | 1920 x 1080 | 16:9 |
| Homepage community photo | 1000 x 1000 | 1:1 |

The backend preset map is authoritative for output dimensions. The frontend map
must use the same aspect ratio so accepted crops never require distortion.
