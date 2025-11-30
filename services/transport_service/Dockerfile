FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY libs/ libs/
COPY services/transport_service/ services/transport_service/

# Install dependencies
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 8009

# Run the service
CMD ["uvicorn", "services.transport_service.app.main:app", "--host", "0.0.0.0", "--port", "8009"]
