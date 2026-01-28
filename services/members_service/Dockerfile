FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY libs/ libs/
COPY services/members_service/ services/members_service/
COPY services/members_service/alembic/ alembic/
COPY services/members_service/alembic.ini .

# Install dependencies
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 8001

# Run the service
CMD ["uvicorn", "services.members_service.app.main:app", "--host", "0.0.0.0", "--port", "8001"]
