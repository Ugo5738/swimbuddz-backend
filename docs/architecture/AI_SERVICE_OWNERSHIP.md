# Generative AI Service Ownership

## Policy

`ai_service` is the only SwimBuddz service allowed to call generative-AI
providers or provider SDKs. This includes text, image, audio, video, and
multimodal generation or analysis powered by a generative model.

Domain services call authenticated, typed `ai_service` HTTP endpoints. They
retain ownership of their domain workflow and data:

- `ai_service` owns prompts, curated model context, provider routing,
  credentials, model invocation, validation of model output, cost/latency
  telemetry, and the `ai_requests` audit record.
- A consuming service owns authorization for the user action, persistence,
  domain formatting, human review, scheduling, publication, notifications, and
  any irreversible business action.
- Generative-provider credentials must be exposed only to `ai_service` and its
  workers. A domain service must not import LiteLLM or a generative provider
  SDK. CI enforces this import boundary.

## Deployment Enforcement

The development, staging, and production Compose definitions mask
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `GEMINI_API_KEY` in every non-AI
application container. `ai-service` and its AI workers retain those variables.
Staging also declares `ai-service-staging` with the `ai-service` network alias
used by the default internal service URL.

Chat reads `OPENAI_MODERATION_API_KEY` for the non-generative moderation
exception. During migration it may fall back to the legacy `OPENAI_API_KEY` so
existing environments do not silently lose safety checks. No generation SDK or
generation endpoint is permitted in chat. After every environment has a
separately scoped moderation key, mask `OPENAI_API_KEY` in the chat Compose
environment and remove the code fallback.

## Article Workflow

1. An authenticated admin asks `communications_service` to generate a draft.
2. Communications calls `POST /ai/content/drafts` with a service-role token.
3. `ai_service` combines the request with the versioned canonical editorial
   context in `services/ai_service/context/swimbuddz.py`, invokes the model,
   validates the structured response, and writes an `ai_requests` audit row.
4. Communications converts the neutral response into BlockNote content and
   stores it as an unpublished draft with its AI request ID, context version,
   model name, and featured-image prompt.
5. An admin reviews and edits the draft. Only an explicit admin action or an
   admin-configured schedule can publish it. Email is sent only according to
   the reviewed post's `email_on_publish` setting.
6. Featured images use the same boundary: communications asks `ai_service` to
   generate an image, then downloads the temporary provider URL and stores the
   asset through `media_service`.

"Full SwimBuddz context" means the complete curated, stable context relevant
to editorial generation. It does not mean dumping source code, member records,
live prices, or schedules into a prompt. Dynamic facts must come from a typed
domain API when a future use case genuinely requires them, and must be included
in the AI request audit.

## Human Review

Generated content is always a draft. AI output must not directly publish,
email members, alter entitlements, take payment, mark attendance, or perform
another irreversible action. The domain service validates the result and a
human admin remains accountable for publication.

## Moderation Exception

`libs/moderation` is an intentional cross-cutting exception to the provider
boundary. Text moderation and image safety classification are non-generative
safety controls: they classify submitted content and never create member-facing
content. They may call dedicated moderation/classification providers directly
so they can run synchronously before unsafe input is persisted or delivered.

Moderation uses a separately scoped `OPENAI_MODERATION_API_KEY`. The legacy
fallback to `OPENAI_API_KEY` exists only for deployment migration and should be
removed after every environment has the dedicated key.

This exception does not permit generation, rewriting, summarisation, coaching,
or other content creation outside `ai_service`.
