"""Emite (salva sem transmitir) um CT-e de diária, reentrega, devolução,
descarga, complementar ou substituição no GW, a partir da chave da NF-e.

Uso:
    python scripts/emitir_cte.py <tipo> <chave_nfe> [--frete 150,00] [--simular] [--ver]

    tipo: diaria | reentrega | devolucao | descarga | complementar | substituicao

    --frete     usa esse valor de frete no lugar do que o GW calcular
    --simular   faz tudo no GW mas NÃO salva (pra conferir o que sairia)
    --ver       abre o navegador na tela, pra acompanhar os cliques

Busca no GW o CT-e que já tem essa NF; se houver mais de um, pergunta qual.
O frete vem da tabela de preço do tomador (reentrega/devolução) ou do CT-e
original (substituição); quando o GW não tiver o valor, pergunta aqui. Antes
de salvar mostra um resumo e pede confirmação. A transmissão à SEFAZ fica
com você, no GW.

Variáveis de ambiente necessárias: PORTAL_EMAIL, PORTAL_SENHA (ver .env).
Precisa de `playwright install chromium` antes de rodar. O GW só aceita uma
sessão por login -- quem estiver logado com o mesmo usuário é desconectado.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.emissao_cte import TIPOS, CteOriginal, EmissaoError, Resultado, emitir  # noqa: E402


def _valor(texto: str) -> float:
    texto = texto.strip().replace("R$", "").replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    return float(texto)


def _moeda(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def escolher_cte(achados: list[CteOriginal]) -> CteOriginal | None:
    print(f"\nA NF está em {len(achados)} CT-e no GW:")
    for i, c in enumerate(achados, 1):
        print(f"  {i}) CT-e {c.numero} série {c.serie} | {c.emissao} | {c.filial} | "
              f"{c.remetente} -> {c.destinatario} | {_moeda(c.total)}")
    while True:
        resp = input("Qual usar? (número, Enter cancela): ").strip()
        if not resp:
            return None
        if resp.isdigit() and 1 <= int(resp) <= len(achados):
            return achados[int(resp) - 1]
        print("Opção inválida.")


def informar_frete(motivo: str) -> float:
    print(f"\nO GW não tem o valor do frete: {motivo}.")
    while True:
        resp = input("Informe o valor do frete (ex: 150,00): ").strip()
        try:
            valor = _valor(resp)
        except ValueError:
            print("Valor inválido.")
            continue
        if valor <= 0:
            print("O frete precisa ser maior que zero.")
            continue
        return valor


def perguntar(mensagem: str) -> bool:
    print(f"\nO GW perguntou: {mensagem}")
    return input("Responder SIM? [s/N]: ").strip().lower() in ("s", "sim")


def _resumo(r: Resultado) -> str:
    o = r.original
    origem = (f"{o.numero} série {o.serie} de {o.emissao} ({_moeda(o.total)})" if o
              else "— (importado da nota fiscal)")
    return (
        f"\n  Tipo:            {r.tipo}\n"
        f"  Número CT:       {r.numero_ct}\n"
        f"  CT-e original:   {origem}\n"
        f"  Remetente:       {r.remetente}\n"
        f"  Destinatário:    {r.destinatario}\n"
        f"  Tomador:         {r.tomador}\n"
        f"  Frete:           {_moeda(r.frete)} -- {r.origem_frete}\n"
        f"  Total prestação: {_moeda(r.total_prestacao)}\n"
        f"  Observação:      {r.observacao}"
    )


def confirmar(r: Resultado) -> bool:
    print(_resumo(r))
    return input("\nSalvar esse CT-e no GW (sem transmitir)? [s/N]: ").strip().lower() in ("s", "sim")


def main() -> int:
    parser = argparse.ArgumentParser(description="Emite CT-e derivado no GW a partir da chave da NF-e.")
    parser.add_argument("tipo", choices=list(TIPOS))
    parser.add_argument("chave", help="chave de acesso da NF-e (44 dígitos)")
    parser.add_argument("--frete", type=_valor, help="valor do frete (ex: 150,00)")
    parser.add_argument("--simular", action="store_true", help="não salva, só mostra o que sairia")
    parser.add_argument("--ver", action="store_true", help="mostra o navegador")
    parser.add_argument("--solicitante", help="nome de quem pediu (vai na observação do CT-e)")
    parser.add_argument("--cubagem", type=_valor, help="m³ da carga, se o frete do cliente for por cubagem (ex: 1,25)")
    args = parser.parse_args()

    print("Entrando no GW e buscando o CT-e da NF...")
    try:
        r = emitir(
            args.tipo, args.chave,
            escolher_cte=escolher_cte, informar_frete=informar_frete,
            perguntar=perguntar, confirmar=confirmar,
            frete_manual=args.frete, solicitante=args.solicitante, cubagem=args.cubagem, simular=args.simular, headless=not args.ver,
        )
    except EmissaoError as e:
        print(f"\nERRO: {e}")
        return 1

    avisos = [m for m in r.mensagens if m]
    if avisos:
        print("\nMensagens do GW:")
        for m in avisos:
            print(f"  - {m}")
    if args.simular:
        print(_resumo(r))
        print("\nSimulação: nada foi salvo no GW.")
    elif r.salvo:
        print(f"\nCT-e {r.numero_ct} salvo no GW (NÃO transmitido). Confira e transmita pela tela de Conhecimentos.")
    else:
        print("\nCT-e NÃO salvo (cancelado, ou o GW recusou -- veja as mensagens acima; "
              "se houver, emissao_cte_salvar_falha.png mostra a tela).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
