import logging

from sqlalchemy import inspect, text

from .db import TABELAS_SEPARADAS, Base, criar_tabelas_separadas, engine, engine_da_tabela, engine_separado

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
        "emite_cte": "BOOLEAN DEFAULT FALSE",
    },
}


def _adicionar_colunas_faltantes(so_separado: bool = False):
    for tabela, colunas in _COLUNAS_ADICIONADAS.items():
        eng = engine_da_tabela(tabela)  # usuarios pode estar no banco separado
        if so_separado and eng is engine:
            continue
        try:
            existentes = {c["name"] for c in inspect(eng).get_columns(tabela)}
        except Exception:
            logger.exception("Não foi possível ler as colunas de %s.", tabela)
            continue
        # No Postgres "IF NOT EXISTS" evita erro quando duas instâncias (a função
        # da Vercel e o GitHub Actions) tentam criar a mesma coluna ao mesmo tempo.
        se_nao_existe = "IF NOT EXISTS " if eng.dialect.name == "postgresql" else ""
        for nome, tipo in colunas.items():
            if nome in existentes:
                continue
            try:
                with eng.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {se_nao_existe}{nome} {tipo}"))
            except Exception:
                # Uma coluna que não deu pra criar não pode derrubar o resto.
                logger.exception("Não foi possível criar a coluna %s.%s", tabela, nome)


# Banco principal novo (vazio): os ids começam altos, pra que a cópia dos dados
# do banco antigo (scripts/migrar_banco_antigo.py) mantenha os ids originais
# sem colidir -- o número do protocolo de faturas é o que o cliente recebe.
_PRIMEIRO_ID_PRINCIPAL = {"protocolos_fatura": 1000}
_PRIMEIRO_ID_PADRAO = 1_000_000
_SEM_AJUSTE_DE_ID = {"cargas"}  # copiadas pelo id_cte, sem manter o id


def _ids_altos_em_tabelas_novas(novas: set[str]) -> None:
    if engine.dialect.name != "postgresql":
        return
    for tabela in Base.metadata.sorted_tables:
        if tabela.name not in novas or tabela.name in _SEM_AJUSTE_DE_ID or "id" not in tabela.c:
            continue
        inicio = _PRIMEIRO_ID_PRINCIPAL.get(tabela.name, _PRIMEIRO_ID_PADRAO)
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER SEQUENCE {tabela.name}_id_seq RESTART WITH {inicio}"))
        except Exception:
            logger.exception("Não foi possível ajustar o primeiro id de %s.", tabela.name)


def init_db_and_seed():
    from . import models  # noqa: F401 (registra as tabelas no Base)

    tabelas = Base.metadata.sorted_tables
    if engine_separado is not engine:
        # Banco separado (consultas, login dos analistas, tratativas): as
        # tabelas dele vão pra lá primeiro, pra funcionarem mesmo que o banco
        # principal esteja fora do ar.
        try:
            criar_tabelas_separadas()
            _adicionar_colunas_faltantes(so_separado=True)
        except Exception:
            logger.exception("Não foi possível preparar o banco separado.")
        tabelas = [t for t in tabelas if t.name not in TABELAS_SEPARADAS]
    existentes = set(inspect(engine).get_table_names())
    Base.metadata.create_all(bind=engine, tables=tabelas)
    if not existentes:  # banco principal vazio = banco novo
        _ids_altos_em_tabelas_novas({t.name for t in tabelas})
    _adicionar_colunas_faltantes()
