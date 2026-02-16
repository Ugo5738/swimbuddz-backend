# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY libs/ libs/
COPY services/media_service/ services/media_service/

RUN pip install --no-cache-dir --prefix=/install .

# ---- Stage 2: Runtime ----
FROM python:3.11-slim

WORKDIR /app

# Pillow needs libjpeg and zlib at runtime
RUN apt-get update && apt-get install -y \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY libs/ libs/
COPY services/media_service/ services/media_service/

EXPOSE 8008

CMD ["uvicorn", "services.media_service.app.main:app", "--host", "0.0.0.0", "--port", "8008"]
