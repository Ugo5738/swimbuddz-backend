FROM python:3.11-slim

WORKDIR /app

# Install minimal dependencies
RUN pip install --no-cache-dir uvicorn

# Copy libs and service code (handled by docker-compose volume mount in dev)
COPY . /app

# Install the package
RUN pip install -e .

CMD ["uvicorn", "services.store_service.app.main:app", "--host", "0.0.0.0", "--port", "8010", "--reload"]
