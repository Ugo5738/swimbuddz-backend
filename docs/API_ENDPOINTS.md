# SwimBuddz Backend – API Endpoints Reference

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
