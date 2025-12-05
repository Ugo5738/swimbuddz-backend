FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY libs/ libs/
COPY services/sessions_service/ services/sessions_service/
COPY services/sessions_service/alembic/ alembic/
COPY services/sessions_service/alembic.ini .

# Install dependencies
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 8002

# Run the service
CMD ["uvicorn", "services.sessions_service.app.main:app", "--host", "0.0.0.0", "--port", "8002"]
