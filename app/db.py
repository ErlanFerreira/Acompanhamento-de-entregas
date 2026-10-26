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


class SessaoRoteada(Session):
    """Uma sessão só pro código inteiro: as tabelas de consulta vão pro banco
    das consultas e todo o resto pro principal."""

    def get_bind(self, mapper=None, clause=None, **kw):
        if engine_consultas is not engine and mapper is not None:
            tabela = getattr(mapper, "local_table", None)
            if tabela is not None and tabela.name in TABELAS_CONSULTA:
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
