# SwimBuddz Backend – API Endpoints Reference

> **Complete route list:** see the auto-generated companion
> [API_ENDPOINTS_GENERATED.md](../../docs/API_ENDPOINTS_GENERATED.md) —
> every operation from `openapi.json`, grouped by tag, guaranteed
> never-stale (backend CI fails if `openapi.json` drifts). Regenerate
> with `python scripts/api/generate-endpoints-doc.py`. This file stays
> hand-curated for auth flows and worked examples.

This document defines the **HTTP interface** exposed via the gateway (`https://api.swimbuddz.com` or `http://localhost:8000` for development).

All endpoints assume Bearer authentication with a Supabase access token unless marked as **Public**.

## Service Coverage

✅ **Fully Documented:** Identity, Members, Sessions, Attendance, Announcements, Admin Dashboard, Academy, Payments, Transport, Events, Media, Store, Volunteers, Wallet

**Note:** All major services are now documented. For complete service information including models, database schema, and implementation details, see [SERVICE_REGISTRY.md](../../docs/reference/SERVICE_REGISTRY.md).

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

### Internal Endpoints (Service-to-Service Only)

These are mounted on the members service directly (not exposed through the gateway) and require an internal service-role JWT.

#### `GET /internal/members/birthdays-today`

- **Auth:** Internal service header
- **Query:** `on` (optional ISO date `YYYY-MM-DD`). Defaults to today in `Africa/Lagos`.
- **Description:** Active, approved members whose `date_of_birth` falls on the target date. Used by the communications-service daily birthday cron.
- **Response 200:** `[{ id, first_name, last_name, email, age }]`. `age` is computed from DOB on the target date so the caller can filter minors without re-deriving it.

#### `GET /internal/members/admins`

- **Auth:** Internal service header
- **Description:** Active members whose `roles` overlap any admin-flavoured role (`admin`, `comms_admin`, `community_manager`). Used to fan out admin-task notifications such as the daily birthday WhatsApp-shoutout reminder.
- **Response 200:** `[{ id, first_name, last_name, email, roles }]`

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

### `POST /api/v1/attendance/sessions/{session_id}/coach-mark`

- **Auth:** Admin or assigned coach
- **Description:** Bulk-upsert attendance records for a session. Powers the admin Attendance tab's per-row marking AND the "Mark all expected as present" bulk button.
- **Behavior is session-kind dependent:**
  - **Cohort sessions** use a default-present model. Status `present` _deletes_ the exception row (reverting the member to implicit present). `excused` / `absent` / `late` upsert an explicit row.
  - **Non-cohort sessions** (`community` / `club` / `event`) have no default-present. Status `present` upserts a real `AttendanceRecord` — this is how the bulk "Mark all expected as present" admin button turns paid bookings into attendance rows.

#### Request Body

```json
{
  "entries": [
    {
      "member_id": "uuid",
      "status": "present",
      "notes": null
    }
  ]
}
```

- `status`: `present` | `absent` | `late` | `excused` | `cancelled`
- Members not included in `entries` are untouched (cohort: implicitly present; non-cohort: still expected/unmarked).

#### Response 200 – `CoachAttendanceMarkResponse`

```json
{
  "session_id": "uuid",
  "upserted": 3,
  "deleted": 1,
  "records": [
    { "id": "uuid", "session_id": "uuid", "member_id": "uuid", "status": "present", "..." }
  ]
}
```

- `upserted`: number of rows created or updated.
- `deleted`: number of exception rows removed (cohort `present` revert path).

### Background Cron (attendance-worker)

The attendance service ships an ARQ worker (`services.attendance_service.worker.WorkerSettings`) running two daily crons. Requires the `attendance-worker` container in `docker-compose.yml`.

| Cron                      | When (UTC) | Purpose                                                                                                                                                                                               |
| ------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `notify_stale_attendance` | 19:00      | For sessions that ended in the last 24h with ≥1 confirmed booking lacking a PRESENT/LATE attendance row, dispatches an in-app notification to the session's coaches + all admins. Dedupes via window. |
| `sweep_no_show_bookings`  | 02:45      | For CONFIRMED bookings older than the notify cron whose session ended without a matching attendance row, auto-creates `AttendanceRecord(status=ABSENT, booking_id=<>)`. Looks back 7 days.            |

The 7-hour gap between the two gives coaches an evening window to mark attendance manually before the sweep fills in ABSENT on their behalf.

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

### Birthday Celebrations (Daily Cron)

A scheduled ARQ task on the communications worker fires daily at 06:00 UTC (07:00 WAT). It pulls today's birthdays from `GET /internal/members/birthdays-today` and:

1. Sends a branded birthday email to each adult (≥18) who has not opted out via `email_birthday` on their notification preferences.
2. Creates an in-app `Notification` row (`type="birthday"`, `category="announcements"`) for each emailed member.
3. Dispatches a single `birthday_admin_reminder` notification to all admin-flavoured roles (via `GET /internal/members/admins`) listing **everyone** with a birthday today (including minors) so a human can post the WhatsApp shoutout.

The opt-out lives on `notification_preferences.email_birthday` (boolean, default `true`); members toggle it on the **Notification Settings** page.

---

## 6. Admin Dashboard

### `GET /api/v1/admin/dashboard-stats`

- **Auth:** Admin
- **Description:** Aggregate statistics for the admin dashboard.
- **Response 200**

```json
{
  "total_members": 150,
  "active_members": 120,
  "inactive_members": 30,
  "upcoming_sessions_count": 5,
  "recent_announcements_count": 2
}
```

---

## 7. Academy (33+ Endpoints)

### Programs

> The program schema includes `faq_json`: an optional ordered list of
> `{ "question": str, "answer": str }` shown on the public program page.
> It is returned by all program reads and accepted (optional) by program
> create/update.

#### `GET /api/v1/academy/programs`

- **Auth:** Public
- **Description:** List all academy programs
- **Response 200:** `ProgramRead[]`

#### `GET /api/v1/academy/programs/{program_id}`

- **Auth:** Public
- **Description:** Get program details including curriculum
- **Response 200:** `ProgramRead`

#### `POST /api/v1/academy/programs`

- **Auth:** Admin
- **Description:** Create new academy program
- **Body:** `ProgramCreate` (name, description, level, duration, prerequisites, etc.)
- **Response 201:** `ProgramRead`

#### `PATCH /api/v1/academy/programs/{program_id}`

- **Auth:** Admin
- **Description:** Update program details
- **Response 200:** `ProgramRead`

### Cohorts

#### `GET /api/v1/academy/cohorts`

- **Auth:** Public/Admin
- **Description:** List all cohorts (filter by status)
- **Query Params:** `status` (open, active, completed), `program_id`
- **Response 200:** `CohortRead[]`

#### `GET /api/v1/academy/cohorts/open`

- **Auth:** Public
- **Description:** List cohorts open for enrollment
- **Response 200:** `CohortRead[]`

#### `GET /api/v1/academy/cohorts/{cohort_id}`

- **Auth:** Public
- **Description:** Get cohort details
- **Response 200:** `CohortRead`

#### `POST /api/v1/academy/cohorts`

- **Auth:** Admin
- **Description:** Create new cohort
- **Body:** `CohortCreate` (program_id, start_date, end_date, capacity, etc.)
- **Response 201:** `CohortRead`

#### `PATCH /api/v1/academy/cohorts/{cohort_id}`

- **Auth:** Admin
- **Description:** Update cohort details
- **Response 200:** `CohortRead`

### Enrollments

#### `POST /api/v1/academy/enrollments/me`

- **Auth:** Required
- **Description:** Self-enroll in a cohort
- **Body:** `{ "cohort_id": "uuid" }`
- **Response 201:** `EnrollmentRead`
- **Note:** Creates payment intent for program fee

#### `GET /api/v1/academy/my-enrollments`

- **Auth:** Required
- **Description:** List current member's enrollments
- **Response 200:** `EnrollmentRead[]`

#### `GET /api/v1/academy/enrollments/{enrollment_id}`

- **Auth:** Required (owner) or Admin
- **Description:** Get enrollment details
- **Response 200:** `EnrollmentRead`

#### `GET /api/v1/academy/enrollments`

- **Auth:** Admin
- **Description:** List all enrollments (filter by cohort, status)
- **Query Params:** `cohort_id`, `status`, `member_id`
- **Response 200:** `EnrollmentRead[]`

#### `PATCH /api/v1/academy/enrollments/{enrollment_id}`

- **Auth:** Admin
- **Description:** Update enrollment status
- **Body:** `{ "status": "enrolled" | "waitlist" | "dropped" | "graduated" }`
- **Response 200:** `EnrollmentRead`

### Progress Tracking

#### `GET /api/v1/academy/enrollments/{enrollment_id}/progress`

- **Auth:** Required (owner) or Admin
- **Description:** Get student progress and milestone completion
- **Response 200:** `ProgressRead` with milestone statuses

#### `POST /api/v1/academy/enrollments/{enrollment_id}/progress`

- **Auth:** Coach or Admin
- **Description:** Update student milestone progress
- **Body:** `{ "milestone_id": "uuid", "status": "completed", "notes": "..." }`
- **Response 200:** `ProgressRead`

#### `GET /api/v1/academy/enrollments/{enrollment_id}/progress/{progress_id}/events`

- **Auth:** Owner (student), assigned coach for the cohort, or Admin
- **Description:** Return the append-only audit trail of claim, review, and
  status-change events for a single milestone progress record. Each event
  includes a snapshot of the notes, evidence media id, and score at the time —
  so rejection feedback and prior evidence are preserved even after resubmits.
