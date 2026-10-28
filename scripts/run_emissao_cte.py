"""Executado pelo workflow do GitHub Actions (.github/workflows/emissao_cte.yml)
a cada etapa de um pedido da tela "Emissão de CT-e" do painel.

Uso: python scripts/run_emissao_cte.py <emissao_id> <preparar|salvar>

- preparar: faz tudo no GW menos salvar. Se a NF estiver em mais de um CT-e,
  grava as opções e para (status "escolher"); senão grava o resumo e o
  frete calculado (status "conferir").
- salvar: refaz o lançamento com o CT-e original e o frete confirmados na
  tela e clica em "Salvar" (sem transmitir). Status "salvo" ou "erro".

Variáveis de ambiente necessárias: DATABASE_URL, PORTAL_EMAIL, PORTAL_SENHA
(no workflow, o login do GW próprio do bot). Precisa de `playwright install
chromium` antes de rodar.
"""

import datetime
import json
import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal  # noqa: E402
from app.emissao_cte import TIPOS, TIPOS_DA_NOTA, EmissaoError, emitir  # noqa: E402
from app.models import EmissaoCte  # noqa: E402


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


def preparar(db, em: EmissaoCte) -> None:
    try:
        r = emitir(
            em.tipo, em.chave_nfe,
            escolher_cte=_escolher,
            informar_frete=lambda motivo: None,  # o supervisor informa na tela
            perguntar=lambda msg: False,
            confirmar=lambda r: False,
            cte_id=em.cte_original_id,
            solicitante=em.solicitante,
            simular=True,
        )
    except _VariosCtes as e:
        em.opcoes = json.dumps([asdict(c) for c in e.achados], ensure_ascii=False)
        em.status = "escolher"
        db.commit()
        print(f"Emissão {em.id}: NF em {len(e.achados)} CT-e -- aguardando escolha.")
        return

    em.cte_original_id = r.original.id if r.original else None
    em.resumo = json.dumps(_resumo(r), ensure_ascii=False)
    em.frete_calculado = None if r.frete_pendente else r.frete
    em.total_prestacao = None if r.frete_pendente else r.total_prestacao
    em.numero_ct = r.numero_ct
    em.mensagens = json.dumps(r.mensagens, ensure_ascii=False)
    em.status = "conferir"
    db.commit()
    print(f"Emissão {em.id}: pronta para conferência (frete {em.frete_calculado}).")


def salvar(db, em: EmissaoCte) -> None:
    da_nota = TIPOS[em.tipo][0] in TIPOS_DA_NOTA
    if (not da_nota and not em.cte_original_id) or not em.frete_final:
        raise EmissaoError("Pedido sem CT-e original ou frete confirmado -- refaça pela tela.")

    def confirmar(r) -> bool:
        # Confere que o GW montou o mesmo lançamento que o supervisor viu.
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
    )
    em.mensagens = json.dumps(r.mensagens, ensure_ascii=False)
    em.total_prestacao = r.total_prestacao
    if r.salvo:
        em.numero_ct = r.numero_ct
        em.status = "salvo"
        db.commit()
        print(f"Emissão {em.id}: CT-e {r.numero_ct} salvo no GW.")
        return
    if not confirmar(r):
        raise EmissaoError(
            f"O GW montou um lançamento diferente do conferido (frete {r.frete:.2f}). Nada foi salvo."
        )
    raise EmissaoError(
        "O GW não confirmou o salvamento. CONFIRA na tela de Conhecimentos do GW se o CT-e "
        "foi gravado antes de tentar de novo. Mensagens do GW: " + " | ".join(r.mensagens[-5:])
    )


def main(emissao_id: int, acao: str):
    db = SessionLocal()
    try:
        em = db.query(EmissaoCte).filter(EmissaoCte.id == emissao_id).first()
        if not em:
            print(f"Emissão {emissao_id} não encontrada.")
            return
        esperado = {"preparar": "preparando", "salvar": "salvando"}[acao]
        if em.status != esperado:
            print(f"Emissão {emissao_id} está '{em.status}', não '{esperado}' -- nada a fazer.")
            return
        em.atualizado_em = datetime.datetime.utcnow()  # início do processamento (ver expiração em main.py)
        db.commit()
        (preparar if acao == "preparar" else salvar)(db, em)
    except Exception as exc:
        resumo = " ".join(str(exc).split())[:900]
        print(f"::error title=Emissão de CT-e {emissao_id} falhou::{resumo}")
        db.rollback()
        em = db.query(EmissaoCte).filter(EmissaoCte.id == emissao_id).first()
        if em:
            em.status = "erro"
            em.erro_mensagem = str(exc)[:990]
            db.commit()
        if not isinstance(exc, EmissaoError):
            raise
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[2] not in ("preparar", "salvar"):
        print("Uso: python scripts/run_emissao_cte.py <emissao_id> <preparar|salvar>")
        sys.exit(1)
    main(int(sys.argv[1]), sys.argv[2])
