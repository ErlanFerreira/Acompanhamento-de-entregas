"""Dados das filiais emissoras de CT-e (por CNPJ), usados no cabeçalho do
protocolo de envio de faturas e na coluna "Filial Ct-e" do controle mensal
de comprovantes (quando o relatório do GW não traz a coluna de filial).

Filial sem cadastro aqui sai só com o CNPJ -- acrescente conforme precisar.
"""

FILIAIS = {
    "20343618000161": {
        "apelido": "MATRIZ",
        "razao_social": "G. FREIRE BEZERRA DE MORAIS",
        "endereco": "R. Jose Jaime Coutinho Dias, 705 a 715 - Bairro Novo / Carpina",
    },
}


def fmt_cnpj(cnpj: str | None) -> str:
    d = "".join(c for c in str(cnpj or "") if c.isdigit())
    if len(d) != 14:
        return cnpj or ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def dados_filial(cnpj: str | None) -> dict:
    f = FILIAIS.get(cnpj or "", {})
    return {
        "cnpj": cnpj,
        "apelido": f.get("apelido") or fmt_cnpj(cnpj),
        "razao_social": f.get("razao_social") or "",
        "endereco": f.get("endereco") or "",
    }
