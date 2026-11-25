"""Roteamento dos bancos: as tabelas de emissão de CT-e vão para o banco da emissão
(EMISSAO_DATABASE_URL); sem ele ficam onde estavam (banco separado ou principal).
Usa três arquivos SQLite, cada cenário num processo novo (app/db.py lê as variáveis ao
importar). Dados fictícios.

Rodar:  python tests/test_roteamento_emissao.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Roda dentro do processo do cenário: grava uma linha em cada tipo de tabela pela sessão
# roteada (inclusive uma contagem, que usa só a coluna) e devolve o que leu de volta.
CODIGO = """
import json, sys
sys.path.insert(0, sys.argv[1])
from sqlalchemy import func
from app import db as banco
from app.models import Carga, EmissaoCte, EmissaoRobo, Usuario
from app.seed import init_db_and_seed

init_db_and_seed()
s = banco.SessionLocal()
s.add(Usuario(nome="Ana", login="ana", senha_hash="x", empresas="[]"))
s.add(EmissaoCte(solicitante="Ana", tipo="normal", chave_nfe="1" * 44, lote="L1"))
s.add(EmissaoRobo(nome="PC-TESTE"))
s.add(Carga(id_cte="1:1", cte="1", cnpj_filial="1"))
s.commit()
saida = {
    "pedidos": s.query(func.count(EmissaoCte.id)).scalar(),
    "lote": [e.chave_nfe[:3] for e in s.query(EmissaoCte).filter(EmissaoCte.lote == "L1")],
    "usuarios": s.query(Usuario).count(),
    "cargas": s.query(Carga).count(),
    "robo": s.query(EmissaoRobo).first().nome,
    "engines": {
        "emissao_eh_separado": banco.engine_emissao is banco.engine_separado,
        "emissao_eh_principal": banco.engine_emissao is banco.engine,
    },
}
print("RESULTADO:" + json.dumps(saida))
"""


def _tabelas(arquivo):
    if not os.path.exists(arquivo):
        return set()
    con = sqlite3.connect(arquivo)
    try:
        return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()


def _rodar(principal, consultas=None, emissao=None):
    env = {k: v for k, v in os.environ.items() if k not in ("CONSULTAS_DATABASE_URL", "EMISSAO_DATABASE_URL", "DATABASE_URL")}
    env["DATABASE_URL"] = "sqlite:///" + principal.replace("\\", "/")
    env["CONSULTAS_DATABASE_URL"] = ("sqlite:///" + consultas.replace("\\", "/")) if consultas else ""
    env["EMISSAO_DATABASE_URL"] = ("sqlite:///" + emissao.replace("\\", "/")) if emissao else ""
    r = subprocess.run([sys.executable, "-I", "-c", CODIGO, RAIZ], env=env, capture_output=True, text=True, cwd=RAIZ, timeout=120)
    linha = next((l for l in r.stdout.splitlines() if l.startswith("RESULTADO:")), None)
    assert linha, r.stdout[-800:] + r.stderr[-1500:]
    return json.loads(linha[len("RESULTADO:"):])


def test_tres_bancos_cada_tabela_no_seu():
    with tempfile.TemporaryDirectory() as pasta:
        principal, consultas, emissao = (os.path.join(pasta, n) for n in ("principal.db", "consultas.db", "emissao.db"))
        r = _rodar(principal, consultas, emissao)
        assert r["pedidos"] == 1 and r["lote"] == ["111"] and r["usuarios"] == 1 and r["cargas"] == 1 and r["robo"] == "PC-TESTE"
        assert r["engines"] == {"emissao_eh_separado": False, "emissao_eh_principal": False}
        t_principal, t_consultas, t_emissao = _tabelas(principal), _tabelas(consultas), _tabelas(emissao)
        assert {"emissoes_cte", "emissao_robos"} <= t_emissao
        assert not ({"emissoes_cte", "emissao_robos", "cargas", "usuarios"} & (t_consultas & t_emissao))
        assert "usuarios" in t_consultas and "usuarios" not in t_emissao and "usuarios" not in t_principal
        assert "emissoes_cte" not in t_consultas and "emissoes_cte" not in t_principal
        assert "cargas" in t_principal and "cargas" not in t_consultas and "cargas" not in t_emissao


def test_sem_banco_da_emissao_fica_no_separado_como_antes():
    with tempfile.TemporaryDirectory() as pasta:
        principal, consultas = os.path.join(pasta, "principal.db"), os.path.join(pasta, "consultas.db")
        r = _rodar(principal, consultas, None)
        assert r["pedidos"] == 1 and r["usuarios"] == 1
        assert r["engines"]["emissao_eh_separado"] is True
        assert {"emissoes_cte", "emissao_robos", "usuarios"} <= _tabelas(consultas)
        assert not ({"emissoes_cte", "usuarios"} & _tabelas(principal))


def test_so_a_emissao_separada_o_resto_no_principal():
    with tempfile.TemporaryDirectory() as pasta:
        principal, emissao = os.path.join(pasta, "principal.db"), os.path.join(pasta, "emissao.db")
        r = _rodar(principal, None, emissao)
        assert r["pedidos"] == 1 and r["usuarios"] == 1 and r["cargas"] == 1
        assert {"emissoes_cte", "emissao_robos"} <= _tabelas(emissao)
        assert "emissoes_cte" not in _tabelas(principal)
        assert {"usuarios", "cargas"} <= _tabelas(principal) and "usuarios" not in _tabelas(emissao)


def test_sem_nenhum_banco_separado_tudo_no_principal():
    with tempfile.TemporaryDirectory() as pasta:
        principal = os.path.join(pasta, "principal.db")
        r = _rodar(principal, None, None)
        assert r["pedidos"] == 1 and r["engines"]["emissao_eh_principal"] is True
        assert {"emissoes_cte", "usuarios", "cargas"} <= _tabelas(principal)


def test_copia_dos_pedidos_para_o_banco_novo_mantendo_ids_e_sem_duplicar():
    """scripts/migrar_banco_antigo.py com ANTIGO = o banco separado de hoje (onde os pedidos
    estão) e EMISSAO = o banco novo: copia só as tabelas de emissão, com os mesmos ids."""
    with tempfile.TemporaryDirectory() as pasta:
        principal, consultas, novo = (os.path.join(pasta, n) for n in ("principal.db", "consultas.db", "emissao.db"))
        _rodar(principal, consultas, None)  # estado de hoje: pedido e sinal do robô no banco separado
        con = sqlite3.connect(consultas)
        con.execute("insert into emissoes_cte (id, solicitante, tipo, chave_nfe, status, lote, salvar_direto) "
                    "values (77, 'Bia', 'reentrega', ?, 'salvo', 'L9', 0)", ("2" * 44,))
        con.commit()
        antes = {r[0] for r in con.execute("select id from emissoes_cte")}
        con.close()

        env = {k: v for k, v in os.environ.items() if k not in ("CONSULTAS_DATABASE_URL", "EMISSAO_DATABASE_URL", "DATABASE_URL")}
        env.update(
            DATABASE_URL="sqlite:///" + principal.replace("\\", "/"),
            CONSULTAS_DATABASE_URL="sqlite:///" + consultas.replace("\\", "/"),
            EMISSAO_DATABASE_URL="sqlite:///" + novo.replace("\\", "/"),
            ANTIGO_DATABASE_URL="sqlite:///" + consultas.replace("\\", "/"),
        )
        for _ in range(2):  # a segunda vez não pode duplicar
            r = subprocess.run([sys.executable, "-I", "scripts/migrar_banco_antigo.py"], env=env, capture_output=True,
                               text=True, cwd=RAIZ, timeout=180)
            assert r.returncode == 0, r.stdout[-800:] + r.stderr[-1500:]
        con = sqlite3.connect(novo)
        copiados = {r[0] for r in con.execute("select id from emissoes_cte")}
        robos = [r[0] for r in con.execute("select nome from emissao_robos")]
        con.close()
        assert copiados == antes and 77 in copiados, (copiados, antes)
        assert robos == ["PC-TESTE"]
        assert "emissoes_cte" in r.stdout and "usuarios: o banco antigo é o mesmo do destino" in r.stdout


if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
