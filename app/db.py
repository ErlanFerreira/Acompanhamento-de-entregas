import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from . import config


def _url(valor: str) -> str:
    return valor.replace("postgres://", "postgresql://", 1) if valor.startswith("postgres://") else valor


def _criar_engine(url: str, rotulo: str = "outro"):
    if url.startswith("sqlite"):
        args = {"check_same_thread": False}
    else:
        # Cada cursor soma o que devolve (ver app/medidor_banco.py e /uso-banco).
        from .medidor_banco import fabrica_de_cursor

        args = {"connect_timeout": 8, "cursor_factory": fabrica_de_cursor(rotulo)}
    return create_engine(url, connect_args=args, pool_pre_ping=True)


# Banco principal (DATABASE_URL). Trocado por um projeto Neon novo em
# 27/10/2026, quando o anterior estourou a cota mensal de rede -- os dados que
# ficaram no anterior são copiados com scripts/migrar_banco_antigo.py.
engine = _criar_engine(config.DATABASE_URL, "principal")

# Banco separado (CONSULTAS_DATABASE_URL, opcional -- o nome ficou de quando
# só as consultas iam pra lá): cada projeto gratuito da Neon tem a própria
# cota mensal de transferência de rede, então o que está aqui não disputa a
# cota do banco principal -- e segue funcionando se ele ficar bloqueado. Sem
# a variável, tudo fica no principal.
#   - consultas de notas (consulta_jobs, consulta_itens);
#   - login dos analistas (usuarios, tentativas_login) e tratativas de
#     comprovante -- o analista entra mesmo com o principal fora, e o
#     histórico das tratativas fica preservado;
#   - pedidos de emissão de CT-e (emissoes_cte) -- a tela consulta o
#     andamento a cada poucos segundos.
# Dados que ficaram num banco anterior: copie com scripts/migrar_banco_antigo.py.
_url_separado = _url(os.environ.get("CONSULTAS_DATABASE_URL", "").strip())
engine_separado = _criar_engine(_url_separado, "separado") if _url_separado else engine
engine_consultas = engine_separado  # nome antigo (scripts/run_consulta.py)

# Banco só da emissão de CT-e (EMISSAO_DATABASE_URL, opcional): os pedidos, o sinal do
# robô do PC e as fotos de cubagem. A tela acompanha os pedidos a cada poucos segundos e
# o robô consulta a fila o dia todo -- isso gastava a cota de transferência do banco
# separado, que também guarda o login dos analistas. Sem a variável, as tabelas de
# emissão ficam onde estavam (no banco separado, ou no principal se ele não existir).
_url_emissao = _url(os.environ.get("EMISSAO_DATABASE_URL", "").strip())
engine_emissao = _criar_engine(_url_emissao, "emissao") if _url_emissao else engine_separado

# Banco só do Painel pronto (PAINEL_DATABASE_URL, opcional): a lista compactada de
# CT-e que o Painel e Comprovantes leem a cada abertura (app/painel_snapshot.py) --
# a leitura mais frequente do app. Num projeto Neon à parte, ela tem a própria cota
# de 5 GB/mês e não divide a do principal. Nada a copiar: a sincronização regrava a
# tabela a cada rodada. Sem a variável, fica no principal.
_url_painel = _url(os.environ.get("PAINEL_DATABASE_URL", "").strip())
engine_painel = _criar_engine(_url_painel, "painel") if _url_painel else engine

TABELAS_CONSULTA = {"consulta_jobs", "consulta_itens"}
TABELAS_ACESSO = {"usuarios", "tentativas_login", "tratativas_comprovante"}
TABELAS_EMISSAO = {"emissoes_cte", "emissao_robos"}
TABELAS_PAINEL = {"painel_snapshots"}
TABELAS_SEPARADAS = TABELAS_CONSULTA | TABELAS_ACESSO | TABELAS_EMISSAO | TABELAS_PAINEL

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
    if _tabelas_separadas_ok or all(e is engine for e in (engine_separado, engine_emissao, engine_painel)):
        return
    criar_tabelas_separadas()
    _tabelas_separadas_ok = True


def engine_da_tabela(nome: str):
    """Em qual banco a tabela mora."""
    if nome in TABELAS_EMISSAO:
        return engine_emissao
    if nome in TABELAS_PAINEL:
        return engine_painel
    return engine_separado if nome in TABELAS_SEPARADAS else engine


def _tabelas_por_banco_separado() -> list[tuple]:
    """[(engine, [tabelas])] de cada banco fora do principal (consultas/acesso, emissão e
    Painel pronto podem ser o mesmo banco ou bancos diferentes)."""
    por_banco: dict[int, tuple] = {}
    for t in Base.metadata.sorted_tables:
        eng = engine_da_tabela(t.name)
        if t.name in TABELAS_SEPARADAS and eng is not engine:
            por_banco.setdefault(id(eng), (eng, []))[1].append(t)
    return list(por_banco.values())


def criar_tabelas_separadas() -> None:
    for eng, tabelas in _tabelas_por_banco_separado():
        existentes = set(inspect(eng).get_table_names())
        Base.metadata.create_all(bind=eng, tables=tabelas)
        if eng.dialect.name != "postgresql":
            continue
        nomes = {t.name for t in tabelas}
        with eng.begin() as conn:
            for nome, inicio in _PRIMEIRO_ID.items():
                if nome in existentes or nome not in nomes:
                    continue  # já existia (a sequência já está andando) ou mora em outro banco
                conn.execute(text(f"ALTER SEQUENCE {nome}_id_seq RESTART WITH {inicio}"))


# Compatibilidade com o nome anterior.
garantir_tabelas_consulta = garantir_tabelas_separadas


class SessaoRoteada(Session):
    """Uma sessão só pro código inteiro: as tabelas do banco separado vão pra
    lá e todo o resto pro principal."""

    def get_bind(self, mapper=None, clause=None, **kw):
        if mapper is not None:
            tabela = getattr(mapper, "local_table", None)
            if tabela is not None and tabela.name in TABELAS_SEPARADAS:
                destino = engine_da_tabela(tabela.name)
                if destino is not engine:
                    garantir_tabelas_separadas()
                    return destino
        return super().get_bind(mapper=mapper, clause=clause, **kw)


SessionLocal = sessionmaker(bind=engine, class_=SessaoRoteada, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