- **Response 200:** `List[MilestoneReviewEventResponse]` ordered by `created_at`
  ascending. Fields: `id`, `progress_id`, `enrollment_id`, `milestone_id`,
  `event_type` (`claimed` | `approved` | `rejected` | `status_changed`),
  `actor_id`, `actor_role` (`student` | `coach` | `admin`), `previous_status`,
  `new_status`, `student_notes_snapshot`, `coach_notes_snapshot`,
  `evidence_media_id_snapshot`, `score_snapshot`, `created_at`.

### Curriculum Management

#### `GET /api/v1/academy/programs/{program_id}/curriculum`

- **Auth:** Public
- **Description:** Get program curriculum structure
- **Response 200:** Curriculum with weeks, lessons, skills

#### `POST /api/v1/academy/programs/{program_id}/curriculum`

- **Auth:** Admin
- **Description:** Create/update curriculum
- **Body:** Complete curriculum structure
- **Response 200:** Updated curriculum

---

## 8. Payments

### Payment Intents

#### `POST /api/v1/payments/intents`

- **Auth:** Required
- **Description:** Create Paystack payment intent
- **Body:**

```json
{
  "amount": 5000,
  "currency": "NGN",
  "payment_type": "session" | "cohort" | "store_order",
  "reference_id": "uuid",
  "callback_url": "https://app.swimbuddz.com/checkout/success"
}
```

- **Response 201:** `PaymentIntentRead` with `authorization_url` for Paystack

#### `GET /api/v1/payments/intents/{intent_id}`

- **Auth:** Required (owner) or Admin
- **Description:** Get payment intent status
- **Response 200:** `PaymentIntentRead`

### Payment Records

#### `GET /api/v1/payments`

- **Auth:** Admin
- **Description:** List all payment records
- **Query Params:** `member_id`, `status`, `payment_type`
- **Response 200:** `PaymentRecordRead[]`

#### `GET /api/v1/payments/{payment_id}`

- **Auth:** Required (owner) or Admin
- **Description:** Get payment record details
- **Response 200:** `PaymentRecordRead`

#### `PATCH /api/v1/payments/{payment_id}/verify`

- **Auth:** Admin
- **Description:** Manually verify payment
- **Body:** `{ "verified": true, "notes": "Bank transfer confirmed" }`
- **Response 200:** Updated `PaymentRecordRead`

### Webhooks

#### `POST /api/v1/payments/webhook`

- **Auth:** Paystack signature validation
- **Description:** Receive Paystack payment notifications
- **Body:** Paystack webhook payload
- **Response 200:** Success acknowledgment

---

## 9. Transport (Ride-Sharing)

### Ride Areas

#### `GET /api/v1/transport/areas`

- **Auth:** Public
- **Description:** List all ride areas
- **Response 200:** `RideAreaRead[]`

#### `POST /api/v1/transport/areas`

- **Auth:** Admin
- **Description:** Create ride area
- **Body:** `{ "name": "Lekki", "slug": "lekki" }`
- **Response 201:** `RideAreaRead`

### Pickup Locations

#### `GET /api/v1/transport/areas/{area_id}/pickups`

- **Auth:** Public
- **Description:** List pickup locations in area
- **Response 200:** `PickupLocationRead[]`

#### `POST /api/v1/transport/areas/{area_id}/pickups`

- **Auth:** Admin
- **Description:** Create pickup location
- **Body:** `PickupLocationCreate` (name, address, GPS coords)
- **Response 201:** `PickupLocationRead`

### Routes

#### `GET /api/v1/transport/routes`

- **Auth:** Public
- **Description:** Get route information
- **Query Params:** `pickup_location_id`, `destination`
- **Response 200:** `RouteInfoRead[]`

#### `POST /api/v1/transport/routes`

- **Auth:** Admin
- **Description:** Create route with distance/duration/cost
- **Response 201:** `RouteInfoRead`

### Session Ride Configuration

#### `GET /api/v1/transport/sessions/{session_id}/config`

- **Auth:** Public
- **Description:** Get ride-sharing config for session
- **Response 200:** `SessionRideConfigRead`

#### `POST /api/v1/transport/sessions/{session_id}/config`

- **Auth:** Admin
- **Description:** Configure ride-sharing for session
- **Body:** `{ "ride_share_enabled": true, "pickup_locations": ["uuid1", "uuid2"] }`
- **Response 201:** `SessionRideConfigRead`

### Ride Bookings

#### `GET /api/v1/transport/sessions/{session_id}/bookings/me`

- **Auth:** Required
- **Description:** Get member's ride booking for session
- **Response 200:** `RideBookingRead`

#### `POST /api/v1/transport/sessions/{session_id}/bookings`

- **Auth:** Required
- **Description:** Create/update ride booking
- **Body:** `{ "ride_share_option": "lead" | "join", "pickup_location_id": "uuid", "seats_offered": 3 }`
- **Response 201:** `RideBookingRead`

#### `GET /api/v1/transport/sessions/{session_id}/bookings`

- **Auth:** Admin
- **Description:** List all ride bookings for session
- **Response 200:** `RideBookingRead[]`

---

## 10. Events (Minimal Implementation)

#### `GET /api/v1/events`

- **Auth:** Public
- **Description:** List community events
- **Response 200:** `EventRead[]`

#### `POST /api/v1/events`

- **Auth:** Admin
- **Description:** Create community event
- **Body:** `EventCreate`
- **Response 201:** `EventRead`

#### `POST /api/v1/events/{event_id}/rsvp`

- **Auth:** Required
- **Description:** RSVP to event
- **Response 201:** `EventRSVPRead`

---

## 11. Media (Minimal Implementation)

#### `GET /api/v1/media/galleries`

- **Auth:** Public
- **Description:** List photo galleries
- **Response 200:** `GalleryRead[]`

#### `GET /api/v1/media/galleries/{gallery_id}`

- **Auth:** Public
- **Description:** Get gallery with media items
- **Response 200:** `GalleryRead` with `MediaItemRead[]`

#### `POST /api/v1/media/galleries`

- **Auth:** Admin
- **Description:** Create gallery
- **Response 201:** `GalleryRead`

#### `POST /api/v1/media/galleries/{gallery_id}/upload`

- **Auth:** Admin
- **Description:** Upload media to gallery
- **Body:** Multipart form with file
- **Response 201:** `MediaItemRead`

---

## 12. Store (Minimal Implementation)

#### `GET /api/v1/store/products`

- **Auth:** Public
- **Description:** List products
- **Query Params:** `category`, `in_stock`
- **Response 200:** `ProductRead[]`

#### `GET /api/v1/store/products/{product_id}`

- **Auth:** Public
- **Description:** Get product details with variants
- **Response 200:** `ProductRead`

#### `POST /api/v1/store/cart`

- **Auth:** Required
- **Description:** Add item to cart
- **Body:** `{ "product_variant_id": "uuid", "quantity": 2 }`
- **Response 201:** `CartRead`

#### `GET /api/v1/store/cart`

- **Auth:** Required
- **Description:** Get member's cart
- **Response 200:** `CartRead` with items

#### `POST /api/v1/store/orders`

- **Auth:** Required
- **Description:** Create order from cart
- **Response 201:** `OrderRead` with payment intent

#### `GET /api/v1/store/orders`

- **Auth:** Required (owner) or Admin
- **Description:** List orders
- **Response 200:** `OrderRead[]`

---

## 13. Coach Management (Members Service)

### Coach Agreement (Coach-facing)

#### `GET /api/v1/coaches/agreement/current`

- **Auth:** Required (coach)
- **Description:** Get the current agreement content for signing (from database)
- **Response 200:** `AgreementContentResponse` with version, title, content (markdown), content_hash, effective_date

#### `GET /api/v1/coaches/agreement/status`

- **Auth:** Required (coach)
- **Description:** Check if the coach has signed the current agreement version
- **Response 200:** `CoachAgreementStatusResponse` with has_signed_current_version, requires_new_signature

#### `POST /api/v1/coaches/agreement/sign`

- **Auth:** Required (coach)
- **Description:** Sign the coach agreement
- **Body:** `{ "signature_type": "typed_name|drawn", "signature_data": "...", "agreement_version": "1.0", "agreement_content_hash": "sha256..." }`
- **Response 200:** `CoachAgreementResponse`

#### `GET /api/v1/coaches/agreement/history`

- **Auth:** Required (coach)
- **Description:** Get the coach's agreement signing history
- **Response 200:** `CoachAgreementHistoryItem[]`

### Admin Agreement Version Management

#### `GET /api/v1/admin/coaches/agreements`

- **Auth:** Admin
- **Description:** List all agreement versions with signature counts
- **Response 200:** `AgreementVersionListItem[]`

#### `POST /api/v1/admin/coaches/agreements`

- **Auth:** Admin
- **Description:** Create a new agreement version (auto-sets as current, notifies active coaches via email)
- **Body:** `{ "version": "2.0", "title": "...", "content": "markdown...", "effective_date": "2026-03-01" }`
- **Response 200:** `AgreementVersionDetail`

#### `GET /api/v1/admin/coaches/agreements/{version_id}`

- **Auth:** Admin
- **Description:** Get a specific agreement version with signature statistics
- **Response 200:** `AgreementVersionDetail` with signature_count and active_signature_count

---

## Summary

**Fully Documented Services:**

- Identity (placeholder)
- Members
- Sessions
- Attendance
- Announcements
- Admin Dashboard
- Academy ⭐ (33+ endpoints)
- Payments
- Transport
- Events (basic)
- Media (basic)
- Store (basic)
- Coach Management (agreements, admin)
- Volunteers
- Wallet (51 endpoints)

