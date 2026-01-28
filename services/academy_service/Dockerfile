FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY libs/ libs/
COPY services/academy_service/ services/academy_service/
COPY services/academy_service/alembic/ alembic/
COPY services/academy_service/alembic.ini .

# Install dependencies
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 8006

# Run the service
CMD ["uvicorn", "services.academy_service.app.main:app", "--host", "0.0.0.0", "--port", "8006"]
