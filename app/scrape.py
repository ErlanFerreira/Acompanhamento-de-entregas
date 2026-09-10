"""Automação de navegador (Playwright) contra o portal Webtrans (GW Sistemas).

Por que isso existe: a API "GW Serviços" só retorna cargas onde o CNPJ
logado é parte (remetente/destinatário) -- um escopo de "cliente", não de
transportadora. O login normal do usuário no portal Webtrans tem a visão
completa da empresa, então automatizamos exatamente os cliques que um
usuário faria: gerar o relatório "Pendências" (ver `baixar_relatorio_pendencias`)
e consultar entregas por número de NF (ver `consultar_notas_fiscais`).

Roda via Playwright (precisa de `playwright install chromium` previamente).
"""

import datetime
import logging
import re

import requests
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import config

logger = logging.getLogger("scrape")

PORTAL_URL = "https://webtrans.saas.gwsistemas.com.br"
RELATORIO_URL = f"{PORTAL_URL}/relcartafrete.jsp?acao=iniciar&modulo=webtrans"
CONSULTA_ENTREGA_URL = f"{PORTAL_URL}/consulta_entrega_ctrc.jsp?acao=iniciar"
NOME_RELATORIO = "Pendências"


class ScrapeError(Exception):
    pass


def _fmt_data(d: datetime.date) -> str:
    return d.strftime("%d/%m/%Y")


def _logout(page: Page) -> None:
    """Desconecta explicitamente a sessão antes de fechar o navegador.

    Por que isso existe: o portal só permite uma sessão ativa por login, e
    antes disso a automação só fechava o navegador sem avisar o servidor --
    deixando sessões "penduradas" que competiam com o próprio usuário e com
    execuções seguintes da automação. O botão "Sair" só existe no menu
    principal (SPA) -- as telas JSP antigas (ex: Consulta Entrega) não têm
    essa barra lateral, então navega de volta pro menu antes.
    """
    try:
        page.goto(f"{PORTAL_URL}/menu", wait_until="networkidle")
        try:
            page.click("text=Pular tour", timeout=2000)
        except PlaywrightTimeoutError:
            pass
        page.click(".logout-item", timeout=5000)
        page.click('button:has-text("Confirmar")', timeout=5000)
        page.wait_for_timeout(1500)
    except Exception:
        logger.exception("Falha ao tentar fazer logout explícito (ignorando).")


def _salvar_debug(page: Page, prefixo: str) -> None:
    """Salva screenshot + HTML da página no diretório de trabalho -- usado
    quando uma navegação falha, pra dar pra inspecionar depois (ex: como
    artefato do GitHub Actions) o que o portal realmente mostrou."""
    try:
        page.screenshot(path=f"{prefixo}_falha.png", full_page=True)
        with open(f"{prefixo}_falha.html", "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception:
        logger.exception("Falha ao salvar debug de %s", prefixo)


def _login(page: Page) -> None:
    if not config.PORTAL_EMAIL or not config.PORTAL_SENHA:
        raise ScrapeError("PORTAL_EMAIL e PORTAL_SENHA precisam estar configurados.")

    page.goto(f"{PORTAL_URL}/menu", wait_until="networkidle")

    # O login às vezes falha silenciosamente na primeira tentativa (parece
    # uma condição de corrida com scripts de terceiros da página) -- tenta
    # algumas vezes antes de desistir. Usa a URL (não o título, que fica
    # "Loading ..." transitoriamente) para decidir se ainda está na tela de
    # login.
    for _tentativa in range(3):
        if "/login" not in page.url:
            break
        page.wait_for_timeout(1500)
        page.fill('input[name="login"]', config.PORTAL_EMAIL)
        page.fill('input[name="senha"]', config.PORTAL_SENHA)
        page.click("button.button-login")
        page.wait_for_timeout(3000)
        page.wait_for_load_state("networkidle")

    if "/login" in page.url:
        _salvar_debug(page, "login")
        raise ScrapeError("Login no portal GW Sistemas falhou -- verifique PORTAL_EMAIL/PORTAL_SENHA.")

    # Após o login, a SPA ainda redireciona/inicializa a sessão por um
    # instante -- navegar cedo demais para outra URL derruba a sessão com
    # erro 500.
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)


