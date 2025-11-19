FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY libs/ libs/
COPY services/attendance_service/ services/attendance_service/
COPY services/members_service/models.py services/members_service/
COPY services/sessions_service/models.py services/sessions_service/
COPY alembic/ alembic/
COPY alembic.ini .

# Install dependencies
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 8003

# Run the service
CMD ["uvicorn", "services.attendance_service.app.main:app", "--host", "0.0.0.0", "--port", "8003"]
