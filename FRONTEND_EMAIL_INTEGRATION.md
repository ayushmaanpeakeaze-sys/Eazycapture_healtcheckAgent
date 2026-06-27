# Frontend Integration — Invites, Email & Team Management

Everything the frontend needs for the invite/email/member-management flow.
All endpoints are under **`/api/v1/auth`** unless noted. All responses are JSON.

## Conventions

- **Auth header** on every admin call: `Authorization: Bearer <access_token>`.
- **Errors are always** `{ "detail": "<readable string>" }` — `detail` is
  guaranteed a string (never an object/array), so you can show it directly.
  Validation (422) errors additionally include
  `"errors": [{ "field": "...", "message": "..." }]`.
- Field names are `snake_case` exactly as shown.

---

## 1. Team list

`GET /api/v1/auth/users`  (admin) →

```json
{
  "total": 6,
  "users": [
    {
      "id": "uuid",
      "email": "member@firm.com",
      "full_name": "Jane",
      "role": "admin | team_member",
      "status": "invited | active | disabled",
      "access_mode": "all | selected",
      "assigned_company_ids": ["uuid", "..."],
      "created_at": "2026-06-03T12:00:00Z",
      "email_status": "sent | delivered | bounced | complained | failed | null"
    }
  ]
}
```

Render per row from these fields:

| Column  | Source |
|---------|--------|
| Role    | `role` |
| Access  | `access_mode === "all" ? "All clients" : "${assigned_company_ids.length} client(s)"` |
| Status  | `status` badge |
| ⚠️ flag | show a warning when `email_status` is `"bounced"` or `"complained"` (invite never reached them — fix the address + resend) |

### Action buttons by `status`

| `status`   | Buttons | Endpoint |
|------------|---------|----------|
| `active`   | **Disable** | `POST /users/{id}/disable` |
| `disabled` | **Enable**, **Remove** | `POST /users/{id}/enable`, `DELETE /users/{id}` |
| `invited`  | **Resend invite**, **Remove** | `POST /users/{id}/resend-invite`, `DELETE /users/{id}` |
| admin row  | *(no actions)* | — |

After any action → refetch `GET /users`. All return either a `UserSummary`
(disable/enable) or `{ "id": "...", "removed": true }` (delete). On 400/403,
show `detail` (e.g. "You cannot remove your own account.").

> **Note:** Disable/Remove now take effect **immediately** — a disabled or
> removed user's existing session is rejected on their next request (401),
> not just blocked from new logins.

---

## 2. Invite a member

`POST /api/v1/auth/invite`  (admin)

```jsonc
// request
{
  "email": "john@gmail.com",
  "full_name": "John",            // optional
  "access_mode": "selected",      // "all" | "selected"
  "company_ids": ["uuid", "..."]  // only used when access_mode === "selected"
}
```

```jsonc
// 201 response
{
  "user_id": "uuid",
  "email": "john@gmail.com",
  "status": "invited",
  "access_mode": "selected",
  "invite_token": "x7Kp9...",
  "accept_url": "http://localhost:3000/accept-invite?token=x7Kp9...",
  "invite_expires_at": "2026-06-10T12:00:00Z",
  "assigned_company_ids": ["uuid"],
  "email_sent": true,
  "email_channel": "email",       // "email" = really sent; "console" = SMTP not configured (logged only)
  "email_error": null
}
```

**Toast logic:**
- `email_sent === true` → ✅ "Invite emailed to john@gmail.com"
- `email_sent === false` → ⚠️ "Invite created, but email failed: {email_error}. Use 'Copy link' or Resend."

**Company multiselect** (for `selected` mode): get the list from
`GET /api/v1/health/companies-panorama/` → `results[].company_id` + `results[].name`.

**Validation:** a malformed email returns `422` with
`detail: "email: Enter a valid email address."`

### Copy-invite-link (alternative to email)

Use `accept_url` straight from the response — it's the ready-made link. No
need to build the URL yourself:
```js
navigator.clipboard.writeText(response.accept_url);
```

---

## 3. Resend invite

`POST /api/v1/auth/users/{id}/resend-invite`  (admin) → same shape as the
invite response (fresh `invite_token` + `accept_url`, `email_sent`, etc.).
Only valid while `status === "invited"` (else 400).

---

## 4. ⭐ Accept-invite page (the one new screen to build)

Route: **`/accept-invite`** (must match `ACCEPT_INVITE_PATH`). The email link
is `http://localhost:3000/accept-invite?token=XXX`.

### Step A — on page load, look up the invite (public, no auth)

`GET /api/v1/auth/invite-info?token=XXX` →

```json
{ "valid": true, "email": "john@gmail.com", "full_name": "John", "expired": false, "reason": null }
```

- `valid === true` → show **"Set your password for {email}"** (email read-only) + password field.
- `valid === false` → show `reason` (e.g. "This invite has expired…", "…already accepted…") and a link to Sign in. No email is leaked for bad tokens.

### Step B — submit the password (public, no auth)

`POST /api/v1/auth/accept-invite`

```jsonc
// request
{ "invite_token": "x7Kp9...", "password": "min-8-chars", "full_name": "John" }
```

```jsonc
// 200 response — they're now logged in
{ "access_token": "...", "token_type": "bearer", "role": "team_member", "user_id": "uuid", "email": "..." }
```

On success: store `access_token` → redirect to the dashboard (no separate
login needed). On error show `detail` ("Invalid or already-used invite.",
"Invite has expired…"). Password must be **≥ 8 chars** (else 422).

### Flow summary

```
email link → /accept-invite?token=XXX
   → GET /invite-info?token=XXX
        valid   → "Set password for john@firm.com" → POST /accept-invite → store JWT → dashboard
        invalid → show reason → link to Sign in
later: normal Sign in (email + password)
```

There is **no public signup** — team members are invite-only. The email is
fixed (the invited one); accept-invite only sets the password.

---

## 5. Email delivery status (for the ⚠️ flag)

Each user carries `email_status` (in the team list above):

| value | meaning |
|-------|---------|
| `null` | no email recorded yet |
| `sent` | handed to the mail server OK (not yet confirmed delivered) |
| `delivered` | provider confirmed delivery |
| `bounced` | bad address — invite did **not** arrive → show ⚠️, suggest fix + resend |
| `complained` | marked as spam → ⚠️ |
| `failed` | send attempt failed |

This updates automatically when the email provider reports back. **The
frontend does nothing for this except display it** — the provider→backend
webhook (`POST /api/v1/webhooks/email`) is server-to-server, not a frontend call.

---

## Quick checklist

- [ ] Send `Bearer` token on all `/auth` admin calls
- [ ] Team list: buttons per `status` (Disable / Enable / Remove / Resend)
- [ ] ⚠️ badge when `email_status` is `bounced`/`complained`
- [ ] Invite modal → `POST /invite`, toast on `email_sent`
- [ ] Company multiselect from `/companies-panorama/`
- [ ] "Copy link" uses `accept_url`
- [ ] **Build `/accept-invite` page** → `invite-info` then `accept-invite`
- [ ] Show `detail` string from any 4xx error
