"""Histórico diário dos números: a cada sincronização grava uma "foto" do
dia (a última do dia vale) -- total, pendentes, vencidos e entregues no
prazo / fora do prazo --, geral e por tomador. Dá as tendências da tela
Histórico e a variação "em relação a ontem" do resumo diário por e-mail.

O que conta como cada coisa é o mesmo do Painel (static/dashboard.js):
pendente = status "PE", vencido = estado "critical", entregue no prazo =
"DP", entregue fora do prazo = "FPE".
"""

import datetime
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from .db import get_db
from .models import Carga, HistoricoDia, HistoricoTomador
from .security import escopo_empresas, require_auth_api

logger = logging.getLogger("historico")

BRASILIA = datetime.timezone(datetime.timedelta(hours=-3))
GUARDAR_DIAS = 400
SEM_TOMADOR = "(SEM TOMADOR)"

router = APIRouter()


def hoje_brasilia() -> datetime.date:
    return datetime.datetime.now(BRASILIA).date()


def cond_em_rota():
    """Pendente com manifesto ou romaneio: saiu pra entrega e está em rota até
    ser baixado."""
    return and_(Carga.status == "PE", or_(Carga.manifesto.is_(True), Carga.romaneio.is_(True)))


def cond_em_deposito():
    """Pendente sem manifesto nem romaneio: ainda parado no depósito."""
    return and_(Carga.status == "PE", ~or_(Carga.manifesto.is_(True), Carga.romaneio.is_(True)))


def _contagens():
    """Colunas de contagem do relatório, iguais às do Painel (e, no fim, as
    duas que separam os pendentes em depósito x em rota)."""
    def soma(condicao):
        return func.coalesce(func.sum(case((condicao, 1), else_=0)), 0)

    return (
        func.count(),
        soma(Carga.status == "PE"),
        soma(Carga.estado == "critical"),
        soma(Carga.status == "DP"),
        soma(Carga.status == "FPE"),
        soma(cond_em_deposito()),
        soma(cond_em_rota()),
    )


def registrar(db: Session, dia: datetime.date | None = None) -> int:
    """Grava (ou regrava) a foto do dia a partir das cargas atuais.
    Devolve quantos tomadores entraram."""
    dia = dia or hoje_brasilia()
    chave = dia.isoformat()

    nome = func.upper(func.trim(func.coalesce(Carga.consignatario, "")))
    linhas = db.query(nome, *_contagens()).group_by(nome).all()

    db.query(HistoricoTomador).filter(HistoricoTomador.data == chave).delete()
    db.query(HistoricoDia).filter(HistoricoDia.data == chave).delete()

    geral = [0] * 7
    for tomador, *contagem in linhas:
        valores = [int(v) for v in contagem]
        geral = [a + b for a, b in zip(geral, valores)]
        db.add(HistoricoTomador(
            data=chave, tomador=(tomador or SEM_TOMADOR)[:255], total=valores[0], pendentes=valores[1],
            vencidos=valores[2], entregues_no_prazo=valores[3], entregues_fora=valores[4],
            em_deposito=valores[5], em_rota=valores[6],
        ))
    db.add(HistoricoDia(
        data=chave, total=geral[0], pendentes=geral[1], vencidos=geral[2],
        entregues_no_prazo=geral[3], entregues_fora=geral[4], em_deposito=geral[5], em_rota=geral[6],
    ))

    limite = (dia - datetime.timedelta(days=GUARDAR_DIAS)).isoformat()
    db.query(HistoricoTomador).filter(HistoricoTomador.data < limite).delete()
    db.query(HistoricoDia).filter(HistoricoDia.data < limite).delete()
    db.commit()
    return len(linhas)


def registrar_sem_falhar(db: Session) -> None:
    """Chamado ao fim da sincronização: um problema aqui não pode derrubá-la."""
    try:
        n = registrar(db)
        logger.info("Histórico do dia gravado (%d tomadores).", n)
    except Exception:
        db.rollback()
        logger.exception("Falha ao gravar o histórico diário (ignorando).")


# ----------------------------------------------------------------- consultas

