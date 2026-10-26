"""Bloqueio de login após várias senhas erradas.

Os contadores ficam no banco (tabela `tentativas_login`) e não em memória:
na Vercel cada requisição pode cair numa instância diferente.

Cada tentativa errada conta em uma ou mais chaves, cada uma com seu limite
de erros dentro da janela; estourou, a chave fica bloqueada por
`BLOQUEIO_MINUTOS` (mesmo com a senha certa):

    usuario:<login>  login de analista (existindo ou não, pra não revelar
                     quais logins existem)           -> 5 erros
    geral:<ip>       senha geral (supervisor), por endereço -> 10 erros
    ip:<ip>          qualquer erro vindo do endereço  -> 30 erros

O limite por endereço é mais folgado porque o escritório inteiro costuma
sair pelo mesmo IP.
"""

import datetime
import logging
import time

from fastapi import Request
from sqlalchemy.orm import Session

from .models import TentativaLogin

logger = logging.getLogger("bloqueio_login")

# Se o banco principal estiver fora do ar (ex.: cota de rede da Neon estourada),
# o login do supervisor (senha geral) não pode travar esperando ele: a primeira
# falha marca o banco como fora por 60 s e as chamadas seguintes nem tentam.
# Nesse intervalo o bloqueio por senha errada fica desligado (só tem efeito
# quando o banco responde).
_BANCO_FORA_ATE = 0.0


def _banco_fora() -> bool:
    return time.monotonic() < _BANCO_FORA_ATE


def _marcar_banco_fora(db: Session) -> None:
    global _BANCO_FORA_ATE
    _BANCO_FORA_ATE = time.monotonic() + 60
    try:
        db.rollback()
    except Exception:
        pass
    logger.warning("Banco indisponível: bloqueio de login desligado por 60 s.", exc_info=True)


JANELA_MINUTOS = 15
BLOQUEIO_MINUTOS = 15
LIMITES = {"usuario": 5, "geral": 10, "ip": 30}


def ip_cliente(request: Request) -> str:
    # Atrás do proxy da Vercel/Render o endereço real vem no X-Forwarded-For
    # (o primeiro da lista é o do cliente).
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "desconhecido")[:64]


def chaves_tentativa(request: Request, login: str) -> list[str]:
    ip = ip_cliente(request)
    return [f"usuario:{login}" if login else f"geral:{ip}", f"ip:{ip}"]


def _agora() -> datetime.datetime:
    return datetime.datetime.utcnow()


def minutos_bloqueado(db: Session, chaves: list[str]) -> int | None:
    """Minutos que faltam pro desbloqueio, se alguma das chaves estiver
    bloqueada; senão None."""
    agora = _agora()
    if _banco_fora():
        return None
    try:
        ate = [
            t.bloqueado_ate
            for t in db.query(TentativaLogin).filter(TentativaLogin.chave.in_(chaves)).all()
            if t.bloqueado_ate and t.bloqueado_ate > agora
        ]
    except Exception:
        _marcar_banco_fora(db)
        return None
    if not ate:
        return None
    return max(1, int((max(ate) - agora).total_seconds() // 60) + 1)


def registrar_falha(db: Session, chaves: list[str]) -> None:
    if _banco_fora():
        return
    try:
        _registrar_falha(db, chaves)
    except Exception:
        _marcar_banco_fora(db)


def _registrar_falha(db: Session, chaves: list[str]) -> None:
    agora = _agora()
    for chave in chaves:
        t = db.query(TentativaLogin).filter(TentativaLogin.chave == chave).first()
        if not t:
            t = TentativaLogin(chave=chave, falhas=0, primeira_falha=agora)
            db.add(t)
        if not t.primeira_falha or agora - t.primeira_falha > datetime.timedelta(minutes=JANELA_MINUTOS):
            t.falhas, t.primeira_falha = 0, agora  # janela venceu: recomeça a contar
        t.falhas += 1
        if t.falhas >= LIMITES[chave.split(":", 1)[0]]:
            t.bloqueado_ate = agora + datetime.timedelta(minutes=BLOQUEIO_MINUTOS)
            t.falhas, t.primeira_falha = 0, None
    db.commit()


def limpar(db: Session, chaves: list[str]) -> None:
    """Zera os contadores (login certo, ou desbloqueio pelo supervisor)."""
    if _banco_fora():
        return
    try:
        db.query(TentativaLogin).filter(TentativaLogin.chave.in_(chaves)).delete(synchronize_session=False)
        db.commit()
    except Exception:
        _marcar_banco_fora(db)


def bloqueios_de_usuarios(db: Session) -> dict[str, datetime.datetime]:
    """login -> bloqueado até (UTC), só dos analistas bloqueados agora."""
    agora = _agora()
    linhas = db.query(TentativaLogin).filter(TentativaLogin.chave.like("usuario:%"), TentativaLogin.bloqueado_ate > agora).all()
    return {t.chave.split(":", 1)[1]: t.bloqueado_ate for t in linhas}
