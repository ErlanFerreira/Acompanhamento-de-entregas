"""Tipos de CT-e que o bot de emissão sabe lançar e validação da chave da
NF-e informada -- separado de `emissao_cte.py` porque o app web (Vercel)
também precisa disso e lá não tem Playwright."""

import re

from .chave_cte import digito_verificador

MODELO_NFE = "55"

# nome usado no terminal/painel -> (valor do <select id="tipoConhecimento"> no GW, rótulo)
# Na ordem em que aparecem na tela -- os mais pedidos primeiro.
TIPOS = {
    "normal": ("n", "Normal"),
    "reentrega": ("r", "Reentrega"),
    "devolucao": ("d", "Devolução"),
    "diaria": ("i", "Diárias"),
    "descarga": ("g", "Descarga"),
    "coleta_pallets": ("p", "Coleta/Pallets"),
    "complementar": ("c", "Complementar"),
    "substituicao": ("s", "Substituição"),
    "cortesia": ("b", "Cortesia"),
}

# Tipos que vêm direto da nota (importada no GW), sem CT-e original.
TIPOS_DA_NOTA = {"n", "b"}

# Pra tela de emissão do painel: ícone e explicação em linguagem do dia a dia.
EXPLICACOES = {
    "normal": ("🚚", "CT-e comum de uma nota que ainda não tem CT-e — o GW importa a nota e calcula o frete pela tabela."),
    "coleta_pallets": ("🪵", "Cobrança da coleta ou devolução de pallets ligada a um CT-e já emitido."),
    "cortesia": ("🎁", "Transporte sem cobrança — não entra no contas a receber. A nota é importada como no Normal."),
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
