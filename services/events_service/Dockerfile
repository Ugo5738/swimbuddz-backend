FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY libs/ libs/
COPY services/events_service/ services/events_service/
COPY alembic/ alembic/
COPY alembic.ini .

# Install dependencies
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 8007

# Run the service
CMD ["uvicorn", "services.events_service.app.main:app", "--host", "0.0.0.0", "--port", "8007"]
