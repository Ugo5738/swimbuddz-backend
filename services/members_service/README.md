# Members Service

SwimBuddz Members Service manages member profiles, registration, and member-related data.

## Features

- Member CRUD operations (Create, Read, Update, Delete)
- Pending registration workflow and approval
- Profile photo upload support
- Tier-based membership management (Community, Club, Academy)
- Volunteer roles and interests tracking
- Club challenges and badges
- Member directory with consent management

## API Endpoints

### Members
- `GET /members` - List all members (with filters)
- `GET /members/{id}` - Get member details
- `POST /members` - Create new member
- `PATCH /members/{id}` - Update member
- `DELETE /members/{id}` - Delete member

### Pending Registrations
- `GET /pending-registrations` - List pending registrations
- `POST /pending-registrations` - Create pending registration
- `POST /pending-registrations/{id}/approve` - Approve registration
- `DELETE /pending-registrations/{id}` - Reject registration

### Volunteers
- `GET /volunteers/roles` - List volunteer roles
- `POST /volunteers/roles` - Create volunteer role
- `POST /volunteers/interest` - Register volunteer interest
- `GET /volunteers/interests` - List volunteer interests

### Challenges
- `GET /challenges` - List club challenges
- `POST /challenges` - Create challenge
- `POST /challenges/{id}/complete` - Mark challenge complete

## Database Tables

- `members` - Core member profiles
- `pending_registrations` - Registration approval queue
- `volunteer_roles` - Available volunteer positions
- `volunteer_interests` - Member volunteer signups
- `club_challenges` - Club badge challenges
- `member_challenge_completions` - Challenge completion records

## Environment Variables

See `.env.dev` for required configuration:
- `DATABASE_URL` - PostgreSQL connection string
- `SUPABASE_*` - Supabase configuration for auth

## Running

```bash
# Via Docker
docker-compose up members-service

# Standalone (dev)
cd services/members_service
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

## Port

Default: `8001`
