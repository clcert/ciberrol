"""Genera una nueva clave aleatoria para la cuenta admin.

Uso:  docker compose exec app python -m ciberrol.reset_admin
"""

import secrets

from sqlalchemy import select

from .db import ADMIN_USERNAME, SessionLocal, init_db
from .models import AdminUser
from .security import hash_password


def main() -> None:
    init_db()
    password = secrets.token_urlsafe(12)
    with SessionLocal() as db:
        admin = db.scalar(select(AdminUser).where(AdminUser.username == ADMIN_USERNAME))
        if admin is None:
            admin = AdminUser(username=ADMIN_USERNAME, password_hash="")
            db.add(admin)
        admin.password_hash = hash_password(password)
        db.commit()
    print(f"Usuario: {ADMIN_USERNAME}\nNueva clave: {password}")


if __name__ == "__main__":
    main()
