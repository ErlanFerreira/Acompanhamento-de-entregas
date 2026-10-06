from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer, URLSafeTimedSerializer

from . import config

serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="painel-session")

# Token de "link de acesso" -- não expira sozinho (diferente da sessão) e usa
# um salt próprio, então dá pra invalidar todos os links compartilhados sem
# derrubar sessões já logadas (bastaria trocar esse salt).
share_serializer = URLSafeSerializer(config.SECRET_KEY, salt="painel-share-link")

COOKIE_NAME = "session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 dias


# Por onde a sessão foi aberta: "senha" (login normal) ou "link" (link de
# acesso compartilhado). Hoje as duas têm o mesmo acesso; fica registrado
# pra poder restringir alguma tela a quem entra com a senha, se precisar
# (sessões antigas, de antes desse campo, não têm "via").
VIA_SENHA = "senha"
VIA_LINK = "link"


def create_session_cookie(via: str) -> str:
    return serializer.dumps({"ok": True, "via": via})


def create_share_token() -> str:
    """Token estável (sempre o mesmo, enquanto SECRET_KEY não mudar) que dá
    acesso direto via link, sem precisar digitar a senha."""
    return share_serializer.dumps({"share": True})


def verify_share_token(token: str) -> bool:
    try:
        data = share_serializer.loads(token)
    except BadSignature:
        return False
    return bool(data.get("share"))


def _sessao(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = serializer.loads(token, max_age=COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data if data.get("ok") else None


def is_authenticated(request: Request) -> bool:
    return _sessao(request) is not None


def require_auth_page(request: Request):
    """Para rotas HTML: redireciona para /login se não autenticado."""
    if not is_authenticated(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def require_auth_api(request: Request):
    """Para rotas de API: responde 401 JSON se não autenticado."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Não autenticado.")

