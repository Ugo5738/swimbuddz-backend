# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY libs/ libs/
COPY services/sessions_service/ services/sessions_service/

RUN pip install --no-cache-dir --prefix=/install .

# ---- Stage 2: Runtime ----
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /install /usr/local

COPY libs/ libs/
COPY services/sessions_service/ services/sessions_service/
COPY services/sessions_service/alembic/ alembic/
COPY services/sessions_service/alembic.ini .

EXPOSE 8002

CMD ["uvicorn", "services.sessions_service.app.main:app", "--host", "0.0.0.0", "--port", "8002"]
