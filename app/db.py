import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from . import config


def _url(valor: str) -> str:
    return valor.replace("postgres://", "postgresql://", 1) if valor.startswith("postgres://") else valor


def _criar_engine(url: str):
    args = {"check_same_thread": False} if url.startswith("sqlite") else {"connect_timeout": 8}
    return create_engine(url, connect_args=args, pool_pre_ping=True)


engine = _criar_engine(config.DATABASE_URL)

# Banco separado só das consultas de notas (CONSULTAS_DATABASE_URL, opcional):
# cada projeto gratuito da Neon tem a própria cota mensal de transferência de
# rede, então as consultas não disputam a cota do banco principal -- e seguem
# funcionando se ele ficar bloqueado. Sem a variável, tudo fica no principal.
_url_consultas = _url(os.environ.get("CONSULTAS_DATABASE_URL", "").strip())
engine_consultas = _criar_engine(_url_consultas) if _url_consultas else engine

TABELAS_CONSULTA = {"consulta_jobs", "consulta_itens"}


_tabelas_consulta_ok = False


def garantir_tabelas_consulta() -> None:
    """Cria as tabelas das consultas no banco delas, uma vez por processo, na
    primeira vez que alguém as usa. A criação na subida do app (seed.py) pode
    não rodar (ex.: a função da Vercel sobe sem executar o lifespan)."""
    global _tabelas_consulta_ok
    if _tabelas_consulta_ok or engine_consultas is engine:
        return
    tabelas = [t for t in Base.metadata.sorted_tables if t.name in TABELAS_CONSULTA]
    Base.metadata.create_all(bind=engine_consultas, tables=tabelas)
    _tabelas_consulta_ok = True


class SessaoRoteada(Session):
    """Uma sessão só pro código inteiro: as tabelas de consulta vão pro banco
    das consultas e todo o resto pro principal."""

    def get_bind(self, mapper=None, clause=None, **kw):
        if engine_consultas is not engine and mapper is not None:
            tabela = getattr(mapper, "local_table", None)
            if tabela is not None and tabela.name in TABELAS_CONSULTA:
                garantir_tabelas_consulta()
                return engine_consultas
        return super().get_bind(mapper=mapper, clause=clause, **kw)


SessionLocal = sessionmaker(bind=engine, class_=SessaoRoteada, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
