"""Copia os dados do banco antigo (ANTIGO_DATABASE_URL) pros bancos atuais:
o principal (DATABASE_URL) e o separado (CONSULTAS_DATABASE_URL -- analistas,
tratativas e consultas de notas, ver app/db.py). Cada tabela vai pro banco
onde ela mora hoje.

Pra quando o banco principal é trocado por um novo (ex.: a cota de rede da
Neon estourou): o app e a sincronização passam a usar o novo na hora, e os
dados antigos vêm depois, quando o antigo voltar a responder.

- Mantém o id de cada registro (número do protocolo de faturas, cookies de
  sessão dos analistas...). O banco novo começa os ids altos (ver
  app/seed.py), então o que foi criado nele antes da cópia não colide.
- CT-e (cargas) vão pelo id_cte, sem o id: os que a sincronização já trouxe
  pro banco novo ficam como estão (são mais recentes).
- O que já existe no destino é pulado: pode rodar quantas vezes quiser.
- Não copia o bloqueio por senha errada (tentativas_login).

Uso:
    python scripts/migrar_banco_antigo.py [--simular]

No GitHub: Actions > "Migrar do banco antigo" > Run (secret ANTIGO_DATABASE_URL).
"""

import os
import sys

from sqlalchemy import create_engine, func, inspect, select, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402,F401 (registra as tabelas)
from app.db import Base, criar_tabelas_separadas, engine, engine_da_tabela, engine_separado  # noqa: E402
from app.seed import init_db_and_seed  # noqa: E402

NAO_COPIAR = {"tentativas_login"}
SEM_ID = {"cargas"}  # copiadas pelo id_cte
LOTE = 1000


def _url(valor: str) -> str:
    return valor.replace("postgres://", "postgresql://", 1) if valor.startswith("postgres://") else valor


def _mesmo_banco(a, b) -> bool:
    return a.url.render_as_string(hide_password=False) == b.url.render_as_string(hide_password=False)


def _inserir(destino, tabela, linhas):
    """Insere ignorando o que já existe (chave primária ou única)."""
    if destino.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        stmt = insert(tabela).on_conflict_do_nothing()
    else:
        stmt = tabela.insert().prefix_with("OR IGNORE")
    with destino.begin() as conn:
        conn.execute(stmt, linhas)


def _acertar_sequencia(destino, tabela) -> None:
    """A sequência do id não pode ficar atrás do maior id copiado."""
    if destino.dialect.name != "postgresql" or "id" not in tabela.c:
        return
    with destino.begin() as conn:
        maior = conn.execute(select(func.max(tabela.c.id))).scalar() or 0
        atual = conn.execute(text(f"SELECT last_value FROM {tabela.name}_id_seq")).scalar() or 0
        if maior >= atual:
            conn.execute(text(f"SELECT setval('{tabela.name}_id_seq', :v)"), {"v": maior})


def copiar(origem, tabela, simular: bool) -> None:
    nome = tabela.name
    destino = engine_da_tabela(nome)
    if _mesmo_banco(origem, destino):
        print(f"{nome}: o banco antigo é o mesmo do destino -- nada a copiar.")
        return
    if nome not in inspect(origem).get_table_names():
        print(f"{nome}: não existe no banco antigo.")
        return
    cols_origem = {c["name"] for c in inspect(origem).get_columns(nome)}
    cols_destino = {c["name"] for c in inspect(destino).get_columns(nome)}
    colunas = [c for c in tabela.columns if c.name in cols_origem & cols_destino and not (nome in SEM_ID and c.name == "id")]

    def contar() -> int:
        with destino.connect() as c:
            return c.execute(select(func.count()).select_from(tabela)).scalar()

    antes = contar()
    total = 0
    with origem.connect() as conn:
        resultado = conn.execution_options(stream_results=True).execute(select(*colunas))
        while True:
            lote = [dict(r._mapping) for r in resultado.fetchmany(LOTE)]
            if not lote:
                break
            total += len(lote)
            if not simular:
                _inserir(destino, tabela, lote)
    if not simular:
        _acertar_sequencia(destino, tabela)
        print(f"{nome}: {total} no banco antigo, {contar() - antes} copiados (o resto já existia).")
    else:
        print(f"{nome}: {total} no banco antigo.")


def main() -> None:
    simular = "--simular" in sys.argv
    url_antigo = _url(os.environ.get("ANTIGO_DATABASE_URL", "").strip())
    if not url_antigo:
        sys.exit("ANTIGO_DATABASE_URL não está definida -- de onde copiar?")
    args = {"check_same_thread": False} if url_antigo.startswith("sqlite") else {"connect_timeout": 15}
    origem = create_engine(url_antigo, connect_args=args)

    init_db_and_seed()  # garante as tabelas nos bancos atuais
    if engine_separado is not engine:
        criar_tabelas_separadas()
    for tabela in Base.metadata.sorted_tables:
        if tabela.name not in NAO_COPIAR:
            copiar(origem, tabela, simular)
    if simular:
        print("(simulação: nada foi gravado)")


if __name__ == "__main__":
    main()
