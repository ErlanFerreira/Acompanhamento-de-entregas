"""Bips da tela de Bipagem para o Painel: o selo verde "comprovante bipado" ao lado
do CT-e.

Um bip registra que o comprovante daquele CT-e foi recebido e lido (`Bipagem`); o
controle mensal de comprovantes usa o primeiro bip como "Recebido". Aqui só sai o
necessário para o Painel ligar bip a CT-e no navegador: filial, número, série
(normalizada) e o instante do primeiro bip -- sem nomes de clientes.

A ligação com o CT-e segue a regra do controle de comprovantes (filial + número +
série; CT-e sem série na base casa com qualquer série): ver static/bipados.js.

Separado de /api/cargas (que é guardado pronto e muda só a cada sincronização)
porque um bip novo tem que aparecer na hora.
"""

import datetime
import hashlib
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import get_db
from .models import Bipagem
from .relatorios_bipagem import serie_normalizada
from .security import exigir_tela_api

router = APIRouter()

# Sem `desde` (período "todas as datas"): só os bips dos últimos dias.
DIAS_SEM_PERIODO = 120
LIMITE = 50_000

# Resposta pronta por (desde, versão dos bips) -- a versão muda a cada bip novo ou desfeito.
_CACHE: dict = {}
_CACHE_MAX = 6


def _inicio(desde: str | None) -> datetime.datetime:
    """Bips anteriores à emissão mais antiga carregada não servem a nenhum CT-e dela:
    o bip vem depois da emissão."""
    try:
        return datetime.datetime.combine(datetime.date.fromisoformat(str(desde)), datetime.time.min)
    except ValueError:
        return datetime.datetime.utcnow() - datetime.timedelta(days=DIAS_SEM_PERIODO)


def _bips_do_periodo(db: Session, inicio: datetime.datetime) -> list[list]:
    """[filial, número, série normalizada ou "", instante UTC] do PRIMEIRO bip de cada CT-e."""
    vistos: set = set()
    saida: list[list] = []
    consulta = (
        db.query(Bipagem.cnpj_filial, Bipagem.numero, Bipagem.serie, Bipagem.bipado_em)
        .filter(Bipagem.bipado_em >= inicio, Bipagem.numero.isnot(None))
        .order_by(Bipagem.bipado_em.asc())
        .limit(LIMITE)
    )
    for filial, numero, serie, bipado_em in consulta:
        serie_n = serie_normalizada(serie) or ""
        chave = (filial, numero, serie_n)
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append([filial, numero, serie_n, bipado_em.isoformat() + "Z"])
    return saida


@router.get("/api/bipados", dependencies=[Depends(exigir_tela_api("painel", "comprovantes"))])
def api_bipados(request: Request, desde: str | None = None, db: Session = Depends(get_db)):
    quantos, ultimo_id = db.query(func.count(Bipagem.id), func.max(Bipagem.id)).one()
    versao = f"{quantos}-{ultimo_id}"
    etag = '"' + hashlib.sha1(repr((desde or "", versao)).encode()).hexdigest()[:32] + '"'
    cabecalhos = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cabecalhos)

    corpo = _CACHE.get((desde, versao))
    if corpo is None:
        corpo = json.dumps({"bips": _bips_do_periodo(db, _inicio(desde))}, separators=(",", ":")).encode("utf-8")
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[(desde, versao)] = corpo
    return Response(content=corpo, media_type="application/json", headers=cabecalhos)
