import logging
import os
import secrets
import time

from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .models import AdminUser, Base, Setting
from .security import hash_password

log = logging.getLogger("ciberrol")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://ciberrol:ciberrol@localhost:5432/ciberrol"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

ADMIN_USERNAME = "admin"
_INIT_LOCK_ID = 7_301_984  # id arbitrario para pg_advisory_lock


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _wait_for_db(retries: int = 30) -> None:
    for attempt in range(retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            log.info("Esperando a Postgres (%s/%s)...", attempt + 1, retries)
            time.sleep(1)
    raise RuntimeError("No se pudo conectar a Postgres")


def init_db() -> str:
    """Crea tablas, estado inicial, clave de sesión y cuenta admin.

    Usa un advisory lock para que varios workers no lo hagan a la vez.
    Devuelve la clave secreta de sesión.
    """
    _wait_for_db()
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": _INIT_LOCK_ID})
        try:
            Base.metadata.create_all(conn)
            # Migraciones livianas (columnas agregadas después de crear las tablas)
            conn.execute(text("ALTER TABLE slides ADD COLUMN IF NOT EXISTS text_general TEXT NOT NULL DEFAULT ''"))
            conn.execute(text("ALTER TABLE activities ADD COLUMN IF NOT EXISTS show_authors BOOLEAN NOT NULL DEFAULT false"))
            conn.execute(text("ALTER TABLE activities ADD COLUMN IF NOT EXISTS split_contexts BOOLEAN NOT NULL DEFAULT true"))
            for p in ("directivo", "comunicacional", "legal", "tecnico"):
                conn.execute(text(f"ALTER TABLE slides ADD COLUMN IF NOT EXISTS q_{p} TEXT NOT NULL DEFAULT ''"))
            conn.execute(text("UPDATE slides SET category = 'Evaluar' WHERE category = 'Aprendizaje'"))
            conn.commit()
            with Session(bind=conn) as db:
                secret = db.get(Setting, "session_secret")
                if secret is None:
                    secret = Setting(key="session_secret", value=secrets.token_urlsafe(48))
                    db.add(secret)

                if db.scalar(select(AdminUser).limit(1)) is None:
                    password = secrets.token_urlsafe(12)
                    db.add(AdminUser(username=ADMIN_USERNAME, password_hash=hash_password(password)))
                    banner = "=" * 64
                    print(
                        f"\n{banner}\n"
                        f"  CUENTA ADMIN CREADA (solo se muestra esta vez)\n"
                        f"  URL:     /admin\n"
                        f"  Usuario: {ADMIN_USERNAME}\n"
                        f"  Clave:   {password}\n"
                        f"{banner}\n",
                        flush=True,
                    )
                db.commit()
                return secret.value
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _INIT_LOCK_ID})
            conn.commit()
