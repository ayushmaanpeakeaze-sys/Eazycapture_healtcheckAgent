# Scrum Brief — Backend Work

## One-liner
Built the full team-invite + email system (real SMTP delivery + bounce
tracking), real enable/disable/remove for team members, production auth
hardening, and decoupled the codebase into a clean modular monolith —
all tested (40 passing) and documented for the frontend.

---

## What I built

### Email & invites (SMTP)
- Real SMTP email sending — invites land in the inbox (verified end-to-end).
- Extensible notifications layer — email now, WhatsApp/Slack later, no rewrite.
- Branded HTML invite email + one-time secure link.
- Production delivery tracking — every send logged; per-user status
  (sent / delivered / bounced); provider webhook (Resend/SendGrid-ready).

### Team / RBAC management
- Real Enable / Disable / Remove / Resend-invite for team members.
- Accept-invite flow (invite-info lookup + set-password).
- Instant access revocation — disabled user locked out immediately.

### Production hardening
- Login rate-limiting (brute-force protection).
- Environment separation (APP_ENV) — refuses to boot insecure in production.
- Consistent API error format (fixed the [object Object] issue).

### Architecture & quality
- Decoupled the codebase into a modular monolith — each module owns its
  data (zero schema change, verified).
- Fixed Xero "Open in Xero" links (bills / contacts / credit-notes).
- Tests + CI — 40 passing, GitHub Actions workflow added.
- Deployment guide + frontend integration spec written.

---

## Small but smart details
- Email sends AFTER the DB commit — SMTP failure never rolls back the invite.
- Console fallback — invites still work locally without SMTP creds.
- Rate-limiting counts failures only, resets on success; fails OPEN if Redis dies.
- Provider-agnostic webhook — parses Resend + SendGrid + generic (no code
  change to switch providers).
- Health-score denominator uses the MAX audit, not the last run.
- Found & fixed a hidden DB index drift that predated this work.

---

## Nango: Proxy vs Actions
- **Proxy** = call Xero's real API through Nango (Nango injects/refreshes the
  token). Returns Xero's full response.
- **Actions** = pre-built scripts on Nango's side; cleaner, but only return
  what the action chooses.
- **We use Proxy** because Xero's Action returned invoices with **empty line
  items**, and our health checks need per-line detail (account code + tax).

### Line items = the rows inside an invoice
One invoice = a header (vendor, total) + several line items (each item with
its own qty, price, account code, tax). Our checks run per-line (e.g. a
laptop coded to the wrong account), so we need the lines — not just the total.

---

## What's left
- Frontend builds the accept-invite page (spec handed over).
- Production: swap Gmail -> Resend/SendGrid — config only, NO code change.
