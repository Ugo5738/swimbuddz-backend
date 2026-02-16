# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY libs/ libs/
COPY services/volunteer_service/ services/volunteer_service/

RUN pip install --no-cache-dir --prefix=/install .

# ---- Stage 2: Runtime ----
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /install /usr/local

COPY libs/ libs/
COPY services/volunteer_service/ services/volunteer_service/
COPY services/volunteer_service/alembic/ alembic/
COPY services/volunteer_service/alembic.ini .

EXPOSE 8012

CMD ["uvicorn", "services.volunteer_service.app.main:app", "--host", "0.0.0.0", "--port", "8012"]
