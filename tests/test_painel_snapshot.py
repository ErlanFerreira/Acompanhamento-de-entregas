"""Painel pronto (app/painel_snapshot.py): tem que devolver exatamente o mesmo que a
leitura das linhas de cargas, para qualquer escopo de analista e período, e o app tem
que seguir funcionando quando o Painel pronto não existe. Dados fictícios, SQLite em memória.

Rodar:  python tests/test_painel_snapshot.py
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite://"

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app import main, painel_snapshot  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Carga, Meta  # noqa: E402

HOJE = datetime.date.today()
EMPRESAS = ["TINTAS ALFA", "TRANSPORTES BETA", "DISTRIBUIDORA GAMA", " espaco  "]


def _montar():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    registros = []
    for i in range(300):
        dias = (i * 7) % 60  # emissões de 0 a 59 dias atrás
        reg = dict(
            id_cte=f"1:{i}", cte=str(i), cnpj_filial="1", emissao=(HOJE - datetime.timedelta(days=dias)).isoformat(),
            remetente=EMPRESAS[i % 4] if i % 3 else "OUTRO REMETENTE",
            consignatario=EMPRESAS[(i + 1) % 4] if i % 5 else "OUTRO TOMADOR",
            cidade_cons="X", destinatario="D", cidade_dest="PETROLINA", uf_dest="PE", manifesto=bool(i % 2), romaneio=False,
            cidade_manifesto="CARPINA" if i % 2 else None, cidade_romaneio=None, ocorrencia="302", entregue=False,
            data_comprovante=None, data_baixa=None, status="PE" if i % 4 else "DP", estado="critical", previsao=None,
            dias_atraso=i % 9, serie="5", filial="MATRIZ", notas_fiscais=str(1000 + i),
            tipo_conhecimento="NORMAL" if i % 6 else "REENTREGA",
            cte_redespacho=None, cnpj_consignatario=None, endereco_consignatario=None, cnpj_remetente=None,
        )
        registros.append(reg)
        db.add(Carga(**reg))
    validade = datetime.datetime.utcnow().isoformat()
    db.add(Meta(chave="last_sync_at", valor=validade))
    db.commit()
    return eng, db, registros, validade


def _chamar(db, empresas, desde, ate=None):
    main.escopo_empresas = lambda request: empresas
    main._CACHE_CARGAS.clear()
    req = Request({"type": "http", "headers": [], "method": "GET", "path": "/api/cargas", "query_string": b""})
    return json.loads(main.api_cargas(req, data_inicial=desde, data_final=ate, db=db).body)


def _sem_painel_pronto(db, validade, fn):
    db.query(painel_snapshot.PainelSnapshot).update({"validade": "outra"})
    db.commit()
    try:
        return fn()
    finally:
        db.query(painel_snapshot.PainelSnapshot).update({"validade": validade})
        db.commit()


def _ids(resposta):
    return sorted(resposta["records"], key=lambda r: r["id_cte"])


def test_pronto_igual_ao_banco_em_todos_os_escopos_e_periodos():
    _, db, registros, validade = _montar()
    assert len(painel_snapshot.gravar(db, registros, HOJE, 90, validade)) == 4
    for empresas in (None, {"TINTAS ALFA"}, {"TRANSPORTES BETA", "DISTRIBUIDORA GAMA"}, {"ESPACO"}, {"NAO EXISTE"}, set()):
        for periodo in (3, 7, 14, 30):
            desde = (HOJE - datetime.timedelta(days=periodo + 2)).isoformat()
            pronto = _chamar(db, empresas, desde)
            banco = _sem_painel_pronto(db, validade, lambda: _chamar(db, empresas, desde))
            assert _ids(pronto) == _ids(banco), (empresas, periodo)
            assert pronto["meta"]["total"] == banco["meta"]["total"]
    # com data final também
    d1, d2 = (HOJE - datetime.timedelta(days=16)).isoformat(), (HOJE - datetime.timedelta(days=5)).isoformat()
    assert _ids(_chamar(db, {"TINTAS ALFA"}, d1, d2)) == _ids(_sem_painel_pronto(db, validade, lambda: _chamar(db, {"TINTAS ALFA"}, d1, d2)))


def test_janela_curta_so_grava_os_periodos_que_cobre():
    _, db, registros, validade = _montar()
    assert len(painel_snapshot.gravar(db, registros, HOJE, 14, validade)) == 2  # 3 e 7 dias


def test_periodo_fora_do_pronto_cai_no_banco():
    _, db, registros, validade = _montar()
    painel_snapshot.gravar(db, registros, HOJE, 90, validade)
    assert len(_chamar(db, None, (HOJE - datetime.timedelta(days=50)).isoformat())["records"]) > 0  # mais antigo que 30 dias
    assert _chamar(db, None, None)["meta"]["carregados"] == 300  # todas as datas


def test_sem_a_tabela_do_pronto_o_app_continua_funcionando():
    eng, db, registros, validade = _montar()
    painel_snapshot.gravar(db, registros, HOJE, 90, validade)
    painel_snapshot.PainelSnapshot.__table__.drop(eng)
    assert len(_chamar(db, {"TINTAS ALFA"}, (HOJE - datetime.timedelta(days=16)).isoformat())["records"]) > 0


def test_mede_o_uso_de_cada_caminho():
    _, db, registros, validade = _montar()
    painel_snapshot.gravar(db, registros, HOJE, 90, validade)
    desde = (HOJE - datetime.timedelta(days=16)).isoformat()
    _chamar(db, None, desde)
    _sem_painel_pronto(db, validade, lambda: _chamar(db, None, desde))
    usos = {m.chave.split(":", 2)[2]: m.valor for m in db.query(Meta).filter(Meta.chave.like("uso:%"))}
    assert usos["cargas_pronto"].startswith("1|") and usos["cargas_banco"].startswith("1|")
    assert int(usos["cargas_pronto"].split("|")[1]) < int(usos["cargas_banco"].split("|")[1])


if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
