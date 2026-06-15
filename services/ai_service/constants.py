"""Shared constants for the AI service."""

import os

# ARQ queue names. Member (logged-in) and PUBLIC (guest analyzer) jobs run on
# SEPARATE queues so a public traffic spike can't starve member analyses. Both
# are processed by the same task; isolation is by queue + a dedicated capped
# worker container (see tasks/worker.py PublicWorkerSettings and the
# docker-compose ai-worker-public service).
MEMBER_QUEUE_NAME = "arq:ai"
PUBLIC_QUEUE_NAME = "arq:ai-public"

# Gumroad checkout base for the paywall "buy credits" links (env-overridable).
GUMROAD_CHECKOUT_BASE = os.environ.get(
    "GUMROAD_CHECKOUT_BASE", "https://swimbuddz.gumroad.com/l/"
)
