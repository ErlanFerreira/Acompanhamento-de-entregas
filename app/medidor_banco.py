"""Mede quanto cada tela/robô tira de cada banco (a cota gratuita da Neon é de
transferência de SAÍDA: o que as consultas devolvem; gravar não conta).

Cada cursor do Postgres soma o tamanho aproximado das linhas que devolve
(texto/bytes pelo comprimento, número/data/booleano ~8 bytes). É uma
estimativa por baixo -- o protocolo do Postgres tem um pouco de sobra --, mas
serve pra saber QUEM gasta. As somas ficam na memória e de tempos em tempos
vão pra tabela `meta` do banco principal, numa linha por dia + banco + origem:

    chave "ub:AAAA-MM-DD:<banco>:<origem>"  valor "consultas|linhas|bytes"

<banco>: p = principal, s = separado (login/tratativas/consultas),
e = emissão, o = outro. <origem>: nome da rota do app (ex. api_cargas) ou
"job:<script>" nos robôs do GitHub Actions/PC. A tela /uso-banco mostra os
números.
"""

import atexit
import contextvars
import datetime
import logging
import os
import sys
import threading
import time

logger = logging.getLogger("medidor_banco")

_SIGLAS = {"principal": "p", "separado": "s", "emissao": "e"}
_INTERVALO_GRAVACAO = 60  # segundos entre gravações das somas no banco

# Origem da requisição em andamento (None fora de uma requisição do app).
_origem = contextvars.ContextVar("origem_leitura_banco", default=None)
_trava = threading.Lock()
# (banco, origem) -> [consultas, linhas, bytes] ainda não gravados.
_pendente: dict[tuple[str, str], list[int]] = {}
_ultima_gravacao = time.monotonic()
_origem_fora = "job:" + os.path.splitext(os.path.basename(sys.argv[0] or "python"))[0][:28]
_gravando = threading.local()
ultimo_erro: str | None = None  # mostrado em /uso-banco se a gravação falhar


def _tamanho(v) -> int:
    if v is None:
        return 1
    if isinstance(v, (str, bytes, bytearray)):
        return len(v)
    if isinstance(v, memoryview):
        return v.nbytes
    return 8


def _somar(banco: str, linhas: list) -> None:
    if getattr(_gravando, "sim", False):
        return  # a própria gravação das somas não entra na conta
    nbytes = sum(sum(_tamanho(v) for v in linha) + 4 for linha in linhas if linha is not None)
    chave = (banco, _origem.get() or _origem_fora)
    with _trava:
        soma = _pendente.setdefault(chave, [0, 0, 0])
        soma[1] += len(linhas)
        soma[2] += nbytes


def _contar_consulta(banco: str) -> None:
    if getattr(_gravando, "sim", False):
        return
    chave = (banco, _origem.get() or _origem_fora)
    with _trava:
        _pendente.setdefault(chave, [0, 0, 0])[0] += 1


def fabrica_de_cursor(rotulo: str):
    """cursor_factory do psycopg2 que mede o que o cursor devolve."""
    import psycopg2.extensions

    banco = _SIGLAS.get(rotulo, "o")

    class CursorMedido(psycopg2.extensions.cursor):
        def execute(self, query, vars=None):
            _contar_consulta(banco)
            return super().execute(query, vars)

        def fetchone(self):
            linha = super().fetchone()
            if linha is not None:
                _somar(banco, [linha])
            return linha

        def fetchmany(self, size=None):
            linhas = super().fetchmany(size) if size is not None else super().fetchmany()
            _somar(banco, linhas)
            return linhas

        def fetchall(self):
            linhas = super().fetchall()
            _somar(banco, linhas)
            return linhas

    return CursorMedido


def iniciar_requisicao(origem: str):
    return _origem.set((origem or "?")[:30])


def terminar_requisicao(token) -> bool:
    """Fecha a requisição; True quando já passou da hora de gravar as somas
    (quem chama grava fora do laço de eventos, com `gravar()`)."""
    _origem.reset(token)
    return time.monotonic() - _ultima_gravacao >= _INTERVALO_GRAVACAO


def gravar() -> None:
    """Soma o que está na memória nas linhas `meta` do banco principal. Nunca
    levanta erro (medir não pode derrubar a tela nem o robô)."""
    global _ultima_gravacao, ultimo_erro
    with _trava:
        pendente = {k: v for k, v in _pendente.items() if any(v)}
        _pendente.clear()
        _ultima_gravacao = time.monotonic()
    if not pendente:
        return
    from .db import engine

    dia = datetime.date.today().isoformat()
    _gravando.sim = True
    try:
        with engine.begin() as conn:
            for (banco, origem), (consultas, linhas, nbytes) in pendente.items():
                chave = f"ub:{dia}:{banco}:{origem}"[:50]
                _upsert(conn, engine.dialect.name, chave, consultas, linhas, nbytes)
        ultimo_erro = None
    except Exception as exc:
        ultimo_erro = f"{datetime.datetime.now():%d/%m %H:%M} -- {' '.join(str(exc).split())[:300]}"
        logger.warning("Não foi possível gravar a medição de uso do banco.", exc_info=True)
    finally:
        _gravando.sim = False


def _upsert(conn, dialeto: str, chave: str, consultas: int, linhas: int, nbytes: int) -> None:
    from sqlalchemy import text

    novo = f"{consultas}|{linhas}|{nbytes}"
    if dialeto == "postgresql":
        # Soma no próprio banco, sem ler a linha antes (ler gastaria cota).
        conn.execute(text(
            "INSERT INTO meta (chave, valor) VALUES (:c, :v) ON CONFLICT (chave) DO UPDATE SET valor = "
            "(split_part(meta.valor, '|', 1)::bigint + :q)::text || '|' || "
            "(split_part(meta.valor, '|', 2)::bigint + :l)::text || '|' || "
            "(split_part(meta.valor, '|', 3)::bigint + :b)::text"
        ), {"c": chave, "v": novo, "q": consultas, "l": linhas, "b": nbytes})
        return
    atual = conn.execute(text("SELECT valor FROM meta WHERE chave = :c"), {"c": chave}).scalar()
    if atual:
        q, l, b = (int(x) for x in atual.split("|"))
        conn.execute(text("UPDATE meta SET valor = :v WHERE chave = :c"),
                     {"c": chave, "v": f"{q + consultas}|{l + linhas}|{b + nbytes}"})
    else:
        conn.execute(text("INSERT INTO meta (chave, valor) VALUES (:c, :v)"), {"c": chave, "v": novo})


def _gravar_periodicamente() -> None:
    while True:
        time.sleep(_INTERVALO_GRAVACAO * 2)
        gravar()


# Robôs (GitHub Actions, PC do escritório) não passam pelas requisições do app:
# gravam ao sair e, se rodarem por muito tempo, de 2 em 2 minutos.
atexit.register(gravar)
if not os.environ.get("VERCEL"):
    threading.Thread(target=_gravar_periodicamente, name="medidor_banco", daemon=True).start()
