FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY libs/ libs/
COPY services/ai_service/ services/ai_service/
COPY services/ai_service/alembic/ alembic/
COPY services/ai_service/alembic.ini .

# Install dependencies
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 8011

# Run the service
CMD ["uvicorn", "services.ai_service.app.main:app", "--host", "0.0.0.0", "--port", "8011"]
