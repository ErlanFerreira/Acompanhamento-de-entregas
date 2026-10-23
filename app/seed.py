import logging

from sqlalchemy import inspect, text

from .db import Base, engine

logger = logging.getLogger("seed")

# Colunas adicionadas depois que a tabela já existia em produção --
# `create_all` só cria tabelas novas, não altera as existentes.
_COLUNAS_ADICIONADAS = {
    "cargas": {
        "cte_redespacho": "VARCHAR(50)",
        "notas_fiscais": "TEXT",
        "cnpj_consignatario": "VARCHAR(20)",
        "cnpj_remetente": "VARCHAR(20)",
        "tipo_conhecimento": "VARCHAR(40)",
        "endereco_consignatario": "VARCHAR(255)",
        "serie": "VARCHAR(10)",
        "filial": "VARCHAR(60)",
        "cidade_manifesto": "VARCHAR(120)",
        "cidade_romaneio": "VARCHAR(120)",
    },
    "historico_dia": {
        "em_deposito": "INTEGER DEFAULT 0",
        "em_rota": "INTEGER DEFAULT 0",
    },
    "historico_tomador": {
        "em_deposito": "INTEGER DEFAULT 0",
        "em_rota": "INTEGER DEFAULT 0",
    },
    "bipagens": {
        "protocolo_id": "INTEGER",
    },
    "usuarios": {
        "trocar_senha": "BOOLEAN DEFAULT TRUE",
        "email": "VARCHAR(255)",
        "acesso_sac": "BOOLEAN DEFAULT FALSE",
    },
}


def _adicionar_colunas_faltantes():
    insp = inspect(engine)
    # No Postgres "IF NOT EXISTS" evita erro quando duas instâncias (a função
    # da Vercel e o GitHub Actions) tentam criar a mesma coluna ao mesmo tempo.
    se_nao_existe = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
    for tabela, colunas in _COLUNAS_ADICIONADAS.items():
        existentes = {c["name"] for c in insp.get_columns(tabela)}
        for nome, tipo in colunas.items():
            if nome in existentes:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {se_nao_existe}{nome} {tipo}"))
            except Exception:
                # Uma coluna que não deu pra criar não pode derrubar o resto.
                logger.exception("Não foi possível criar a coluna %s.%s", tabela, nome)


def init_db_and_seed():
    Base.metadata.create_all(bind=engine)
    _adicionar_colunas_faltantes()
