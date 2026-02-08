FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY libs/ libs/
COPY services/volunteer_service/ services/volunteer_service/
COPY services/volunteer_service/alembic/ alembic/
COPY services/volunteer_service/alembic.ini .

# Install dependencies
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 8012

# Run the service
CMD ["uvicorn", "services.volunteer_service.app.main:app", "--host", "0.0.0.0", "--port", "8012"]
