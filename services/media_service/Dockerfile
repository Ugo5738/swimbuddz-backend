FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for PIL/Pillow
RUN apt-get update && apt-get install -y \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy root pyproject.toml and install project
COPY pyproject.toml /app/
RUN pip install --no-cache-dir -e .

# Copy shared libs
COPY libs/ /app/libs/

# Copy service code
COPY services/media_service/ /app/services/media_service/

# Expose port
EXPOSE 8000

# Run service
CMD ["uvicorn", "services.media_service.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
