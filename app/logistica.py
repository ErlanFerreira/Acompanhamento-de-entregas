"""Onde está cada CT-e pendente: parado no depósito ou em rota de entrega.

Regra: um CT-e pendente de entrega (status "PE") está **em rota** quando tem
manifesto ou romaneio emitido pela **matriz** (Carpina); sem nenhum deles --
ou só com documento emitido por outra cidade (as filiais) -- está **em
depósito**, parado. Ao ser baixado (entregue) sai das duas contagens.

A cidade de emissão vem das colunas do relatório "Pendências" do GW
(Carga.cidade_manifesto / Carga.cidade_romaneio). Enquanto o relatório não
traz essa coluna (valor `None`, em vez de texto), vale só o "Tem Manifesto" /
"Tem Romaneio", como antes.
"""

from sqlalchemy import and_, func, or_

from .models import Carga

CIDADE_MATRIZ = "CARPINA"


def _da_matriz(cidade: str | None) -> bool:
    texto = (cidade or "").strip().upper()
    return texto.startswith(CIDADE_MATRIZ)


def _doc_valido(tem: bool | None, cidade: str | None) -> bool:
    """Documento que conta como "saiu pra entrega": existe e foi emitido pela
    matriz (ou a cidade não é conhecida, relatório antigo)."""
    return bool(tem) and (cidade is None or _da_matriz(cidade))


def classificar(c: Carga) -> str | None:
    """"rota", "deposito", ou None se o CT-e não está pendente."""
    if c.status != "PE":
        return None
    if _doc_valido(c.manifesto, c.cidade_manifesto) or _doc_valido(c.romaneio, c.cidade_romaneio):
        return "rota"
    return "deposito"


# ---- as mesmas regras como condições SQL (histórico e resumo por e-mail) ----

def _doc_sql(tem, cidade):
    return and_(tem.is_(True), or_(cidade.is_(None), func.upper(func.trim(cidade)).like(CIDADE_MATRIZ + "%")))


def _saiu_pra_entrega_sql():
    return or_(_doc_sql(Carga.manifesto, Carga.cidade_manifesto), _doc_sql(Carga.romaneio, Carga.cidade_romaneio))


def cond_em_rota():
    return and_(Carga.status == "PE", _saiu_pra_entrega_sql())


def cond_em_deposito():
    return and_(Carga.status == "PE", ~_saiu_pra_entrega_sql())
