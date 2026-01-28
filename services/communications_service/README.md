# Communications Service

SwimBuddz Communications Service manages announcements, content posts, and community communications.

## Features

- Announcement management (broadcast messages)
- Content/Tips posting system
- Tier-based content access control
- Comment moderation
- Email/push notification triggers (future)

## API Endpoints

### Announcements
- `GET /announcements` - List announcements (with filters)
- `GET /announcements/{id}` - Get announcement details
- `POST /announcements` - Create announcement
- `PATCH /announcements/{id}` - Update announcement
- `DELETE /announcements/{id}` - Delete announcement
- `POST /announcements/{id}/publish` - Publish announcement

### Content Posts
- `GET /content` - List content posts
- `GET /content/{id}` - Get post details
- `POST /content` - Create content post
- `PATCH /content/{id}` - Update post
- `DELETE /content/{id}` - Delete post

### Comments (future)
- `GET /content/{id}/comments` - List comments
- `POST /content/{id}/comments` - Add comment
- `DELETE /comments/{id}` - Delete comment

## Database Tables

- `announcements` - Platform-wide announcements
- `content_posts` - Educational content, tips, articles
- `post_comments` - User comments on content (future)

## Key Features

### Announcements
- Urgent vs. general priority
- Tier-specific targeting
- Scheduled publishing
- Read tracking

### Content System
- Markdown support for rich formatting
- Category tagging
- Featured content
- SEO-friendly slugs

### Access Control
- Community tier: General content only
- Club tier: Training tips & techniques
- Academy tier: All educational content

## Environment Variables

See `.env.dev` for required configuration:
- `DATABASE_URL` - PostgreSQL connection string

## Running

```bash
# Via Docker
docker-compose up communications-service

# Standalone (dev)
cd services/communications_service
uvicorn app.main:app --host 0.0.0.0 --port 8004 --reload
```

## Port

Default: `8004`
