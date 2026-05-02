# libs/moderation

Provider-agnostic content moderation wrappers for SwimBuddz.

## Providers

| Module                  | Provider              | Scope                  | Status                                      |
| ----------------------- | --------------------- | ---------------------- | ------------------------------------------- |
| `libs.moderation.text`  | OpenAI Moderation API | Text                   | Wrapper ready; not wired into any route yet |
| `libs.moderation.image` | AWS Rekognition       | Images (JPEG/PNG/WebP) | Wrapper ready; not wired yet                |

Video moderation deliberately deferred — chat only permits ≤30s clips (chat design doc §8.2). Add when Phase 2 safeguarding ships.

## Usage

```python
from libs.moderation import moderate_text, moderate_image, ProviderUnavailableError

try:
    result = await moderate_text(body, flag_threshold=0.5)
    if result.flagged:
        # Quarantine + enqueue in safeguarding review queue
        ...
except ProviderUnavailableError:
    # Policy: open by default in dev, fail closed in prod
    ...
```

For images:

```python
result = await moderate_image(s3_bucket="chat-attachments-quarantine", s3_key=key)
```

Return type is a stable `ModerationResult`:

- `flagged: bool` — whether any label exceeded its threshold
- `labels: list[ModerationLabel]` — category + confidence + original provider label
- `provider: str` — for audit
- `raw: Any` — full provider response; opaque

## Design rules (do not violate)

1. **Never auto-delete.** Flagged content must always be routed to a manual review queue. Consumers call `moderate_*` and then enqueue; they never delete on their own.
2. **Thresholds are tunable.** Don't hard-code policy. Pass `flag_threshold` / `flag_thresholds` from the caller's config so a `safeguarding_admin` can tune without a code deploy.
3. **Swim-context caveat.** Children in swimwear at pools is SwimBuddz's normal content. Generic moderators will false-positive aggressively under "Suggestive" labels. The `SUGGESTIVE` category is kept separate from `SEXUAL` so consumer policy can treat them differently.
4. **Open vs closed on provider error.** `ProviderUnavailableError` is raised on creds or network failure. Callers decide: open (deliver anyway) in dev; closed (hold as pending) in prod. Default should be closed for any channel containing minors.

## Configuration

Required environment variables when these providers are used:

| Var                                    | Provider | Notes                                                     |
| -------------------------------------- | -------- | --------------------------------------------------------- |
| `OPENAI_API_KEY`                       | text     | Required only if `moderate_text` is invoked               |
| `AWS_REGION` (or `AWS_DEFAULT_REGION`) | image    | Must match the S3 bucket region                           |
| Standard AWS credentials               | image    | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` or IAM role |

No creds required for these modules to _import_ — wrappers only touch providers when called.

## See also

- [docs/design/CHAT_SERVICE_DESIGN.md](../../../docs/design/CHAT_SERVICE_DESIGN.md) §6 — safeguarding rules
- [docs/design/CHAT_SERVICE_DESIGN.md](../../../docs/design/CHAT_SERVICE_DESIGN.md) §8.2 — attachment rules
