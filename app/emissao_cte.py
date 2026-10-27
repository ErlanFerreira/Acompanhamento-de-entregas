"""Emissão de CT-e derivado (diária, reentrega, devolução, descarga,
complementar, substituição) no portal Webtrans (GW Sistemas), a partir da
chave da NF-e.

Faz exatamente o que o usuário faria na tela "Conhecimentos > Novo cadastro"
(`frameset_conhecimento`):

1. escolhe o "Tipo" do CT-e;
2. clica no "..." ao lado do tipo, que abre o popup "Localizar conhecimento
   de transporte para complemento, devolução, descarga, reentrega ou
   diárias" (`pops/localiza_conhecimento.jsp`), e pesquisa por
   "Chave Acesso NF-e";
3. escolhe o CT-e original -- o próprio GW copia remetente, destinatário,
   tomador, notas e calcula o frete (`retornapai` do popup);
4. confere o frete e SALVA sem transmitir (o botão "Salvar [F8]", nunca o
   "Salvar e Transmitir") -- a transmissão à SEFAZ fica com o usuário.

De onde vem o frete (regra do próprio popup do GW):
- Reentrega/Devolução: total do CT-e original x "% reentrega"/"% devolução"
  da tabela de preço do cliente, respeitando o frete mínimo. Sem tabela ou
  com o % zerado, o GW não tem o valor -> pergunta ao usuário.
- Substituição: o mesmo valor do CT-e original.
- Diária/Descarga/Complementar: a tabela não tem valor próprio pra isso (o
  GW só repete o total do original) -> pergunta ao usuário.

As interações (escolher entre vários CT-e, informar frete, responder a
confirmações do GW, confirmar antes de salvar) ficam a cargo de quem chama,
via callbacks -- ver `scripts/emitir_cte.py`.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from playwright.sync_api import Dialog, Frame, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import scrape
from .tipos_cte import TIPOS, EmissaoError, validar_chave_nfe  # noqa: F401 (reexportados pro CLI)

logger = logging.getLogger("emissao_cte")

CADASTRO_URL = f"{scrape.PORTAL_URL}/frameset_conhecimento?acao=iniciar"

# Campo escondido de cada linha do popup com o % da tabela do cliente que o
# GW aplica sobre o total do CT-e original.
_CAMPO_PERCENTUAL = {"r": "valorReentrega", "d": "valorDevolucao"}


@dataclass
class CteOriginal:
    id: str
    numero: str
    serie: str
    frete: str
    emissao: str
    filial: str
    remetente: str
    destinatario: str
    total: float
    tabela_id: int
    percentual_reentrega: float
    percentual_devolucao: float
    frete_minimo: float


@dataclass
class Resultado:
    tipo: str
    original: CteOriginal
    numero_ct: str
    remetente: str
    destinatario: str
    tomador: str
    frete: float
    total_prestacao: float
    origem_frete: str
    frete_pendente: bool = False
    salvo: bool = False
    mensagens: list[str] = field(default_factory=list)


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _fmt_moeda(v: float) -> str:
    return f"{v:.2f}"


class _Dialogos:
    """Responde aos alert/confirm do GW. As confirmações conhecidas do popup
    têm resposta fixa; qualquer outra vai pro usuário decidir (ex: "Modalidade
    do frete diferente do cadastro do cliente. Deseja continuar?")."""

    RESPOSTAS_FIXAS = (
        ("acrescentar as notas fiscais", True),
        # Devolução: a mercadoria volta do destinatário pro remetente.
        ("inverter o remetente e destinat", True),
    )

    def __init__(self, perguntar: Callable[[str], bool]):
        self.perguntar = perguntar
        self.mensagens: list[str] = []

    def __call__(self, dialog: Dialog) -> None:
        msg = dialog.message.strip()
        self.mensagens.append(msg)
        if dialog.type != "confirm":
            logger.info("GW avisou: %s", msg)
            dialog.accept()
            return
        for trecho, resposta in self.RESPOSTAS_FIXAS:
            if trecho in msg.lower():
                dialog.accept() if resposta else dialog.dismiss()
                return
        dialog.accept() if self.perguntar(msg) else dialog.dismiss()


def _abrir_cadastro(page: Page) -> Frame:
    for _tentativa in range(3):
        page.goto(CADASTRO_URL, wait_until="networkidle")
        frame = page.frame(name="framePrincipal")
        if frame is not None:
            try:
                frame.wait_for_selector("#tipoConhecimento", timeout=15_000)
                return frame
            except PlaywrightTimeoutError:
                pass
        page.wait_for_timeout(3000)
    scrape._salvar_debug(page, "emissao_cte")
    raise EmissaoError(f"Não consegui abrir a tela de lançamento de CT-e. {scrape._resumo_pagina(page)}")


def _pesquisar_por_chave(popup: Page, chave: str) -> None:
    # Mesmo que o usuário faria: "Chave Acesso NF-e", "Igual à palavra/frase",
    # todas as filiais. As datas precisam ir vazias, senão o `consulta()` do
    # popup também filtra pelo período (hoje, por padrão).
    popup.select_option("#campo_consulta", "chave_acesso")
    popup.evaluate(
        """(chave) => {
            document.getElementById('operador_consulta').value = '4';
            document.getElementById('valor_consulta').value = chave;
            document.getElementById('filialId').value = '0';
            document.getElementById('dtemissao1').value = '';
            document.getElementById('dtemissao2').value = '';
        }""",
        chave,
    )
    with popup.expect_navigation(wait_until="networkidle", timeout=60_000):
        popup.click("#pesquisar")


def _ler_resultados(popup: Page) -> list[CteOriginal]:
    linhas = popup.evaluate(
        """() => [...document.querySelectorAll('div.linkEditar[onclick*="retornapai("]')].map(div => {
            const id = div.getAttribute('onclick').match(/retornapai\\((\\d+)\\)/)[1];
            const v = (n) => (document.getElementById(n + id) || {}).value || '';
            const cels = [...div.closest('tr').children].map(td => td.innerText.trim());
            return {
                id, cels,
                numero: v('numero_'), total: v('total_'), tabela: v('client_tariff_id_'),
                reentrega: v('valorReentrega_'), devolucao: v('valorDevolucao_'), minimo: v('valor_frete_minimo_'),
            };
        })"""
    )
    resultados = []
    for l in linhas:
        cels = l["cels"] + [""] * 8
        resultados.append(CteOriginal(
            id=l["id"],
            numero=l["numero"] or cels[0],
            serie=cels[1],
            frete=cels[2],
            emissao=cels[3],
            filial=cels[4],
            remetente=cels[5],
            destinatario=cels[6],
            total=_num(l["total"]),
            tabela_id=int(_num(l["tabela"])),
            percentual_reentrega=_num(l["reentrega"]),
            percentual_devolucao=_num(l["devolucao"]),
            frete_minimo=_num(l["minimo"]),
        ))
    return resultados


def _ler_formulario(frame: Frame) -> dict:
    return frame.evaluate(
        """() => {
            const v = (id) => (document.getElementById(id) || {}).value || '';
            return {
                numero_ct: v('nfiscal'), remetente: v('rem_rzs'), destinatario: v('dest_rzs'),
                tomador: v('con_rzs'), valor_frete: v('valor_frete'), total: v('total'),
                id_original: v('id_dev_reen'), id_conhecimento: v('idConhecimento'),
            };
        }"""
    )


def _frete_da_tabela(tipo: str, original: CteOriginal, valor_frete_gw: float) -> tuple[Optional[float], str]:
    """Valor que o GW achou sozinho e de onde veio -- (None, motivo) quando
    o GW não tem como saber e o usuário precisa informar."""
    if tipo == "s":
        return valor_frete_gw, f"mesmo valor do CT-e original {original.numero}"
    campo = _CAMPO_PERCENTUAL.get(tipo)
    if campo is None:
        return None, "a tabela de preço não tem valor para esse tipo de CT-e"
    percentual = original.percentual_reentrega if tipo == "r" else original.percentual_devolucao
    if original.tabela_id <= 0:
        return None, "o tomador não tem tabela de preço no GW"
    if percentual <= 0:
        rotulo = "reentrega" if tipo == "r" else "devolução"
        return None, f"a tabela de preço do tomador não tem % de {rotulo}"
    return valor_frete_gw, f"tabela de preço do tomador ({percentual:g}% do CT-e {original.numero})"


def _aplicar_frete(frame: Frame, valor: float) -> None:
    # O popup já zera a composição do frete e deixa o valor todo em "Frete
    # Valor" -- só troca esse campo e recalcula o total (ICMS etc.).
    frame.evaluate(
        """(valor) => {
            document.getElementById('valor_frete').value = valor;
            recalcular(false);
        }""",
        _fmt_moeda(valor),
    )
    frame.wait_for_timeout(500)


def _salvar(page: Page, frame: Frame, dialogos: _Dialogos) -> bool:
    abaixo = page.frame(name="frameAbaixo")
    if abaixo is None:
        raise EmissaoError("Não achei o rodapé da tela (frameAbaixo) com o botão Salvar.")
    qtd_antes = len(dialogos.mensagens)
    janela = None
    try:
        # O GW valida e, se estiver tudo certo, submete o formulário numa
        # janelinha "pop" -- se a validação falhar, só mostra um alert.
        with page.context.expect_page(timeout=30_000) as info:
            abaixo.click("#salvar")
        janela = info.value
        janela.wait_for_load_state("networkidle", timeout=120_000)
        try:
            dialogos.mensagens.append("Retorno do GW: " + janela.inner_text("body", timeout=5000).strip()[:500])
        except Exception:
            pass
        try:
            janela.wait_for_event("close", timeout=60_000)
        except PlaywrightTimeoutError:
            pass
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(3000)
    try:
        page.wait_for_load_state("networkidle", timeout=60_000)
    except PlaywrightTimeoutError:
        pass

    novas = " ".join(dialogos.mensagens[qtd_antes:]).lower()
    if janela is None:
        return False
    if any(p in novas for p in ("erro", "exception", "não foi possível", "nao foi possivel")):
        return False
    # Depois de salvar o GW recarrega a tela com o CT-e gravado (id > 0).
    frame_atual = page.frame(name="framePrincipal") or frame
    try:
        return int(_num(_ler_formulario(frame_atual)["id_conhecimento"])) > 0 or "sucesso" in novas
    except Exception:
        return "sucesso" in novas


def emitir(
    tipo: str,
    chave_nfe: str,
    escolher_cte: Callable[[list[CteOriginal]], Optional[CteOriginal]],
    informar_frete: Callable[[str], Optional[float]],
    perguntar: Callable[[str], bool],
    confirmar: Callable[[Resultado], bool],
    frete_manual: Optional[float] = None,
    cte_id: Optional[str] = None,
    simular: bool = False,
    headless: bool = True,
) -> Resultado:
    """Lança o CT-e `tipo` (chave de `TIPOS`) para a NF-e informada e salva
    sem transmitir. `simular=True` faz tudo menos salvar.

    - escolher_cte(lista): chamado quando a NF está em mais de um CT-e;
      retorna o escolhido (None cancela).
    - informar_frete(motivo): chamado quando o GW não tem o frete. Na
      simulação pode devolver None -- o resultado volta com
      `frete_pendente=True` pra quem chamou pedir o valor depois.
    - perguntar(mensagem): confirmações do GW que não têm resposta fixa.
    - confirmar(resultado): última conferência antes de salvar.
    - frete_manual: usa esse valor no lugar do que o GW calcular.
    - cte_id: id (do GW) do CT-e original já escolhido antes -- pula o
      escolher_cte.
    """
    if tipo not in TIPOS:
        raise EmissaoError(f"Tipo de CT-e '{tipo}' não suportado. Use um destes: {', '.join(TIPOS)}.")
    chave = validar_chave_nfe(chave_nfe)
    valor_tipo, rotulo_tipo = TIPOS[tipo]
    dialogos = _Dialogos(perguntar)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1600, "height": 1200})
        context.on("page", lambda pg: pg.on("dialog", dialogos))
        page = context.new_page()
        try:
            scrape._login(page)
            frame = _abrir_cadastro(page)

            frame.select_option("#tipoConhecimento", valor_tipo)
            frame.wait_for_timeout(800)

            with context.expect_page(timeout=30_000) as info:
                frame.click("#localiza_ctrc")
            popup = info.value
            popup.wait_for_load_state("networkidle")
            _pesquisar_por_chave(popup, chave)

            achados = _ler_resultados(popup)
            if not achados:
                raise EmissaoError(
                    f"Nenhum CT-e do GW tem a NF-e {chave} (em todas as filiais) disponível para {rotulo_tipo}. "
                    "Confira a chave -- ou se o CT-e original já foi substituído/cancelado."
                )
            if cte_id:
                original = next((c for c in achados if c.id == str(cte_id)), None)
                if original is None:
                    raise EmissaoError(
                        "O CT-e escolhido não aparece mais na busca do GW para essa NF "
                        "(pode ter sido substituído ou cancelado nesse meio tempo)."
                    )
            else:
                original = achados[0] if len(achados) == 1 else escolher_cte(achados)
            if original is None:
                raise EmissaoError("Nenhum CT-e escolhido -- emissão cancelada.")

            popup.click(f"div.linkEditar[onclick*='retornapai({original.id})']")
            try:
                popup.wait_for_event("close", timeout=90_000)
            except PlaywrightTimeoutError:
                scrape._salvar_debug(popup, "emissao_cte_popup")
                raise EmissaoError("O GW não terminou de carregar os dados do CT-e original (popup não fechou).")
            frame.wait_for_timeout(2000)
            page.wait_for_load_state("networkidle")

            dados = _ler_formulario(frame)
            if dados["id_original"] != original.id:
                scrape._salvar_debug(page, "emissao_cte")
                raise EmissaoError(f"O GW não vinculou o CT-e original {original.numero} ao novo lançamento.")

            valor_gw = _num(dados["valor_frete"])
            frete_pendente = False
            if frete_manual is not None:
                frete, origem = frete_manual, "informado pelo usuário"
            else:
                frete, origem = _frete_da_tabela(valor_tipo, original, valor_gw)
                if frete is None:
                    frete = informar_frete(origem)
                    if frete is None:
                        if not simular:
                            raise EmissaoError(f"Sem valor de frete ({origem}). Nada foi salvo.")
                        frete_pendente = True
                    else:
                        origem = f"informado pelo usuário ({origem})"
            if frete is not None and abs(frete - valor_gw) >= 0.005:
                _aplicar_frete(frame, frete)
            dados = _ler_formulario(frame)

            resultado = Resultado(
                tipo=rotulo_tipo,
                original=original,
                numero_ct=dados["numero_ct"],
                remetente=dados["remetente"],
                destinatario=dados["destinatario"],
                tomador=dados["tomador"],
                frete=0.0 if frete_pendente else _num(dados["valor_frete"]),
                total_prestacao=0.0 if frete_pendente else _num(dados["total"]),
                origem_frete=origem,
                frete_pendente=frete_pendente,
                mensagens=dialogos.mensagens,
            )
            if frete_pendente:
                return resultado
            if resultado.frete <= 0:
                raise EmissaoError("Frete zerado -- o GW não aceitou o valor. Nada foi salvo.")
            if simular or not confirmar(resultado):
                return resultado

            resultado.salvo = _salvar(page, frame, dialogos)
            if not resultado.salvo:
                scrape._salvar_debug(page, "emissao_cte_salvar")
                return resultado
            # O número mostrado antes de salvar é só o próximo livre -- se
            # alguém lançou outro CT-e nesse meio tempo, o gravado é outro.
            try:
                salvo = _ler_formulario(page.frame(name="framePrincipal") or frame)
                if int(_num(salvo["id_conhecimento"])) > 0 and salvo["numero_ct"]:
                    resultado.numero_ct = salvo["numero_ct"]
            except Exception:
                logger.exception("Não consegui ler o número do CT-e depois de salvar.")
            return resultado
        finally:
            scrape._logout(page)
            browser.close()
