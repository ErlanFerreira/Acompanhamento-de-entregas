import bcrypt
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import User

serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="painel-session")

COOKIE_NAME = "session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 dias


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_session_cookie(user_id: int) -> str:
    return serializer.dumps({"uid": user_id})


def read_session_cookie(token: str):
    try:
        data = serializer.loads(token, max_age=COOKIE_MAX_AGE)
        return data.get("uid")
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    uid = read_session_cookie(token)
    if not uid:
        return None
    return db.query(User).filter(User.id == uid).first()


def require_user_page(request: Request, db: Session = Depends(get_db)):
    """Para rotas HTML: redireciona para /login se não autenticado."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin_page(user: User = Depends(require_user_page)):
    if user.role != "admin":
        raise HTTPException(status_code=303, headers={"Location": "/painel"})
    return user


def require_user_api(request: Request, db: Session = Depends(get_db)):
    """Para rotas de API: responde 401 JSON se não autenticado."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    return user


def require_admin_api(user: User = Depends(require_user_api)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    return user
