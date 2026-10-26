"""Copia os analistas (usuarios) e as tratativas de comprovante do banco
principal (DATABASE_URL) pro banco separado (CONSULTAS_DATABASE_URL) -- ver
app/db.py, que passou a guardar essas tabelas lá.

Mantém o id de cada registro (os cookies de sessão dos analistas guardam o
id) e pula o que já estiver no banco separado: pode rodar quantas vezes
quiser. Os registros criados direto no banco separado começam em ids altos
(1000 / 100000), então não colidem com os copiados.

O bloqueio por senha errada (tentativas_login) não é copiado -- é só um
contador de 15 minutos.

Uso:
    python scripts/migrar_banco_separado.py [--simular]

Precisa do banco principal no ar (com a cota da Neon estourada, rode depois
que ela renovar). No GitHub: Actions > "Migrar pro banco separado" > Run.
"""

import os
import sys

from sqlalchemy import func, inspect, select, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402,F401 (registra as tabelas)
from app.db import Base, criar_tabelas_separadas, engine, engine_separado  # noqa: E402

TABELAS = ["usuarios", "tratativas_comprovante"]


def copiar(nome: str, simular: bool) -> None:
    tabela = Base.metadata.tables[nome]
    nomes_principal = set(inspect(engine).get_table_names())
    if nome not in nomes_principal:
        print(f"{nome}: não existe no banco principal -- nada a copiar.")
        return
    # Só as colunas que existem nos dois lados (o principal pode estar sem
    # alguma coluna mais nova).
    no_principal = {c["name"] for c in inspect(engine).get_columns(nome)}
    colunas = [c for c in tabela.columns if c.name in no_principal]

    with engine.connect() as origem:
        linhas = [dict(r._mapping) for r in origem.execute(select(*colunas))]
    with engine_separado.connect() as destino:
        ids_destino = {i for (i,) in destino.execute(select(tabela.c.id))}
        logins_destino = (
            {l for (l,) in destino.execute(select(tabela.c.login))} if nome == "usuarios" else set()
        )

    novas = []
    for linha in linhas:
        if linha["id"] in ids_destino:
            continue
        if nome == "usuarios" and linha["login"] in logins_destino:
            print(f"  usuarios: login '{linha['login']}' já existe no banco separado (recriado lá) -- mantido o de lá.")
            continue
        novas.append(linha)

    print(f"{nome}: {len(linhas)} no principal, {len(novas)} a copiar.")
    if simular or not novas:
        return
    with engine_separado.begin() as destino:
        destino.execute(tabela.insert(), novas)
        if engine_separado.dialect.name == "postgresql":
            # A sequência não pode ficar atrás do maior id copiado.
            maior = destino.execute(select(func.max(tabela.c.id))).scalar() or 0
            atual = destino.execute(text(f"SELECT last_value FROM {nome}_id_seq")).scalar() or 0
            if maior >= atual:
                destino.execute(text(f"SELECT setval('{nome}_id_seq', :v)"), {"v": maior})
    print(f"{nome}: copiados {len(novas)}.")


def main() -> None:
    simular = "--simular" in sys.argv
    if engine_separado is engine:
        sys.exit("CONSULTAS_DATABASE_URL não está definida -- não há banco separado pra onde copiar.")
    criar_tabelas_separadas()
    for nome in TABELAS:
        copiar(nome, simular)
    if simular:
        print("(simulação: nada foi gravado)")


if __name__ == "__main__":
    main()
