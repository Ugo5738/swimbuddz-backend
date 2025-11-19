FROM python:3.11-slim

WORKDIR /app

# Install system dependencies required for building Python packages (e.g. asyncpg)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project metadata
COPY pyproject.toml README.md ./

# Copy shared libraries and services
COPY libs/ libs/
COPY services/ services/

# Install the project in editable mode
RUN pip install --no-cache-dir -e .

# Default command (can be overridden by docker-compose)
CMD ["uvicorn", "services.gateway_service.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
