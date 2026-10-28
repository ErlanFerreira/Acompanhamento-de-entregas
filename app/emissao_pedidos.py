"""Etapas de um pedido da tela "Emissão de CT-e" (tabela emissoes_cte): quem
roda é o robô local do PC do escritório (scripts/robo_emissao.py) ou, com
ele desligado, o GitHub Actions (scripts/run_emissao_cte.py).

- preparar: faz tudo no GW menos salvar. Se a NF estiver em mais de um CT-e,
  grava as opções e para (status "escolher"); senão grava o resumo e o frete
  calculado (status "conferir"). Pedido com "salvar direto" (lote) e tudo
  batendo -- frete calculado pelo GW, um CT-e original só -- já salva aqui
  mesmo, numa passada só (status "salvo").
- salvar: refaz o lançamento com o CT-e original e o frete confirmados na
  tela e clica em "Salvar" (sem transmitir). Status "salvo" ou "erro".
"""

import datetime
import json
import logging
from dataclasses import asdict
from typing import Optional

from .emissao_cte import TIPOS_DA_NOTA, EmissaoError, SessaoGW, emitir
from .models import EmissaoCte

logger = logging.getLogger("emissao_pedidos")

ETAPA_DO_STATUS = {"preparando": "preparar", "salvando": "salvar"}


class _VariosCtes(Exception):
    def __init__(self, achados):
        self.achados = achados


def _escolher(achados):
    raise _VariosCtes(achados)


def _resumo(r) -> dict:
    o = r.original  # None em Normal/Cortesia (vêm da nota, sem CT-e original)
    return {
        "tipo": r.tipo,
        "numero_ct": r.numero_ct,
        "cte_original": o.numero if o else None,
        "serie_original": o.serie if o else None,
        "emissao_original": o.emissao if o else None,
        "total_original": o.total if o else None,
        "remetente": r.remetente,
        "destinatario": r.destinatario,
        "tomador": r.tomador,
        "origem_frete": r.origem_frete,
        "observacao": r.observacao,
    }


def _frete_do_gw(r) -> bool:
    """O frete veio do GW (tabela do cliente / CT-e original), não de alguém
    digitando -- é o que dá pra salvar sem ninguém conferir."""
    return not r.frete_pendente and r.frete > 0 and "informado" not in r.origem_frete


def _nao_salvou(r) -> EmissaoError:
    return EmissaoError(
        "O GW não confirmou o salvamento. CONFIRA na tela de Conhecimentos do GW se o CT-e "
        "foi gravado antes de tentar de novo. Mensagens do GW: " + " | ".join(r.mensagens[-5:])
    )


def preparar(db, em: EmissaoCte, sessao: Optional[SessaoGW] = None, simular: bool = False) -> None:
    direto = bool(em.salvar_direto)
    tentou_salvar = []

    def confirmar(r) -> bool:
        ok = direto and _frete_do_gw(r)
        tentou_salvar.append(ok)
        return ok

    try:
        r = emitir(
            em.tipo, em.chave_nfe,
            escolher_cte=_escolher,
            informar_frete=lambda motivo: None,  # quem pediu informa na tela
            perguntar=lambda msg: False,
            confirmar=confirmar,
            cte_id=em.cte_original_id,
            solicitante=em.solicitante,
            simular=simular or not direto,
            sessao=sessao,
        )
    except _VariosCtes as e:
        em.opcoes = json.dumps([asdict(c) for c in e.achados], ensure_ascii=False)
        em.status = "escolher"
        db.commit()
        logger.info("Emissão %s: NF em %s CT-e -- aguardando escolha.", em.id, len(e.achados))
        return

    em.cte_original_id = r.original.id if r.original else None
    em.resumo = json.dumps(_resumo(r), ensure_ascii=False)
    em.frete_calculado = None if r.frete_pendente else r.frete
    em.total_prestacao = None if r.frete_pendente else r.total_prestacao
    em.numero_ct = r.numero_ct
    em.mensagens = json.dumps(r.mensagens, ensure_ascii=False)
    # Na simulação o emitir não chega a chamar o confirmar -- decide aqui.
    if r.salvo or (simular and direto and _frete_do_gw(r)):
        em.frete_final = r.frete
        em.status = "salvo"
        if not r.salvo:
            em.numero_ct = f"SIMULAÇÃO ({r.numero_ct})"
        db.commit()
        logger.info("Emissão %s: salva direto (CT-e %s).", em.id, em.numero_ct)
        return
    if any(tentou_salvar):
        raise _nao_salvou(r)
    em.status = "conferir"
    db.commit()
    logger.info("Emissão %s: pronta para conferência (frete %s).", em.id, em.frete_calculado)


def salvar(db, em: EmissaoCte, sessao: Optional[SessaoGW] = None, simular: bool = False) -> None:
    da_nota = em.tipo in TIPOS_DA_NOTA
    if (not da_nota and not em.cte_original_id) or not em.frete_final:
        raise EmissaoError("Pedido sem CT-e original ou frete confirmado -- refaça pela tela.")

    def confirmar(r) -> bool:
        # Confere que o GW montou o mesmo lançamento que a pessoa viu.
        mesmo_original = (r.original.id if r.original else None) == em.cte_original_id
        return mesmo_original and abs(r.frete - em.frete_final) < 0.005

    r = emitir(
        em.tipo, em.chave_nfe,
        escolher_cte=_escolher,
        informar_frete=lambda motivo: em.frete_final,
        perguntar=lambda msg: False,
        confirmar=confirmar,
        frete_manual=em.frete_final,
        cte_id=em.cte_original_id,
        solicitante=em.solicitante,
        simular=simular,
        sessao=sessao,
    )
    em.mensagens = json.dumps(r.mensagens, ensure_ascii=False)
    em.total_prestacao = r.total_prestacao
    if not confirmar(r):
        raise EmissaoError(
            f"O GW montou um lançamento diferente do conferido (frete {r.frete:.2f}). Nada foi salvo."
        )
    if r.salvo or simular:
        em.numero_ct = r.numero_ct if r.salvo else f"SIMULAÇÃO ({r.numero_ct})"
        em.status = "salvo"
        db.commit()
        logger.info("Emissão %s: CT-e %s salvo no GW.", em.id, em.numero_ct)
        return
    raise _nao_salvou(r)


def processar(db, emissao_id: int, acao: str, sessao: Optional[SessaoGW] = None, simular: bool = False) -> None:
    """Roda uma etapa do pedido; qualquer falha vira status "erro" com a
    mensagem (erros inesperados também sobem, pro log de quem chamou)."""
    em = db.query(EmissaoCte).filter(EmissaoCte.id == emissao_id).first()
    if not em:
        logger.warning("Emissão %s não encontrada.", emissao_id)
        return
    esperado = {"preparar": "preparando", "salvar": "salvando"}[acao]
    if em.status != esperado:
        logger.info("Emissão %s está '%s', não '%s' -- nada a fazer.", emissao_id, em.status, esperado)
        return
    try:
        em.atualizado_em = datetime.datetime.utcnow()  # início do processamento (ver expiração em main.py)
        db.commit()
        (preparar if acao == "preparar" else salvar)(db, em, sessao=sessao, simular=simular)
    except Exception as exc:
        db.rollback()
        em = db.query(EmissaoCte).filter(EmissaoCte.id == emissao_id).first()
        if em:
            em.status = "erro"
            em.erro_mensagem = str(exc)[:990]
            db.commit()
        if not isinstance(exc, EmissaoError):
            raise
