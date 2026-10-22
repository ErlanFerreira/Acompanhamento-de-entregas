import hashlib
import hmac
import json
import secrets

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer, URLSafeTimedSerializer

from . import config
from .db import SessionLocal
from .models import Usuario

serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="painel-session")

# Token de "link de acesso" -- não expira sozinho (diferente da sessão) e usa
# um salt próprio, então dá pra invalidar todos os links compartilhados sem
# derrubar sessões já logadas (bastaria trocar esse salt).
share_serializer = URLSafeSerializer(config.SECRET_KEY, salt="painel-share-link")

COOKIE_NAME = "session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 dias


# Por onde a sessão foi aberta:
#   "senha"   -- senha geral (PAINEL_SENHA): o supervisor, vê tudo e cadastra
#                os analistas na tela de Usuários;
#   "usuario" -- login próprio de um analista: vê todas as telas, mas os CT-e
#                (Painel, Comprovantes, tratativas) só das empresas dele;
#   "link"    -- link de acesso compartilhado: sem a tela de Comprovantes.
# Sessões antigas, de antes desse campo, não têm "via" -- são tratadas como
# link (restritas) até a pessoa entrar de novo com a senha.
VIA_SENHA = "senha"
VIA_USUARIO = "usuario"
VIA_LINK = "link"


def create_session_cookie(via: str, usuario_id: int | None = None) -> str:
    dados = {"ok": True, "via": via}
    if usuario_id is not None:
        dados["uid"] = usuario_id
    return serializer.dumps(dados)


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


# ------------------------------------------------- senhas dos analistas ----

_PBKDF2_ITERACOES = 260_000


def hash_senha(senha: str) -> str:
    sal = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal.encode(), _PBKDF2_ITERACOES).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERACOES}${sal}${h}"


def conferir_senha(senha: str, guardado: str | None) -> bool:
    try:
        _, iteracoes, sal, h = (guardado or "").split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal.encode(), int(iteracoes)).hex()
    except ValueError:
        return False
    return hmac.compare_digest(calc, h)


# ---------------------------------------------------------------- sessão ----

def _sessao(request: Request) -> dict | None:
    # Guardado no request: várias checagens por requisição (dependência,
    # menu do template...) não repetem a consulta ao banco.
    if hasattr(request.state, "sessao"):
        return request.state.sessao
    request.state.sessao = _carregar_sessao(request)
    return request.state.sessao


def _carregar_sessao(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = serializer.loads(token, max_age=COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not data.get("ok"):
        return None
    if data.get("via") == VIA_USUARIO:
        # Confere a cada requisição: analista desativado (ou com as empresas
        # trocadas) vale na hora, sem esperar o cookie expirar.
        db = SessionLocal()
        try:
            u = db.get(Usuario, data.get("uid"))
            if not u or not u.ativo:
                return None
            data["usuario"] = {
                "id": u.id, "nome": u.nome, "login": u.login, "empresas": json.loads(u.empresas or "[]"),
                "trocar_senha": bool(u.trocar_senha), "acesso_sac": bool(u.acesso_sac),
            }
        finally:
            db.close()
    return data


def is_authenticated(request: Request) -> bool:
    return _sessao(request) is not None


def tem_acesso_completo(request: Request) -> bool:
    """Entrou com senha -- a geral (supervisor) ou a própria (analista); não
    pelo link de acesso compartilhado."""
    sessao = _sessao(request)
    return bool(sessao) and sessao.get("via") in (VIA_SENHA, VIA_USUARIO)


def eh_supervisor(request: Request) -> bool:
    """Entrou com a senha geral -- vê todas as empresas e cadastra analistas."""
    sessao = _sessao(request)
    return bool(sessao) and sessao.get("via") == VIA_SENHA


def usuario_logado(request: Request) -> dict | None:
    """O analista logado (id, nome, login, empresas), ou None."""
    sessao = _sessao(request)
    return sessao.get("usuario") if sessao else None


def pode_ver_sac(request: Request) -> bool:
    """Atalho do portal do SAC no menu: o supervisor sempre; analista só se o
    supervisor liberou na tela Usuários; nunca pelo link de acesso."""
    if eh_supervisor(request):
        return True
    u = usuario_logado(request)
    return bool(u and u.get("acesso_sac") and not u.get("trocar_senha"))


def escopo_empresas(request: Request) -> set[str] | None:
    """Empresas (nome em maiúsculas) que o analista logado pode ver -- `None`
    quando não há restrição (supervisor ou link de acesso)."""
    u = usuario_logado(request)
    return set(u["empresas"]) if u else None


def precisa_trocar_senha(request: Request) -> bool:
    """Analista com senha provisória (primeiro acesso ou redefinida pelo
    supervisor) -- só pode usar a tela de troca de senha."""
    u = usuario_logado(request)
    return bool(u and u.get("trocar_senha"))


def require_auth_page(request: Request):
    """Para rotas HTML: redireciona para /login se não autenticado (ou pra
    troca de senha, no primeiro acesso do analista)."""
    if not is_authenticated(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if precisa_trocar_senha(request):
        raise HTTPException(status_code=303, headers={"Location": "/trocar-senha"})


def require_auth_api(request: Request):
    """Para rotas de API: responde 401 JSON se não autenticado."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Não autenticado.")
    if precisa_trocar_senha(request):
        raise HTTPException(status_code=403, detail="Troque a senha antes de continuar.")


def require_acesso_completo_api(request: Request):
    require_auth_api(request)
    if not tem_acesso_completo(request):
        raise HTTPException(status_code=404, detail="Not Found")


def require_acesso_completo_page(request: Request):
    """Telas restritas a quem entrou com senha -- quem veio pelo link de
    acesso volta pro painel, como se a tela não existisse."""
    require_auth_page(request)
    if not tem_acesso_completo(request):
        raise HTTPException(status_code=303, headers={"Location": "/painel"})


def require_supervisor_api(request: Request):
    require_auth_api(request)
    if not eh_supervisor(request):
        raise HTTPException(status_code=404, detail="Not Found")


def require_supervisor_page(request: Request):
    """Telas só do supervisor (ex.: Usuários) -- os demais voltam pro painel."""
    require_auth_page(request)
    if not eh_supervisor(request):
        raise HTTPException(status_code=303, headers={"Location": "/painel"})
