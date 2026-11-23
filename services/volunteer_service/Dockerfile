ARG BASE_IMAGE=deps-base

# ---- Fallback Stage 1: Build dependencies locally (skipped when BASE_IMAGE is set) ----
FROM python:3.11-slim AS deps-builder

WORKDIR /build

COPY pyproject.toml constraints.txt ./

RUN python -c "import tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; open('requirements.txt', 'w').write('\n'.join(deps) + '\n')" \
    && pip install --no-cache-dir --prefix=/install -c constraints.txt -r requirements.txt \
    && rm -f requirements.txt

# ---- Fallback Stage 2: Shared runtime dependencies (skipped when BASE_IMAGE is set) ----
FROM python:3.11-slim AS deps-base

WORKDIR /app
ENV PYTHONPATH=/app

COPY --from=deps-builder /install /usr/local
RUN printf '/app\n' > /usr/local/lib/python3.11/site-packages/swimbuddz.pth

# ---- Runtime ----
FROM ${BASE_IMAGE} AS runtime

WORKDIR /app
ENV PYTHONPATH=/app

COPY libs/ libs/
COPY services/volunteer_service/ services/volunteer_service/
COPY services/volunteer_service/alembic/ alembic/
COPY services/volunteer_service/alembic.ini .

EXPOSE 8012

CMD ["uvicorn", "services.volunteer_service.app.main:app", "--host", "0.0.0.0", "--port", "8012", "--proxy-headers", "--forwarded-allow-ips=*"]
