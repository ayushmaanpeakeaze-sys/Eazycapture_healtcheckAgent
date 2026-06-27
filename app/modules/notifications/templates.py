"""Message templates — the *content* of notifications, kept separate from
*delivery* (channels) so the same message can go to any channel and copy
changes don't touch transport code.

Each builder returns a channel-agnostic :class:`Message`.
"""
from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.modules.notifications.channels.base import Message


def invite_email(
    *,
    accept_url: str,
    expires_days: int,
    inviter_email: Optional[str] = None,
) -> Message:
    """Team-member invite. ``accept_url`` is the frontend accept-invite link
    carrying the one-time token."""
    app = settings.APP_NAME
    by = f" by {inviter_email}" if inviter_email else ""

    subject = f"You've been invited to {app}"

    body_text = (
        f"Hi,\n\n"
        f"You've been invited{by} to join {app} — bookkeeping health checks "
        f"for accounting firms.\n\n"
        f"Accept your invite and set your password here:\n"
        f"{accept_url}\n\n"
        f"This link expires in {expires_days} days.\n\n"
        f"If you weren't expecting this invite, you can safely ignore this email.\n"
    )

    body_html = f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0;">
      <tr><td align="center">
        <table role="presentation" width="480" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e6e8eb;">
          <tr><td style="padding:28px 32px 8px 32px;">
            <div style="font-size:18px;font-weight:700;color:#5b21b6;">{app}</div>
          </td></tr>
          <tr><td style="padding:8px 32px 0 32px;">
            <h1 style="font-size:20px;color:#111827;margin:8px 0 4px 0;">You've been invited</h1>
            <p style="font-size:14px;line-height:22px;color:#374151;margin:8px 0;">
              You've been invited{by} to join <strong>{app}</strong> — bookkeeping
              health checks for accounting firms. Accept your invite and set a
              password to get started.
            </p>
          </td></tr>
          <tr><td align="center" style="padding:20px 32px 8px 32px;">
            <a href="{accept_url}"
               style="display:inline-block;background:#6d28d9;color:#ffffff;text-decoration:none;
                      font-size:15px;font-weight:600;padding:12px 28px;border-radius:8px;">
              Accept invite
            </a>
          </td></tr>
          <tr><td style="padding:8px 32px 28px 32px;">
            <p style="font-size:12px;line-height:18px;color:#6b7280;margin:12px 0 0 0;">
              This link expires in {expires_days} days. If you weren't expecting
              this invite you can safely ignore this email.
            </p>
            <p style="font-size:11px;color:#9ca3af;margin:14px 0 0 0;word-break:break-all;">
              {accept_url}
            </p>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""

    return Message(subject=subject, body_text=body_text, body_html=body_html)
