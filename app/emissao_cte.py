"""Emissão de CT-e no portal Webtrans (GW Sistemas) a partir da chave da
NF-e: normal, cortesia e os derivados de um CT-e já emitido (diária,
reentrega, devolução, descarga, coleta/pallets, complementar, substituição).

Faz exatamente o que o usuário faria na tela "Conhecimentos > Novo cadastro"
(`frameset_conhecimento`):

1. escolhe o "Tipo" do CT-e;
2. clica no "..." ao lado do tipo, que abre o popup "Localizar conhecimento
   de transporte para complemento, devolução, descarga, reentrega ou
   diárias" (`pops/localiza_conhecimento.jsp`), e pesquisa por
   "Chave Acesso NF-e";
3. escolhe o CT-e original -- o próprio GW copia remetente, destinatário,
   tomador, notas e calcula o frete (`retornapai` do popup);
   Normal e Cortesia não têm CT-e original: em vez do passo 2-3, importa a
   nota pelo "Importar XML/DANFE" do próprio GW (chave da NF-e, procurando
   no banco do GW e no servidor DF-e -- nunca na consulta pública, que pede
   captcha), e o GW preenche participantes e calcula o frete pela tabela;
4. confere o frete e SALVA sem transmitir (o botão "Salvar [F8]", nunca o
   "Salvar e Transmitir") -- a transmissão à SEFAZ fica com o usuário.

De onde vem o frete (regra do próprio popup do GW):
- Reentrega/Devolução: total do CT-e original x "% reentrega"/"% devolução"
  da tabela de preço do cliente, respeitando o frete mínimo. Sem tabela ou
  com o % zerado, o GW não tem o valor -> pergunta ao usuário.
- Substituição: o mesmo valor do CT-e original.
- Diária/Descarga/Complementar/Coleta-Pallets: a tabela não tem valor
  próprio pra isso (o GW só repete o total do original -- ou, em
  Coleta/Pallets, aplica o % de devolução) -> pergunta ao usuário.
- Normal/Cortesia: a tabela de preço do tomador, calculada pelo GW ao
  importar a nota. Sem tabela -> pergunta ao usuário.

"Frete" aqui é a soma da composição (frete peso, frete valor, despacho,
GRIS, pedágio...), sem os impostos. Quando o usuário informa outro valor, a
composição é zerada e o valor todo vai em "Frete Valor" (como o próprio GW
faz nos CT-e derivados).

As interações (escolher entre vários CT-e, informar frete, responder a
confirmações do GW, confirmar antes de salvar) ficam a cargo de quem chama,
via callbacks -- ver `scripts/emitir_cte.py`.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from playwright.sync_api import Dialog, Frame, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import scrape
from .tipos_cte import TIPOS, TIPOS_DA_NOTA, EmissaoError, validar_chave_nfe  # noqa: F401 (reexportados pro CLI)

logger = logging.getLogger("emissao_cte")

CADASTRO_URL = f"{scrape.PORTAL_URL}/frameset_conhecimento?acao=iniciar"

# Campo escondido de cada linha do popup com o % da tabela do cliente que o
# GW aplica sobre o total do CT-e original.
_CAMPO_PERCENTUAL = {"r": "valorReentrega", "d": "valorDevolucao"}

# Campos da "Composição do Frete" (somados; o desconto é subtraído).
_COMPOSICAO = ("valor_taxa_fixa", "valor_itr", "valor_peso", "valor_frete", "valor_despacho", "valor_ademe",
               "valor_outros", "valor_gris", "valor_pedagio", "valor_sec_cat")


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
    original: Optional[CteOriginal]  # None em Normal/Cortesia (vêm da nota)
    numero_ct: str
    remetente: str
    destinatario: str
    tomador: str
    frete: float
    total_prestacao: float
    origem_frete: str
    observacao: str = ""
    frete_pendente: bool = False
    # Frete do tomador é por peso/cubagem e a nota não traz os m³: quem pediu
    # informa a cubagem e o GW calcula pela tabela (em vez de digitar o frete).
    precisa_cubagem: bool = False
    salvo: bool = False
    mensagens: list[str] = field(default_factory=list)


def _num(v) -> float:
    try:
        n = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0
    return n if math.isfinite(n) else 0.0


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


# Normal/Cortesia saem da nota: se ela já tem CT-e no GW, emitir de novo
# duplicaria o frete -- quem for cobrar outra coisa usa reentrega,
# complementar etc.
_TIPOS_CHECAR_EXISTENTE = {"normal", "cortesia"}


def _ctes_da_nota(context, chave: str) -> list[CteOriginal]:
    """CT-e do GW (todas as filiais) que já têm essa NF-e -- a mesma busca
    do popup "Localizar conhecimento", aberta numa aba à parte (só leitura)."""
    aba = context.new_page()
    try:
        aba.goto(f"{scrape.PORTAL_URL}/pops/localiza_conhecimento.jsp?acao=iniciar&tipoConhecimento=r"
                 "&isCancelado=false&filialId=0", wait_until="networkidle")
        _pesquisar_por_chave(aba, chave)
        return _ler_resultados(aba)
    finally:
        aba.close()


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
        """(composicao) => {
            const v = (id) => (document.getElementById(id) || {}).value || '';
            const n = (id) => parseFloat(v(id).replace(',', '.')) || 0;
            // Laço comum: a tela do GW usa o Prototype.js, que troca o
            // Array.prototype.reduce por um que não soma (dava NaN).
            let soma = -n('valor_desconto');
            for (const id of composicao) soma += n(id);
            return {
                numero_ct: v('nfiscal'), remetente: v('rem_rzs'), destinatario: v('dest_rzs'),
                tomador: v('con_rzs'), valor_frete: v('valor_frete'), total: v('total'),
                id_original: v('id_dev_reen'), id_conhecimento: v('idConhecimento'),
                observacao: [1, 2, 3, 4, 5].map(i => v('obs_lin' + i)).filter(Boolean).join(' | '),
                tabela_id: v('client_tariff_id'),
                tipo_gw: v('tipoConhecimento'),
                tipo_taxa: v('tipotaxa'), cub_metro: v('cub_metro'),
                composicao: soma,
            };
        }""",
        list(_COMPOSICAO),
    )


def _anotar_solicitante(frame: Frame, solicitante: str) -> None:
    """Grava quem pediu na primeira linha livre da observação do CT-e (a
    primeira o GW já usa: "Reentrega do CTRC: ..."). O robô entra no GW com
    um usuário só dele, então é assim que dá pra saber, olhando o CT-e no
    GW, quem pediu a emissão. Sai também no DACTE."""
    frame.evaluate(
        """(texto) => {
            for (let i = 2; i <= 5; i++) {
                const campo = document.getElementById('obs_lin' + i);
                if (campo && !campo.value.trim()) { campo.value = texto; return; }
            }
            const ultima = document.getElementById('obs_lin5');
            if (ultima) ultima.value = (ultima.value + ' | ' + texto).slice(0, 399);
        }""",
        f"Solicitado por: {solicitante} (painel)"[:399],
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
    # Mesmo que o popup do GW faz nos CT-e derivados: zera a composição e
    # deixa o valor todo em "Frete Valor"; depois recalcula o total (ICMS etc.).
    frame.evaluate(
        """([valor, campos]) => {
            for (const id of campos.concat(['valor_desconto'])) {
                const c = document.getElementById(id);
                if (c) c.value = '0.00';
            }
            document.getElementById('valor_frete').value = valor;
            recalcular(false);
        }""",
        [_fmt_moeda(valor), list(_COMPOSICAO)],
    )
    frame.wait_for_timeout(500)


def _carregar_original(context, frame: Frame, chave: str, rotulo_tipo: str, cte_id: Optional[str],
                       escolher_cte: Callable[[list[CteOriginal]], Optional[CteOriginal]]) -> CteOriginal:
    """Passos 2-3: acha o CT-e original pela chave da NF no popup do GW e
    deixa o GW copiar os dados dele pro lançamento novo."""
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
    return original


# Avisos do GW quando o servidor dele engasga (passageiro).
_INSTAVEL = ("status: 502", "tempo limite", "status: 503", "status: 504")


def _gw_instavel(mensagens: list[str]) -> bool:
    return any(t in m.lower() for m in mensagens for t in _INSTAVEL)


def _importar_nota(context, frame: Frame, chave: str, dialogos: Optional[_Dialogos] = None) -> dict:
    """Normal/Cortesia: "Importar XML/DANFE" do GW com a chave da NF-e. O GW
    procura a nota no banco dele e, se não tiver, no servidor DF-e; a consulta
    pública fica desligada (pede captcha). Só preenche a tela -- não grava
    nada (conferido: o próximo número de CT-e não muda)."""
    with context.expect_page(timeout=30_000) as info:
        frame.click("#bt_import")
    popup = info.value
    popup.wait_for_load_state("networkidle")
    popup.wait_for_selector("#chaveAcessoNFe_0", timeout=30_000)
    popup.evaluate("""() => {
        document.getElementById('consultarNoServidorDFe').checked = true;
        document.getElementById('consultaPublica').checked = false;
        const api = document.getElementById('consultarNaApi');
        if (api) api.checked = false;
    }""")
    popup.fill("#chaveAcessoNFe_0", chave)
    avisos = dialogos.mensagens if dialogos is not None else []
    achou = False
    for tentativa in range(3):
        # O servidor do GW às vezes responde "Status: 502 / tempo limite" na
        # busca da nota: espera um pouco e tenta de novo (até 2 vezes).
        antes = len(avisos)
        popup.click("#consultarChaveAcesso_0")
        for _ in range(90):
            if popup.is_visible("#removerChaveAcesso_0"):
                achou = True
                break
            if _gw_instavel(avisos[antes:]):
                break
            popup.wait_for_timeout(1000)
        if achou or not _gw_instavel(avisos[antes:]):
            break
        logger.info("GW instável ao buscar a NF-e %s -- tentando de novo (%s).", chave, tentativa + 1)
        popup.wait_for_timeout(5000 * (tentativa + 1))
    if not achou:
        scrape._salvar_debug(popup, "emissao_cte_importar")
        if _gw_instavel(avisos):
            raise EmissaoError(
                "O GW está instável (erro 502 / tempo esgotado) e não respondeu à busca da nota, mesmo "
                "tentando 3 vezes. Tente de novo em alguns minutos."
            )
        raise EmissaoError(
            f"O GW não encontrou a NF-e {chave} (nem no banco dele, nem no servidor DF-e). "
            "Confira a chave -- ou importe o XML da nota no GW e peça de novo."
        )
    popup.click("#btVisualizar")
    # O GW importa e preenche a tela de cadastro (remetente, notas, frete).
    try:
        frame.wait_for_function("() => (document.getElementById('rem_rzs') || {}).value", timeout=120_000)
    except PlaywrightTimeoutError:
        raise EmissaoError(
            "O GW não terminou de importar a nota" +
            (" (servidor instável: erro 502 / tempo esgotado)" if _gw_instavel(avisos) else "") +
            ". Tente de novo em alguns minutos."
        )
    try:
        popup.wait_for_event("close", timeout=30_000)
    except PlaywrightTimeoutError:
        pass
    frame.wait_for_timeout(3000)
    return _ler_formulario(frame)


MOTIVO_CUBAGEM = "falta a cubagem (m³) da nota -- o frete desse cliente é por peso/cubagem"


def _falta_cubagem(dados: dict) -> bool:
    # Tipo de taxa "Peso/Cubagem" (1) sem os m³: o GW avisa e não calcula
    # (a nota fiscal não traz a cubagem).
    return dados.get("tipo_taxa") == "1" and _num(dados.get("cub_metro")) <= 0


def _informar_cubagem(frame: Frame, cubagem: float) -> dict:
    """Preenche o "=  m³" da cubagem como uma pessoa faria; o GW busca a
    tabela do tomador (com a base kg/m³ dele) e recalcula o frete -- usando o
    maior entre o peso real e o cubado."""
    frame.fill("#cub_metro", f"{cubagem:.4f}")
    frame.dispatch_event("#cub_metro", "change")
    frame.wait_for_timeout(4000)
    return _ler_formulario(frame)


def _frete_da_nota(dados: dict, valor_gw: float, mensagens: list[str]) -> tuple[Optional[float], str]:
    """Normal/Cortesia: o GW calcula pela tabela do tomador ao importar."""
    if _falta_cubagem(dados):
        return None, MOTIVO_CUBAGEM
    sem_tabela = any("não possui tabela de preço" in m for m in mensagens)
    if sem_tabela or int(_num(dados.get("tabela_id"))) <= 0:
        return None, "o tomador não tem tabela de preço no GW"
    if valor_gw <= 0:
        return None, "a tabela de preço do tomador não calculou o frete dessa nota"
    return valor_gw, "tabela de preço do tomador"


def _corrigir_obs_pallets(frame: Frame, numero_original: str) -> None:
    # O popup do GW trata Coleta/Pallets como devolução na observação
    # ("Devolução do CTRC: ...") -- corrige o texto.
    frame.evaluate(
        """(texto) => {
            const c = document.getElementById('obs_lin1');
            if (c && /^Devolu/.test(c.value)) c.value = texto;
        }""",
        f"Coleta/Pallets do CTRC: {numero_original}",
    )


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
    solicitante: Optional[str] = None,
    simular: bool = False,
    headless: bool = True,
    sessao: Optional["SessaoGW"] = None,
    cubagem: Optional[float] = None,
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
    - solicitante: nome de quem pediu -- vai na observação do CT-e.
    - cubagem: m³ da carga (Normal/Cortesia), quando o frete do tomador é por
      cubagem e a nota não traz -- o GW calcula o frete com ela.
    - sessao: navegador já logado (robô local, ver SessaoGW) -- sem ela,
      abre um navegador, entra no GW e sai no fim.
    """
    if tipo not in TIPOS:
        raise EmissaoError(f"Tipo de CT-e '{tipo}' não suportado. Use um destes: {', '.join(TIPOS)}.")
    chave = validar_chave_nfe(chave_nfe)
    valor_tipo, rotulo_tipo = TIPOS[tipo]
    dialogos = _Dialogos(perguntar)

    args = (tipo, chave, valor_tipo, rotulo_tipo, dialogos, escolher_cte, informar_frete, confirmar,
            frete_manual, cte_id, solicitante, simular, cubagem)
    if sessao is not None:
        return _emitir(sessao, *args)
    with SessaoGW(headless=headless) as nova:
        return _emitir(nova, *args)


