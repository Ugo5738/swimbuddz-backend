FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for PIL/Pillow
RUN apt-get update && apt-get install -y \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml /app/
COPY libs/ /app/libs/
COPY services/media_service/ /app/services/media_service/

# Install the project in editable mode
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 8008

# Run service
CMD ["uvicorn", "services.media_service.app.main:app", "--host", "0.0.0.0", "--port", "8008"]
