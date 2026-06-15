"""Shared constants for the AI service."""

# ARQ queue names. Member (logged-in) and PUBLIC (guest analyzer) jobs run on
# SEPARATE queues so a public traffic spike can't starve member analyses. Both
# are processed by the same task; isolation is by queue + a dedicated capped
# worker container (see tasks/worker.py PublicWorkerSettings and the
# docker-compose ai-worker-public service).
MEMBER_QUEUE_NAME = "arq:ai"
PUBLIC_QUEUE_NAME = "arq:ai-public"
