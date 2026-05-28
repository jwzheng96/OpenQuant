"""Seed the database with the first admin user.

Run from project root:
    cd web/backend
    PYTHONPATH=. python scripts/web_init.py

Idempotent: if the admin user already exists, prints a notice and exits.

Default credentials (CHANGE AFTER FIRST LOGIN):
    username: admin
    password: admin
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Allow running from various cwds
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import hash_password
from app.db.session import _get_sessionmaker
from app.models.db_models import User, UserRole


DEFAULT_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
DEFAULT_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@openquant.local")


async def main() -> None:
    Session = _get_sessionmaker()
    async with Session() as session:  # type: AsyncSession
        existing = (
            await session.execute(
                select(User).where(User.username == DEFAULT_ADMIN_USERNAME)
            )
        ).scalar_one_or_none()
        if existing:
            print(
                f"⚠️  user '{DEFAULT_ADMIN_USERNAME}' already exists "
                f"(role={existing.role}, active={existing.is_active}). nothing to do."
            )
            return

        user = User(
            username=DEFAULT_ADMIN_USERNAME,
            email=DEFAULT_ADMIN_EMAIL,
            password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
            role=UserRole.admin.value,
            locale="zh-CN",
            is_active=True,
        )
        session.add(user)
        await session.commit()

        print(f"✅ created admin user")
        print(f"   username: {DEFAULT_ADMIN_USERNAME}")
        print(f"   password: {DEFAULT_ADMIN_PASSWORD}")
        print(f"   email   : {DEFAULT_ADMIN_EMAIL}")
        print()
        print("⚠️  CHANGE the password after first login (or via UI in Phase 3).")


if __name__ == "__main__":
    asyncio.run(main())
