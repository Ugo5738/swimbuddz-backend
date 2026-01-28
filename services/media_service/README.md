# Media Service

SwimBuddz Media Service for managing photos, albums, and gallery functionality.

## Features

- Album management (session/event/academy/general types)
- Photo upload with automatic thumbnail generation
- Member tagging in photos (respecting media consent)
- Featured photos for homepage
- Storage backends: Supabase Storage (default) or AWS S3

## Storage Configuration

### Supabase Storage (Default)
```env
STORAGE_BACKEND=supabase
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
SUPABASE_STORAGE_BUCKET=swimbuddz-media
```

### AWS S3 (Fallback)
```env
STORAGE_BACKEND=s3
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_S3_BUCKET=your_bucket_name
AWS_REGION=us-east-1
```

## API Endpoints

### Albums
- `POST /media/albums` - Create album
- `GET /media/albums` - List albums
- `GET /media/albums/{id}` - Get album with photos
- `PUT /media/albums/{id}` - Update album
- `DELETE /media/albums/{id}` - Delete album

### Photos
- `POST /media/albums/{id}/photos` - Upload photo
- `GET /media/photos` - List photos
- `GET /media/photos/featured` - Get featured photos
- `PUT /media/photos/{id}` - Update photo metadata
- `DELETE /media/photos/{id}` - Delete photo

### Tags
- `POST /media/photos/{id}/tags` - Tag member
- `DELETE /media/photos/{id}/tags/{member_id}` - Remove tag

## Permissions

- Album/Photo creation: Admin or Media Volunteer role required
- Public viewing: All members can view galleries
- Tagging: Admin or Media Volunteer role required
