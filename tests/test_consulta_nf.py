"""Consulta de NFs: o CT-e achado pelo número da NF só vale se for do cliente da
linha da planilha.

Caso real que motivou o teste: a NF 61355 existe em dois CT-e -- um "Diárias"
de 2025 (outros clientes) e um "Normal" de 2026. A leitura antiga só reconhecia
linhas do tipo "Normal": emendava a linha "Diárias" com a seguinte, e o CT-e de
2025 era devolvido com os nomes do CT-e certo. Dados abaixo são fictícios.

Rodar:  python tests/test_consulta_nf.py   (ou pytest tests)
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "scripts"))

import run_consulta  # noqa: E402
from app import scrape  # noqa: E402


def _linha(classe, id_, cte, tipo, tomador, remetente, destinatario):
    # mesma estrutura da tabela de resultado da Consulta Entrega do GW
    return f"""
<tr class="{classe}" style=""> <td> <img src="img/plus.jpg" id="plus_{id_}" name="plus_{id_}" class="imagemLink" align="right">
 <img style="display:none" src="img/minus.jpg" id="minus_{id_}" name="minus_{id_}" class="imagemLink" align="right"> </td>
 <td> <div class="linkEditar" onclick="verCtrc('{id_}','ct');"> <font size="1">{cte}</font></div> </td>
 <td> {tipo}</td> <td> MATRIZ</td> <td> {tomador}</td> <td> {remetente}</td> <td> {destinatario}</td> <td> </td> <td> 0,01</td> <td> CON</td> </tr>
<tr class="{classe}"> <td> <img src="img/atencao.gif"> </td> <td colspan="3"> <b>STATUS: Entrega Realizada Normalmente</b> </td>
 <td> <b>Notas Fiscais: <img id="plusNF_{id_}" src="img/plus.jpg"> 61355</b> </td> </tr>
<tr id="trDetalheNF_{id_}" style="display:none;"> <td class="{classe}"></td> <td colspan="10">--</td> </tr>
"""


HTML = (
    "<html><body><form id='formBx'><table>"
    + _linha("CelulaZebra1", "100", "000111/M", "Diárias", "Transportadora Beta Ltda", "Atacado Gama SA", "Industria Delta SA")
    + _linha("CelulaZebra2", "200", "000222/5", "Normal", "Transportadora Beta Ltda", "Lubrificantes Alfa Especiais", "Motores Omega Ltda")
    + "</table></form></body></html>"
)


def test_cada_cte_fica_com_os_proprios_nomes():
    linhas = scrape.ler_linhas_ctes(HTML)
    assert [l["cte"] for l in linhas] == ["000111/M", "000222/5"]
    diarias, normal = linhas
    assert diarias["tipo"] == "Diárias"
    assert (diarias["remetente"], diarias["destinatario"]) == ("Atacado Gama SA", "Industria Delta SA")
    assert normal["tipo"] == "Normal"
    assert (normal["remetente"], normal["destinatario"]) == ("Lubrificantes Alfa Especiais", "Motores Omega Ltda")
    assert normal["idconhecimento"] == "200"


def test_so_o_cte_do_cliente_da_planilha_e_aceito():
    nomes = scrape._extrair_nomes_por_cte(HTML)
    matches = [{"cte": cte, **n} for cte, n in nomes.items()]
    aceitos = run_consulta._filtrar_por_parceiro(matches, ["Lubrificantes Alfa Especiais", "Motores Omega Ltda"])
    assert [m["cte"] for m in aceitos] == ["000222/5"]


def test_cliente_que_nao_esta_em_nenhum_cte_nao_recebe_nada():
    nomes = scrape._extrair_nomes_por_cte(HTML)
    matches = [{"cte": cte, **n} for cte, n in nomes.items()]
    assert run_consulta._filtrar_por_parceiro(matches, ["Empresa Que Nao Aparece SA"]) == []


def test_cte_sem_nome_lido_nao_e_aceito():
    sem_nome = {"cte": "000333/M", "consignatario": None, "remetente": None, "destinatario": None}
    assert run_consulta._filtrar_por_parceiro([sem_nome], ["Lubrificantes Alfa Especiais"]) == []


if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
