"""Sincronização com o relatório "Pendências" do portal Webtrans.

Ver `scrape.py` para o porquê disso usar automação de navegador em vez da
API "GW Serviços" (essa API só enxerga cargas onde o CNPJ logado é
remetente/destinatário -- não a operação completa da transportadora).
"""

import datetime
import logging
import re
import unicodedata
from io import BytesIO

import openpyxl
from sqlalchemy.orm import Session

from . import config
from .models import Carga, Meta
from .scrape import ScrapeError, baixar_relatorio_pendencias

logger = logging.getLogger("sync")

SyncError = ScrapeError  # mantém o nome usado pelo resto do app

# Índices das colunas no xlsx exportado pelo relatório personalizado "Pendências".
COL_EMISSAO = 0
COL_CTE = 1
COL_REMETENTE = 2
COL_CONSIGNATARIO = 4
COL_CNPJ_CONSIGNATARIO = 5
COL_CIDADE_CONSIGNATARIO = 6
COL_DESTINATARIO = 7
COL_CIDADE_DESTINATARIO = 9
COL_UF_DESTINATARIO = 10
COL_TEM_MANIFESTO = 11
COL_TEM_ROMANEIO = 12
COL_ULTIMA_OCORRENCIA = 13
COL_ENTREGUE = 15
COL_DATA_COMPROVANTE = 16
COL_DATA_BAIXA = 17
COL_STATUS_ENTREGA = 18
COL_PREVISAO_ENTREGA = 19
COL_CNPJ_FILIAL = 21

# Colunas localizadas pelo nome do cabeçalho (não pela posição), já que não
# existiam na versão original do relatório -- assim dá pra acrescentar
# colunas no relatório personalizado do GW sem quebrar os índices acima.
# Cada teste recebe o cabeçalho normalizado (minúsculo, sem acento).
COLUNAS_OPCIONAIS = {
    "cte_redespacho": lambda n: "redespacho" in n,
    "notas_fiscais": lambda n: re.search(r"\bnotas?\b|\bnf e?\b|\bnfs\b", n) is not None,
    "serie": lambda n: "serie" in n,
    "filial": lambda n: "filial" in n and "cnpj" not in n,
    "endereco_consignatario": lambda n: "endereco" in n and ("consig" in n or "tomador" in n),
}


def _normalizar_cabecalho(s) -> str:
    texto = unicodedata.normalize("NFD", str(s or ""))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[-_/.]+", " ", texto.lower())
    return re.sub(r"\s+", " ", texto).strip()


def detectar_colunas_opcionais(cabecalho) -> dict[str, int]:
    indices = {}
    for campo, testar in COLUNAS_OPCIONAIS.items():
        for i, c in enumerate(cabecalho):
            if i not in indices.values() and testar(_normalizar_cabecalho(c)):
                indices[campo] = i
                break
    return indices


def _so_digitos(s):
    return "".join(c for c in str(s or "") if c.isdigit())


def _data_iso(v):
    if isinstance(v, datetime.datetime):
        return v.date().isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    return None


def _sim(v):
    return str(v or "").strip().upper() == "SIM"


def _texto_ou_none(v):
    texto = str(v).strip() if v is not None else ""
    return texto or None


