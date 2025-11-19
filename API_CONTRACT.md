# SwimBuddz Backend – API Contract (Core)

This contract defines the **HTTP interface** exposed via the gateway (`https://api.swimbuddz.com`). All endpoints assume Bearer authentication with a Supabase access token unless marked as public.

---

## 1. Identity

### `GET /api/v1/identity/me`

- **Auth:** Required
- **Description:** Return the Supabase identity plus linked SwimBuddz member info.
- **Response 200**

```json
{
  "user_id": "supabase-user-id",
  "email": "user@example.com",
  "role": "member",
  "member_id": "uuid-or-null"
}
```

---

## 2. Members

### `POST /api/v1/members/`

- **Auth:** Required
- **Description:** Create a SwimBuddz member profile for the authenticated Supabase user.
- **Body:** `MemberCreate` schema (includes `supabase_user_id`, contact info, emergency contact, etc.).
- **Response 201:** `MemberRead`

### `GET /api/v1/members/me`

- **Auth:** Required
- **Description:** Fetch the logged-in member profile.
- **Response 200:** `MemberRead`

### `PATCH /api/v1/members/me`

- **Auth:** Required
- **Description:** Partially update the logged-in member profile.
- **Body:** `MemberUpdate`
- **Response 200:** `MemberRead`

### `GET /api/v1/members/admin`

- **Auth:** Admin
- **Description:** List/filter all members.
- **Response 200:** `MemberRead[]`

### `PATCH /api/v1/members/admin/{member_id}/status`

- **Auth:** Admin
- **Description:** Update `membership_status`.
- **Body**

```json
{
  "status_value": "active" | "inactive" | "banned"
}
```

- **Response 200:** Updated `MemberRead`

---

## 3. Sessions

### `POST /api/v1/sessions/`

- **Auth:** Admin
- **Description:** Create a session/event (club training, meetup, trip, etc.).
- **Body:** `SessionCreate`
- **Response 201:** `SessionRead`

### `GET /api/v1/sessions/`

- **Auth:** Public
- **Description:** List upcoming sessions.
- **Query Params:** `location`, `session_type`, `limit` (optional).
- **Response 200:** `SessionRead[]`

### `GET /api/v1/sessions/{session_id}`

- **Auth:** Public
- **Description:** Retrieve details for a single session.
- **Response 200:** `SessionRead`

---

## 4. Attendance & Sign-In

### `POST /api/v1/sessions/{session_id}/sign-in`

- **Auth:** Required
- **Description:** Create or update the current member's attendance record for a session.
- **Default Flow:** Full session, no ride-share, total fee = session `pool_fee`.

#### Request Body

```json
{
  "time_variant": "full",
  "time_variant_note": null,
  "ride_share_role": "none",
  "ride_share_seats_offered": 0
}
```

- `time_variant` (string, optional; default `full`):
  - `full` – attending entire session.
  - `arrive_late` – arriving after scheduled start.
  - `leave_early` – leaving before scheduled end.
  - `custom` – custom timing; requires `time_variant_note`.
- `time_variant_note` (string, optional) – required when `time_variant = "custom"` (e.g. `"Joining from 1pm"`).
- `ride_share_role` (string, optional; default `none`):
  - `none` – no community ride-share.
  - `passenger` – member needs a ride.
  - `driver` – member can drive others.
- `ride_share_seats_offered` (integer, optional; default `0`) – number of seats when `ride_share_role = "driver"`.

#### Response 200 – `AttendanceRead`

```json
{
  "attendance_id": "uuid",
  "session_id": "uuid",
  "member_id": "uuid",
  "attendance_status": "registered",
  "time_variant": "full",
  "time_variant_note": null,
  "ride_share_role": "none",
  "ride_share_seats_offered": 0,
  "pool_fee": 2000,
  "ride_share_fee": 0,
  "total_fee": 2000,
  "payment_status": "unpaid",
  "payment_reference": "SB-YABA-2025-11-22-0001"
}
```

- `attendance_status`: `registered` | `confirmed_paid` | `cancelled` | `no_show`.
- `payment_status`: `unpaid` | `member_reported_paid` | `confirmed_paid`.
- `payment_reference`: string reference members use for transfers.

### `GET /api/v1/members/me/attendance`

- **Auth:** Required
- **Description:** Attendance summary and history for the logged-in member.

#### Response 200

```json
{
  "summary": {
    "total_sessions_last_60_days": 7,
    "total_sessions_all_time": 15
  },
  "items": [
    {
      "session_id": "uuid",
      "session_title": "Yaba Club Training",
      "pool_location": "yaba_rowe_park",
      "session_type": "club_training",
      "start_datetime": "2025-11-16T12:00:00+01:00",
      "attendance_status": "confirmed_paid",
      "payment_status": "confirmed_paid",
      "time_variant": "full"
    }
  ]
}
```

- `summary.total_sessions_last_60_days`: integer count.
- `summary.total_sessions_all_time`: integer count.
- `items[].session_type`: `club_training`, `community_meetup`, `trip`, `camp`, `open_water`, etc.
- `items[].attendance_status`: `registered` | `confirmed_paid` | `cancelled` | `no_show`.
- `items[].payment_status`: `unpaid` | `member_reported_paid` | `confirmed_paid`.
- `items[].time_variant`: same enum as sign-in body.

### `GET /api/v1/sessions/{session_id}/attendance/admin`

- **Auth:** Admin
- **Description:** List attendance for a specific session.
- **Response 200:** `AttendanceRead[]`

### `GET /api/v1/sessions/{session_id}/pool-list`

- **Auth:** Admin
- **Description:** Export paid attendee list for pool management (CSV or JSON).

---

## 5. Announcements (Communications)

Announcements power the public noticeboard and admin share helpers.

### `POST /api/v1/announcements/`

- **Auth:** Admin
- **Description:** Create a noticeboard announcement.

#### Request Body – `AnnouncementCreate`

```json
{
  "title": "Yaba session delayed due to rain",
  "summary": "Today’s Yaba training will start 30 minutes later due to rain.",
  "body": "Hi SwimBuddz! Due to the heavy rain around Yaba, we are delaying today’s session by 30 minutes...",
  "category": "rain_update"
}
```

- `title`: short headline.
- `summary`: short preview/snippet.
- `body`: full long-form content.
- `category`: `rain_update` | `schedule_change` | `event` | `competition` | `general`.

#### Response 201 – `AnnouncementRead`

```json
{
  "id": "uuid",
  "title": "Yaba session delayed due to rain",
  "summary": "Today’s Yaba training will start 30 minutes later due to rain.",
  "body": "Hi SwimBuddz! Due to the heavy rain around Yaba, we are delaying today’s session by 30 minutes...",
  "category": "rain_update",
  "created_at": "2025-11-19T10:15:00Z",
  "updated_at": "2025-11-19T10:15:00Z",
  "published_at": "2025-11-19T10:15:00Z",
  "is_pinned": false
}
```

### `GET /api/v1/announcements/`

- **Auth:** Public
- **Description:** List announcements (newest first). List responses may omit `body` to reduce payload.
- **Response 200**

```json
[
  {
    "id": "uuid",
    "title": "Yaba session delayed due to rain",
    "summary": "Today’s Yaba training will start 30 minutes later due to rain.",
    "category": "rain_update",
    "created_at": "2025-11-19T10:15:00Z",
    "published_at": "2025-11-19T10:15:00Z",
    "is_pinned": false
  }
]
```

### `GET /api/v1/announcements/{id}`

- **Auth:** Public
- **Description:** Fetch the full announcement content.
- **Response 200:** `AnnouncementRead`
