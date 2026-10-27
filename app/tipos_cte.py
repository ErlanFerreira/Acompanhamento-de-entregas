"""Tipos de CT-e que o bot de emissão sabe lançar e validação da chave da
NF-e informada -- separado de `emissao_cte.py` porque o app web (Vercel)
também precisa disso e lá não tem Playwright."""

import re

from .chave_cte import digito_verificador

MODELO_NFE = "55"

# nome usado no terminal/painel -> (valor do <select id="tipoConhecimento"> no GW, rótulo)
# Na ordem em que aparecem na tela -- os mais pedidos primeiro.
TIPOS = {
    "reentrega": ("r", "Reentrega"),
    "devolucao": ("d", "Devolução"),
    "diaria": ("i", "Diárias"),
    "descarga": ("g", "Descarga"),
    "complementar": ("c", "Complementar"),
    "substituicao": ("s", "Substituição"),
}

# Pra tela de emissão do painel: ícone e explicação em linguagem do dia a dia.
EXPLICACOES = {
    "reentrega": ("🔁", "A entrega não deu certo e a mercadoria vai sair de novo para o mesmo destinatário."),
    "devolucao": ("↩️", "A mercadoria volta para o remetente — o GW inverte remetente e destinatário."),
    "diaria": ("⏱️", "Cobrança de diária do veículo que ficou parado esperando carregar ou descarregar."),
    "descarga": ("📦", "Cobrança do serviço de descarga da mercadoria."),
    "complementar": ("➕", "Cobra a diferença de um CT-e que saiu com valor menor que o devido."),
    "substituicao": ("📝", "Substitui um CT-e que saiu com dados errados, mantendo o mesmo valor."),
}


class EmissaoError(Exception):
    pass


def validar_chave_nfe(leitura: str) -> str:
    chave = re.sub(r"\D", "", leitura or "")
    if len(chave) != 44:
        raise EmissaoError(f"A chave da NF-e tem 44 dígitos -- veio com {len(chave)}.")
    if digito_verificador(chave[:43]) != int(chave[43]):
        raise EmissaoError("Dígito verificador da chave não confere -- confira se foi digitada inteira.")
    if chave[20:22] != MODELO_NFE:
        raise EmissaoError(f"Modelo {chave[20:22]} não é NF-e (55) -- informe a chave da nota fiscal, não do CT-e.")
    return chave