class SessaoGW:
    """Navegador logado no GW, reaproveitado entre um CT-e e outro -- o robô
    local (scripts/robo_emissao.py) entra uma vez e emite vários em seguida,
    sem pagar o login (~25 s) a cada um. Use com `with`: no fim, sai do GW."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._dialogos: Optional[_Dialogos] = None

    def __enter__(self) -> "SessaoGW":
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context(viewport={"width": 1600, "height": 1200})
        self.context.on("page", lambda pg: pg.on("dialog", self._responder))
        self.page = self.context.new_page()
        scrape._login(self.page)
        return self

    def __exit__(self, *exc) -> None:
        try:
            scrape._logout(self.page)
        finally:
            self.browser.close()
            self._pw.stop()

    def _responder(self, dialog: Dialog) -> None:
        if self._dialogos is not None:
            self._dialogos(dialog)
        else:
            dialog.accept()

    def comecar(self, dialogos: _Dialogos) -> Frame:
        """Prepara a sessão para um CT-e novo: fecha janelas que sobraram do
        anterior, passa os avisos do GW pra este CT-e e abre o cadastro em
        branco -- entrando de novo no GW se a sessão tiver caído."""
        self._dialogos = dialogos
        for pg in self.context.pages:
            if pg is not self.page:
                pg.close()
        try:
            return _abrir_cadastro(self.page)
        except EmissaoError:
            scrape._login(self.page)
            return _abrir_cadastro(self.page)


def _emitir(sessao: SessaoGW, tipo, chave, valor_tipo, rotulo_tipo, dialogos, escolher_cte, informar_frete,
            confirmar, frete_manual, cte_id, solicitante, simular, cubagem=None) -> Resultado:
    page, context = sessao.page, sessao.context
    frame = sessao.comecar(dialogos)

    frame.select_option("#tipoConhecimento", valor_tipo)
    frame.wait_for_timeout(800)

    if tipo in TIPOS_DA_NOTA:
        original = None
        if tipo in _TIPOS_CHECAR_EXISTENTE:
            existentes = _ctes_da_nota(context, chave)
            if existentes:
                lista = ", ".join(f"{c.numero} série {c.serie} de {c.emissao}" for c in existentes[:3])
                raise EmissaoError(
                    f"Essa NF-e já tem CT-e no GW ({lista}) -- não emiti outro {rotulo_tipo} pra não "
                    "duplicar. Se for outra cobrança, use Reentrega, Complementar etc."
                )
        dados = _importar_nota(context, frame, chave, dialogos)
        if dados["tipo_gw"] != valor_tipo:
            # A importação não pode trocar o tipo (ex.: devolução com
            # NFD virar Normal) -- volta pro escolhido.
            frame.select_option("#tipoConhecimento", valor_tipo)
            frame.wait_for_timeout(800)
            dados = _ler_formulario(frame)
            if dados["tipo_gw"] != valor_tipo:
                raise EmissaoError(f"O GW trocou o tipo do CT-e ao importar a nota ({dados['tipo_gw']}).")
        if cubagem and cubagem > 0:
            dados = _informar_cubagem(frame, cubagem)
    else:
        original = _carregar_original(context, frame, chave, rotulo_tipo, cte_id, escolher_cte)
        page.wait_for_load_state("networkidle")
        dados = _ler_formulario(frame)
        if dados["id_original"] != original.id:
            scrape._salvar_debug(page, "emissao_cte")
            raise EmissaoError(f"O GW não vinculou o CT-e original {original.numero} ao novo lançamento.")
        if valor_tipo == "p":
            _corrigir_obs_pallets(frame, original.numero)

    valor_gw = round(_num(dados["composicao"]), 2)
    frete_pendente = False
    if frete_manual is not None:
        frete, origem = frete_manual, "informado pelo usuário"
    else:
        if original is None:
            frete, origem = _frete_da_nota(dados, valor_gw, dialogos.mensagens)
            if frete is not None and cubagem:
                origem = f"{origem}, com {cubagem:g} m³".replace(".", ",")
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
    if solicitante:
        _anotar_solicitante(frame, solicitante)
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
        frete=0.0 if frete_pendente else round(_num(dados["composicao"]), 2),
        total_prestacao=0.0 if frete_pendente else _num(dados["total"]),
        origem_frete=origem,
        observacao=dados["observacao"],
        frete_pendente=frete_pendente,
        precisa_cubagem=frete_pendente and origem == MOTIVO_CUBAGEM,
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
