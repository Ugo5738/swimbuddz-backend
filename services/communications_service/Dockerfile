FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY libs/ libs/
COPY services/communications_service/ services/communications_service/
COPY alembic/ alembic/
COPY alembic.ini .

# Install dependencies
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 8004

# Run the service
CMD ["uvicorn", "services.communications_service.app.main:app", "--host", "0.0.0.0", "--port", "8004"]