**Service Details:** For complete service information including models, database schema, and use cases, see [SERVICE_REGISTRY.md](../../docs/reference/SERVICE_REGISTRY.md)

---

## 13. Volunteers

The Volunteer Service manages volunteer roles, opportunities, scheduling, hours tracking, tier/recognition, and rewards.

### Member Endpoints (Auth Required)

#### Roles

| Method | Endpoint                        | Description                          |
| ------ | ------------------------------- | ------------------------------------ |
| `GET`  | `/api/v1/volunteers/roles`      | List active volunteer roles (Public) |
| `GET`  | `/api/v1/volunteers/roles/{id}` | Get role details                     |

#### Profile

| Method  | Endpoint                        | Description                      |
| ------- | ------------------------------- | -------------------------------- |
| `GET`   | `/api/v1/volunteers/profile/me` | Get my volunteer profile         |
| `POST`  | `/api/v1/volunteers/profile/me` | Register as volunteer            |
| `PATCH` | `/api/v1/volunteers/profile/me` | Update preferences (roles, days) |

#### Opportunities

| Method   | Endpoint                                      | Description                                                                              |
| -------- | --------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `GET`    | `/api/v1/volunteers/opportunities`            | List open opportunities (filterable by status, role, date, **session_id**, **event_id**) |
| `GET`    | `/api/v1/volunteers/opportunities/upcoming`   | Upcoming 14 days                                                                         |
| `GET`    | `/api/v1/volunteers/opportunities/{id}`       | Opportunity detail                                                                       |
| `POST`   | `/api/v1/volunteers/opportunities/{id}/claim` | Claim a slot (auto-approves for open_claim)                                              |
| `DELETE` | `/api/v1/volunteers/opportunities/{id}/claim` | Cancel my claim (tracks late cancellations)                                              |

`session_id` / `event_id` filters power the booking-time "Volunteer at this session" panel. See [VOLUNTEER_OPPORTUNITY_CONTEXT_DESIGN.md](../../docs/design/VOLUNTEER_OPPORTUNITY_CONTEXT_DESIGN.md).

#### Hours & Leaderboard

| Method | Endpoint                               | Description                                       |
| ------ | -------------------------------------- | ------------------------------------------------- |
| `GET`  | `/api/v1/volunteers/hours/me`          | My hours history                                  |
| `GET`  | `/api/v1/volunteers/hours/me/summary`  | Summary with tier, recognition, by-role breakdown |
| `GET`  | `/api/v1/volunteers/hours/leaderboard` | Top volunteers (all_time or this_month)           |

#### Rewards

| Method | Endpoint                                 | Description     |
| ------ | ---------------------------------------- | --------------- |
| `GET`  | `/api/v1/volunteers/rewards/me`          | My rewards      |
| `POST` | `/api/v1/volunteers/rewards/{id}/redeem` | Redeem a reward |

### Admin Endpoints (Admin Auth Required)

#### Roles CRUD

| Method   | Endpoint                              | Description     |
| -------- | ------------------------------------- | --------------- |
| `POST`   | `/api/v1/admin/volunteers/roles`      | Create role     |
| `PATCH`  | `/api/v1/admin/volunteers/roles/{id}` | Update role     |
| `DELETE` | `/api/v1/admin/volunteers/roles/{id}` | Deactivate role |

#### Profile Management

| Method  | Endpoint                                        | Description                                |
| ------- | ----------------------------------------------- | ------------------------------------------ |
| `GET`   | `/api/v1/admin/volunteers/profiles`             | List all profiles (filter by tier, active) |
| `GET`   | `/api/v1/admin/volunteers/profiles/{member_id}` | Get profile                                |
| `PATCH` | `/api/v1/admin/volunteers/profiles/{member_id}` | Update tier, admin notes, etc.             |

#### Opportunity Management

| Method   | Endpoint                                              | Description                                   |
| -------- | ----------------------------------------------------- | --------------------------------------------- |
| `POST`   | `/api/v1/admin/volunteers/opportunities`              | Create opportunity (as draft)                 |
| `POST`   | `/api/v1/admin/volunteers/opportunities/bulk`         | Bulk create opportunities                     |
| `PATCH`  | `/api/v1/admin/volunteers/opportunities/{id}`         | Update opportunity                            |
| `DELETE` | `/api/v1/admin/volunteers/opportunities/{id}`         | Cancel opportunity (cancels all active slots) |
| `POST`   | `/api/v1/admin/volunteers/opportunities/{id}/publish` | Publish draft → open                          |

#### Slot Management

| Method  | Endpoint                                            | Description                        |
| ------- | --------------------------------------------------- | ---------------------------------- |
| `GET`   | `/api/v1/admin/volunteers/opportunities/{id}/slots` | List slots for opportunity         |
| `PATCH` | `/api/v1/admin/volunteers/slots/{id}`               | Approve/reject slot                |
| `POST`  | `/api/v1/admin/volunteers/slots/{id}/checkin`       | Check-in volunteer                 |
| `POST`  | `/api/v1/admin/volunteers/slots/{id}/checkout`      | Check-out (auto-logs hours)        |
| `POST`  | `/api/v1/admin/volunteers/slots/{id}/no-show`       | Mark no-show (updates reliability) |
| `POST`  | `/api/v1/admin/volunteers/slots/bulk-complete`      | Bulk complete active slots         |

#### Hours & Rewards

| Method | Endpoint                                | Description            |
| ------ | --------------------------------------- | ---------------------- |
| `POST` | `/api/v1/admin/volunteers/hours/manual` | Add manual hours entry |
| `POST` | `/api/v1/admin/volunteers/rewards`      | Grant a reward         |
| `GET`  | `/api/v1/admin/volunteers/rewards/all`  | List all rewards       |

#### Dashboard & Reports

| Method | Endpoint                                      | Description                                                      |
| ------ | --------------------------------------------- | ---------------------------------------------------------------- |
| `GET`  | `/api/v1/admin/volunteers/dashboard`          | Dashboard summary (active, hours, unfilled, no-show rate, top 5) |
| `GET`  | `/api/v1/admin/volunteers/reliability-report` | Reliability report (sorted worst-first)                          |

#### Template Management (Recurring Volunteer Needs)

Two template surfaces, see [VOLUNTEER_OPPORTUNITY_CONTEXT_DESIGN.md](../../docs/design/VOLUNTEER_OPPORTUNITY_CONTEXT_DESIGN.md):

Session-template volunteer slots — fan out automatically when sessions_service generates a session from the parent template:

| Method   | Endpoint                                                                           | Description                                 |
| -------- | ---------------------------------------------------------------------------------- | ------------------------------------------- |
| `GET`    | `/api/v1/admin/volunteers/session-templates/{session_template_id}/slots`           | List volunteer needs for a session template |
| `POST`   | `/api/v1/admin/volunteers/session-templates/{session_template_id}/slots`           | Attach a new volunteer need                 |
| `PATCH`  | `/api/v1/admin/volunteers/session-templates/{session_template_id}/slots/{slot_id}` | Update a slot row                           |
| `DELETE` | `/api/v1/admin/volunteers/session-templates/{session_template_id}/slots/{slot_id}` | Remove a slot row                           |

Standalone volunteer-opportunity templates — recurring opportunities not tied to a session:

| Method   | Endpoint                                                                   | Description                                                 |
| -------- | -------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `GET`    | `/api/v1/admin/volunteers/opportunity-templates?active_only=`              | List standalone templates                                   |
| `POST`   | `/api/v1/admin/volunteers/opportunity-templates`                           | Create template                                             |
| `PATCH`  | `/api/v1/admin/volunteers/opportunity-templates/{template_id}`             | Update template                                             |
| `DELETE` | `/api/v1/admin/volunteers/opportunity-templates/{template_id}`             | Delete template                                             |
| `POST`   | `/api/v1/admin/volunteers/opportunity-templates/{template_id}/materialise` | Generate concrete opportunities through a date (idempotent) |

### Internal Endpoints (Service-to-Service Only)

| Method | Endpoint                                                  | Description                                                      |
| ------ | --------------------------------------------------------- | ---------------------------------------------------------------- |
| `POST` | `/internal/volunteer/ensure-profile`                      | Create a VolunteerProfile for a member if missing (idempotent)   |
| `POST` | `/internal/volunteer/log-hours`                           | Idempotently credit volunteer hours (source + ext_ref tuple)     |
| `GET`  | `/internal/volunteer/member-summary/{auth_id}?from=&to=`  | Aggregate hours for reporting                                    |
| `POST` | `/internal/volunteer/opportunities/cancel-for-context`    | Cascade-cancel opportunities when a session/event is cancelled   |
| `POST` | `/internal/volunteer/opportunities/from-session-template` | Materialise SessionTemplateVolunteerSlot rows into concrete opps |

---

## 14. Wallet ("Bubbles" Closed-Loop Credit System)

The Wallet Service manages the "Bubbles" closed-loop credit system. Members top up Bubbles via Paystack and spend them on sessions, events, academy fees, store purchases, and transport. Port 8013.

### Member Endpoints (Auth Required)

#### `GET /api/v1/wallet/me`

- **Auth:** Required
- **Description:** Get the current member's wallet
- **Response 200:** `WalletRead`

#### `POST /api/v1/wallet/create`

- **Auth:** Required
- **Description:** Create a wallet for the authenticated member
- **Response 201:** `WalletRead`

#### `POST /api/v1/wallet/topup`

- **Auth:** Required
- **Description:** Initiate a Bubbles topup via Paystack
- **Body:**

```json
{
  "bubbles_amount": 100,
  "payment_method": "paystack"
}
```

