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

# Colunas do relatório personalizado "Pendências", localizadas pelo NOME do
# cabeçalho (não pela posição) -- o relatório é editável no GW e já teve
# colunas inseridas no meio (Série, Filial, Notas, endereço do tomador...),
# o que deslocava todas as posições fixas de antes.
#
# Cada teste recebe o cabeçalho normalizado (minúsculo, sem acento nem
# pontuação). A ordem importa: os mais específicos vêm antes (ex: "CNPJ
# Consignatario" antes de "Consignatario"), e uma coluna já usada não é
# reaproveitada por outro campo.
COLUNAS = [
    ("emissao", lambda n: n.startswith("emissao")),
    ("cte", lambda n: n.startswith("numero ct")),
    ("serie", lambda n: n.startswith("serie")),
    ("cnpj_filial", lambda n: n == "cnpj filial"),
    ("filial", lambda n: "filial" in n and "cnpj" not in n),
    ("notas_fiscais", lambda n: re.search(r"\bnotas?\b|\bnf e?\b|\bnfs\b", n) is not None),
    ("cte_redespacho", lambda n: "redespacho" in n),
    ("cnpj_consignatario", lambda n: n.startswith("cnpj consignatario")),
    ("endereco_cons", lambda n: n.startswith("endereco consignatario")),
    ("compl_cons", lambda n: n.startswith("compl consignatario")),
    ("bairro_cons", lambda n: n.startswith("bairro consignatario")),
    ("cep_cons", lambda n: n.startswith("cep consignatario")),
    ("cidade_cons", lambda n: n.startswith("cidade consignatario")),
    ("consignatario", lambda n: n.startswith("consignatario")),
    ("remetente", lambda n: n == "remetente"),
    ("cidade_dest", lambda n: n.startswith("cidade destinatario")),
    ("uf_dest", lambda n: n.startswith("uf destinatario")),
    ("destinatario", lambda n: n == "destinatario"),
    ("manifesto", lambda n: n.startswith("tem manifesto")),
    ("romaneio", lambda n: n.startswith("tem romaneio")),
    ("ocorrencia", lambda n: n == "ultima ocorrencia"),
    ("entregue", lambda n: n.startswith("entregue")),
    ("data_comprovante", lambda n: n.startswith("data comprovante")),
    ("data_baixa", lambda n: n.startswith("data efetiva baixa")),
    ("status", lambda n: n.startswith("status entrega")),
    ("previsao", lambda n: n.startswith("previsao entrega")),
]
# Sem essas não dá pra montar o registro -- falha a sincronização em vez de
# gravar lixo (ou nada) em silêncio.
COLUNAS_OBRIGATORIAS = ("emissao", "cte", "cnpj_filial", "status", "previsao")
STATUS_VALIDOS = ("DP", "FPE", "PE")


def _normalizar_cabecalho(s) -> str:
    texto = unicodedata.normalize("NFD", str(s or ""))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^a-z0-9]+", " ", texto.lower())
    return texto.strip()


def detectar_colunas(cabecalho) -> dict[str, int]:
    normalizados = [_normalizar_cabecalho(c) for c in cabecalho]
    indices: dict[str, int] = {}
    for campo, testar in COLUNAS:
        for i, n in enumerate(normalizados):
            if n and i not in indices.values() and testar(n):
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


def _montar_endereco(v) -> str | None:
    """Ex: "R MANOEL MARIA FERNANDES, 77 - JARDIM ELIZABETE - CEP 06786-300"."""
    rua = ", ".join(str(p) for p in (v("endereco_cons"), v("compl_cons")) if p)
    cep = _so_digitos(v("cep_cons"))
    if len(cep) == 8:
        cep = f"{cep[:5]}-{cep[5:]}"
    partes = [rua, v("bairro_cons"), f"CEP {cep}" if cep else None]
    return " - ".join(str(p) for p in partes if p) or None


