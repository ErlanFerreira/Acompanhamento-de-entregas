"""O que um analista pode ver: CT-e em que alguma das empresas dele (lista de
nomes em maiúsculas, cadastrada pelo supervisor) é tomador (consignatário) ou
remetente. `empresas=None` em todas as funções = sem restrição (supervisor ou
link de acesso)."""

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .models import Carga


def nome_empresa(coluna):
    """Nome da empresa como é comparado com a lista do analista."""
    return func.upper(func.trim(coluna))


def filtro_cargas(empresas: set[str]):
    """Condição SQL: CT-e de alguma das empresas (tomador ou remetente)."""
    lista = list(empresas) or [""]
    return or_(nome_empresa(Carga.consignatario).in_(lista), nome_empresa(Carga.remetente).in_(lista))


def carga_visivel(carga: Carga | None, empresas: set[str] | None) -> bool:
    if empresas is None:
        return True
    if carga is None:  # CT-e fora da base: não dá pra saber de quem é
        return False
    nomes = {(carga.consignatario or "").strip().upper(), (carga.remetente or "").strip().upper()}
    return bool(nomes & empresas)


def ctes_visiveis(db: Session, empresas: set[str]) -> set[tuple[str, int]]:
    """(CNPJ da filial, número) dos CT-e das empresas -- pra conferir bips
    (que guardam filial + número) sem consultar CT-e por CT-e."""
    linhas = db.query(Carga.cnpj_filial, Carga.cte).filter(filtro_cargas(empresas)).all()
    return {(filial, int(cte)) for filial, cte in linhas if cte and cte.isdigit()}