def baixar_relatorio_pendencias(data_inicial: datetime.date, data_final: datetime.date) -> bytes:
    """Faz login no portal Webtrans, gera o relatório personalizado
    "Pendências" para o período informado e retorna os bytes do .xlsx."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            _login(page)

            page.goto(RELATORIO_URL, wait_until="networkidle")

            page.click("text=Relatórios Personalizados")
            page.wait_for_timeout(500)

            radio = page.locator("label", has_text=NOME_RELATORIO).locator("input[type=radio]")
            radio.click()
            page.wait_for_timeout(1500)  # AJAX carrega os filtros dinâmicos do relatório

            page.fill("#argumento_0", _fmt_data(data_inicial))
            page.fill("#input_0_ate", _fmt_data(data_final))
            page.check("#excel2")

            with page.expect_popup(timeout=30_000) as popup_info:
                page.evaluate("gerarRelatorio(true)")
            popup = popup_info.value
            popup.wait_for_selector("text=Clique no link", timeout=180_000)
            link = popup.eval_on_selector("a", "el => el.href")
            popup.close()

            if not link:
                raise ScrapeError("Não encontrei o link de download do relatório gerado.")
        finally:
            _logout(page)
            browser.close()

    resp = requests.get(link, timeout=180)
    resp.raise_for_status()
    return resp.content


# ------------------------------------------------- consulta por NF ----

_BLOCO_CTE_RE = re.compile(r"(\d{4,10}/\d{1,3})")
_STATUS_RE = re.compile(r"STATUS:\s*(.*?)\s*Notas Fiscais:", re.DOTALL)
_PREVISAO_RE = re.compile(r"Previsão entrega:\s*(\d{2}/\d{2}/\d{4})")
_ENTREGA_RE = re.compile(r"(?<!Previsão )Entrega:\s*(\d{2}/\d{2}/\d{4})?\s*às:\s*([\d:]*)\s*OBS:\s*(.*)")
_ATRASO_RE = re.compile(r"Atraso de (\d+) Dias")


def _parse_resultado(texto: str) -> list[dict]:
    """Extrai os blocos de resultado (um por CT-e) do texto visível da
    tabela de resultados da consulta de entrega."""
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    # Descarta o cabeçalho da tabela.
    corpo = "\n".join(linhas)
    if "STATUS:" not in corpo:
        return []

    # Cada bloco começa num número de CT-e ("068499/5") e vai até o próximo.
    indices = [m.start() for m in _BLOCO_CTE_RE.finditer(corpo)]
    blocos = []
    for i, start in enumerate(indices):
        end = indices[i + 1] if i + 1 < len(indices) else len(corpo)
        blocos.append(corpo[start:end])

    resultados = []
    for bloco in blocos:
        cte_match = _BLOCO_CTE_RE.match(bloco)
        status_match = _STATUS_RE.search(bloco)
        previsao_match = _PREVISAO_RE.search(bloco)
        entrega_match = _ENTREGA_RE.search(bloco)
        atraso_match = _ATRASO_RE.search(bloco)

        resultados.append({
            "cte": cte_match.group(1) if cte_match else None,
            "status": status_match.group(1).strip() if status_match else None,
            "previsao_entrega": previsao_match.group(1) if previsao_match else None,
            "data_entrega": entrega_match.group(1) if entrega_match and entrega_match.group(1) else None,
            "hora_entrega": entrega_match.group(2) if entrega_match and entrega_match.group(2) else None,
            "observacao": entrega_match.group(3).strip() if entrega_match else None,
            "dias_atraso": int(atraso_match.group(1)) if atraso_match else None,
        })
    return resultados


def consultar_notas_fiscais(numeros_nf: list[str], progresso=None) -> dict[str, list[dict]]:
    """Faz login uma vez e consulta cada número de NF na tela "Consulta
    Entrega" do portal Webtrans, reaproveitando a mesma página/sessão.

    Retorna um dict {numero_nf: [resultado, ...]} -- lista vazia se a NF não
    foi encontrada, mais de um item se a NF aparece em mais de um CT-e.

    `progresso`, se informado, é chamado como progresso(i, total) após cada
    consulta (para reportar andamento de lotes grandes).
    """
    resultados: dict[str, list[dict]] = {}
    total = len(numeros_nf)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            _login(page)

            # Assim como o login, essa navegação às vezes não termina de
            # carregar o formulário na primeira tentativa (SPA) -- tenta de
            # novo antes de desistir em vez de estourar timeout direto.
            # Um caso específico e já confirmado: o portal só permite uma
            # sessão ativa por login, então se alguém (ex: o próprio dono
            # da conta) estiver logado ao mesmo tempo no navegador normal,
            # essa navegação volta com HTTP 401 em vez do formulário.
            for _tentativa in range(3):
                resposta = page.goto(CONSULTA_ENTREGA_URL, wait_until="networkidle")
                if resposta is not None and resposta.status == 401:
                    if _tentativa == 2:
                        _salvar_debug(page, "consulta_entrega")
                        raise ScrapeError(
                            "O portal Webtrans recusou a sessão (401) ao abrir a tela de Consulta Entrega. "
                            "Pode ser sessão duplicada ou bloqueio temporário -- tente de novo em alguns minutos."
                        )
                    page.wait_for_timeout(5000)
                    continue
                try:
                    page.wait_for_selector("#tipoFiltro2", timeout=15_000)
                    break
                except PlaywrightTimeoutError:
                    if _tentativa == 2:
                        _salvar_debug(page, "consulta_entrega")
                        raise
                    page.wait_for_timeout(2000)

            page.check("#tipoFiltro2")
            page.uncheck("#chkNaoEntregue")

            for i, numero in enumerate(numeros_nf, start=1):
                try:
                    page.fill("#valorConsultaNota", str(numero))
                    page.click("#visualizar")
                    page.wait_for_timeout(1200)
                    page.wait_for_load_state("networkidle")
                    texto = page.eval_on_selector("#formBx", "el => el.innerText")
                    resultados[numero] = _parse_resultado(texto)
                except Exception:
                    logger.exception("Falha ao consultar NF %s", numero)
                    resultados[numero] = []
                if progresso:
                    progresso(i, total)
        finally:
            _logout(page)
            browser.close()

    return resultados
