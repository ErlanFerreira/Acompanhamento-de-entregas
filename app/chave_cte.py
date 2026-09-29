"""Decodificação da chave de acesso do CT-e (44 dígitos) -- é o que o código
de barras do DACTE carrega. O QR Code do DACTE traz uma URL da SEFAZ com a
mesma chave no parâmetro `chCTe`, então aceita as duas leituras.

Também aceita o código de barras da minuta do GW (modelo 99, mesmo layout).

Layout da chave (Manual do CT-e):
  cUF(2) AAMM(4) CNPJ emitente(14) modelo(2) série(3) nCT(9) tpEmis(1) cCT(8) DV(1)

A NF e o conhecimento do parceiro NÃO fazem parte da chave -- vêm do banco
sincronizado com o GW (ver `sync.py`).
"""

import re

MODELO_CTE = "57"
MODELO_CTE_OS = "67"
# Minuta do GW (série "M") -- não é documento fiscal, mas o GW imprime um
# código de barras no mesmo layout da chave do CT-e, com modelo 99.
MODELO_MINUTA = "99"

TIPOS = {MODELO_CTE: "CT-e", MODELO_CTE_OS: "CT-e OS", MODELO_MINUTA: "Minuta"}

UFS = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
    "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR",
    "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}


class ChaveInvalida(ValueError):
    pass


def extrair_chave(leitura: str) -> str:
    """Tira a chave de 44 dígitos do que o leitor mandou -- número puro,
    com espaços/pontuação, ou a URL do QR Code (`...?chCTe=<chave>&...`)."""
    texto = (leitura or "").strip()
    m = re.search(r"chCTe=(\d{44})", texto, re.IGNORECASE)
    if m:
        return m.group(1)
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) != 44:
        raise ChaveInvalida(f"Leitura com {len(digitos)} dígitos -- a chave do CT-e tem 44.")
    return digitos


def digito_verificador(chave43: str) -> int:
    """Módulo 11 com pesos 2..9 da direita pra esquerda (mesmo da NF-e)."""
    soma = 0
    peso = 2
    for c in reversed(chave43):
        soma += int(c) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def decodificar(leitura: str) -> dict:
    chave = extrair_chave(leitura)
    if digito_verificador(chave[:43]) != int(chave[43]):
        raise ChaveInvalida("Dígito verificador não confere -- leitura provavelmente incompleta, bipe de novo.")

    modelo = chave[20:22]
    if modelo not in TIPOS:
        raise ChaveInvalida(
            f"Modelo {modelo} não é CT-e (57) nem minuta (99) -- pode ser a chave de uma NF-e (55)."
        )

    cnpj = chave[6:20]
    return {
        "chave": chave,
        "uf": UFS.get(chave[0:2], chave[0:2]),
        "emissao_aamm": f"{chave[4:6]}/20{chave[2:4]}",
        "cnpj_emitente": cnpj,
        "cnpj_emitente_fmt": f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}",
        "modelo": modelo,
        "tipo": TIPOS[modelo],
        "serie": str(int(chave[22:25])),
        "numero": int(chave[25:34]),
    }
