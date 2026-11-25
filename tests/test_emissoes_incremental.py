"""/api/emissoes?lote=...&desde=...: o acompanhamento do lote (de 5 em 5 segundos) só recebe
os pedidos que mudaram desde a última resposta, em vez de reler o lote inteiro.
SQLite em memória, dados fictícios.

Rodar:  python tests/test_emissoes_incremental.py
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

from app import main  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import EmissaoCte  # noqa: E402

T0 = datetime.datetime(2026, 11, 25, 10, 0, 0)


def _montar(n=3):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    for i in range(n):
        db.add(EmissaoCte(
            solicitante="ANALISTA TESTE", tipo="normal", chave_nfe=f"{i:044d}", status="preparando", lote="L1",
            opcoes=json.dumps([{"numero": str(k)} for k in range(20)]),
            mensagens=json.dumps(["texto longo " * 20] * 5),
            criado_em=T0 + datetime.timedelta(seconds=i), atualizado_em=T0 + datetime.timedelta(seconds=i),
        ))
    db.add(EmissaoCte(solicitante="ANALISTA TESTE", tipo="normal", chave_nfe="9" * 44, status="preparando", lote="OUTRO",
                      criado_em=T0, atualizado_em=T0 + datetime.timedelta(hours=1)))
    db.commit()
    main.usuario_logado = lambda request: None  # supervisor: vê todos os pedidos
    return db


def _chamar(db, lote="L1", desde=""):
    req = Request({"type": "http", "headers": [], "method": "GET", "path": "/api/emissoes", "query_string": b""})
    return main.api_emissoes_list(req, lote=lote, desde=desde, db=db)["emissoes"]


def test_sem_desde_volta_o_lote_inteiro_com_atualizado_em():
    db = _montar()
    todos = _chamar(db)
    assert [e["id"] for e in todos] == [1, 2, 3]
    assert all(e["atualizado_em"].endswith("Z") for e in todos)


def test_com_desde_so_o_que_mudou_depois():
    db = _montar()
    ultimo = max(e["atualizado_em"] for e in _chamar(db))
    assert _chamar(db, desde=ultimo) == []                       # nada mudou: resposta vazia
    db.query(EmissaoCte).filter(EmissaoCte.id == 2).first().status = "aguardando"
    db.commit()                                                   # onupdate renova atualizado_em
    mudaram = _chamar(db, desde=ultimo)
    assert [e["id"] for e in mudaram] == [2] and mudaram[0]["status"] == "aguardando"


def test_desde_invalido_volta_tudo_e_outro_lote_nao_entra():
    db = _montar()
    assert len(_chamar(db, desde="lixo")) == 3
    assert [e["id"] for e in _chamar(db, lote="OUTRO")] == [4]


def test_resposta_incremental_vazia_e_minuscula():
    db = _montar(n=50)
    completo = len(json.dumps(_chamar(db)))
    ultimo = max(e["atualizado_em"] for e in _chamar(db))
    vazio = len(json.dumps(_chamar(db, desde=ultimo)))
    print(f"   lote de 50 pedidos: completo {completo // 1024} KB, sem mudança {vazio} bytes")
    assert completo > 50_000 and vazio < 10


if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
