import datetime
import io
import json
import logging
import re
import unicodedata
from contextlib import asynccontextmanager
from copy import copy

import openpyxl
from openpyxl.styles import PatternFill
import requests
from fastapi import Depends, FastAPI, Form, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import chave_cte, config, relatorios_bipagem
from .prazo_comprovante import prazo_comprovante
from .db import get_db
from .models import Bipagem, Carga, ConsultaItem, ConsultaJob, Meta, ProtocoloFatura
from .security import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    create_session_cookie,
    VIA_LINK,
    VIA_SENHA,
    create_share_token,
    is_authenticated,
    require_acesso_completo_page,
    require_auth_api,
    require_auth_page,
    tem_acesso_completo,
    verify_share_token,
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
# Usado no menu (base.html) pra esconder as telas restritas de quem entrou
# pelo link de acesso.
templates.env.globals["tem_acesso_completo"] = tem_acesso_completo


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
    token = create_session_cookie(VIA_SENHA)
    resp = RedirectResponse(url="/painel", status_code=303)
    resp.set_cookie(COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/acesso/{token}")
def acesso_via_link(token: str):
    """Login automático via link compartilhável (sem digitar senha) -- ver
    botão "Copiar link de acesso" no painel."""
    if not verify_share_token(token):
        return RedirectResponse(url="/login")
    session_token = create_session_cookie(VIA_LINK)
    resp = RedirectResponse(url="/painel", status_code=303)
    resp.set_cookie(COOKIE_NAME, session_token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.get("/api/link-acesso", dependencies=[Depends(require_auth_api)])
def api_link_acesso(request: Request):
    token = create_share_token()
    url = str(request.base_url).rstrip("/") + f"/acesso/{token}"
    return {"url": url}


# ------------------------------------------------------------- painel -----

@app.get("/painel", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
def painel_page(request: Request):
    return templates.TemplateResponse("painel.html", {"request": request, "active": "painel"})


@app.get("/comprovantes", response_class=HTMLResponse, dependencies=[Depends(require_acesso_completo_page)])
def comprovantes_page(request: Request):
    """Quantos CT-e têm comprovante de entrega no GW (coluna "Data
    Comprovante" do relatório) e quantos ainda faltam -- usa os mesmos dados
    de /api/cargas, a conta é feita no navegador (static/comprovantes.js).
    Só pra quem entrou com a senha (não aparece pra quem veio pelo link de
    acesso)."""
    return templates.TemplateResponse("comprovantes.html", {"request": request, "active": "comprovantes"})


def _row_to_dict(r: Carga) -> dict:
    prazo_dias, distancia_km = prazo_comprovante(r.cidade_dest, r.uf_dest)
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
        "serie": r.serie,
        "filial": r.filial,
        "cnpj_filial": r.cnpj_filial,
        "notas_fiscais": r.notas_fiscais,
        "prazo_comprovante_dias": prazo_dias,
        "distancia_km": distancia_km,
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


def _dispatch_workflow(workflow_file: str, inputs: dict | None = None):
    """Dispara um workflow do GitHub Actions (workflow_dispatch).

    O app web (Vercel) não roda a automação de navegador diretamente -- ela
    precisa de um Chromium instalado, inviável numa função serverless. Isso
    só enfileira o job no GitHub; quem processa é o runner do Actions.
    """
    if not config.GITHUB_REPO or not config.GITHUB_DISPATCH_TOKEN:
        return {"ok": False, "erro": "GITHUB_REPO/GITHUB_DISPATCH_TOKEN não configurados nesta implantação."}
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/actions/workflows/{workflow_file}/dispatches"
    headers = {
        "Authorization": f"Bearer {config.GITHUB_DISPATCH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    body = {"ref": "main"}
    if inputs:
        body["inputs"] = inputs
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=15)
        if resp.status_code >= 300:
            return {"ok": False, "erro": f"GitHub respondeu {resp.status_code}: {resp.text[:300]}"}
        return {"ok": True}
    except requests.RequestException as exc:
        return {"ok": False, "erro": str(exc)}


@app.post("/api/sync/run", dependencies=[Depends(require_auth_api)])
def api_sync_run():
    """Dispara a sincronização periódica fora do horário agendado.

    Assíncrono -- o resultado aparece no painel após o workflow terminar
    (~1-2 min), não instantaneamente.
    """
    resultado = _dispatch_workflow("sync.yml")
    if not resultado["ok"]:
        return JSONResponse(resultado, status_code=502 if "GitHub respondeu" in resultado.get("erro", "") else 500)
    return {"ok": True, "queued": True}


# ------------------------------------------------------------ bipagem -----

@app.get("/bipagem", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
def bipagem_page(request: Request):
    return templates.TemplateResponse("bipagem.html", {"request": request, "active": "bipagem"})


def _resultado_bipagem(db: Session, leitura: str) -> dict:
    try:
        dados = chave_cte.decodificar(leitura)
    except chave_cte.ChaveInvalida as exc:
        return {"ok": False, "leitura": leitura, "erro": str(exc)}

    carga = relatorios_bipagem.buscar_carga(db, dados)
    resultado = {"ok": True, **dados, "encontrado": carga is not None}
    if carga:
        resultado.update({
            "cte": carga.cte,
            "notas_fiscais": carga.notas_fiscais,
            "cte_redespacho": carga.cte_redespacho,
            "emissao": carga.emissao,
            "remetente": carga.remetente,
            "consignatario": carga.consignatario,
            "cnpj_consignatario": carga.cnpj_consignatario,
            "destinatario": carga.destinatario,
            "cidade_dest": carga.cidade_dest,
            "uf_dest": carga.uf_dest,
            "status": carga.status,
            "estado": carga.estado,
        })
    return resultado


@app.post("/api/bipagem", dependencies=[Depends(require_auth_api)])
def api_bipagem_registrar(payload: dict, db: Session = Depends(get_db)):
    """Registra um bip -- o mesmo bip vale pro controle de comprovantes e
    pro protocolo de faturas. Repetir a mesma chave não cria outro registro:
    volta `duplicado` com a data do primeiro bip."""
    resultado = _resultado_bipagem(db, str(payload.get("leitura") or ""))
    if not resultado["ok"]:
        return resultado

    bip = (
        db.query(Bipagem)
        .filter(Bipagem.chave == resultado["chave"])
        .order_by(Bipagem.bipado_em.asc())
        .first()
    )
    resultado["duplicado"] = bip is not None
    if not bip:
        bip = Bipagem(
            chave=resultado["chave"],
            finalidade=relatorios_bipagem.FINALIDADE_GERAL,
            cnpj_filial=resultado["cnpj_emitente"],
            numero=resultado["numero"],
            serie=resultado["serie"],
        )
        db.add(bip)
        db.commit()
    resultado["bipado_em"] = bip.bipado_em.isoformat() + "Z"
    resultado["protocolo_id"] = bip.protocolo_id
    return resultado


@app.delete("/api/bipagem", dependencies=[Depends(require_auth_api)])
def api_bipagem_remover(chave: str, db: Session = Depends(get_db)):
    """Desfaz um bip (ex: bipado por engano) -- no controle de comprovantes
    o CT-e volta a ficar sem data de envio. Não desfaz bip que já entrou num
    protocolo de faturas (desfaça o protocolo antes)."""
    faturado = (
        db.query(Bipagem.protocolo_id)
        .filter(Bipagem.chave == chave, Bipagem.protocolo_id.isnot(None))
        .first()
    )
    if faturado:
        return JSONResponse(
            {"erro": f"Esse CT-e já está no protocolo de faturas nº {faturado[0]} -- desfaça o protocolo antes."},
            status_code=409,
        )
    db.query(Bipagem).filter(Bipagem.chave == chave).delete()
    db.commit()
    return {"ok": True}


def _xlsx_response(conteudo: bytes, nome: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(conteudo),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@app.get("/api/bipagem/tomadores", dependencies=[Depends(require_auth_api)])
def api_bipagem_tomadores(db: Session = Depends(get_db)):
    return {"tomadores": relatorios_bipagem.tomadores_disponiveis(db)}


@app.get("/api/bipagem/controle", dependencies=[Depends(require_auth_api)])
def api_bipagem_controle(tomador: str, mes: str, responsavel: str = "", db: Session = Depends(get_db)):
    """Controle mensal de comprovantes (modelo "SETEMBRO - GUANABARA") --
    `tomador` é a raiz (8 dígitos) do CNPJ ou "nome:<nome>" (ver
    `tomadores_disponiveis`), `mes` no formato AAAA-MM."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", mes or "")
    prefixo = relatorios_bipagem.PREFIXO_TOMADOR_NOME
    if tomador.startswith(prefixo) and tomador[len(prefixo):].strip():
        tomador_id, sufixo = tomador, "".join(c for c in tomador[len(prefixo):] if c.isalnum())[:30]
    else:
        tomador_id = sufixo = "".join(c for c in tomador if c.isdigit())[:8]
        if len(tomador_id) != 8:
            tomador_id = None
    if not m or not 1 <= int(m.group(2)) <= 12 or not tomador_id:
        return JSONResponse({"erro": "Informe o tomador e o mês (AAAA-MM)."}, status_code=400)
    ano, mes_num = int(m.group(1)), int(m.group(2))
    conteudo = relatorios_bipagem.gerar_controle_comprovantes(db, tomador_id, ano, mes_num, responsavel.strip())
    return _xlsx_response(conteudo, f"controle_comprovantes_{sufixo}_{mes}.xlsx")


def _protocolo_to_dict(p: ProtocoloFatura) -> dict:
    return {
        "id": p.id,
        # "Z": gravado em UTC -- sem isso o navegador mostra 3h adiantado.
        "criado_em": p.criado_em.isoformat() + "Z" if p.criado_em else None,
        "tomador_id": p.tomador_id,
        "tomador_nome": p.tomador_nome,
        "quantidade": p.quantidade,
    }


@app.get("/api/bipagem/faturas/pendentes", dependencies=[Depends(require_auth_api)])
def api_faturas_pendentes(db: Session = Depends(get_db)):
    """CT-e bipados que ainda não entraram em nenhum protocolo de faturas."""
    return {"pendentes": relatorios_bipagem.faturas_pendentes(db)}


@app.post("/api/bipagem/faturas/protocolos", dependencies=[Depends(require_auth_api)])
def api_faturas_gerar_protocolo(payload: dict, db: Session = Depends(get_db)):
    """Gera o protocolo de envio de faturas (modelo "ENVIO DE COMPROVANTE -
    PETROCARGAS") com os CT-e pendentes do tomador e dá baixa neles -- o
    próximo protocolo não leva de novo os mesmos comprovantes. O arquivo
    fica guardado; baixe por `/api/bipagem/faturas/protocolos/{id}/arquivo`."""
    tomador = str(payload.get("tomador") or "")
    protocolo = relatorios_bipagem.criar_protocolo_faturas(db, tomador)
    if not protocolo:
        return JSONResponse({"erro": "Nenhum CT-e pendente de protocolo para esse tomador."}, status_code=404)
    chaves = [b.chave for b in db.query(Bipagem).filter(Bipagem.protocolo_id == protocolo.id)]
    return {**_protocolo_to_dict(protocolo), "chaves": chaves}


@app.get("/api/bipagem/faturas/protocolos", dependencies=[Depends(require_auth_api)])
def api_faturas_protocolos(db: Session = Depends(get_db)):
    protocolos = db.query(ProtocoloFatura).order_by(ProtocoloFatura.criado_em.desc()).limit(50).all()
    return {"protocolos": [_protocolo_to_dict(p) for p in protocolos]}


@app.get("/api/bipagem/faturas/protocolos/{protocolo_id}/arquivo", dependencies=[Depends(require_auth_api)])
def api_faturas_protocolo_arquivo(protocolo_id: int, db: Session = Depends(get_db)):
    p = db.query(ProtocoloFatura).filter(ProtocoloFatura.id == protocolo_id).first()
    if not p or not p.arquivo:
        return JSONResponse({"erro": "Protocolo não encontrado."}, status_code=404)
    nome = "".join(c for c in (p.tomador_nome or "") if c.isalnum() or c == " ").strip().replace(" ", "_")[:40]
    data = relatorios_bipagem.data_br(p.criado_em).isoformat()
    # `preservar_espacos` de novo: protocolos gerados antes dessa correção
    # ficaram guardados com o defeito que faz o Excel pedir pra reparar.
    arquivo = relatorios_bipagem.preservar_espacos(p.arquivo)
    return _xlsx_response(arquivo, f"protocolo_faturas_{p.id}_{nome}_{data}.xlsx")


@app.delete("/api/bipagem/faturas/protocolos/{protocolo_id}", dependencies=[Depends(require_auth_api)])
def api_faturas_desfazer_protocolo(protocolo_id: int, db: Session = Depends(get_db)):
    """Desfaz um protocolo gerado por engano -- os CT-e dele voltam a ficar
    pendentes de protocolo."""
    if not relatorios_bipagem.desfazer_protocolo(db, protocolo_id):
        return JSONResponse({"erro": "Protocolo não encontrado."}, status_code=404)
    return {"ok": True}


_COLUNAS_BIPAGEM = [
    ("Chave de acesso", "chave"),
    ("Tipo", "tipo"),
    ("CT-e", "numero"),
    ("Série", "serie"),
    ("CNPJ emitente", "cnpj_emitente_fmt"),
    ("NF", "notas_fiscais"),
    ("CT-e parceiro", "cte_redespacho"),
    ("Emissão", "emissao"),
    ("Remetente", "remetente"),
    ("Destinatário", "destinatario"),
    ("Cidade destino", "cidade_dest"),
    ("UF", "uf_dest"),
    ("Status", "status"),
    ("Na base?", "encontrado"),
]


@app.post("/api/bipagem/exportar", dependencies=[Depends(require_auth_api)])
def api_bipagem_exportar(payload: dict, db: Session = Depends(get_db)):
    leituras = [str(x) for x in (payload.get("leituras") or [])][:2000]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Bipagem"
    ws.append([titulo for titulo, _ in _COLUNAS_BIPAGEM])
    for leitura in leituras:
        r = _resultado_bipagem(db, leitura)
        if not r["ok"]:
            linha = [None] * len(_COLUNAS_BIPAGEM)
            linha[0], linha[-2], linha[-1] = leitura, r["erro"], "Não"
            ws.append(linha)
            continue
        linha = []
        for _, campo in _COLUNAS_BIPAGEM:
            v = r.get(campo)
            linha.append(("Sim" if v else "Não") if campo == "encontrado" else v)
        ws.append(linha)
    for col, largura in zip("ABCDEFGHIJKLMN", (48, 8, 10, 7, 20, 18, 16, 12, 34, 34, 20, 5, 8, 9)):
        ws.column_dimensions[col].width = largura
    # Chave como texto -- senão o Excel mostra em notação científica.
    for (celula,) in ws.iter_rows(min_row=2, min_col=1, max_col=1):
        celula.number_format = "@"

    buffer = io.BytesIO()
    wb.save(buffer)
    return _xlsx_response(buffer.getvalue(), f"bipagem_{datetime.date.today().isoformat()}.xlsx")


# --------------------------------------------------- consulta de notas ----

@app.get("/consultas", response_class=HTMLResponse, dependencies=[Depends(require_auth_page)])
def consultas_page(request: Request):
    return templates.TemplateResponse("consultas.html", {"request": request, "active": "consultas"})


# Tempo máximo que um job pode ficar "pendente" (workflow disparado, mas o
# run_consulta.py ainda não começou). O GitHub cancela por conta própria um
# job que não consegue máquina após ~15 min, e nesse caso nada atualiza o
# banco -- sem isso a linha ficaria "Na fila" para sempre.
_MINUTOS_MAX_PENDENTE = 20
# Tempo máximo sem sinal de vida de um job "processando" -- o script avisa a
# cada NF consultada (poucos segundos cada), então 15 min parado = morreu.
_MINUTOS_MAX_SEM_SINAL = 15


def _expirar_jobs_sem_iniciar(db: Session) -> None:
    limite = datetime.datetime.utcnow() - datetime.timedelta(minutes=_MINUTOS_MAX_PENDENTE)
    expirados = (
        db.query(ConsultaJob)
        .filter(ConsultaJob.status == "pendente", ConsultaJob.criado_em < limite)
        .all()
    )
    for job in expirados:
        job.status = "erro"
        job.erro_mensagem = (
            f"A consulta não iniciou em {_MINUTOS_MAX_PENDENTE} minutos (o GitHub Actions "
            "não alocou uma máquina). Exclua e envie a planilha novamente."
        )
    limite_sinal = datetime.datetime.utcnow() - datetime.timedelta(minutes=_MINUTOS_MAX_SEM_SINAL)
    parados = (
        db.query(ConsultaJob)
        .filter(ConsultaJob.status == "processando", ConsultaJob.ultimo_sinal < limite_sinal)
        .all()
    )
    for job in parados:
        job.status = "erro"
        job.erro_mensagem = (
            f"A consulta parou de responder há mais de {_MINUTOS_MAX_SEM_SINAL} minutos "
            "(o processamento no GitHub Actions foi interrompido). Exclua e envie a planilha novamente."
        )
    if expirados or parados:
        db.commit()


def _job_to_dict(job: ConsultaJob) -> dict:
    return {
        "id": job.id,
        # "Z" no final: criado_em é gravado em UTC (datetime.utcnow), mas
        # sem isso o navegador interpreta a string como horário local e
        # exibe 3h adiantado (sem descontar o fuso de Brasília).
        "criado_em": job.criado_em.isoformat() + "Z" if job.criado_em else None,
        "nome_arquivo": job.nome_arquivo,
        "status": job.status,
        "total_itens": job.total_itens,
        "processados": job.processados,
        "erro_mensagem": job.erro_mensagem,
    }


@app.get("/api/consultas", dependencies=[Depends(require_auth_api)])
def api_consultas_list(db: Session = Depends(get_db)):
    _expirar_jobs_sem_iniciar(db)
    jobs = db.query(ConsultaJob).order_by(ConsultaJob.criado_em.desc()).limit(30).all()
    return {"jobs": [_job_to_dict(j) for j in jobs]}


@app.get("/api/consultas/{job_id}", dependencies=[Depends(require_auth_api)])
def api_consultas_status(job_id: int, db: Session = Depends(get_db)):
    _expirar_jobs_sem_iniciar(db)
    job = db.query(ConsultaJob).filter(ConsultaJob.id == job_id).first()
    if not job:
        return JSONResponse({"erro": "Job não encontrado."}, status_code=404)
    return _job_to_dict(job)


@app.delete("/api/consultas/{job_id}", dependencies=[Depends(require_auth_api)])
def api_consultas_delete(job_id: int, db: Session = Depends(get_db)):
    job = db.query(ConsultaJob).filter(ConsultaJob.id == job_id).first()
    if not job:
        return JSONResponse({"erro": "Job não encontrado."}, status_code=404)
    db.query(ConsultaItem).filter(ConsultaItem.job_id == job_id).delete()
    db.delete(job)
    db.commit()
    return {"ok": True}


def _primeira_nf(valor) -> str:
    """Às vezes a célula tem mais de uma NF (mesmo conhecimento, notas
    diferentes), ex.: "59185 / 59186" -- usa só a primeira para a consulta
    (o valor original completo continua preservado em `linha_original`)."""
    texto = str(valor).strip()
    return re.split(r"[/,;-]", texto)[0].strip()


def _normalizar_cabecalho(s) -> str:
    texto = unicodedata.normalize("NFD", str(s or ""))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.lower()
    texto = re.sub(r"[-_/]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _detectar_coluna(cabecalho: list[str], testar) -> str | None:
    for c in cabecalho:
        if testar(_normalizar_cabecalho(c)):
            return c
    return None


def _detectar_coluna_nf(cabecalho: list[str]) -> str | None:
    return _detectar_coluna(cabecalho, lambda n: re.search(r"\bnotas?\b|\bnf\b", n) is not None)


def _detectar_coluna_remetente(cabecalho: list[str]) -> str | None:
    return _detectar_coluna(cabecalho, lambda n: re.search(r"\bremetente\b", n) is not None)


def _detectar_coluna_destinatario(cabecalho: list[str]) -> str | None:
    def testar(n: str) -> bool:
        if re.search(r"\bdestinatario\b", n):
            return True
        # Padrão comum em exportações do próprio GW: "NM_ENTREGA_..._CLIENTES".
        return "entrega" in n and "cliente" in n

    return _detectar_coluna(cabecalho, testar)


def _detectar_coluna_tomador(cabecalho: list[str]) -> str | None:
    """Tomador do serviço (quem contrata/paga o frete) -- ex: "Cliente
    Pagador", "Tomador". Não é extraído da tela do GW (que só mostra
    remetente/destinatário/consignatário), mas na prática costuma ser um
    deles, então serve como mais um candidato pra validar o CT-e achado."""
    return _detectar_coluna(cabecalho, lambda n: re.search(r"\btomador\b|\bpagador\b", n) is not None)


def _detectar_coluna_cliente(cabecalho: list[str]) -> str | None:
    """Fallback quando a planilha nem separa remetente/destinatário/tomador
    -- só tem uma coluna genérica "Cliente" dizendo de quem é o embarque."""
    return _detectar_coluna(cabecalho, lambda n: re.search(r"\bcliente\b", n) is not None)


@app.post("/api/consultas", dependencies=[Depends(require_auth_api)])
async def api_consultas_create(
    arquivo: UploadFile,
    db: Session = Depends(get_db),
):
    conteudo = await arquivo.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
    except Exception:
        return JSONResponse({"erro": "Não consegui ler o arquivo. Confirme que é um .xlsx válido."}, status_code=400)

    job = ConsultaJob(
        nome_arquivo=arquivo.filename,
        status="pendente",
        arquivo_original=conteudo,
    )
    db.add(job)
    db.flush()

    # Cada aba/guia é conferida e processada separadamente -- se a
    # planilha tiver mais de uma, todas com coluna de nota fiscal
    # detectável entram na consulta (não só a primeira/ativa).
    colunas_nf_detectadas: list[str] = []
    total = 0
    for nome_aba in wb.sheetnames:
        ws = wb[nome_aba]
        linhas = list(ws.iter_rows(values_only=True))
        if not linhas:
            continue

        cabecalho = [str(c).strip() if c is not None else "" for c in linhas[0]]
        coluna_nf = _detectar_coluna_nf(cabecalho)
        if not coluna_nf:
            continue  # essa aba não tem coluna de nota fiscal -- pula
        idx_nf = cabecalho.index(coluna_nf)
        colunas_nf_detectadas.append(coluna_nf)

        colunas_validacao = [
            _detectar_coluna_remetente(cabecalho),
            _detectar_coluna_destinatario(cabecalho),
            _detectar_coluna_tomador(cabecalho),
            _detectar_coluna_cliente(cabecalho),
        ]

        for i, row in enumerate(linhas[1:], start=1):
            if row is None or all(c is None for c in row):
                continue
            nf_valor = row[idx_nf] if idx_nf < len(row) else None
            if nf_valor is None or str(nf_valor).strip() == "":
                continue
            linha_dict = {cabecalho[c]: row[c] for c in range(len(cabecalho)) if cabecalho[c] and c < len(row)}
            nomes = [
                str(linha_dict[coluna]) for coluna in colunas_validacao
                if coluna and linha_dict.get(coluna) not in (None, "")
            ]
            item = ConsultaItem(
                job_id=job.id,
                aba=nome_aba,
                linha_idx=i,
                linha_original=json.dumps(linha_dict, ensure_ascii=False, default=str),
                nf_numero=_primeira_nf(nf_valor),
                nomes_esperados=json.dumps(nomes, ensure_ascii=False) if nomes else None,
            )
            db.add(item)
            total += 1

    if not colunas_nf_detectadas:
        db.rollback()
        abas = ", ".join(wb.sheetnames)
        return JSONResponse(
            {"erro": f"Não encontrei uma coluna de nota fiscal em nenhuma aba da planilha (abas: {abas})."},
            status_code=400,
        )
    if total == 0:
        db.rollback()
        return JSONResponse({"erro": "Nenhuma linha com nota fiscal preenchida na planilha."}, status_code=400)

    job.coluna_nf = ", ".join(sorted(set(colunas_nf_detectadas)))
    job.total_itens = total
    db.commit()

    resultado = _dispatch_workflow("consulta.yml", inputs={"job_id": str(job.id)})
    if not resultado["ok"]:
        job.status = "erro"
        job.erro_mensagem = resultado["erro"][:490]
        db.commit()
        return JSONResponse({"erro": resultado["erro"]}, status_code=502)

    return {"ok": True, "job_id": job.id, "total_itens": total}


_COLUNAS_NOVAS = ["CT-e", "Status Entrega", "Previsão Entrega", "Data Entrega", "Encontrado?"]
# Azul, Ênfase 1, mais claro 60% -- cor padrão do tema do Excel/Office.
_FILL_DADOS_GW = PatternFill(fill_type="solid", start_color="FFB4C7E7", end_color="FFB4C7E7")


def _clonar_estilo_linha(ws, linha_origem: int, linha_destino: int, num_colunas: int):
    """Copia a formatação (fonte, borda, preenchimento, alinhamento) de uma
    linha para outra -- usado ao inserir linhas extras (NF com mais de um
    CT-e), pra manter o visual igual ao da linha original."""
    for col in range(1, num_colunas + 1):
        origem = ws.cell(row=linha_origem, column=col)
        destino = ws.cell(row=linha_destino, column=col)
        destino.font = copy(origem.font)
        destino.border = copy(origem.border)
        destino.fill = copy(origem.fill)
        destino.alignment = copy(origem.alignment)
        destino.number_format = origem.number_format
    if linha_origem in ws.row_dimensions:
        ws.row_dimensions[linha_destino].height = ws.row_dimensions[linha_origem].height


@app.get("/api/consultas/{job_id}/arquivo", dependencies=[Depends(require_auth_api)])
def api_consultas_arquivo(job_id: int, db: Session = Depends(get_db)):
    job = db.query(ConsultaJob).filter(ConsultaJob.id == job_id).first()
    if not job:
        return JSONResponse({"erro": "Job não encontrado."}, status_code=404)
    if not job.arquivo_original:
        return JSONResponse({"erro": "Arquivo original desta consulta não está mais disponível."}, status_code=404)

    itens = (
        db.query(ConsultaItem)
        .filter(ConsultaItem.job_id == job_id)
        .order_by(ConsultaItem.linha_idx.asc(), ConsultaItem.id.asc())
        .all()
    )

    wb = openpyxl.load_workbook(io.BytesIO(job.arquivo_original))

    # Agrupa por aba e, dentro de cada aba, por linha_idx (linha original
    # daquela aba) preservando a ordem de inserção -- cada grupo pode ter
    # mais de um item (NF com mais de um CT-e). `aba` é `None` em consultas
    # antigas (de antes de suportar múltiplas abas) -- trata como a
    # primeira/única aba da planilha.
    por_aba: dict[str, dict[int, list[ConsultaItem]]] = {}
    for item in itens:
        aba = item.aba or wb.sheetnames[0]
        por_aba.setdefault(aba, {}).setdefault(item.linha_idx, []).append(item)

    for aba, grupos in por_aba.items():
        if aba not in wb.sheetnames:
            continue  # aba renomeada/removida desde o upload -- ignora com segurança
        ws = wb[aba]
        max_col_original = ws.max_column

        cabecalho_estilo = ws.cell(row=1, column=max_col_original)
        for offset, titulo in enumerate(_COLUNAS_NOVAS, start=1):
            celula = ws.cell(row=1, column=max_col_original + offset, value=titulo)
            celula.font = copy(cabecalho_estilo.font)
            celula.border = copy(cabecalho_estilo.border)
            celula.fill = copy(cabecalho_estilo.fill)
            celula.alignment = copy(cabecalho_estilo.alignment)
            ws.column_dimensions[celula.column_letter].width = 16

        total_colunas = max_col_original + len(_COLUNAS_NOVAS)

        def _escrever_resultado(linha_planilha: int, item: ConsultaItem, ws=ws, max_col_original=max_col_original):
            valores = [item.cte, item.status_entrega, item.previsao_entrega, item.data_entrega, "Sim" if item.encontrado else "Não"]
            for offset, valor in enumerate(valores, start=1):
                celula = ws.cell(row=linha_planilha, column=max_col_original + offset, value=valor)
                celula.fill = _FILL_DADOS_GW

        # Processa das últimas linhas para as primeiras: inserir linhas
        # extras (NF com mais de um CT-e) desloca tudo abaixo, então
        # precisa ir de baixo pra cima para não bagunçar a posição das
        # linhas ainda não processadas.
        for linha_idx in sorted(grupos.keys(), reverse=True):
            grupo = grupos[linha_idx]
            linha_planilha = linha_idx + 1  # linha 1 é o cabeçalho
            primeiro, *extras = grupo
            _escrever_resultado(linha_planilha, primeiro)

            valores_originais = [ws.cell(row=linha_planilha, column=c).value for c in range(1, max_col_original + 1)]
            insercao = linha_planilha + 1
            for extra in extras:
                ws.insert_rows(insercao)
                _clonar_estilo_linha(ws, linha_planilha, insercao, total_colunas)
                for c, valor in enumerate(valores_originais, start=1):
                    ws.cell(row=insercao, column=c, value=valor)
                _escrever_resultado(insercao, extra)
                insercao += 1

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    nome_saida = f"consulta_{job_id}_resultado.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome_saida}"'},
    )
