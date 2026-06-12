# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY libs/ libs/
COPY services/ai_service/ services/ai_service/

# Install the strokelab extra — pulls torch / ultralytics / mediapipe / opencv.
# Other services use `pip install .` and skip these heavy deps.
#
# On amd64, PyPI's default torch wheel bundles CUDA (libtorch_cuda.so, ~5-8GB)
# even though prod has no GPU — pure dead weight that also runs the deploy box
# out of disk. Inference already runs on CPU here (nano YOLO + lite MediaPipe,
# no .cuda() calls). So we pin the CPU-only torch build from PyTorch's CPU index
# (same versions, just +cpu); ultralytics then finds torch already satisfied and
# skips the CUDA wheel, cutting the image from ~10GB to ~3GB. Gated on amd64 —
# arm64 has no CUDA torch variant, so it installs normally there. Bump these two
# pins together with any torch upgrade (keep them matched: torch<->torchvision).
ARG TARGETARCH
RUN if [ "$TARGETARCH" = "amd64" ]; then \
        pip install --no-cache-dir --prefix=/install \
            --extra-index-url https://download.pytorch.org/whl/cpu \
            "torch==2.12.0+cpu" "torchvision==0.27.0+cpu" ".[strokelab]"; \
    else \
        pip install --no-cache-dir --prefix=/install ".[strokelab]"; \
    fi

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
