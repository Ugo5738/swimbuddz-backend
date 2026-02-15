# SwimBuddz Backend – API Endpoints Reference

This document defines the **HTTP interface** exposed via the gateway (`https://api.swimbuddz.com` or `http://localhost:8000` for development).

All endpoints assume Bearer authentication with a Supabase access token unless marked as **Public**.

## Service Coverage

✅ **Fully Documented:** Identity, Members, Sessions, Attendance, Announcements, Admin Dashboard, Academy, Payments, Transport, Events, Media, Store, Volunteers

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

| Method   | Endpoint                                      | Description                                                |
| -------- | --------------------------------------------- | ---------------------------------------------------------- |
| `GET`    | `/api/v1/volunteers/opportunities`            | List open opportunities (filterable by status, role, date) |
| `GET`    | `/api/v1/volunteers/opportunities/upcoming`   | Upcoming 14 days                                           |
| `GET`    | `/api/v1/volunteers/opportunities/{id}`       | Opportunity detail                                         |
| `POST`   | `/api/v1/volunteers/opportunities/{id}/claim` | Claim a slot (auto-approves for open_claim)                |
| `DELETE` | `/api/v1/volunteers/opportunities/{id}/claim` | Cancel my claim (tracks late cancellations)                |

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
