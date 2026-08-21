from . import config
from .db import Base, SessionLocal, engine
from .models import User
from .security import hash_password


def init_db_and_seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        has_users = db.query(User).first() is not None
        if not has_users and config.ADMIN_EMAIL and config.ADMIN_PASSWORD:
            admin = User(
                email=config.ADMIN_EMAIL,
                password_hash=hash_password(config.ADMIN_PASSWORD),
                nome=config.ADMIN_NOME,
                role="admin",
            )
            db.add(admin)
            db.commit()
    finally:
        db.close()