def mapear_linha_para_registro(row, referencia: str, opcionais: dict[str, int] | None = None) -> dict:
    cnpj_filial = _so_digitos(row[COL_CNPJ_FILIAL])
    cte = str(row[COL_CTE] or "").strip()
    id_cte = f"{cnpj_filial}:{cte}" if cnpj_filial else cte

    status = (row[COL_STATUS_ENTREGA] or "").strip()
    previsao = _data_iso(row[COL_PREVISAO_ENTREGA])
    data_comprovante = _data_iso(row[COL_DATA_COMPROVANTE])
    data_baixa = _data_iso(row[COL_DATA_BAIXA])

    dias_atraso = None
    if status == "FPE":
        estado = "serious"
        if previsao and data_comprovante:
            d1 = datetime.date.fromisoformat(data_comprovante)
            d2 = datetime.date.fromisoformat(previsao)
            dias_atraso = (d1 - d2).days
    elif status == "DP":
        estado = "good"
    else:  # PE
        vencido = bool(previsao) and previsao < referencia
        estado = "critical" if vencido else "warning"
        if previsao:
            d1 = datetime.date.fromisoformat(referencia)
            d2 = datetime.date.fromisoformat(previsao)
            dias_atraso = (d1 - d2).days

    extras = {
        campo: _texto_ou_none(row[idx]) if idx < len(row) else None
        for campo, idx in (opcionais or {}).items()
    }

    return {
        **extras,
        "id_cte": id_cte,
        "cte": cte,
        "cnpj_filial": cnpj_filial,
        "emissao": _data_iso(row[COL_EMISSAO]),
        "remetente": row[COL_REMETENTE] or "",
        "consignatario": row[COL_CONSIGNATARIO] or "",
        "cnpj_consignatario": _so_digitos(row[COL_CNPJ_CONSIGNATARIO]) or None,
        "cidade_cons": row[COL_CIDADE_CONSIGNATARIO] or None,
        "destinatario": row[COL_DESTINATARIO] or "",
        "cidade_dest": row[COL_CIDADE_DESTINATARIO] or "",
        "uf_dest": row[COL_UF_DESTINATARIO] or "",
        "manifesto": _sim(row[COL_TEM_MANIFESTO]),
        "romaneio": _sim(row[COL_TEM_ROMANEIO]),
        "ocorrencia": row[COL_ULTIMA_OCORRENCIA] or None,
        "entregue": _sim(row[COL_ENTREGUE]),
        "data_comprovante": data_comprovante,
        "data_baixa": data_baixa,
        "status": status,
        "estado": estado,
        "previsao": previsao,
        "dias_atraso": dias_atraso,
    }


def _set_meta(db: Session, chave: str, valor: str):
    m = db.query(Meta).filter(Meta.chave == chave).first()
    if m:
        m.valor = valor
    else:
        db.add(Meta(chave=chave, valor=valor))


def run_sync(db: Session, window_days: int = None) -> int:
    window_days = window_days or config.SYNC_WINDOW_DAYS
    hoje = datetime.date.today()
    data_inicial = hoje - datetime.timedelta(days=window_days)
    referencia = hoje.isoformat()

    _set_meta(db, "last_sync_status", "running")
    db.commit()

    try:
        conteudo = baixar_relatorio_pendencias(data_inicial, hoje)
        wb = openpyxl.load_workbook(BytesIO(conteudo), data_only=True)
        ws = wb.active
        cabecalho = next(ws.iter_rows(min_row=2, max_row=2, values_only=True), ())
        opcionais = detectar_colunas_opcionais(cabecalho)
        logger.info("Colunas opcionais detectadas no relatório: %s", opcionais)
        linhas = list(ws.iter_rows(min_row=3, values_only=True))

        registros_by_id = {}
        for row in linhas:
            if not row or not row[COL_CTE]:
                continue
            # Ignora CT-e cancelados (status "PEC"/"FPC" -- variantes
            # canceladas de PE/FPE) -- não são pendências reais.
            if (row[COL_STATUS_ENTREGA] or "").strip() not in ("DP", "FPE", "PE"):
                continue
            reg = mapear_linha_para_registro(row, referencia, opcionais)
            registros_by_id[reg["id_cte"]] = reg

        count = 0
        for reg in registros_by_id.values():
            existing = db.query(Carga).filter(Carga.id_cte == reg["id_cte"]).first()
            if existing:
                for k, v in reg.items():
                    setattr(existing, k, v)
            else:
                db.add(Carga(**reg))
            count += 1
        db.commit()

        _set_meta(db, "last_sync_at", datetime.datetime.utcnow().isoformat())
        _set_meta(db, "last_sync_count", str(count))
        _set_meta(db, "last_sync_status", "ok")
        db.commit()
        return count
    except Exception as exc:
        db.rollback()
        _set_meta(db, "last_sync_status", "erro")
        _set_meta(db, "last_sync_error", str(exc)[:250])
        db.commit()
        raise
