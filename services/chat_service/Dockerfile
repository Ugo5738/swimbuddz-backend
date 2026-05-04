# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY libs/ libs/
COPY services/chat_service/ services/chat_service/

RUN pip install --no-cache-dir --prefix=/install .

# ---- Stage 2: Runtime ----
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /install /usr/local

COPY libs/ libs/
COPY services/chat_service/ services/chat_service/
COPY services/chat_service/alembic/ alembic/
COPY services/chat_service/alembic.ini .

EXPOSE 8016

CMD ["uvicorn", "services.chat_service.app.main:app", "--host", "0.0.0.0", "--port", "8016"]
