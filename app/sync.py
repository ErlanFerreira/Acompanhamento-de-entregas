"""Sincronização com a API "GW Serviços": autentica, busca cargas e grava no banco.

Lógica de autenticação e mapeamento validada manualmente em 21-08-2026 --
ver HANDOFF.md para o histórico de como as credenciais corretas foram
descobertas (não são o e-mail/senha do portal).
"""

import datetime
import logging

import requests
from sqlalchemy.orm import Session

from . import config
from .models import Carga, Meta

logger = logging.getLogger("sync")

BASE_URL = "https://api.saas.gwsistemas.com.br/webresources/v2/servicosGW"
TOKEN_URL = f"{BASE_URL}/solicitarToken"
LISTAR_CARGAS_URL = f"{BASE_URL}/listarCargas/"


class SyncError(Exception):
    pass


def solicitar_token() -> str:
    if not config.GWSERVICOS_LOGIN or not config.GWSERVICOS_SENHA or not config.GWSERVICOS_GUID:
        raise SyncError("GWSERVICOS_LOGIN, GWSERVICOS_SENHA e GWSERVICOS_GUID precisam estar configurados.")

    headers = {
        "Login": config.GWSERVICOS_LOGIN,
        "senha": config.GWSERVICOS_SENHA,
        "GUID": config.GWSERVICOS_GUID,
    }
    resp = requests.get(TOKEN_URL, headers=headers, timeout=20)
    data = resp.json()
    if data.get("codigo") != "000":
        raise SyncError(f"Login GW Serviços falhou: {data}")
    token = data.get("token")
    if not token:
        raise SyncError(f"Login retornou codigo 000 sem token: {data}")
    return token


def listar_cargas(token: str, parametro1: str, parametro2: str):
    headers = {"TOKEN": token, "Content-Type": "application/json", "Accept": "application/json"}
    body = {"tipo": 1, "parametro1": parametro1, "parametro2": parametro2}
    resp = requests.post(LISTAR_CARGAS_URL, headers=headers, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "codigo" in data:
        logger.warning("listarCargas retornou erro: %s", data)
        return []
    return data


def _parse_data_ddmmyyyy(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%d%m%Y%H%M", "%d%m%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def mapear_carga_para_registro(carga: dict, referencia: str) -> dict:
    previsao = _parse_data_ddmmyyyy(carga.get("previsaoEntrega"))
    data_entrega = _parse_data_ddmmyyyy(carga.get("dataEntrega"))
    entregue = bool(data_entrega)

    manifesto = bool((carga.get("numeroManifesto") or "").strip())
    romaneio = bool((carga.get("numeroRomaneio") or "").strip())

    cod_oc = (carga.get("codUltimaOcorrencia") or "").strip()
    desc_oc = (carga.get("descricaoUltimaOcorrencia") or "").strip()
    ocorrencia = f"{cod_oc}-{desc_oc}" if cod_oc or desc_oc else None

    dias_atraso = None
    if entregue:
        if previsao and data_entrega and data_entrega > previsao:
            status, estado = "FPE", "serious"
            try:
                d1 = datetime.datetime.strptime(data_entrega, "%Y-%m-%d")
                d2 = datetime.datetime.strptime(previsao, "%Y-%m-%d")
                dias_atraso = (d1 - d2).days
            except ValueError:
                pass
        else:
            status, estado = "DP", "good"
    else:
        status = "PE"
        vencido = bool(previsao) and previsao < referencia
        estado = "critical" if vencido else "warning"
        if previsao:
            try:
                d1 = datetime.datetime.strptime(referencia, "%Y-%m-%d")
                d2 = datetime.datetime.strptime(previsao, "%Y-%m-%d")
                dias_atraso = (d1 - d2).days
            except ValueError:
                pass

    return {
        "emissao": _parse_data_ddmmyyyy(carga.get("emissaoCTE") or carga.get("emissaoCte")),
        "cte": str(carga.get("cte") or carga.get("idCte") or ""),
        "remetente": carga.get("razaoRemetente") or carga.get("remetente") or "",
        "consignatario": carga.get("consignatario") or "",
        "cidade_cons": carga.get("cidadeConsignatario") or None,
        "destinatario": carga.get("destinatario") or "",
        "cidade_dest": carga.get("cidadeDestinatario") or "",
        "uf_dest": carga.get("ufDestinatario") or "",
        "manifesto": manifesto,
        "romaneio": romaneio,
        "ocorrencia": ocorrencia,
        "entregue": entregue,
        "data_comprovante": data_entrega,
        "data_baixa": data_entrega,
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
    hoje = datetime.datetime.now()
    data_inicial = (hoje - datetime.timedelta(days=window_days)).strftime("%d%m%Y")
    data_final = hoje.strftime("%d%m%Y")
    referencia = hoje.strftime("%Y-%m-%d")

    _set_meta(db, "last_sync_status", "running")
    db.commit()

    try:
        token = solicitar_token()
        cargas = listar_cargas(token, data_inicial, data_final)

        # A API retorna uma linha por combinação CT-e + nota fiscal -- o mesmo
        # CT-e pode aparecer várias vezes no mesmo lote. Dedupe por "cte"
        # antes de gravar (os campos de pendência são os mesmos entre as
        # repetições), senão a constraint única do banco quebra.
        registros_by_cte = {}
        for c in cargas:
            reg = mapear_carga_para_registro(c, referencia)
            if not reg["cte"]:
                continue
            registros_by_cte[reg["cte"]] = reg

        count = 0
        for reg in registros_by_cte.values():
            existing = db.query(Carga).filter(Carga.cte == reg["cte"]).first()
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
        _set_meta(db, "last_sync_error", str(exc)[:500])
        db.commit()
        raise
