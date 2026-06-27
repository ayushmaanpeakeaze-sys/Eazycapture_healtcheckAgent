# RBAC — Frontend Integration

Two roles: **admin** and **team_member**. Team members are invite-only and
gated to companies. Two assignment modes: **all** or **selected**.

This service issues its OWN JWTs. Turn auth on by setting `JWT_SECRET` in the
backend `.env` (empty = POC mode, everything open for demos).

Base URL: `http://localhost:8001`

---

## 0. One-time bootstrap (backend, not frontend)

The first admin is created on the server (you can't invite yourself):
```bash
.venv/bin/python -m scripts.create_admin admin@firm.com 'StrongPass123' 'Admin Name'
```

---

## 1. Login — every user

```
POST /api/v1/auth/login
Body: { "email": "...", "password": "..." }
```
```json
// 200
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "role": "admin",            // or "team_member"
  "user_id": "uuid",
  "email": "admin@firm.com"
}
// 401 → { "detail": "Invalid email or password." }
```
Store `access_token`. Send it on **every** request:
```js
headers: { Authorization: `Bearer ${token}` }
```
Use `role` to decide which menus to show (admin sees Team/Invite).

---

## 2. Who am I — on app load

```
GET /api/v1/auth/me        (Authorization: Bearer)
```
```json
{
  "user_id": "uuid",
  "email": "...",
  "role": "team_member",
  "access_mode": "selected",            // "all" | "selected"
  "assigned_company_ids": ["uuid", ...] // [] when access_mode = "all"
}
```
Use this to scope the client list:
- `role === "admin"` → show all companies
- `access_mode === "all"` → show all companies
- `access_mode === "selected"` → show only `assigned_company_ids`

---

## 3. Accept invite — team member onboarding

Team member clicks the invite link you built:
`https://yourapp.com/accept-invite?token=XXXXX`

```
POST /api/v1/auth/accept-invite
Body: { "invite_token": "XXXXX", "password": "min8chars", "full_name": "optional" }
```
```json
// 200 → same shape as /login (returns a token, they're now logged in)
// 400 → { "detail": "Invalid or already-used invite." }
//       { "detail": "Invite has expired. Ask an admin to re-invite you." }
```

---

## 4. ADMIN — invite a team member

This is where the **2 modes** live.

```
POST /api/v1/auth/invite      (admin only)
```

**Mode A — assign ALL companies:**
```json
{
  "email": "member@firm.com",
  "full_name": "Jane",
  "access_mode": "all"
  // company_ids ignored
}
```

**Mode B — assign SELECTED companies:**
```json
{
  "email": "member@firm.com",
  "full_name": "Jane",
  "access_mode": "selected",
  "company_ids": ["uuid-1", "uuid-2"]
}
```

Response:
```json
// 201
{
  "user_id": "uuid",
  "email": "member@firm.com",
  "role": "team_member",
  "access_mode": "all",
  "invite_token": "IXE58ohWoul_...",          // ← build the invite link from this
  "invite_expires_at": "2026-06-08T...",       // 7-day expiry
  "assigned_company_ids": []                   // populated in "selected" mode
}
// 409 → { "detail": "A user with this email already exists." }
// 400 → { "detail": "Unknown company id(s): ..." }
```

**Build the invite link:**
```js
const link = `https://yourapp.com/accept-invite?token=${res.invite_token}`;
// email it to them, or show it to the admin to copy
```

---

## 5. ADMIN — list all users (Team page)

```
GET /api/v1/auth/users        (admin only)
```
```json
{
  "users": [
    {
      "id": "uuid",
      "email": "member@firm.com",
      "full_name": "Jane",
      "role": "team_member",
      "status": "active",                  // invited | active | disabled
      "access_mode": "selected",           // all | selected
      "created_at": "...",
      "assigned_company_ids": ["uuid-1"]
    }
  ],
  "total": 3
}
```

---

## 6. ADMIN — change a member's company access

Same 2 modes. Replaces the whole assignment.

```
PUT /api/v1/auth/users/{user_id}/companies    (admin only)
```

**Switch to ALL:**
```json
{ "access_mode": "all" }
```

**Switch to SELECTED (specific list):**
```json
{ "access_mode": "selected", "company_ids": ["uuid-1", "uuid-3"] }
```

Response: the updated `UserSummary` (same shape as in the list).

---

## 7. ADMIN — disable a user

```
POST /api/v1/auth/users/{user_id}/disable     (admin only)
```
Disabled users can't log in. Returns the updated `UserSummary`.
(You can't disable your own account → 400.)

---

## Error handling

| Status | Meaning | Frontend action |
|---|---|---|
| 401 | Bad/missing/expired token | Redirect to login |
| 403 `Admin access required.` | Team member hit an admin route | Hide the UI / show "no permission" |
| 403 `You are not assigned to this company.` | Team member opened a company they can't see | Show "no access", remove from their list |
| 404 `Unknown company.` | Company doesn't exist | — |

---

## UI summary

**Admin sees:**
- All companies in the panorama
- "Team" menu → list users, invite, assign companies, disable
- Invite form with a toggle: **( ) All companies   ( ) Selected companies** → if Selected, show a multi-select of companies

**Team member sees:**
- Only their companies (all, or their assigned subset — from `/auth/me`)
- No Team/Invite menus
- 403 on any company outside their access

---

## The invite-mode toggle (the key new UI)

```jsx
// Invite form
<RadioGroup value={mode} onChange={setMode}>
  <Radio value="all">All companies (incl. future ones)</Radio>
  <Radio value="selected">Selected companies</Radio>
</RadioGroup>

{mode === "selected" && (
  <MultiSelect options={companies} value={companyIds} onChange={setCompanyIds} />
)}

// On submit:
POST /api/v1/auth/invite {
  email, full_name,
  access_mode: mode,
  company_ids: mode === "selected" ? companyIds : []
}
```

**"all" means flag-based** — if a new company is connected later, the member
automatically gets access. No re-assignment needed. "selected" is a fixed list.
