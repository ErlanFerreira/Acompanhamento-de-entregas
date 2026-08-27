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
from .models import Carga, Meta
from .security import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    create_session_cookie,
    is_authenticated,
    require_auth_api,
    require_auth_page,
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
def root(request: Request):
    return RedirectResponse(url="/painel" if is_authenticated(request) else "/login")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/painel")
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, senha: str = Form(...)):
    if not config.PAINEL_SENHA or senha != config.PAINEL_SENHA:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Senha incorreta."},
            status_code=401,
        )
    token = create_session_cookie()
    resp = RedirectResponse(url="/painel", status_code=303)
    resp.set_cookie(COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ------------------------------------------------------------- painel -----

@app.get("/painel", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
def painel_page(request: Request):
    return templates.TemplateResponse("painel.html", {"request": request, "active": "painel"})


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


@app.get("/api/cargas", dependencies=[Depends(require_auth_api)])
def api_cargas(
    data_inicial: str | None = None,
    data_final: str | None = None,
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


@app.post("/api/sync/run", dependencies=[Depends(require_auth_api)])
def api_sync_run():
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
