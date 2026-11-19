"""/api/bipados (app/painel_bips.py): bips da tela de Bipagem para o selo verde do Painel.
SQLite em memória, dados fictícios.

Rodar:  python tests/test_bipados.py
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

from app import painel_bips  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Bipagem  # noqa: E402

AGORA = datetime.datetime.utcnow().replace(microsecond=0)


def _db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    painel_bips._CACHE.clear()
    return sessionmaker(bind=eng)()


def _bip(db, chave, filial, numero, serie, dias_atras):
    db.add(Bipagem(chave=chave, finalidade="geral", cnpj_filial=filial, numero=numero, serie=serie,
                   bipado_em=AGORA - datetime.timedelta(days=dias_atras)))
    db.commit()


def _chamar(db, desde=None, etag=None):
    headers = [(b"if-none-match", etag.encode())] if etag else []
    req = Request({"type": "http", "headers": headers, "method": "GET", "path": "/api/bipados", "query_string": b""})
    return painel_bips.api_bipados(req, desde=desde, db=db)


def test_primeiro_bip_de_cada_cte_com_serie_normalizada():
    db = _db()
    _bip(db, "k1", "111", 96945, "5", 5)     # primeiro bip
    _bip(db, "k2", "111", 96945, "05", 2)    # mesmo CT-e (série "05" = "5"): o primeiro vale
    _bip(db, "k3", "111", 500, "99", 3)      # minuta: "99" na chave impressa = "M" no GW
    _bip(db, "k4", "111", None, "5", 1)      # sem número: não serve
    r = _chamar(db)
    bips = json.loads(r.body)["bips"]
    assert len(bips) == 2, bips
    por_numero = {b[1]: b for b in bips}
    assert por_numero[96945][2] == "5" and por_numero[500][2] == "M"
    assert por_numero[96945][3] == (AGORA - datetime.timedelta(days=5)).isoformat() + "Z"
    assert bips[0][1] == 96945  # do mais antigo para o mais novo


def test_desde_ignora_bip_anterior_ao_periodo():
    db = _db()
    _bip(db, "a", "111", 1, "5", 30)
    _bip(db, "b", "111", 2, "5", 2)
    desde = (AGORA - datetime.timedelta(days=10)).date().isoformat()
    assert [b[1] for b in json.loads(_chamar(db, desde).body)["bips"]] == [2]
    assert len(json.loads(_chamar(db, None).body)["bips"]) == 2   # sem período: últimos 120 dias
    assert len(json.loads(_chamar(db, "lixo").body)["bips"]) == 2  # data inválida cai no padrão


def test_etag_304_e_bip_novo_aparece_na_hora():
    db = _db()
    _bip(db, "a", "111", 1, "5", 2)
    r1 = _chamar(db)
    etag = r1.headers["etag"]
    assert _chamar(db, etag=etag).status_code == 304
    _bip(db, "b", "111", 2, "5", 0)                      # bip novo
    r2 = _chamar(db, etag=etag)
    assert r2.status_code == 200 and r2.headers["etag"] != etag
    assert len(json.loads(r2.body)["bips"]) == 2
    db.query(Bipagem).filter(Bipagem.chave == "b").delete()   # bip desfeito
    db.commit()
    assert len(json.loads(_chamar(db).body)["bips"]) == 1


if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
