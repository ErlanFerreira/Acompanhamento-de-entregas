import datetime
import logging
from contextlib import asynccontextmanager

import requests
from fastapi import Depends, FastAPI, Form, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import Carga, Meta, User
from .security import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    create_session_cookie,
    get_current_user,
    hash_password,
    require_admin_api,
    require_admin_page,
    require_user_api,
    require_user_page,
    verify_password,
)
from .seed import init_db_and_seed

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A sincronização periódica roda via GitHub Actions (ver .github/workflows/sync.yml),
    # não dentro do processo web -- necessário porque a Vercel roda o app como função
    # serverless (sem processo de fundo de longa duração).
    init_db_and_seed()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.exception_handler(HTTPException)
async def redirect_aware_exception_handler(request: Request, exc: HTTPException):
    location = (exc.headers or {}).get("Location")
    if exc.status_code == 303 and location:
        return RedirectResponse(url=location, status_code=303)
    return await http_exception_handler(request, exc)


# ---------------------------------------------------------------- auth ----

@app.get("/", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return RedirectResponse(url="/painel" if user else "/login")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse(url="/painel")
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    senha: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user or not verify_password(senha, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "E-mail ou senha incorretos."},
            status_code=401,
        )
    token = create_session_cookie(user.id)
    resp = RedirectResponse(url="/painel", status_code=303)
    resp.set_cookie(COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ------------------------------------------------------------- painel -----

@app.get("/painel", response_class=HTMLResponse)
def painel_page(request: Request, user: User = Depends(require_user_page)):
    return templates.TemplateResponse(
        "painel.html", {"request": request, "user": user, "active": "painel"}
    )


def _row_to_dict(r: Carga) -> dict:
    return {
        "emissao": r.emissao,
        "cte": r.cte,
        "remetente": r.remetente,
        "consignatario": r.consignatario,
        "cidade_cons": r.cidade_cons,
        "destinatario": r.destinatario,
        "cidade_dest": r.cidade_dest,
        "uf_dest": r.uf_dest,
        "manifesto": r.manifesto,
        "romaneio": r.romaneio,
        "ocorrencia": r.ocorrencia,
        "entregue": r.entregue,
        "data_comprovante": r.data_comprovante,
        "data_baixa": r.data_baixa,
        "status": r.status,
        "estado": r.estado,
        "previsao": r.previsao,
        "dias_atraso": r.dias_atraso,
    }


@app.get("/api/cargas")
def api_cargas(
    data_inicial: str | None = None,
    data_final: str | None = None,
    user: User = Depends(require_user_api),
    db: Session = Depends(get_db),
):
    q = db.query(Carga)
    if data_inicial:
        q = q.filter(Carga.emissao >= data_inicial)
    if data_final:
        q = q.filter(Carga.emissao <= data_final)
    rows = q.all()
    records = [_row_to_dict(r) for r in rows]

    last_sync_at = db.query(Meta).filter(Meta.chave == "last_sync_at").first()
    gerado_em = last_sync_at.valor[:10] if last_sync_at and last_sync_at.valor else datetime.date.today().isoformat()

    meta = {
        "gerado_em": gerado_em,
        "referencia": datetime.date.today().isoformat(),
        "total": len(records),
        "fonte": "Webtrans (GW Sistemas) - relatório \"Pendências\", atualização automática",
    }
    return {"meta": meta, "records": records}


@app.post("/api/sync/run")
def api_sync_run(user: User = Depends(require_admin_api)):
    """Dispara a sincronização via GitHub Actions (workflow_dispatch).

    O app web (Vercel) não roda a automação de navegador diretamente --
    ela precisa de um Chromium instalado, inviável numa função serverless.
    Isso só enfileira o job; o resultado aparece no painel após o workflow
    terminar (~1-2 min), não instantaneamente.
    """
    if not config.GITHUB_REPO or not config.GITHUB_DISPATCH_TOKEN:
        return JSONResponse(
            {"ok": False, "erro": "GITHUB_REPO/GITHUB_DISPATCH_TOKEN não configurados nesta implantação."},
            status_code=500,
        )
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/actions/workflows/sync.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {config.GITHUB_DISPATCH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    try:
        resp = requests.post(url, headers=headers, json={"ref": "main"}, timeout=15)
        if resp.status_code >= 300:
            return JSONResponse({"ok": False, "erro": f"GitHub respondeu {resp.status_code}: {resp.text[:300]}"}, status_code=502)
        return {"ok": True, "queued": True}
    except requests.RequestException as exc:
        return JSONResponse({"ok": False, "erro": str(exc)}, status_code=502)


# ----------------------------------------------------- gestão de usuários -

@app.get("/usuarios", response_class=HTMLResponse)
def usuarios_page(request: Request, user: User = Depends(require_admin_page)):
    return templates.TemplateResponse(
        "usuarios.html", {"request": request, "user": user, "active": "usuarios"}
    )


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "nome": u.nome,
        "email": u.email,
        "role": u.role,
        "created_at": u.created_at.strftime("%Y-%m-%d") if u.created_at else None,
    }


@app.get("/api/usuarios")
def api_usuarios_list(user: User = Depends(require_admin_api), db: Session = Depends(get_db)):
    rows = db.query(User).order_by(User.created_at.asc()).all()
    return {"usuarios": [_user_to_dict(u) for u in rows]}


@app.post("/api/usuarios")
def api_usuarios_create(
    payload: dict,
    user: User = Depends(require_admin_api),
    db: Session = Depends(get_db),
):
    email = (payload.get("email") or "").strip().lower()
    senha = payload.get("senha") or ""
    nome = (payload.get("nome") or "").strip()
    role = payload.get("role") or "user"

    if not email or not senha:
        return JSONResponse({"erro": "E-mail e senha são obrigatórios."}, status_code=400)
    if role not in ("admin", "user"):
        return JSONResponse({"erro": "Papel inválido."}, status_code=400)
    if db.query(User).filter(User.email == email).first():
        return JSONResponse({"erro": "Já existe um usuário com esse e-mail."}, status_code=409)

    novo = User(email=email, password_hash=hash_password(senha), nome=nome or None, role=role)
    db.add(novo)
    db.commit()
    return _user_to_dict(novo)


@app.put("/api/usuarios/{user_id}")
def api_usuarios_update(
    user_id: int,
    payload: dict,
    user: User = Depends(require_admin_api),
    db: Session = Depends(get_db),
):
    alvo = db.query(User).filter(User.id == user_id).first()
    if not alvo:
        return JSONResponse({"erro": "Usuário não encontrado."}, status_code=404)

    if "nome" in payload:
        alvo.nome = (payload.get("nome") or "").strip() or None
    if "role" in payload and payload["role"] in ("admin", "user"):
        if alvo.id == user.id and payload["role"] != "admin":
            return JSONResponse({"erro": "Você não pode remover seu próprio acesso de administrador."}, status_code=400)
        alvo.role = payload["role"]
    if payload.get("senha"):
        alvo.password_hash = hash_password(payload["senha"])

    db.commit()
    return _user_to_dict(alvo)


@app.delete("/api/usuarios/{user_id}")
def api_usuarios_delete(
    user_id: int,
    user: User = Depends(require_admin_api),
    db: Session = Depends(get_db),
):
    if user_id == user.id:
        return JSONResponse({"erro": "Você não pode remover o próprio usuário."}, status_code=400)
    alvo = db.query(User).filter(User.id == user_id).first()
    if not alvo:
        return JSONResponse({"erro": "Usuário não encontrado."}, status_code=404)
    db.delete(alvo)
    db.commit()
    return {"ok": True}
