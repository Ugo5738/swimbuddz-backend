# Events Service

SwimBuddz Events Service manages community events, RSVPs, and social activities.

## Features

- Community event management
- Event RSVP tracking
- Capacity management
- Event types categorization
- Tier-based access control

## API Endpoints

### Events
- `GET /events` - List events (with filters)
- `GET /events/{id}` - Get event details
- `POST /events` - Create event
- `PATCH /events/{id}` - Update event
- `DELETE /events/{id}` - Delete event

### RSVPs
- `GET /events/{id}/rsvps` - List event RSVPs
- `POST /events/{id}/rsvp` - Register RSVP
- `PATCH /rsvps/{id}` - Update RSVP status
- `DELETE /rsvps/{id}` - Cancel RSVP

## Database Tables

- `events` - Community event records
- `event_rsvps` - Member RSVP responses

## Event Types

- **Social**: Hangouts, meetups, social gatherings
- **Volunteer**: Beach cleanups, community service
- **Beach Day**: Ocean swim outings
- **Watch Party**: Competition viewing events
- **Training**: Special training sessions

## Key Features

### RSVP Management
- Going / Maybe / Not Going status
- Capacity tracking
- Waitlist support
- RSVP notifications

### Access Control
- Community events: All members
- Club events: Club & Academy tiers
- Academy events: Academy tier only

### Event Details
- Date, time, and location
- Description and requirements
- Capacity limits
- RSVP deadlines

## Environment Variables

See `.env.dev` for required configuration:
- `DATABASE_URL` - PostgreSQL connection string

## Running

```bash
# Via Docker
docker-compose up events-service

# Standalone (dev)
cd services/events_service
uvicorn app.main:app --host 0.0.0.0 --port 8007 --reload
```

## Port

Default: `8007`