def mapear_linha_para_registro(row, referencia: str, colunas: dict[str, int]) -> dict:
    def v(campo):
        idx = colunas.get(campo)
        if idx is None or idx >= len(row):
            return None
        valor = row[idx]
        if isinstance(valor, str):
            valor = valor.strip()
        return valor if valor not in ("", None) else None

    cnpj_filial = _so_digitos(v("cnpj_filial"))
    cte = str(v("cte") or "").strip()
    serie = str(v("serie")).upper() if v("serie") is not None else None
    # CT-e e minuta (série "M") usam a mesma numeração na mesma filial --
    # a série entra na chave pra um não sobrescrever o outro.
    id_cte = ":".join(p for p in (cnpj_filial, cte, serie) if p)

    status = str(v("status") or "")
    previsao = _data_iso(v("previsao"))
    data_comprovante = _data_iso(v("data_comprovante"))
    data_baixa = _data_iso(v("data_baixa"))

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

    return {
        "id_cte": id_cte,
        "cte": cte,
        "serie": serie,
        "cnpj_filial": cnpj_filial,
        "filial": _texto_ou_none(v("filial")),
        "emissao": _data_iso(v("emissao")),
        "notas_fiscais": _texto_ou_none(v("notas_fiscais")),
        "cte_redespacho": _texto_ou_none(v("cte_redespacho")),
        "remetente": v("remetente") or "",
        "consignatario": v("consignatario") or "",
        "cnpj_consignatario": _so_digitos(v("cnpj_consignatario")) or None,
        "endereco_consignatario": _montar_endereco(v),
        "cidade_cons": v("cidade_cons"),
        "destinatario": v("destinatario") or "",
        "cidade_dest": v("cidade_dest") or "",
        "uf_dest": v("uf_dest") or "",
        "manifesto": _sim(v("manifesto")),
        "romaneio": _sim(v("romaneio")),
        "ocorrencia": v("ocorrencia"),
        "entregue": _sim(v("entregue")),
        "data_comprovante": data_comprovante,
        "data_baixa": data_baixa,
        "status": status,
        "estado": estado,
        "previsao": previsao,
        "dias_atraso": dias_atraso,
    }


def ler_relatorio(conteudo: bytes, referencia: str) -> list[dict]:
    """Converte o .xlsx do relatório "Pendências" em registros de `Carga`."""
    ws = openpyxl.load_workbook(BytesIO(conteudo), data_only=True).active
    # Linha 1 é o título do relatório; o cabeçalho costuma ser a 2ª, mas
    # procura nas primeiras pra não depender disso.
    linhas = list(ws.iter_rows(values_only=True))
    inicio, colunas = None, {}
    for i, linha in enumerate(linhas[:5]):
        colunas = detectar_colunas(linha)
        if "cte" in colunas and "status" in colunas:
            inicio = i + 1
            break
    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in colunas]
    if inicio is None or faltando:
        raise SyncError(
            'O relatório "Pendências" do GW não tem as colunas esperadas '
            f"(faltando: {', '.join(faltando) or 'cabeçalho'}). Confira se o relatório personalizado foi alterado."
        )
    logger.info("Colunas detectadas no relatório: %s", colunas)

    registros: dict[str, dict] = {}
    total_linhas = 0
    for row in linhas[inicio:]:
        if not row or row[colunas["cte"]] in (None, ""):
            continue
        total_linhas += 1
        # Ignora CT-e cancelados (status "PEC"/"FPC" -- variantes
        # canceladas de PE/FPE) -- não são pendências reais.
        if str(row[colunas["status"]] or "").strip() not in STATUS_VALIDOS:
            continue
        reg = mapear_linha_para_registro(row, referencia, colunas)
        registros[reg["id_cte"]] = reg

    if total_linhas and not registros:
        # Foi exatamente o que aconteceu quando colunas foram inseridas no
        # meio do relatório: todas as linhas descartadas, "sucesso" com 0.
        raise SyncError(
            f"Nenhuma das {total_linhas} linhas do relatório tem status de entrega válido "
            f'({"/".join(STATUS_VALIDOS)}) -- a coluna "Status Entrega" deve ter mudado.'
        )
    return list(registros.values())


def gravar_registros(db: Session, registros: list[dict]) -> int:
    ids = [r["id_cte"] for r in registros]
    existentes: dict[str, Carga] = {}
    for i in range(0, len(ids), 1000):
        for c in db.query(Carga).filter(Carga.id_cte.in_(ids[i:i + 1000])):
            existentes[c.id_cte] = c

    # Registros gravados antes da série entrar na chave ("cnpj:numero") --
    # agora que o relatório traz a série, o mesmo documento volta como
    # "cnpj:numero:serie"; apaga o antigo pra não ficar duplicado (e pra não
    # continuar misturando CT-e e minuta de mesmo número).
    antigos = sorted({f"{r['cnpj_filial']}:{r['cte']}" for r in registros if r["serie"]})
    for i in range(0, len(antigos), 1000):
        db.query(Carga).filter(Carga.id_cte.in_(antigos[i:i + 1000])).delete(synchronize_session=False)

    for reg in registros:
        existente = existentes.get(reg["id_cte"])
        if existente:
            for k, valor in reg.items():
                setattr(existente, k, valor)
        else:
            db.add(Carga(**reg))
    db.commit()
    return len(registros)


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
        registros = ler_relatorio(conteudo, referencia)
        count = gravar_registros(db, registros)

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
