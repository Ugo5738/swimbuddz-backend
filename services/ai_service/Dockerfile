ARG BASE_IMAGE=deps-base

# ---- Fallback Stage 1: Build shared base dependencies locally (skipped when BASE_IMAGE is set) ----
FROM python:3.11-slim AS deps-builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

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

# Install ONLY the strokelab extra deps (torch / ultralytics / mediapipe / opencv)
# on top of the shared Python base image BEFORE copying app source, so code-only
# edits hit the GHA Docker layer cache instead of reinstalling PyTorch every deploy.
#
# On amd64, PyPI's default torch wheel bundles CUDA (libtorch_cuda.so, ~5-8GB)
# even though prod has no GPU — pure dead weight that also runs the deploy box
# out of disk. Inference already runs on CPU here (nano YOLO + lite MediaPipe,
# no .cuda() calls). So we pin the CPU-only torch build from PyTorch's CPU index
# (same versions, just +cpu); ultralytics then finds torch already satisfied and
# skips the CUDA wheel, cutting the image from ~10GB to ~3GB. Gated on amd64 —
# arm64 has no CUDA torch variant, so it installs normally there. Bump these two
# pins together with any torch upgrade (keep them matched: torch<->torchvision).
COPY pyproject.toml ./
ARG TARGETARCH
RUN python -c "import tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['optional-dependencies']['strokelab']; open('/tmp/strokelab-requirements.txt', 'w').write('\n'.join(deps) + '\n')" \
    && if [ "$TARGETARCH" = "amd64" ]; then \
        pip install --no-cache-dir \
            --extra-index-url https://download.pytorch.org/whl/cpu \
            "torch==2.12.0+cpu" "torchvision==0.27.0+cpu" -r /tmp/strokelab-requirements.txt; \
    else \
        pip install --no-cache-dir -r /tmp/strokelab-requirements.txt; \
    fi \
    && rm -f /tmp/strokelab-requirements.txt pyproject.toml

COPY libs/ libs/
COPY services/ai_service/ services/ai_service/
COPY services/ai_service/alembic/ alembic/
COPY services/ai_service/alembic.ini .

EXPOSE 8011

CMD ["uvicorn", "services.ai_service.app.main:app", "--host", "0.0.0.0", "--port", "8011", "--proxy-headers", "--forwarded-allow-ips=*"]
