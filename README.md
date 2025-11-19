# SwimBuddz Backend

Monorepo for the SwimBuddz backend services and shared libraries.

- Python 3.11+
- FastAPI + SQLAlchemy async stack
- See `TODO.md` for the ordered implementation plan.

Install editable dependencies:

```bash
pip install -e .
```

Run the gateway service locally (placeholder app for now):

```bash
python -m uvicorn services.gateway_service.app.main:app --reload
```

## Docker & Compose

Each microservice runs in its own container so failures stay isolated. Once Task 0.2 is complete, you will be able to:

- Build all services: `docker compose build`
- Start a single service plus dependencies (e.g., gateway + db): `docker compose up gateway`
- Tail logs for an individual service: `docker compose logs -f members`
- Override env vars per service by copying `.env` into `.env.<service>` and referencing it in `docker-compose.yml`.

Keep the services decoupled—if one crashes, compose should keep the others healthy. Use per-service restart policies and avoid tightly coupling processes inside a single container.
