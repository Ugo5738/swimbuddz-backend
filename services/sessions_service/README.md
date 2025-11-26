# Sessions Service

SwimBuddz Sessions Service manages training sessions, locations, and routes.

## Features

- Session scheduling and management
- Location and pool management
- Route planning with departure times
- Session capacity tracking
- Session status management (scheduled, in-progress, completed, cancelled)

## API Endpoints

### Sessions
- `GET /sessions` - List sessions (with filters)
- `GET /sessions/{id}` - Get session details
- `POST /sessions` - Create new session
- `PATCH /sessions/{id}` - Update session
- `POST /sessions/{id}/cancel` - Cancel session
- `DELETE /sessions/{id}` - Delete session

### Locations
- `GET /locations` - List all locations
- `GET /locations/{id}` - Get location details
- `POST /locations` - Create location
- `PATCH /locations/{id}` - Update location

### Routes
- `GET /routes` - List routes
- `POST /routes` - Create route
- `PATCH /routes/{id}` - Update route

## Database Tables

- `sessions` - Training session records
- `locations` - Swimming pool/venue information
- `routes` - Transportation routes to locations

## Key Features

### Session Types
- Club training sessions
- Academy cohort sessions
- Open water sessions
- Special event sessions

### Location Management
- Pool details (capacity, amenities)
- Geographic coordinates
- Operating hours
- Access requirements

### Route Planning
- Departure times before session
- Meeting point information
- Transportation options

## Environment Variables

See `.env.dev` for required configuration:
- `DATABASE_URL` - PostgreSQL connection string

## Running

```bash
# Via Docker
docker-compose up sessions-service

# Standalone (dev)
cd services/sessions_service
uvicorn app.main:app --host 0.0.0.0 --port 8002 --reload
```

## Port

Default: `8002`