def _linha_dict(data, total, pend, venc, no_prazo, fora, deposito=None, rota=None) -> dict:
    deposito, rota = int(deposito or 0), int(rota or 0)
    # Dias gravados antes da separação em depósito x em rota têm os dois em
    # zero mesmo com pendentes: sem dado (None), não "zero parados".
    sem_dado = int(pend or 0) > 0 and deposito + rota == 0
    return {
        "data": data, "total": int(total or 0), "pendentes": int(pend or 0), "vencidos": int(venc or 0),
        "entregues_no_prazo": int(no_prazo or 0), "entregues_fora": int(fora or 0),
        "em_deposito": None if sem_dado else deposito, "em_rota": None if sem_dado else rota,
    }


def serie(db: Session, dias: int, empresas: set[str] | None) -> list[dict]:
    """Um ponto por dia gravado, dos últimos `dias` dias. Com `empresas`
    (analista), soma só os tomadores dele."""
    desde = (hoje_brasilia() - datetime.timedelta(days=dias)).isoformat()
    if empresas is None:
        q = db.query(HistoricoDia).filter(HistoricoDia.data >= desde).order_by(HistoricoDia.data)
        return [_linha_dict(r.data, r.total, r.pendentes, r.vencidos, r.entregues_no_prazo, r.entregues_fora,
                            r.em_deposito, r.em_rota) for r in q]
    q = (
        db.query(
            HistoricoTomador.data, func.sum(HistoricoTomador.total), func.sum(HistoricoTomador.pendentes),
            func.sum(HistoricoTomador.vencidos), func.sum(HistoricoTomador.entregues_no_prazo),
            func.sum(HistoricoTomador.entregues_fora), func.sum(HistoricoTomador.em_deposito),
            func.sum(HistoricoTomador.em_rota),
        )
        .filter(HistoricoTomador.data >= desde, HistoricoTomador.tomador.in_(list(empresas) or [""]))
        .group_by(HistoricoTomador.data)
        .order_by(HistoricoTomador.data)
    )
    return [_linha_dict(*r) for r in q]


def _datas(db: Session) -> list[str]:
    return [d for (d,) in db.query(HistoricoDia.data).order_by(HistoricoDia.data.desc()).limit(120).all()]


def _foto_por_tomador(db: Session, data: str, empresas: set[str] | None) -> dict[str, dict]:
    q = db.query(HistoricoTomador).filter(HistoricoTomador.data == data)
    if empresas is not None:
        q = q.filter(HistoricoTomador.tomador.in_(list(empresas) or [""]))
    return {r.tomador: _linha_dict(r.data, r.total, r.pendentes, r.vencidos, r.entregues_no_prazo, r.entregues_fora,
                                   r.em_deposito, r.em_rota) for r in q}


def ranking(db: Session, comparar_dias: int, empresas: set[str] | None, limite: int = 30) -> dict:
    """Tomadores com mais vencidos hoje e a variação contra a foto de
    `comparar_dias` dias atrás (ou a mais antiga que existir, se o histórico
    ainda for curto)."""
    datas = _datas(db)
    if not datas:
        return {"data_atual": None, "data_comparacao": None, "itens": []}
    atual = datas[0]
    alvo = (datetime.date.fromisoformat(atual) - datetime.timedelta(days=comparar_dias)).isoformat()
    anteriores = [d for d in datas[1:] if d <= alvo]
    comparacao = anteriores[0] if anteriores else (datas[-1] if len(datas) > 1 else None)

    agora = _foto_por_tomador(db, atual, empresas)
    antes = _foto_por_tomador(db, comparacao, empresas) if comparacao else {}
    itens = []
    for nome, r in agora.items():
        if not (r["pendentes"] or r["vencidos"]):
            continue
        a = antes.get(nome, {})
        itens.append({
            "tomador": nome, "pendentes": r["pendentes"], "vencidos": r["vencidos"],
            "pendentes_antes": a.get("pendentes") if comparacao else None,
            "vencidos_antes": a.get("vencidos") if comparacao else None,
        })
    itens.sort(key=lambda i: (-i["vencidos"], -i["pendentes"], i["tomador"]))
    return {"data_atual": atual, "data_comparacao": comparacao, "itens": itens[:limite]}


@router.get("/api/historico", dependencies=[Depends(require_auth_api)])
def api_historico(request: Request, dias: int = 30, comparar: int = 7, db: Session = Depends(get_db)):
    dias = max(7, min(dias, GUARDAR_DIAS))
    comparar = max(1, min(comparar, 60))
    empresas = escopo_empresas(request)
    return {"serie": serie(db, dias, empresas), "ranking": ranking(db, comparar, empresas)}
