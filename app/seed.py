from sqlalchemy import inspect, text

from .db import Base, engine

# Colunas adicionadas depois que a tabela já existia em produção --
# `create_all` só cria tabelas novas, não altera as existentes.
_COLUNAS_ADICIONADAS = {
    "cargas": {
        "cte_redespacho": "VARCHAR(50)",
        "notas_fiscais": "TEXT",
        "cnpj_consignatario": "VARCHAR(20)",
        "endereco_consignatario": "VARCHAR(255)",
        "serie": "VARCHAR(10)",
        "filial": "VARCHAR(60)",
    },
}


def _adicionar_colunas_faltantes():
    insp = inspect(engine)
    for tabela, colunas in _COLUNAS_ADICIONADAS.items():
        existentes = {c["name"] for c in insp.get_columns(tabela)}
        faltantes = {nome: tipo for nome, tipo in colunas.items() if nome not in existentes}
        if not faltantes:
            continue
        with engine.begin() as conn:
            for nome, tipo in faltantes.items():
                conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}"))


def init_db_and_seed():
    Base.metadata.create_all(bind=engine)
    _adicionar_colunas_faltantes()
