import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from . import config


def _url(valor: str) -> str:
    return valor.replace("postgres://", "postgresql://", 1) if valor.startswith("postgres://") else valor


def _criar_engine(url: str):
    args = {"check_same_thread": False} if url.startswith("sqlite") else {"connect_timeout": 8}
    return create_engine(url, connect_args=args, pool_pre_ping=True)


# Banco principal (DATABASE_URL). Trocado por um projeto Neon novo em
# 27/10/2026, quando o anterior estourou a cota mensal de rede -- os dados que
# ficaram no anterior são copiados com scripts/migrar_banco_antigo.py.
engine = _criar_engine(config.DATABASE_URL)

# Banco separado (CONSULTAS_DATABASE_URL, opcional -- o nome ficou de quando
# só as consultas iam pra lá): cada projeto gratuito da Neon tem a própria
# cota mensal de transferência de rede, então o que está aqui não disputa a
# cota do banco principal -- e segue funcionando se ele ficar bloqueado. Sem
# a variável, tudo fica no principal.
#   - consultas de notas (consulta_jobs, consulta_itens);
#   - login dos analistas (usuarios, tentativas_login) e tratativas de
#     comprovante -- o analista entra mesmo com o principal fora, e o
#     histórico das tratativas fica preservado.
# Dados que ficaram num banco anterior: copie com scripts/migrar_banco_antigo.py.
_url_separado = _url(os.environ.get("CONSULTAS_DATABASE_URL", "").strip())
engine_separado = _criar_engine(_url_separado) if _url_separado else engine
engine_consultas = engine_separado  # nome antigo (scripts/run_consulta.py)

TABELAS_CONSULTA = {"consulta_jobs", "consulta_itens"}
TABELAS_ACESSO = {"usuarios", "tentativas_login", "tratativas_comprovante"}
TABELAS_SEPARADAS = TABELAS_CONSULTA | TABELAS_ACESSO

# Primeiro id das tabelas de acesso quando são criadas no banco separado --
# os registros copiados do principal (ids pequenos) mantêm o id original sem
# colidir com os criados lá antes da cópia. Mantém válidos os cookies de
# sessão dos analistas, que guardam o id.
_PRIMEIRO_ID = {"usuarios": 1000, "tratativas_comprovante": 100_000}

_tabelas_separadas_ok = False


def garantir_tabelas_separadas() -> None:
    """Cria as tabelas do banco separado, uma vez por processo, na primeira vez
    que alguém as usa. A criação na subida do app (seed.py) pode não rodar
    (ex.: a função da Vercel sobe sem executar o lifespan)."""
    global _tabelas_separadas_ok
    if _tabelas_separadas_ok or engine_separado is engine:
        return
    criar_tabelas_separadas()
    _tabelas_separadas_ok = True


def engine_da_tabela(nome: str):
    """Em qual banco a tabela mora."""
    return engine_separado if nome in TABELAS_SEPARADAS else engine


def criar_tabelas_separadas() -> None:
    tabelas = [t for t in Base.metadata.sorted_tables if t.name in TABELAS_SEPARADAS]
    existentes = set(inspect(engine_separado).get_table_names())
    Base.metadata.create_all(bind=engine_separado, tables=tabelas)
    if engine_separado.dialect.name != "postgresql":
        return
    with engine_separado.begin() as conn:
        for nome, inicio in _PRIMEIRO_ID.items():
            if nome in existentes:
                continue  # já existia: a sequência já está andando
            conn.execute(text(f"ALTER SEQUENCE {nome}_id_seq RESTART WITH {inicio}"))


# Compatibilidade com o nome anterior.
garantir_tabelas_consulta = garantir_tabelas_separadas


class SessaoRoteada(Session):
    """Uma sessão só pro código inteiro: as tabelas do banco separado vão pra
    lá e todo o resto pro principal."""

    def get_bind(self, mapper=None, clause=None, **kw):
        if engine_separado is not engine and mapper is not None:
            tabela = getattr(mapper, "local_table", None)
            if tabela is not None and tabela.name in TABELAS_SEPARADAS:
                garantir_tabelas_separadas()
                return engine_separado
        return super().get_bind(mapper=mapper, clause=clause, **kw)


SessionLocal = sessionmaker(bind=engine, class_=SessaoRoteada, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
