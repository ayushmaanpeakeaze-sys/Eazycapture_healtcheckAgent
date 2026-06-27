"""Toggle Demo Co's Nango connection between known Xero orgs.

Usage:

    python -m scripts.switch_profile sir-test
    python -m scripts.switch_profile xero-demo
    python -m scripts.switch_profile list

This is a demo-time convenience: every profile is one ``UPDATE``
statement against the ``company`` table — no code change, no env
flip required. The active Nango secret key in ``.env`` already
works for both connections because they live under the same Nango
account.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.core.db import SyncSessionLocal
from app.modules.healthcheck.models import Company
from app.modules.healthcheck.seed_data import DEMO_CO_ID


@dataclass(frozen=True)
class Profile:
    slug: str
    description: str
    connection_id: str
    tenant_id: str
    shortcode: Optional[str]


PROFILES: dict[str, Profile] = {
    "ayushmaan": Profile(
        slug="ayushmaan",
        description="Ayushmaan's own Nango connection to Xero Demo Company (Global).",
        connection_id="81d61fa2-1b9e-4e3b-9af3-b0ff26381fa2",
        tenant_id="f3a3aa51-33d9-4287-83d3-94d9988c82e4",
        shortcode="!S9bXm",
    ),
    "sir-test": Profile(
        slug="sir-test",
        description=(
            "Sir's private Xero org named 'test' — 6 invoices, all "
            "ACCREC with one POC Test Contact vendor."
        ),
        connection_id="8c9f4dbb-2044-4562-a0ab-aa6dd77a2ca5",
        tenant_id="9890742a-4a79-4b72-8dab-cdcda61c4fa8",
        shortcode="!54DJ-",
    ),
    "xero-demo": Profile(
        slug="xero-demo",
        description=(
            "Xero standard fictional 'Demo Company (Global)' — 68 "
            "documents (27 ACCREC + 41 ACCPAY + 5 credit notes), "
            "messy realistic data."
        ),
        connection_id="cd7ce114-81c3-4ddd-b5a1-ac19e7657ef8",
        tenant_id="f3a3aa51-33d9-4287-83d3-94d9988c82e4",
        shortcode="!S9bXm",
    ),
}


def _print_profiles(active: Optional[Profile]) -> None:
    print("Available profiles:\n")
    for p in PROFILES.values():
        marker = "●" if active and active.slug == p.slug else " "
        print(f"  {marker} {p.slug}")
        print(f"      {p.description}")
        print(f"      connection_id = {p.connection_id}")
        print(f"      tenant_id     = {p.tenant_id}")
        print(f"      shortcode     = {p.shortcode}")
        print()
    print("Switch with:  python -m scripts.switch_profile <slug>")


def _current_profile() -> Optional[Profile]:
    with SyncSessionLocal() as db:
        company = db.execute(
            select(Company).where(Company.id == DEMO_CO_ID)
        ).scalar_one_or_none()
        if company is None:
            return None
        for p in PROFILES.values():
            if (company.nango_connection_id or "").strip() == p.connection_id:
                return p
    return None


def _apply(profile: Profile) -> None:
    with SyncSessionLocal() as db:
        company = db.execute(
            select(Company).where(Company.id == DEMO_CO_ID)
        ).scalar_one_or_none()
        if company is None:
            raise SystemExit("Demo Co row missing; run `make seed` first.")
        company.nango_connection_id = profile.connection_id
        company.xero_tenant_id = profile.tenant_id
        company.xero_shortcode = profile.shortcode
        db.commit()
    print(f"[switch_profile] activated '{profile.slug}'")
    print(f"  description: {profile.description}")
    print()
    print("Next:")
    print("  make reset-demo            # wipes audit state + re-audits via new profile")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help", "list"}:
        _print_profiles(active=_current_profile())
        return 0
    slug = argv[1].strip()
    profile = PROFILES.get(slug)
    if profile is None:
        print(f"unknown profile: {slug!r}")
        _print_profiles(active=_current_profile())
        return 1
    _apply(profile)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
