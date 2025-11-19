# SwimBuddz MCP – `swimbuddz_core_mcp`

This directory contains the **Model Context Protocol (MCP)** server implementation for SwimBuddz.

MCP is used to expose backend capabilities as tools that AI agents (e.g. ChatGPT, Claude, internal agents) can call.

---

## 1. Design Goals

- Provide a **single MCP server** for core SwimBuddz operations.
- Make tools **thin wrappers** around the existing backend:
  - Call domain functions or gateway HTTP endpoints.
- Avoid duplicating business logic:
  - All business rules live in the backend services, not in tool handlers.
- Keep tool definitions **stable and well-documented**.

---

## 2. Directory Structure

```text
mcp/swimbuddz_core_mcp/
  __init__.py
  server.py            # MCP entrypoint
  tools/
    members_tools.py
    sessions_tools.py
    attendance_tools.py
    communications_tools.py
```

- `server.py` boots the MCP server, registers tools, and wires authentication.
- `tools/*.py` define tool handlers grouped by domain.

---

## 3. Tools Overview

### 3.1 Members (`members_tools.py`)

- `get_current_member_profile`
  - **Input:** none.
  - **Output:** safe subset of the member profile.
  - **Backend call:** identity/auth + members service query.
- `update_member_profile`
  - **Input:** editable fields (phone, swim level, availability, etc.).
  - **Output:** updated profile summary.
  - **Backend call:** PATCH through domain logic or gateway.

### 3.2 Sessions (`sessions_tools.py`)

- `list_upcoming_sessions` – optional filters (`location`, `session_type`, `limit`); returns concise rows.
- `get_session_details` – fetches the full record for a given `session_id`.

### 3.3 Attendance (`attendance_tools.py`)

- `sign_in_to_session`
  - **Input:** `session_id` plus optional `time_variant`, `time_variant_note`, `ride_share_role`, `ride_share_seats_offered`.
  - **Output:** attendance summary (status, total_fee, payment_reference).
  - **Backend call:** attendance service/gateway endpoint—never bypass rules.
- `get_my_attendance_history`
  - **Input:** optional date filters.
  - **Output:** summary metrics + record list.

### 3.4 Communications (`communications_tools.py`)

- `list_announcements` – optional `limit`/`category`.
- `create_announcement` – admin-only tool to post a notice.

---

## 4. Authentication & Identity

1. Host (ChatGPT, Claude, etc.) provides a Supabase token or signed context.
2. MCP server validates the token (Supabase public key or gateway endpoint).
3. Server constructs an `AuthUser` (user_id, email, role) and passes it to tool handlers.

Rules:

- Tools that mutate state (`update_member_profile`, `sign_in_to_session`, `create_announcement`) must enforce auth/roles before touching backend logic.
- If auth fails, return an MCP error—do not proceed.

---

## 5. Running the MCP Server

```bash
python -m mcp.swimbuddz_core_mcp.server
```

The server should:

- Expose MCP tools/resources over stdio or another configured transport.
- Advertise tool metadata according to the MCP spec.

---

## 6. Implementation Rules

- Prefer importing backend modules directly when running within the same repo.
- HTTP calls to the gateway are acceptable when decoupling is needed.
- Never bypass backend rules (no direct DB writes from MCP).
- If a needed capability does **not** exist yet:
  1. Build it in the relevant backend service.
  2. Then expose it through a tool that simply calls that capability.
