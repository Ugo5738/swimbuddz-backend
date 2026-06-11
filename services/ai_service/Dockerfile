# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY libs/ libs/
COPY services/ai_service/ services/ai_service/

# Install with the strokelab extra — pulls torch / ultralytics / mediapipe /
# opencv. Other services use `pip install .` and skip these heavy deps.
RUN pip install --no-cache-dir --prefix=/install ".[strokelab]"

# ---- Stage 2: Runtime ----
FROM python:3.11-slim

WORKDIR /app

# System deps:
#   * ffmpeg — used by services.ai_service.analysis (Stroke Lab) and by
#     opencv-python-headless to decode pool footage. Without it, MediaPipe
#     pose detection runs but every video read fails on the worker.
#   * libgl1 / libglib2.0-0 — opencv-python-headless needs the GL + glib
#     shared objects at import time on slim images.
#   * libgles2 / libegl1 — MediaPipe's pose landmarker initialises a GL/EGL
#     context on x86_64 Linux and fails at runtime with
#     "libGLESv2.so.2: cannot open shared object file" without these. (Not
#     needed on the arm64 dev image, which is why this only bit in prod.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg libgl1 libglib2.0-0 libgles2 libegl1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY libs/ libs/
COPY services/ai_service/ services/ai_service/
COPY services/ai_service/alembic/ alembic/
COPY services/ai_service/alembic.ini .

EXPOSE 8011

CMD ["uvicorn", "services.ai_service.app.main:app", "--host", "0.0.0.0", "--port", "8011", "--proxy-headers", "--forwarded-allow-ips=*"]