- `bubbles_amount` (integer, required): Amount of Bubbles to purchase (25–5000).
- `payment_method` (string, required): Currently only `"paystack"`.
- **Response 201:** `TopupRead` with payment URL

#### `GET /api/v1/wallet/topup/{topup_id}`

- **Auth:** Required
- **Description:** Get topup status
- **Response 200:** `TopupRead`

#### `GET /api/v1/wallet/topups`

- **Auth:** Required
- **Description:** List the current member's topups
- **Query Params:** `skip`, `limit`
- **Response 200:** `TopupRead[]`

#### `GET /api/v1/wallet/transactions`

- **Auth:** Required
- **Description:** List the current member's wallet transactions
- **Query Params:** `skip`, `limit`, `transaction_type`
- **Response 200:** `TransactionRead[]`

#### `GET /api/v1/wallet/transactions/{transaction_id}`

- **Auth:** Required
- **Description:** Get transaction detail
- **Response 200:** `TransactionRead`

#### `POST /api/v1/wallet/debit`

- **Auth:** Required
- **Description:** Debit Bubbles from a wallet
- **Body:**

```json
{
  "idempotency_key": "unique-key",
  "member_auth_id": "supabase-user-id",
  "amount": 500,
  "transaction_type": "session_fee",
  "description": "Club training session fee",
  "service_source": "sessions_service"
}
```

- **Response 200:** `TransactionRead`

#### `POST /api/v1/wallet/credit`

- **Auth:** Required
- **Description:** Credit Bubbles to a wallet
- **Body:** Same structure as debit
- **Response 200:** `TransactionRead`

#### `POST /api/v1/wallet/check-balance`

- **Auth:** Required
- **Description:** Check if a member has sufficient balance
- **Body:**

```json
{
  "member_auth_id": "supabase-user-id",
  "required_amount": 500
}
```

- **Response 200:** Balance check result

### Referral Endpoints (Auth Required)

#### `GET /api/v1/wallet/referral/code`

- **Auth:** Required
- **Description:** Get or create the current member's referral code. Returns share link and share text.
- **Response 200:** `ReferralCodeResponse` — `{ code, share_link, share_text, is_active, uses_count, successful_referrals, max_uses, expires_at, created_at }`

#### `GET /api/v1/wallet/referral/stats`

- **Auth:** Required
- **Description:** Get referral statistics for the current member
- **Response 200:** `ReferralStatsResponse` — `{ total_referrals_sent, registered, qualified, rewarded, pending, total_bubbles_earned, is_ambassador, referrals_to_ambassador, max_referrals, remaining_referrals }`

#### `GET /api/v1/wallet/referral/history`

- **Auth:** Required
- **Description:** Get paginated referral history for the current member
- **Query Params:** `skip`, `limit`
- **Response 200:** `ReferralHistoryItem[]`

#### `POST /api/v1/wallet/referral/apply`

- **Auth:** Required
- **Description:** Apply a referral code for the current member (one-time, during/after registration)
- **Body:** `{ "code": "SB-JOHN-A3K7" }`
- **Response 200:** `{ success, message }`

#### `GET /api/v1/wallet/referral/ambassador`

- **Auth:** Required
- **Description:** Get ambassador badge status (ambassador = 10+ successful referrals)
- **Response 200:** `AmbassadorStatusResponse` — `{ is_ambassador, successful_referrals, referrals_to_ambassador, ambassador_since, total_referral_bubbles_earned }`

#### `GET /api/v1/wallet/referral/leaderboard`

- **Auth:** Required
- **Description:** Public referral leaderboard (top 10, codes partially anonymized)
- **Response 200:** `ReferralLeaderboardResponse` — `{ entries: [{ rank, referral_code, successful_referrals, total_bubbles_earned, conversion_rate }], period }`

### Notification Preferences (Auth Required)

#### `GET /api/v1/wallet/notifications/preferences`

- **Auth:** Required
- **Description:** Get reward notification preferences (auto-creates defaults on first access)
- **Response 200:** `NotificationPreferenceResponse` — `{ notify_on_reward, notify_on_referral_qualified, notify_on_ambassador_milestone, notify_on_streak_milestone, notify_channel }`

#### `PATCH /api/v1/wallet/notifications/preferences`

- **Auth:** Required
- **Description:** Update reward notification preferences (partial update)
- **Body:** `NotificationPreferenceUpdateRequest` — all fields optional
- **Response 200:** `NotificationPreferenceResponse`

### Admin Endpoints (Admin Auth Required)

#### `GET /api/v1/admin/wallet/wallets`

- **Auth:** Admin
- **Description:** List all wallets
- **Query Params:** `skip`, `limit`, `status`
- **Response 200:** `WalletRead[]`

#### `GET /api/v1/admin/wallet/wallets/{wallet_id}`

- **Auth:** Admin
- **Description:** Get wallet details
- **Response 200:** `WalletRead`

#### `POST /api/v1/admin/wallet/wallets/{wallet_id}/freeze`

- **Auth:** Admin
- **Description:** Freeze a member's wallet
- **Body:** `{ "reason": "Suspicious activity" }`
- **Response 200:** Updated `WalletRead`

#### `POST /api/v1/admin/wallet/wallets/{wallet_id}/unfreeze`

- **Auth:** Admin
- **Description:** Unfreeze a member's wallet
- **Body:** `{ "reason": "Investigation cleared" }`
- **Response 200:** Updated `WalletRead`

#### `POST /api/v1/admin/wallet/wallets/{wallet_id}/adjust`

- **Auth:** Admin
- **Description:** Manually adjust wallet balance (credit or debit)
- **Body:** `{ "amount": 100, "reason": "Compensation for service disruption" }`
- **Response 200:** Updated `WalletRead`

#### `POST /api/v1/admin/wallet/grants`

- **Auth:** Admin
- **Description:** Issue promotional Bubbles to a member
- **Body:**

```json
{
  "member_auth_id": "supabase-user-id",
  "bubbles_amount": 50,
  "grant_type": "welcome_bonus",
  "reason": "New member welcome"
}
```

- **Response 201:** `GrantRead`

#### `GET /api/v1/admin/wallet/grants`

- **Auth:** Admin
- **Description:** List promotional grants
- **Query Params:** `skip`, `limit`, `grant_type`
- **Response 200:** `GrantRead[]`

#### `GET /api/v1/admin/wallet/stats`

- **Auth:** Admin
- **Description:** System-wide wallet statistics
- **Response 200:** Aggregate stats (total Bubbles in circulation, active wallets, etc.)

#### `GET /api/v1/admin/wallet/transactions`

- **Auth:** Admin
- **Description:** List all wallet transactions across the platform
- **Query Params:** `skip`, `limit`, `transaction_type`
- **Response 200:** `TransactionRead[]`

#### `GET /api/v1/admin/wallet/audit-log`

- **Auth:** Admin
- **Description:** View wallet audit log (admin actions)
- **Query Params:** `skip`, `limit`
- **Response 200:** `AuditLogRead[]`

#### `GET /api/v1/admin/wallet/referrals/`

- **Auth:** Admin
- **Description:** List all referral records with optional status filter
- **Query Params:** `status` (pending|registered|qualified|rewarded|expired|cancelled), `skip`, `limit`
- **Response 200:** `AdminReferralListResponse` — `{ items, total, skip, limit }`

#### `GET /api/v1/admin/wallet/referrals/stats`

- **Auth:** Admin
- **Description:** Program-wide referral statistics
- **Response 200:** `AdminReferralProgramStats` — `{ total_codes_generated, total_registrations, total_qualified, total_rewarded, conversion_rate, total_bubbles_distributed }`

#### `PATCH /api/v1/admin/wallet/referrals/{referral_id}`

- **Auth:** Admin
- **Description:** Cancel or manually qualify a referral record
- **Query Params:** `action` (cancel|qualify)
- **Response 200:** `{ success, message }`

#### `GET /api/v1/admin/wallet/referrals/leaderboard`

- **Auth:** Admin
- **Description:** Top referrers leaderboard sorted by successful referrals
- **Query Params:** `period` (all_time|this_month|this_year), `limit` (default 20)
- **Response 200:** `ReferralLeaderboardResponse` — `{ entries: [{ rank, member_auth_id, referral_code, successful_referrals, total_bubbles_earned, conversion_rate }], period }`

### Admin Rewards Endpoints (Admin Auth Required)

#### `GET /api/v1/admin/wallet/rewards/rules`

- **Auth:** Admin
- **Description:** List all reward rules with optional filters
- **Query Params:** `category` (acquisition|retention|community|spending|academy), `is_active` (bool), `skip`, `limit`
- **Response 200:** `RewardRuleListResponse` — `{ items, total }`

#### `GET /api/v1/admin/wallet/rewards/rules/{rule_id}`

- **Auth:** Admin
- **Description:** Get reward rule details with usage stats
- **Response 200:** `RewardRuleDetailResponse` — rule fields + `{ total_grants, total_bubbles_distributed }`

#### `PATCH /api/v1/admin/wallet/rewards/rules/{rule_id}`

- **Auth:** Admin
- **Description:** Update a reward rule (amount, caps, active status, display name, description)
- **Body:** `RewardRuleUpdateRequest` — partial update, all fields optional
- **Response 200:** `RewardRuleResponse`

#### `GET /api/v1/admin/wallet/rewards/events`

- **Auth:** Admin
- **Description:** List ingested events with optional filters
- **Query Params:** `event_type`, `processed` (bool), `skip`, `limit`
- **Response 200:** `RewardEventListResponse` — `{ items, total }`

#### `GET /api/v1/admin/wallet/rewards/events/failed`

