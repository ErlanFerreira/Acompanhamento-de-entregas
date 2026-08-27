from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import config

serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="painel-session")

COOKIE_NAME = "session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 dias


def create_session_cookie() -> str:
    return serializer.dumps({"ok": True})


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        data = serializer.loads(token, max_age=COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("ok"))


def require_auth_page(request: Request):
    """Para rotas HTML: redireciona para /login se não autenticado."""
    if not is_authenticated(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def require_auth_api(request: Request):
    """Para rotas de API: responde 401 JSON se não autenticado."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Não autenticado.")