- **Auth:** Admin
- **Description:** List events that failed processing (non-null processing_error)
- **Query Params:** `skip`, `limit`
- **Response 200:** `RewardEventListResponse`

#### `GET /api/v1/admin/wallet/rewards/stats`

- **Auth:** Admin
- **Description:** Rewards engine dashboard stats
- **Response 200:** `RewardStatsResponse` — `{ total_rules_active, total_events_processed, total_events_pending, total_bubbles_distributed, events_by_type, top_rules_by_usage }`

#### `POST /api/v1/admin/wallet/rewards/events/submit`

- **Auth:** Admin
- **Description:** Submit a reward event directly (for ad-hoc community rewards that don't have automated hooks yet, e.g. content creation, social shares, event volunteering). Automatically injects `admin_confirmed: true` and `submitted_by` into event data.
- **Body:** `AdminEventSubmitRequest`

```json
{
  "event_type": "content.blog_published",
  "member_auth_id": "supabase-user-id",
  "event_data": { "content_url": "https://example.com/blog/..." },
  "description": "Published blog post about community event"
}
```

- **Response 200:** `EventIngestResponse` — `{ event_id, accepted, rewards_granted, rewards: [{ rule_name, bubbles }] }`
- **Note:** Returns 400 if no active rules exist for the given `event_type`.

### Anti-Abuse Alerts (Admin Auth Required)

#### `GET /api/v1/admin/wallet/rewards/alerts`

- **Auth:** Admin
- **Description:** List anti-abuse alerts with optional filters
- **Query Params:** `status` (open|acknowledged|resolved|dismissed), `severity` (low|medium|high|critical), `skip`, `limit`
- **Response 200:** `RewardAlertListResponse` — `{ items, total }`

#### `GET /api/v1/admin/wallet/rewards/alerts/summary`

- **Auth:** Admin
- **Description:** Alert counts by status and severity
- **Response 200:** `RewardAlertSummaryResponse` — `{ total_open, total_acknowledged, total_resolved, total_dismissed, by_severity }`

#### `GET /api/v1/admin/wallet/rewards/alerts/{alert_id}`

- **Auth:** Admin
- **Description:** Get a single alert detail
- **Response 200:** `RewardAlertResponse`

#### `PATCH /api/v1/admin/wallet/rewards/alerts/{alert_id}`

- **Auth:** Admin
- **Description:** Update alert status (acknowledge, resolve, dismiss)
- **Body:** `RewardAlertUpdateRequest` — `{ status, resolution_notes? }`
- **Response 200:** `RewardAlertResponse`

### Rewards Analytics (Admin Auth Required)

#### `GET /api/v1/admin/wallet/rewards/analytics`

- **Auth:** Admin
- **Description:** Detailed rewards analytics with category breakdown
- **Query Params:** `period_start`, `period_end` (defaults to last 30 days)
- **Response 200:** `RewardAnalyticsResponse` — `{ period_start, period_end, total_events, total_rewards_granted, total_bubbles_distributed, unique_members_rewarded, by_category, avg_bubbles_per_member, top_event_types }`

### Internal Endpoints (Service-to-Service Only)

These endpoints are **not proxied through the gateway**. They are called directly by other backend services using `require_service_role` authentication.

#### `POST /internal/wallet/debit`

- **Auth:** Service Role
- **Description:** Debit Bubbles from a wallet (called by sessions, academy, store, etc.)
- **Body:** Same as member debit endpoint

#### `POST /internal/wallet/credit`

- **Auth:** Service Role
- **Description:** Credit Bubbles to a wallet (refunds, rewards)
- **Body:** Same as member credit endpoint

#### `GET /internal/wallet/balance/{auth_id}`

- **Auth:** Service Role
- **Description:** Get a member's wallet balance
- **Response 200:** Balance info

#### `POST /internal/wallet/check-balance`

- **Auth:** Service Role
- **Description:** Check if a member has sufficient Bubbles for a transaction
- **Body:** `{ "member_auth_id": "...", "required_amount": 500 }`
- **Response 200:** Balance check result

#### `POST /internal/wallet/confirm-topup`

- **Auth:** Service Role
- **Description:** Confirm a topup after successful Paystack payment (called by payments service)

#### `POST /internal/wallet/create`

- **Auth:** Service Role
- **Description:** Create a wallet for a new member (called by members service during registration). Optionally applies a referral code.
- **Body:** `{ "member_id": "uuid", "member_auth_id": "supabase-id", "referral_code": "SB-JOHN-A3K7" }` — `referral_code` is optional

#### `POST /internal/wallet/events`

- **Auth:** None (internal network only)
- **Description:** Submit an event for rewards engine processing. Deduplicates by `event_id` and `idempotency_key`.
- **Body:** `EventIngestRequest` — `{ event_id, event_type, member_auth_id, member_id?, service_source, occurred_at, event_data, idempotency_key }`
- **Response 200:** `EventIngestResponse` — `{ event_id, accepted, rewards_granted, rewards: [{ rule_name, bubbles }] }`

---

## 15. Flywheel Metrics (Reporting Service)

Cross-service ecosystem health: cohort fill operational state, community→club / club→academy funnel conversion, and wallet cross-service spend. See [docs/reference/FLYWHEEL_METRICS_DESIGN.md](../../docs/reference/FLYWHEEL_METRICS_DESIGN.md) for the underlying model.

Snapshots are computed by ARQ tasks on cron (daily for cohort fill, weekly for funnel + wallet) and persisted to `cohort_fill_snapshots`, `funnel_conversion_snapshots`, `wallet_ecosystem_snapshots` in the reporting_service database.

### Admin Endpoints (Admin Auth Required)

#### `GET /api/v1/admin/reports/flywheel/overview`

- **Auth:** Admin
- **Description:** Single-call dashboard overview combining the latest snapshot for each metric category. Returns `is_stale=true` if no snapshot in the last 36 hours.
- **Response 200:** `FlywheelOverviewResponse` — `{ cohort_fill_avg, open_cohorts_count, open_cohorts_at_risk_count, community_to_club_rate, community_to_club_period, club_to_academy_rate, club_to_academy_period, wallet_cross_service_rate, wallet_active_users, last_refreshed_at, is_stale }`. All rate fields are `0.0–1.0` floats and may be `null` if no snapshot exists yet.

#### `GET /api/v1/admin/reports/flywheel/cohorts`

- **Auth:** Admin
- **Query:** `status` (default `open,active`, comma-separated), `sort` (one of `fill_rate_asc` (default), `fill_rate_desc`, `starts_at_asc`, `starts_at_desc`)
- **Description:** Latest fill snapshot per cohort, default sorted by lowest fill rate first (operational use case: act on cold cohorts).
- **Response 200:** `List[CohortFillSnapshotResponse]` — fields include `cohort_id`, `cohort_name`, `program_name`, `capacity`, `active_enrollments`, `pending_approvals`, `waitlist_count`, `fill_rate`, `starts_at`, `ends_at`, `cohort_status`, `days_until_start`, `snapshot_taken_at`.

#### `GET /api/v1/admin/reports/flywheel/funnel`

- **Auth:** Admin
- **Query:** `funnel_stage` (optional, one of `community_to_club`, `club_to_academy`, `community_to_academy`), `cohort_period` (optional, e.g. `2026-Q1`), `limit` (default 20, max 100)
- **Description:** Funnel conversion snapshots ordered by `snapshot_taken_at` desc, filterable by stage and period.
- **Response 200:** `List[FunnelConversionSnapshotResponse]` — `{ funnel_stage, cohort_period, period_start, period_end, observation_window_days, source_count, converted_count, conversion_rate, breakdown_by_source, snapshot_taken_at }`.

#### `GET /api/v1/admin/reports/flywheel/wallet`

- **Auth:** Admin
- **Description:** Most recent wallet ecosystem snapshot. Returns `null` if no snapshot exists yet.
- **Response 200:** `Optional[WalletEcosystemSnapshotResponse]` — `{ period_start, period_end, period_days, active_wallet_users, single_service_users, cross_service_users, cross_service_rate, total_bubbles_spent, total_topup_bubbles, spend_distribution, snapshot_taken_at }`.

#### `POST /api/v1/admin/reports/flywheel/refresh`

- **Auth:** Admin
- **Description:** Enqueue an ARQ job (`task_refresh_all_flywheel`) on the `arq:reporting` queue to recompute all three snapshot categories. Returns immediately; poll `/overview` to see the updated `last_refreshed_at`.
- **Response 200:** `RefreshFlywheelResponse` — `{ job_enqueued: true, message: "..." }`

### Internal Endpoints (Service-to-Service Only)

The flywheel computation tasks call these prerequisite endpoints on academy, members, and wallet services.

#### `GET /internal/academy/cohorts`

- **Auth:** Internal service header
- **Query:** `status` (comma-separated, e.g. `open,active`)
- **Description:** List cohorts in the given statuses. Used by the cohort-fill snapshot task.
- **Response 200:** `{ cohorts: [{ id, name, program_name, capacity, status, start_date, end_date }] }`

#### `GET /internal/academy/cohorts/{cohort_id}/enrollment-counts`

- **Auth:** Internal service header
- **Description:** Enrollment counts grouped by status for a cohort.
- **Response 200:** `{ active, pending_approval, waitlist, dropped, graduated }`

#### `GET /internal/members/joined-tier`

- **Auth:** Internal service header
- **Query:** `tier` (one of `community`, `club`, `academy`), `from` (ISO date), `to` (ISO date)
- **Description:** Members who entered the given tier between the dates. Powers the funnel-conversion source-count.
- **Response 200:** `JoinedTierResponse` — `{ members: [{ id, source_joined_at, acquisition_source }] }`. `acquisition_source` is null for legacy registrations that pre-date the typed enum.

#### `GET /internal/members/{member_id}/tier-history`

- **Auth:** Internal service header
- **Description:** Chronological tier-entry history for a single member. Used to determine whether/when a member crossed to the target tier within the observation window.
- **Response 200:** `TierHistoryResponse` — `{ entries: [{ tier, entered_at, exited_at }] }`

#### `GET /internal/wallet/ecosystem-stats`

- **Auth:** Internal service header
- **Query:** `from` (ISO date), `to` (ISO date)
- **Description:** Aggregated wallet usage stats over the period. Cross-service user = ≥2 distinct `service_source` values in DEBIT transactions.
- **Response 200:** `{ active_wallet_users, single_service_users, cross_service_users, total_bubbles_spent, total_topup_bubbles, spend_distribution: { sessions, academy, store, ... } }`

---

## 16. Chat (Real-time Messaging)

In-app messaging across cohorts, pods, events, trips, DMs. Phase 1 backend ships member-facing CRUD, admin moderation, and internal s2s reconciliation. Real-time transport (Supabase Realtime), push notifications via communications_service, and frontend land in subsequent slices. See [docs/design/CHAT_SERVICE_DESIGN.md](../../docs/design/CHAT_SERVICE_DESIGN.md).

**Auth model:** member endpoints use Supabase JWT (member-facing); admin endpoints use admin JWT (with `safeguarding_admin` role gate for hard-delete in minor channels); internal endpoints use service-role JWT.

### Member Endpoints (Auth Required)

#### `GET /api/v1/chat/channels`

- **Description:** All my active (non-archived) channels with last-message preview, my role, mute state, and unread count.
- **Response 200:** `List[ChannelSummary]`.

#### `GET /api/v1/chat/channels/{channel_id}`

- **Description:** Channel detail. 403 if not a member.
- **Response 200:** `ChannelDetail`.

#### `GET /api/v1/chat/channels/{channel_id}/messages`

- **Query:** `before_id` (cursor — pass previous page's `next_before_id`), `limit` (default 50, max 100).
- **Description:** Newest-first cursor page. 403 if not a member.
- **Response 200:** `MessageListPage` — `{ items, next_before_id, has_more }`.

#### `POST /api/v1/chat/channels/{channel_id}/messages`

- **Body:** `{ body, attachments?, reply_to_id?, client_message_id (UUID) }`
- **Description:** Send a message. `client_message_id` is the idempotency key — server uses it as the row PK; retries with the same id converge on one row. Body capped at 4,000 chars. Pre-persist text moderation runs via OpenAI Moderation API; flagged messages are still delivered but tagged for the safeguarding queue (design rule: never auto-delete).
- **Response 201:** `MessageOut`.

#### `PATCH /api/v1/chat/messages/{message_id}`

- **Body:** `{ body }`
- **Description:** Edit own message. 403 for others' messages; 400 if soft-deleted.
- **Response 200:** `MessageOut`.

#### `DELETE /api/v1/chat/messages/{message_id}`

- **Description:** Soft-delete own message — body becomes `[deleted]`, row stays for audit. Hard-delete is admin-only.
- **Response 200:** `MessageOut`.

#### `POST /api/v1/chat/messages/{message_id}/reactions`

- **Body:** `{ emoji }`
- **Description:** Add reaction. Restricted set: 👍 ❤️ 😂 😮 😢 🎉 ✅. Idempotent.
- **Response 201:** `MessageOut` (with updated reactions summary).

#### `DELETE /api/v1/chat/messages/{message_id}/reactions/{emoji}`

- **Description:** Remove own reaction. Idempotent.
- **Response 200:** `MessageOut`.

#### `POST /api/v1/chat/channels/{channel_id}/read`

- **Body:** `{ message_id }`
- **Description:** Mark-read up to message. Refuses to move pointer backward.
- **Response 204.**

#### `POST /api/v1/chat/channels/{channel_id}/mute`

- **Body:** `{ muted_until: ISO datetime | null }`
- **Description:** Mute notifications for this channel until the given time (or clear with null).
- **Response 204.**

#### `POST /api/v1/chat/channels/{channel_id}/leave`

- **Description:** Soft-leave a manually-joined channel. Refuses for derived memberships (those follow the parent — leave the cohort/RSVP/pod instead).
- **Response 204.**

#### `POST /api/v1/chat/attachments`

- **Content-Type:** `multipart/form-data`
- **Form fields:** `file` (required, image/JPEG | PNG | WebP, ≤10 MB), `mime` (optional override).
- **Description:** Upload an image attachment. Bytes are scanned by AWS Rekognition **before** they touch our storage. If the scan hits a category we never deliver under any circumstance (currently `SAFEGUARDING` — child-safety), the upload is rejected and the bytes are discarded. Otherwise the image lands in the `chat-attachments` bucket and a descriptor comes back. Attach the descriptor to a subsequent `POST /channels/{id}/messages` `attachments` array. Per-channel policy on non-safeguarding flags is applied at message-send time (minor channels reject; adult channels deliver-with-flag for safeguarding queue review).
- **Response 201:** `AttachmentUploadResponse` — `{ descriptor: { type, storage_key, mime, size, width, height, public_url, moderation }, rejected, rejection_reason }`. On rejection, `descriptor=null` and `rejected=true`.

#### `POST /api/v1/chat/messages/{message_id}/reports`

- **Body:** `{ reason: safeguarding|harassment|spam|other, note? }`
- **Description:** File a moderation report. Re-reporting an open report by the same reporter returns the existing one rather than duplicating the queue entry.
- **Response 201:** `ReportOut`.

### Admin Endpoints (Admin Auth Required)

#### `GET /api/v1/admin/chat/channels/{channel_id}`

- **Description:** Channel detail without requiring membership.
- **Response 200:** `ChannelDetail`.

#### `POST /api/v1/admin/chat/channels/{channel_id}/archive`

- **Description:** Soft-archive (read-only). Idempotent.
- **Response 200:** `ChannelDetail`.

#### `PATCH /api/v1/admin/chat/channels/{channel_id}/members/{member_id}`

- **Body:** `{ role: observer|member|moderator|admin }`
- **Response 200:** `ChannelMemberOut`.

#### `DELETE /api/v1/admin/chat/channels/{channel_id}/members/{member_id}`

- **Description:** Soft-remove. Prefer the internal `memberships/reconcile` endpoint when removal is driven by an upstream parent change.
- **Response 204.**

#### `DELETE /api/v1/admin/chat/messages/{message_id}`

- **Body:** `{ note }` (required — recorded in audit)
- **Description:** Hard-delete. **Inline gate:** if the channel's `safeguarding_flags.has_minors=true`, the caller must additionally have the `safeguarding_admin` role (per design §6.1 rule 5).
- **Response 204.**

#### `GET /api/v1/admin/chat/reports`

- **Query:** `status?`, `reason?`, `assigned_to?`, `skip` (default 0), `limit` (default 50, max 200).
- **Description:** Moderator queue. FIFO by `created_at`. Each row attaches the reported message's channel/sender/preview.
- **Response 200:** `List[ReportListItem]`.

#### `PATCH /api/v1/admin/chat/reports/{report_id}`

- **Body:** `{ status?, assigned_to?, resolution_note? }`
- **Description:** Resolve / dismiss / assign. `resolved_at` stamped on transition to `resolved` or `dismissed`.
- **Response 200:** `ReportOut`.

#### `GET /api/v1/admin/chat/audit`

- **Query:** `channel_id?`, `actor_id?`, `subject_member_id?`, `before_id?` (cursor), `limit` (default 100, max 500).
- **Description:** Cursor-paginated audit log slice (newest-first).
- **Response 200:** `AuditLogPage`.

#### `GET /api/v1/admin/chat/safeguarding/health`

- **Auth:** Safeguarding-admin role required.
- **Description:** Trivial endpoint that returns 200 only for safeguarding admins. Used by the admin UI to decide whether to render safeguarding panels.
- **Response 200:** `{ safeguarding_admin: true, user_id }`.

### Internal Endpoints (Service-to-Service Only)

Not proxied by the gateway — called directly by upstream services with a service-role JWT.

#### `POST /internal/chat/channels/ensure`

- **Body:** `{ type, parent_entity_type, parent_entity_id?, name, retention_policy, description?, created_by?, safeguarding_flags? }`
- **Description:** Idempotent create-or-fetch keyed on `(type, parent_entity_type, parent_entity_id)`. Returns the existing row on subsequent calls. `created_by` is added as channel admin on first create.
- **Response 200:** `{ channel_id, created }`.

#### `POST /internal/chat/memberships/reconcile`

- **Body:** `{ channel_id? | (parent_entity_type + parent_entity_id), member_id, action: add|remove, role?, derived_from?, derivation_ref? }`
- **Description:** Add or soft-remove a member. Idempotent: re-adding an active member is a no-op (with role/derivation upgrade), re-removing a left member is a no-op.
- **Response 200:** `ReconcileMembershipResponse`.

### Upstream callers (currently wired)

- **`academy_service`** — calls `channels/ensure` on cohort create and `memberships/reconcile` on admin enroll / dropout approve / dropout reverse.
- **`events_service`** — calls `channels/ensure` on event create and `memberships/reconcile` on RSVP create/update (`going` → add, anything else → remove).
- **`transport_service`** — calls `channels/ensure` on RideBooking create (parent = `session_ride_config_id`) and `memberships/reconcile` to add the booker. When a member moves between configs on the same session, the old config is reconciled to `remove` before the new one is reconciled to `add`. Admin bulk-delete (`/transport/admin/members/{member_id}`) does NOT yet notify chat — known gap.

- **`members_service`** — calls `channels/ensure` on pod create, `memberships/reconcile` on pod assignment add/remove (admin add, member self-join, lead transfer, member leave, dissolve). See pod endpoints in §17 below. (Pods moved from `sessions_service` to `members_service` in May 2026 — see [docs/club/POD_OPERATIONS.md](../../docs/club/POD_OPERATIONS.md).)

### Notifications (chat → communications_service)

On every successful `POST /api/v1/chat/channels/{id}/messages`, chat fans out a notification to every active channel member except the sender, skipping anyone whose `muted_until` is in the future. The payload uses `type=chat_message`, `category=chat`, and `action_url=/account/chat/{channel_id}`. Delivery is best-effort — communications_service downtime never blocks the send.

---

## 17. Pods (Members Service)

A pod is a 2–5 member persistent training sub-group inside a Club, with one **Pod Lead** (required), optional **Assistant Pod Lead**, and a 3-month review cycle. Pods are peer-led — they have no coaches (coaches only exist in the Academy layer). Each pod has an optional public `handle` (Dolphins, Orcas, …) and a default session schedule (day, time, duration, pool) that inherits from the parent Club. Pods get a chat channel automatically (`parent_entity_type=pod`). See [docs/club/POD_OPERATIONS.md](../../docs/club/POD_OPERATIONS.md).

> **Note:** Pods moved from `sessions_service` to `members_service` in May 2026. Endpoint paths changed from `/sessions/pods/*` to `/members/pods/*`. The earlier [POD_MODEL_DESIGN.md](../../docs/design/POD_MODEL_DESIGN.md) is superseded.

### Member Endpoints (Auth Required)

#### `GET /api/v1/members/pods/me`

- **Description:** My current pod, or `null` if I'm not in one.
- **Response 200:** `PodSummary | null`.

#### `GET /api/v1/members/pods/public`

- **Query:** `club_id?` (filter to one club's pods)
- **Description:** Public-directory listing — public+active pods only. Used by dashboard and registration picker.
- **Response 200:** `PodSummary[]`.

#### `POST /api/v1/members/pods/{pod_id}/join`

- **Description:** Self-join a public pod with capacity. 403 if pod is private; 400 if not active; 409 if pod full or member already in another pod.
- **Response 201:** `PodMemberOut`.

#### `POST /api/v1/members/pods/me/leave`

- **Description:** Leave my current pod (no-op if not in one). Triggers chat-channel reconcile remove.
- **Response 204.**

### Admin Endpoints (Admin Auth Required)

#### `POST /api/v1/admin/members/pods`

- **Body:** `PodCreateRequest` — `{ club_id, name?, handle?, description?, pod_lead_id, assistant_pod_lead_id?, min_size?, max_size?, default_session_day?, default_session_time?, default_session_duration_minutes?, default_pool_id?, visibility? }`
- **Description:** Create a pod. Auto-names `{club.slug}-pod-{N}` if name omitted. Schedule fields inherit from the parent Club when omitted. Creates the chat channel and promotes the Pod Lead to channel admin. 409 if `handle` is already taken in this club.
- **Response 201:** `PodSummary`.

#### `GET /api/v1/admin/members/pods/review-queue`

- **Description:** Pods with `review_due_at <= now()` — admin/Pod Lead decides continue / rebalance / dissolve.
- **Response 200:** `PodSummary[]`.

#### `GET /api/v1/admin/members/pods/{pod_id}` / `PATCH .../{pod_id}`

- **Description:** Inspect / partial update (name, handle, description, leads, sizes, schedule, visibility).
- **Response 200:** `PodDetail` / `PodSummary`.

#### `POST /api/v1/admin/members/pods/{pod_id}/dissolve`

- **Description:** Mark inactive, soft-leave every active member, fire chat-channel reconcile remove for each. Chat channel archive is NOT automatic — admin archives via chat admin API once the final messages settle.
- **Response 200:** `PodSummary`.

#### `POST /api/v1/admin/members/pods/{pod_id}/extend`

- **Description:** Bump `cycle_started_at` to now and reset `review_due_at` to +90 days. Admin/Pod Lead chose to continue this pod for another cycle.
- **Response 200:** `PodSummary`.

#### `POST /api/v1/admin/members/pods/{pod_id}/members`

- **Body:** `{ member_id }`
- **Description:** Admin manually add a member. Refuses if pod full or member already in another active pod.
- **Response 201:** `PodMemberOut`.

#### `DELETE /api/v1/admin/members/pods/{pod_id}/members/{member_id}`

- **Description:** Admin remove a member (soft-leave). 404 if member not in this pod.
- **Response 204.**

#### `POST /api/v1/admin/members/pods/{pod_id}/transfers`

- **Query:** `member_id` (the member being moved — kept in query so the body stays focused on the move target).
- **Body:** `{ target_pod_id }`
- **Description:** Pod Lead / admin moves a member from this pod to another. Capacity check on target before the source leave commits. Records `assigned_by=lead_transfer` on the new assignment.
- **Response 204.**

### Internal Endpoints (Service-to-Service Only)

These power the Sessions ↔ Pods read-time integration. `sessions_service` calls them when scheduling a Club session that's scoped to a specific pod (needs the pod's default schedule and active member roster). Service-role JWT only — never exposed via the gateway.

Use the helpers in `libs/common/service_client.py`:

```python
from libs.common.service_client import get_pod_by_id, list_pods

pod = await get_pod_by_id(pod_id, calling_service="sessions")
pods = await list_pods(calling_service="sessions", club_id=club_id)
```

#### `GET /internal/members/pods/{pod_id}`

- **Auth:** Internal service header
- **Description:** Single pod lookup. Returns the schedule fields and the list of active member ids — used by sessions_service when creating a Club session for the pod (so it knows when, where, and who to schedule).
- **Response 200:** `PodInternalDetail` — `{ id, club_id, name, slug, handle, pod_lead_id, assistant_pod_lead_id, status, visibility, min_size, max_size, active_member_count, default_session_day, default_session_time, default_session_duration_minutes, default_pool_id, active_member_ids: [...] }`
- **Response 404:** Pod not found.

#### `GET /internal/members/pods`

- **Auth:** Internal service header
- **Query:** `club_id?` (filter to one club), `status?` (`active`|`inactive`|`all`, defaults to `active`).
- **Description:** Batch listing — used by sessions_service for "create this Saturday's sessions for every active pod in club X". Omits the active-member-ids list (use the per-pod GET when you need it).
- **Response 200:** `PodInternalSummary[]`.

---

## Audit Fixes & New Endpoints (May 2026)

The endpoints below were added to close gaps surfaced by the May 2026 payment-paths audit and to ship two member features (1/3 deposit floor + member-initiated mid-cohort custom-amount payment). They're listed here as a coherent set — see commit history for the full design notes.

### Academy — Withdrawal Flow

#### `POST /api/v1/academy/my-enrollments/{enrollment_id}/withdraw`

- **Auth:** Required (member must own the enrollment)
- **Description:** Voluntary withdrawal from an active cohort. Refund policy: 90% before cohort start, 50% of unused prorated portion in the mid-entry window (week 1 → `cohort.mid_entry_cutoff_week`), 0 after the cutoff. Remaining unpaid installments are always waived. Multi-cohort safe: `academy_paid_until` is recomputed from the member's remaining ENROLLED cohorts.
- **Request Body** – `WithdrawEnrollmentRequest`

```json
{ "reason": "Optional, max 500 chars" }
```

- **Response 200** – `WithdrawEnrollmentResponse`

```json
{
  "enrollment_id": "uuid",
  "status": "DROPPED",
  "window": "before_start | mid_entry_window | after_cutoff",
  "refund_kobo": 6250000,
  "refund_percent": 0.9,
  "paid_kobo": 6250000,
  "waived_installment_count": 1,
  "payment_references": ["PAY-XXXXX"],
  "refund_note": "Human-readable explanation for the member"
}
```

- **Side effects:** Records refund obligation on each related payment's `metadata.refund_owed`. Sends a withdrawal-confirmation email to the member and an `admin_refund_owed` email to `settings.ADMIN_EMAIL` when `refund_kobo > 0`. Disbursement is manual (typically direct bank transfer) — see `/payments/admin/refunds-owed`.

#### `POST /api/v1/academy/admin/enrollments/{enrollment_id}/mark-paid`

- **Auth:** Service role
- **New optional field on `EnrollmentMarkPaidRequest`:** `amount_kobo` — when set and larger than the target installment's amount, the payment is applied across multiple installments via `apply_member_payment_across_installments`. Partial leftovers reduce the next installment's stipulated amount.

### Members — Tier Lifecycle

#### `POST /admin/members/by-auth/{auth_id}/club/extend`

- **Auth:** Service role / admin
- **Description:** Extend club membership without eligibility checks. Intended for service-to-service grants such as the free 1-month post-academy club bridge (PRICING_STRATEGY.md). Skips readiness/requested-tier gates that `/club/activate` enforces. `club_paid_until` becomes `max(current, anchor) + months`, calendar-correct via `relativedelta`.
- **Request Body** – `ExtendClubRequest`

```json
{
  "months": 1,
  "from_date": "2026-07-11T00:00:00Z",
  "reason": "Free post-academy club bridge (cohort X)"
}
```

- **Response 200:** `MemberResponse`.

#### `POST /admin/members/by-auth/{auth_id}/academy/expire`

- **Auth:** Admin
- **Description:** Set `academy_paid_until` to `NOW`, effectively expiring academy access. Used by academy_service after a withdrawal when the member has no remaining ENROLLED cohorts. Subsequent reads strip "academy" from `active_tiers` via `normalize_member_tiers`.
- **Response 200:** `MemberResponse`.

### Payments — Refund Queue

#### `POST /internal/payments/{reference}/annotate-refund`

- **Auth:** Service role
- **Description:** Write a refund obligation to a payment's `payment_metadata.refund_owed` list. Idempotent: re-calls for the same `enrollment_id` overwrite the prior entry rather than appending.
- **Request Body** – `AnnotateRefundRequest`

```json
{
  "refund_kobo": 6250000,
  "enrollment_id": "uuid",
  "window": "before_start",
  "reason": "Optional"
}
```

#### `GET /api/v1/payments/admin/refunds-owed`

- **Auth:** Admin
- **Description:** List outstanding refund obligations across all paid payments (`disbursed_at IS NULL`). Sorted oldest-first for FIFO disbursement.
- **Response 200** – `RefundQueueResponse`

```json
{
  "total_owed_kobo": 6250000,
  "total_owed_naira": 62500,
  "item_count": 1,
  "items": [
    {
      "payment_reference": "PAY-XXXXX",
      "payment_amount": 62500,
      "payer_email": "member@example.com",
      "member_auth_id": "supabase-uuid",
      "refund_kobo": 6250000,
      "refund_naira": 62500,
      "enrollment_id": "uuid",
      "window": "before_start",
      "reason": "Optional member reason",
      "annotated_at": "2026-05-14T02:22:36+00:00",
      "disbursed_at": null
    }
  ]
}
```

#### `POST /api/v1/payments/admin/refunds-owed/{reference}/mark-disbursed`

- **Auth:** Admin
- **Description:** Mark one refund obligation as disbursed. Identified by `(reference, enrollment_id)` since a payment may have multiple obligations across enrollments. Idempotent.
- **Request Body** – `MarkRefundDisbursedRequest`

```json
{ "enrollment_id": "uuid", "note": "UBA transfer ref ABC123, sent 2026-05-15" }
```

- **Response 200:** `PaymentResponse`.

### Payments — Member Custom-Amount Payment

#### `POST /api/v1/payments/intents` (academy_cohort purpose)

- **New optional field on `CreatePaymentIntentRequest`:** `amount_override_kobo` — member-initiated custom amount. Validated as `>= next_installment_amount` and `<= remaining_balance`. Used to pay ahead or recover from a missed auto-collection. Threaded through the webhook → mark-paid chain via the `amount_kobo` field on `EnrollmentMarkPaidRequest`.

### Removed Endpoints

#### `POST /api/v1/academy/enrollments/{id}/installments/{installment_id}/pay-with-bubbles` — **REMOVED**

Per founder policy (May 2026), academy cohort installments must be paid in real money (card/bank transfer). Bubbles wallet remains usable for session fees and ride share via the existing `bubbles_to_apply` field on `CreatePaymentIntentRequest`. The cron task `attempt_wallet_auto_deduction` was updated in the same change to skip the wallet-debit branch and only email Paystack checkout links.

---

### Corporate Wellness — Phase 1 Backend (May 2026)

New `corporate_service` on port 8017. Admin-only surface exposed via gateway at `/api/v1/admin/corporate/*`. The pricing rules, sales cycle, and outreach playbook are documented in [docs/marketing/CORPORATE_WELLNESS.md](../../docs/marketing/CORPORATE_WELLNESS.md).

#### Contacts (sales accounts)
- `GET    /api/v1/admin/corporate/contacts` — list with filters (`industry`, `company_size`, `source`, `is_active`, `search`)
- `POST   /api/v1/admin/corporate/contacts` — create
- `GET    /api/v1/admin/corporate/contacts/{id}` — detail
- `PATCH  /api/v1/admin/corporate/contacts/{id}` — update
- `DELETE /api/v1/admin/corporate/contacts/{id}` — soft-delete (`is_active=false`)

#### Touchpoints (outreach log)
- `POST /api/v1/admin/corporate/contacts/{contact_id}/touchpoints` — log a touchpoint (email/call/note). Cascades `last_touch_at` onto the linked deal if `deal_id` is passed.
- `GET  /api/v1/admin/corporate/contacts/{contact_id}/touchpoints` — list, newest first

#### Deals (pipeline)
- `POST  /api/v1/admin/corporate/contacts/{contact_id}/deals` — open a new deal
- `GET   /api/v1/admin/corporate/deals` — pipeline view (filter by `stage`, `contact_id`, `owner_auth_id`)
- `GET   /api/v1/admin/corporate/deals/{id}` — detail
- `PATCH /api/v1/admin/corporate/deals/{id}` — update (cannot set stage to `won`/`lost` — use dedicated endpoints)
- `POST  /api/v1/admin/corporate/deals/{id}/win` — close-won; creates a draft `CorporateProgram` with auto-priced totals
- `POST  /api/v1/admin/corporate/deals/{id}/lose` — close-lost with `lost_reason`

#### Programs (sold cohorts)
- `POST   /api/v1/admin/corporate/programs` — direct create (skips pipeline; auto-prices if `per_employee_kobo` / `total_kobo` = 0)
- `GET    /api/v1/admin/corporate/programs` — list (filter by `status`, `contact_id`)
- `GET    /api/v1/admin/corporate/programs/{id}` — detail
- `PATCH  /api/v1/admin/corporate/programs/{id}` — update (recomputes pricing if `employee_count` or `discount_tier` changes and explicit prices aren't passed)
- `DELETE /api/v1/admin/corporate/programs/{id}` — soft-cancel (status → `cancelled`)

#### Employees (program manifest)
- `GET    /api/v1/admin/corporate/programs/{id}/employees` — list manifest
- `POST   /api/v1/admin/corporate/programs/{id}/employees` — bulk-add (idempotent on email, within and across requests; max 500 per call)
- `DELETE /api/v1/admin/corporate/programs/{id}/employees/{employee_id}` — remove
- `POST   /api/v1/admin/corporate/programs/{id}/employees/match-members` — resolve emails against `members_service`, set `member_id` + `member_auth_id`, bump status → `registered`

#### Orchestration (calls into other services)
- `POST /api/v1/admin/corporate/programs/{id}/link-cohort` — verify cohort exists in `academy_service`, store `cohort_id`
- `POST /api/v1/admin/corporate/programs/{id}/provision-wallet` — create a `CorporateWallet` in `wallet_service` (budget defaults to program `total_kobo`)
- `POST /api/v1/admin/corporate/programs/{id}/enroll-all` — call `sessions_service` `/internal/sessions/bookings/bulk` to enroll every `member_id`-resolved employee in every cohort session; bumps program status to `active`

Cross-service IDs (`cohort_id`, `corporate_wallet_id`, `member_id`, `member_auth_id`) are stored as plain UUIDs / strings without FK constraints — corporate_service never reads other services' tables directly.

Companion endpoint added on wallet_service (called only by corporate_service):
- `POST /internal/wallet/corporate/create` — provisions the `CorporateWallet` row (Phase 5 stub tables in wallet_service got their first writer here).

---

### Corporate Wellness — Phase 2 (May 2026)

#### Public landing page
- `POST /api/v1/corporate/leads` — inbound lead from `swimbuddz.com/corporate`. Public (no auth), rate-limited 5/min, honeypot field `website`, dedupe window 24h on email. Creates a `CorporateContact` (source=inbound_web) + a `CorporateTouchpoint` summarising the submission; best-effort admin notification.

#### Outcome reports (SwimBuddz Wrapped) — admin
- `GET /api/v1/admin/corporate/programs/{id}/report` — aggregate attendance + milestone summary built live from `attendance_service` + `academy_service` per employee. Always fresh (no persisted report).
- `POST /api/v1/admin/corporate/programs/{id}/report/email` — email the report to the contact's primary email; logs an `email_followup_1` touchpoint.

#### HR self-serve portal — magic-link auth
- `POST /api/v1/corporate/me/auth/request-link` — send a magic link (24h TTL). Anti-enumeration: always returns `{sent: true}`.
- `POST /api/v1/corporate/me/auth/verify` — exchange magic-link token for a 7-day session JWT.
- `GET  /api/v1/corporate/me` — identity hint for the portal header.
- `GET  /api/v1/corporate/me/programs` — list programs for the caller's company.
- `GET  /api/v1/corporate/me/programs/{id}` — scoped program detail.
- `GET  /api/v1/corporate/me/programs/{id}/employees` — read-only manifest.
- `GET  /api/v1/corporate/me/programs/{id}/report` — same outcome report as admin, scoped to the caller's company.

#### Automated outreach — admin
- `GET    /api/v1/admin/corporate/contacts/{id}/outreach` — current sequence state (next-due email number, last-send timestamp, paused flag, inbound-reply detection).
- `POST   /api/v1/admin/corporate/contacts/{id}/outreach/start`  — kick off the sequence.
- `POST   /api/v1/admin/corporate/contacts/{id}/outreach/pause`  — suspend.
- `POST   /api/v1/admin/corporate/contacts/{id}/outreach/resume` — resume.
- `GET    /api/v1/admin/corporate/contacts/{id}/outreach/preview` — render all 3 emails as text + HTML.
- `POST   /api/v1/admin/corporate/contacts/{id}/outreach/send-now` — force the next email now (honours pause / done / inbound-reply guards).
- `POST   /api/v1/admin/corporate/outreach/run-cycle` — manually tick the scheduler.

Outreach scheduler runs daily at 07:00 UTC via the `corporate-worker` ARQ container (`arq services.corporate_service.worker.WorkerSettings`).
